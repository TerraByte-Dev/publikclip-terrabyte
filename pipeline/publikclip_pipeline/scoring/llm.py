"""LLM backends: Gemini (BYO key, default) and Ollama (local fallback).

One interface: generate_json(prompt, schema, images) → dict, with disk
caching keyed on (backend, model, prompt, schema) so re-runs never re-spend
— the M2 gate requires cache hits on identical inputs.

Key resolution: PUBLIKCLIP_GEMINI_API_KEY env var, then
PUBLIKCLIP_HOME/secrets.json {"gemini_api_key": "..."} (written by the
app's onboarding). Ollama needs no key — just a running daemon, and PUBLIKCLIP_OLLAMA_MODEL
to override which of its models judges.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from .. import config

# The rolling alias, deliberately: Google retires pinned models for NEW api
# keys while still advertising them in ListModels (learned live — 404 "no
# longer available to new users" on gemini-2.5-flash with a fresh key).
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OLLAMA_URL = "http://localhost:11434"
LLM_TIMEOUT = 120.0
OLLAMA_TIMEOUT = 600.0

# Ollama sizes the KV cache from the model's FULL trained context unless told
# otherwise, and a 131k-context 8B model then wants ~14,336 MiB of KV — which
# pushes most of the model back onto the CPU (9 of 29 layers stayed on the GPU)
# and drops generation to ~0.05 tok/s, i.e. a hang, not an error.
#
# 8192 is a CEILING, not headroom. Measured on qwen3.5:latest / 3070 Laptop
# 8 GiB, reading `load_tensors: offloaded N/34` out of Ollama's server.log:
#    8192 -> 34/34 layers, 256 MiB KV,  2 graph splits
#   10240 -> 33/34 layers, 320 MiB KV, 19 graph splits   <-- spills
#   16384 -> 32/34 layers, 512 MiB KV, 36 graph splits, -30% tok/s
# The weight buffer already saturates the card, so ~130 MiB of extra allocation
# costs a whole layer. Never raise this to "make room" — cap the input instead.
#
# It has to hold prompt AND output across all three local call sites. Measured:
# T1 208-478 tokens over the 15-75 s candidate window; the music brief ~270
# (brief.py truncates the transcript to 600 chars); the overlay planner ~6.2
# tokens per transcript word — 1,257 at 200 words. All fit. What does NOT fit
# is a reasoning model left to reason — see OLLAMA_THINK.
OLLAMA_NUM_CTX = 8192

# `think` must be a TOP-LEVEL key of the /api/chat body. Put it inside
# "options" and ollama 0.33.0 returns HTTP 200, silently discards it and
# reasons anyway — byte-identical to sending nothing. Untestable by eye, so
# test_llm_picker.py asserts on the body shape.
#
# With thinking at its default, qwen3.5:latest spends the whole context
# reasoning — 275 prompt + 7,919 thinking = 8,194 — returns
# message.content == "", and json.loads("") surfaced as the useless
# "Ollama call failed: Expecting value: line 1 column 1 (char 0)" after ~190 s.
# Every call. Same prompt with think off: 6.6 s, 159 tokens, valid JSON.
# 5 of the 9 models installed here advertise `thinking`, so this is not a
# qwen3.5 quirk. 0.33.0 ignores `think` on models that don't reason (verified
# on dolphin3:8b, llama3.2, qwen2.5-coder), so it is sent unconditionally;
# older daemons returned 400, which makes 0.33.0 the floor.
OLLAMA_THINK = False

# /api/tags reports manifest bytes, NOT VRAM footprint — gemma4:e2b is 7.16 GB
# on disk and loads in 1.71 GB (nested MatFormer weights). So this is a coarse
# sanity bound, not a fit test: it exists solely to stop a rank-by-size picker
# choosing laguna-xs-2.1 (33.4B, 20.3 GB) on an 8 GiB card. It admits
# everything else installed here, including qwen3.5:latest at 6.59 GB.
OLLAMA_MAX_MANIFEST_BYTES = 12_000_000_000


class LlmError(Exception):
    """User-actionable LLM failure (bad key, daemon down, model missing)."""


def gemini_api_key() -> str | None:
    key = os.environ.get("PUBLIKCLIP_GEMINI_API_KEY")
    if key:
        return key
    secrets_path = config.home_dir() / "secrets.json"
    if secrets_path.exists():
        try:
            return json.loads(secrets_path.read_text()).get("gemini_api_key")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _cache_dir() -> Path:
    path = config.home_dir() / "llm-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(
    backend: str,
    model: str,
    prompt: str,
    schema: dict,
    images: list[bytes],
    variant: str = "",
) -> str:
    h = hashlib.sha256()
    h.update(backend.encode())
    h.update(model.encode())
    h.update(prompt.encode())
    h.update(json.dumps(schema, sort_keys=True).encode())
    for img in images:
        h.update(hashlib.sha256(img).digest())
    # think and num_ctx change the answer but were invisible to the key, so a
    # cached reasoning-mode reply would replay verbatim after a fix and make
    # the fix look like a no-op. The empty default keeps Gemini keys byte
    # identical; only Ollama entries roll over.
    h.update(variant.encode())
    return h.hexdigest()[:32]


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse_ollama_reply(payload: dict, model: str) -> dict:
    """One /api/chat reply -> dict, or an LlmError that says what went wrong.

    An empty message.content used to surface as "Ollama call failed: Expecting
    value: line 1 column 1 (char 0)" after a three-minute wait, and stage.py
    hands that string straight to the user as the reason the job died. The
    daemon gives two discriminators the old one-liner threw away:
    done_reason == "length", and a non-empty message.thinking.
    """
    msg = payload.get("message") or {}
    content = _strip_fences(msg.get("content") or "")
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError as err:
            raise LlmError(
                f"Ollama ({model}) did not return JSON ({err}). "
                f"First 200 chars of its answer: {content[:200]!r}"
            ) from err
    if (msg.get("thinking") or "").strip() or payload.get("done_reason") == "length":
        raise LlmError(
            f"Ollama ({model}) spent its whole {OLLAMA_NUM_CTX}-token context reasoning and "
            f"never answered (done_reason={payload.get('done_reason')!r}, "
            f"{payload.get('eval_count')} tokens generated, 0 of them an answer). publikclip "
            'sends "think": false as a top-level key; if it drifted into "options", ollama '
            f"ignores it silently. Otherwise run `ollama show {model}` — if it lists the "
            "`thinking` capability, point PUBLIKCLIP_OLLAMA_MODEL at one that doesn't."
        )
    raise LlmError(
        f"Ollama ({model}) returned an empty message (done_reason={payload.get('done_reason')!r})."
    )


class GeminiClient:
    backend = "gemini"

    def __init__(self, model: str = GEMINI_MODEL):
        self.model = model
        key = gemini_api_key()
        if not key:
            raise LlmError(
                "No Gemini API key found. Add one in Settings (or set "
                "PUBLIKCLIP_GEMINI_API_KEY), or switch to Ollama mode."
            )
        self._key = key

    def generate_json(
        self, prompt: str, schema: dict, images: list[bytes] | None = None
    ) -> dict:
        images = images or []
        cache_file = _cache_dir() / f"{_cache_key(self.backend, self.model, prompt, schema, images)}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())

        parts: list[dict[str, Any]] = [{"text": prompt}]
        for img in images:
            import base64

            parts.append(
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}}
            )
        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": 0.2,
            },
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                # Header, never ?key= — httpx puts the request URL verbatim in
                # every HTTPStatusError message, so a query-param key ends up
                # in tracebacks, logs, and the jobs.error column on any 4xx/5xx.
                res = httpx.post(
                    GEMINI_URL.format(model=self.model),
                    headers={"x-goog-api-key": self._key},
                    json=body,
                    timeout=LLM_TIMEOUT,
                )
                if res.status_code in (401, 403):
                    raise LlmError("Gemini rejected the API key. Check it in Settings.")
                if res.status_code == 429:
                    import time

                    # Surface the API's own words — a quota backoff and a
                    # "credits depleted" billing stop look identical as bare
                    # 429s but need opposite user actions.
                    try:
                        detail = res.json()["error"]["message"]
                    except Exception:  # noqa: BLE001
                        detail = "rate limited"
                    last_err = LlmError(f"Gemini 429: {detail}")
                    if "credit" in detail.lower() or "billing" in detail.lower():
                        raise last_err
                    time.sleep(4 * (attempt + 1))
                    continue
                res.raise_for_status()
                payload = res.json()
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(_strip_fences(text))
                cache_file.write_text(json.dumps(data))
                return data
            except LlmError:
                raise
            except (httpx.HTTPError, KeyError, json.JSONDecodeError, IndexError) as err:
                last_err = err
                # 500/503 is Flash being overloaded and it clears in seconds —
                # three retries fired back-to-back all hit the same bad moment
                # and burn the whole run's scoring stage.
                if attempt < 2:
                    import time

                    time.sleep(4 * (attempt + 1))
        raise LlmError(f"Gemini call failed after retries: {last_err}")


class OllamaClient:
    backend = "ollama"

    def __init__(self, model: str | None = None):
        try:
            res = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            res.raise_for_status()
        except httpx.HTTPError as err:
            raise LlmError(
                "Ollama isn't running. Start it (`ollama serve`) or switch to Gemini mode."
            ) from err
        # Keep the raw records: the picker needs details.parameter_size and
        # capabilities, and flattening to names one line early is exactly what
        # made the old picker score the 9.7B qwen3.5:latest as 0.0.
        entries = res.json().get("models", [])
        names = [m["name"] for m in entries]
        if not names:
            raise LlmError("Ollama has no models. Pull one, e.g. `ollama pull qwen3.5`.")
        if model and model not in names:
            raise LlmError(
                f"Ollama has no model {model!r}. Installed: {', '.join(sorted(names))}"
            )
        self.model = model or _pick_ollama_model(entries)

    def generate_json(
        self, prompt: str, schema: dict, images: list[bytes] | None = None
    ) -> dict:
        images = images or []
        # Images ARE passed through now: qwen3.5 advertises `vision` in
        # /api/tags and reads a game HUD crop correctly (5/2/2 kill rows on
        # three Valorant frames, correctly empty on two others). A model
        # without vision returns a normal answer that simply ignores them.
        #
        # The cache key MUST include the images. It used to hardcode [] here,
        # which was harmless only because images were discarded one line above
        # — lift that without this and every crop of every video collides on
        # one key, so frame 2 onward is served frame 1's answer. On a 2700-crop
        # scan that is a flat, confidently wrong curve.
        variant = f"think={OLLAMA_THINK};num_ctx={OLLAMA_NUM_CTX}"
        cache_file = (
            _cache_dir()
            / f"{_cache_key(self.backend, self.model, prompt, schema, images, variant)}.json"
        )
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        message: dict = {"role": "user", "content": prompt}
        if images:
            import base64

            message["images"] = [base64.b64encode(img).decode() for img in images]
        body = {
            "model": self.model,
            "messages": [message],
            "format": schema,
            "stream": False,
            # TOP-LEVEL, never inside "options" — see OLLAMA_THINK.
            "think": OLLAMA_THINK,
            "options": {"temperature": 0.1, "num_ctx": OLLAMA_NUM_CTX},
        }
        try:
            res = httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=OLLAMA_TIMEOUT)
            if res.status_code >= 400:
                # raise_for_status keeps only the status and the URL, so
                # ollama's own explanation — '"nomic-embed-text:latest" does
                # not support chat' — is discarded exactly when it is the only
                # useful thing in the reply.
                try:
                    detail = res.json().get("error") or res.text[:200]
                except ValueError:
                    detail = res.text[:200]
                raise LlmError(f"Ollama ({self.model}) rejected the request: {detail}")
            data = _parse_ollama_reply(res.json(), self.model)
        except httpx.TimeoutException as err:
            # Two causes look identical from here: a model paging out of VRAM
            # (~0.05 tok/s), or a reasoning model generating at full speed into
            # a context it will never escape. Name both — the old text sent
            # operators hunting a VRAM problem on a 100%-resident model.
            raise LlmError(
                f"Ollama ({self.model}) produced nothing in {OLLAMA_TIMEOUT:.0f}s. Either it is "
                "spilling out of VRAM (free the GPU, or set PUBLIKCLIP_OLLAMA_MODEL to a smaller "
                "model), or it reasoned past the context — check `ollama show` for the `thinking` "
                "capability. Or switch to Gemini mode."
            ) from err
        except (httpx.HTTPError, json.JSONDecodeError) as err:
            raise LlmError(f"Ollama call failed: {err}") from err
        cache_file.write_text(json.dumps(data))
        return data


_PARAM_SUFFIX = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
# Coarse magnitude classes. Ranking the class ABOVE family preference is what
# stops a favoured family's toy beating another family's real model — a draft
# that ranked family first picked qwen3.5:0.8b (873M) over dolphin3:8b (8.0B).
_PARAM_CLASSES = (1e9, 3e9, 6e9, 12e9, 30e9)
# Preference, not eligibility: unknown families default to 2 and stay in the
# running, so gemma5 / llama5 / qwen4 need no code change to be seen. The old
# prefix whitelist matched 4 of the 9 models installed here — not gemma4, not
# dolphin3, not laguna. qwen35 is tier 3 on measured judgment: on the same clip
# it found the punchline and flagged no bait, where dolphin3 returned
# punchline_index -1 and called the spoken line "Did you actually say that" bait.
_FAMILY_TIER = {"qwen35": 3}
# details.family reports qwen2.5-coder as plain "qwen2", indistinguishable from
# a general qwen2.5, so coder detection has to key on the tag name. "-code"
# rather than bare "code" so a general model with 'code' in its name is safe;
# the startswith covers codegemma/codeqwen/codegeex4, which carry neither.
_CODER_MARKERS = ("coder", "-code", "codellama", "codestral", "starcoder", "sqlcoder", "devstral")


def _param_count(text: str | None) -> float:
    """'9.7B' -> 9.7e9, '873.44M' -> 8.7344e8. The unit is load-bearing: a
    float() of the numeric prefix ranks the 873M model above every 8B."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMBT]?)\s*", (text or "").upper())
    return float(m.group(1)) * _PARAM_SUFFIX[m.group(2)] if m else 0.0


