"""The Ollama model picker and request shape.

Two bugs live here and neither is visible by reading the code:

1. The old picker read the parameter count out of the TAG NAME, so every
   ':latest' tag scored 0.0 — it chose qwen2.5-coder:7b (a code model judging
   humor) over qwen3.5:latest (9.7B), and would have fallen to qwen3.5:0.8b
   (873M) the moment the coder was deleted. test_deleting_the_coder_* is the
   regression gate for that.

2. `think` must be a TOP-LEVEL key of the /api/chat body. Inside "options"
   ollama returns HTTP 200 and silently reasons anyway, which on a reasoning
   model burns the whole context and returns an empty message.content. Nothing
   about that failure looks like a misplaced key, so test_request_body_* pins
   the shape.

RAW is a verbatim /api/tags 'models' array from the dev machine (ollama
0.33.0), trimmed to the fields the picker reads.
"""

from __future__ import annotations

import json

import httpx
import pytest

from publikclip_pipeline.scoring import llm


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # A dev who exported PUBLIKCLIP_OLLAMA_MODEL takes a different code path
    # through make_client and would never exercise the picker at all.
    monkeypatch.delenv("PUBLIKCLIP_OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("PUBLIKCLIP_HOME", str(tmp_path / "home"))


def _m(name, size, params, family, caps):
    return {
        "name": name,
        "size": size,
        "capabilities": caps,
        "details": {"family": family, "parameter_size": params},
    }


RAW = [
    _m("qwen3.5:latest", 6594474711, "9.7B", "qwen35", ["vision", "completion", "tools", "thinking"]),
    _m("laguna-xs-2.1:latest", 20274303147, "33.4B", "laguna", ["completion", "tools", "thinking"]),
    _m("dolphin3:8b", 4920757726, "8.0B", "llama", ["completion"]),
    _m("nomic-embed-text:latest", 274302450, "137M", "nomic-bert", ["embedding"]),
    _m("qwen2.5-coder:7b", 4683087561, "7.6B", "qwen2", ["completion", "tools", "insert"]),
    _m("gemma4:e2b", 7162405886, "5.1B", "gemma4", ["completion", "tools", "thinking"]),
    _m("gemma4:e4b", 9608350718, "8.0B", "gemma4", ["completion", "tools", "thinking"]),
    _m("qwen3.5:0.8b", 1036046583, "873.44M", "qwen35", ["vision", "completion", "tools", "thinking"]),
    _m("llama3.2:latest", 2019393189, "3.2B", "llama", ["completion", "tools"]),
]


def only(*names):
    return [m for m in RAW if m["name"] in names]


def without(*names):
    return [m for m in RAW if m["name"] not in names]


# --- parameter parsing ------------------------------------------------------


def test_param_count_honours_the_unit_suffix():
    # float("873.44") > float("8.0") — dropping the unit ranks a 873M toy above
    # every 8B model, which is the same class of bug from the other direction.
    assert llm._param_count("873.44M") < llm._param_count("8.0B")
    assert llm._param_count("8.0B") < llm._param_count("9.7B")
    assert llm._param_count("9.7B") < llm._param_count("33.4B")
    assert llm._param_count("137M") < llm._param_count("873.44M")


@pytest.mark.parametrize("junk", [None, "", "garbage", "1.2Q"])
def test_param_count_never_raises(junk):
    # A ValueError here escapes OllamaClient.__init__, and neither cli.py nor
    # stage.py catches anything but LlmError.
    assert llm._param_count(junk) == 0.0


# --- selection --------------------------------------------------------------


def test_picks_the_largest_general_model():
    assert llm._pick_ollama_model(RAW) == "qwen3.5:latest"


def test_deleting_the_coder_does_not_demote_the_judge():
    """THE regression gate. Under the old picker this returned qwen3.5:0.8b."""
    assert llm._pick_ollama_model(without("qwen2.5-coder:7b")) == "qwen3.5:latest"


def test_selection_is_independent_of_api_tags_order():
    # /api/tags sorts newest-pull-first, so an order-sensitive picker silently
    # changes its mind every time anything is pulled.
    assert llm._pick_ollama_model(list(reversed(RAW))) == "qwen3.5:latest"


def test_a_general_model_beats_a_coder_of_the_same_magnitude():
    assert llm._pick_ollama_model(only("qwen2.5-coder:7b", "dolphin3:8b")) == "dolphin3:8b"


def test_a_coder_still_beats_a_toy():
    # Magnitude class is ranked above the coder demotion on purpose: a 7.6B
    # coder is a worse judge than an 8B general model but a far better one
    # than a 873M model of any family.
    assert llm._pick_ollama_model(only("qwen2.5-coder:7b", "qwen3.5:0.8b")) == "qwen2.5-coder:7b"


def test_unfittable_models_are_skipped_not_preferred():
    # laguna-xs-2.1 is 33.4B / 20.3 GB — the largest installed, and ruinous on
    # an 8 GiB card. Rank-by-size without a bound picks exactly this.
    assert llm._pick_ollama_model(only("laguna-xs-2.1:latest", "llama3.2:latest")) == "llama3.2:latest"


def test_an_unfittable_model_is_still_better_than_nothing():
    assert llm._pick_ollama_model(only("laguna-xs-2.1:latest")) == "laguna-xs-2.1:latest"


def test_embedding_models_are_never_selected():
    # nomic-embed-text answers /api/chat with 400 "does not support chat". The
    # old `return models[0]` fallback could hand it back.
    for subset in (RAW, only("qwen2.5-coder:7b", "nomic-embed-text:latest")):
        assert llm._pick_ollama_model(subset) != "nomic-embed-text:latest"


def test_an_embedding_only_library_fails_loudly():
    with pytest.raises(llm.LlmError, match="embedding"):
        llm._pick_ollama_model(only("nomic-embed-text:latest"))


# --- request shape ----------------------------------------------------------


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _stub_daemon(monkeypatch, reply, captured):
    class Res:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    monkeypatch.setattr(llm.httpx, "get", lambda *a, **k: Res({"models": RAW}))

    def post(url, json=None, **kw):
        captured.append(json)
        return Res(reply)

    monkeypatch.setattr(llm.httpx, "post", post)


def test_request_body_sends_think_at_the_top_level(monkeypatch):
    captured: list[dict] = []
    _stub_daemon(monkeypatch, {"message": {"content": '{"ok": true}'}}, captured)

    assert llm.OllamaClient().generate_json("p", SCHEMA) == {"ok": True}

    body = captured[0]
    assert body["think"] is False
    assert "think" not in body["options"], "inside options ollama ignores it silently"
    assert body["options"]["num_ctx"] == llm.OLLAMA_NUM_CTX


def test_cache_key_separates_request_variants():
    # Without the variant component a reply cached under the old think-on
    # request replays verbatim and the fix looks like a no-op.
    args = ("ollama", "m", "p", SCHEMA, [])
    assert llm._cache_key(*args, "think=False;num_ctx=8192") != llm._cache_key(*args, "think=True;num_ctx=8192")
    # Gemini passes no variant, so its keys must not move.
    assert llm._cache_key(*args) == llm._cache_key(*args, "")


# --- reply parsing ----------------------------------------------------------


def test_a_model_that_reasons_past_the_context_says_so():
    """The failure this whole change exists to prevent.

    Before: json.loads("") -> "Ollama call failed: Expecting value: line 1
    column 1 (char 0)", which stage.py hands to the user as the reason the job
    died, after ~190 s of waiting.
    """
    payload = {
        "message": {"content": "", "thinking": "step 1. " * 500},
        "done_reason": "length",
        "eval_count": 7919,
    }
    with pytest.raises(llm.LlmError) as err:
        llm._parse_ollama_reply(payload, "qwen3.5:latest")
    msg = str(err.value)
    assert "think" in msg and "length" in msg and "7919" in msg


def test_non_json_content_is_reported_with_the_answer():
    with pytest.raises(llm.LlmError, match="did not return JSON"):
        llm._parse_ollama_reply({"message": {"content": "I'd rather not."}}, "m")


def test_fenced_json_still_parses():
    payload = {"message": {"content": '```json\n{"ok": true}\n```'}}
    assert llm._parse_ollama_reply(payload, "m") == {"ok": True}


def test_a_daemon_error_body_reaches_the_user(monkeypatch):
    class Res:
        status_code = 400

        text = '{"error": "\\"nomic-embed-text:latest\\" does not support chat"}'

        def json(self):
            return json.loads(self.text)

    monkeypatch.setattr(llm.httpx, "get", lambda *a, **k: type("R", (), {
        "raise_for_status": lambda s: None, "json": lambda s: {"models": RAW}})())
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: Res())

    with pytest.raises(llm.LlmError, match="does not support chat"):
        llm.OllamaClient().generate_json("p", SCHEMA)


def test_a_stopped_daemon_is_actionable(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(llm.httpx, "get", boom)
    with pytest.raises(llm.LlmError, match="isn't running"):
        llm.OllamaClient()