def _is_chat_model(m: dict) -> bool:
    caps = m.get("capabilities")
    if caps is not None:
        return "completion" in caps
    # Older daemons omit capabilities from /api/tags; fall back to the only
    # other signal rather than rejecting the whole library.
    fam = ((m.get("details") or {}).get("family") or "").lower()
    return "embed" not in m.get("name", "").lower() and not fam.endswith("bert")


def _is_coder(name: str) -> bool:
    n = name.lower()
    return n.startswith("code") or any(k in n for k in _CODER_MARKERS)


def _pick_ollama_model(entries: list[dict]) -> str:
    """Pick the judge from raw /api/tags records.

    The old picker read the size out of the TAG STRING, so every ':latest' tag
    scored 0.0 — it chose qwen2.5-coder:7b (7.0) over qwen3.5:latest (9.7B,
    scored 0.0), and would have fallen to qwen3.5:0.8b the moment the coder was
    deleted. Rank on what /api/tags actually reports, in this order: magnitude
    class, then general-over-coder, then family, then exact params, then the
    smaller manifest (gemma4:e4b and dolphin3:8b both report "8.0B"; 4.9 GB is
    the better bet on an 8 GiB card than 9.6 GB).
    """
    chat = [m for m in entries if _is_chat_model(m)]
    if not chat:
        # nomic-embed-text 400s on /api/chat. An embedding model is not a
        # degraded judge, it is a hard failure — say so now, not in 600 s.
        raise LlmError(
            "Ollama has only embedding models installed. Pull a chat model, "
            "e.g. `ollama pull qwen3.5`."
        )
    fits = [m for m in chat if (m.get("size") or 0) <= OLLAMA_MAX_MANIFEST_BYTES]

    def rank(m: dict) -> tuple:
        details = m.get("details") or {}
        name = m.get("name", "")
        params = _param_count(details.get("parameter_size"))
        return (
            bisect.bisect_right(_PARAM_CLASSES, params),
            0 if _is_coder(name) else 1,
            _FAMILY_TIER.get((details.get("family") or "").lower(), 2),
            params,
            -(m.get("size") or 0),
        )

    # Never fall back to list order: /api/tags is sorted newest-pull-first, so
    # `models[0]` meant "whatever was pulled last" and could hand /api/chat an
    # embedding model. If nothing clears the bound, the smallest chat model is
    # the only one with a chance of staying resident.
    if not fits:
        return min(chat, key=lambda m: m.get("size") or 0)["name"]
    return max(fits, key=rank)["name"]


def make_client(llm_mode: str):
    if llm_mode == "ollama":
        # PUBLIKCLIP_OLLAMA_MODEL overrides the picker. Note the 9B is tagged
        # only `qwen3.5:latest` here — `qwen3.5:9b` does not resolve.
        return OllamaClient(os.environ.get("PUBLIKCLIP_OLLAMA_MODEL"))
    return GeminiClient()
