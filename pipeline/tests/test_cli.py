"""The sidecar contract: every run ends with exactly one result line.

The Tauri shell reads stdout and nothing else — it nulls the child's stderr
— so a stage that dies with anything other than StageError used to leave the
app with a dead pipe and no words, which it reports as "the pipeline exited
unexpectedly". These tests pin the guarantee that a crash is still a result."""

import json

import pytest

from publikclip_pipeline import cli, config
from publikclip_pipeline.jobs import queue


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIKCLIP_HOME", str(tmp_path / "home"))
    yield


def _job() -> queue.Job:
    return queue.create_job("file", "/tmp/x.mp4", json.dumps(config.Settings().to_json()))


def _result_lines(capsys) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{") and json.loads(line).get("event") == "result"
    ]


class _Boom(queue.Stage):
    name = "score"
    schema_version = 1

    def __init__(self, err: Exception):
        self._err = err

    def run(self, ctx):
        raise self._err


def test_unexpected_exception_still_emits_a_result(capsys, monkeypatch):
    stage = _Boom(RuntimeError("torch fell over"))
    monkeypatch.setattr(cli, "_stages", lambda: [stage])
    monkeypatch.setattr(cli, "_preflight", lambda job, stages: None)

    assert cli._execute(_job(), jsonl=True) == 1

    results = _result_lines(capsys)
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "torch fell over" in results[0]["error"]


def test_stage_error_still_emits_a_result(capsys, monkeypatch):
    stage = _Boom(queue.StageError("no dialogue in this video"))
    monkeypatch.setattr(cli, "_stages", lambda: [stage])
    monkeypatch.setattr(cli, "_preflight", lambda job, stages: None)

    assert cli._execute(_job(), jsonl=True) == 1
    assert _result_lines(capsys)[0]["error"] == "no dialogue in this video"


def test_preflight_fails_before_any_stage_runs(capsys, monkeypatch):
    from publikclip_pipeline.scoring import llm as llm_mod

    ran = []

    class _Tracking(queue.Stage):
        name = "ingest"
        schema_version = 1

        def run(self, ctx):
            ran.append(self.name)
            return {}

    monkeypatch.setattr(cli, "_stages", lambda: [_Tracking(), _Boom(RuntimeError("unreached"))])

    def _no_backend(mode):
        raise llm_mod.LlmError("No Gemini API key found.")

    monkeypatch.setattr(llm_mod, "make_client", _no_backend)

    job = _job()
    assert cli._execute(job, jsonl=True) == 1
    assert ran == []  # ingest never paid for a run that could not finish
    assert "No Gemini API key" in _result_lines(capsys)[0]["error"]
    assert queue.get_job(job.id).status == "failed"


def test_preflight_skipped_when_score_is_already_checkpointed(capsys, monkeypatch):
    from publikclip_pipeline.scoring import llm as llm_mod

    job = _job()
    queue.write_checkpoint(job, "score", 1, {"clips": []})

    class _Score(queue.Stage):
        name = "score"
        schema_version = 1

        def run(self, ctx):  # pragma: no cover — cached, never called
            raise AssertionError("should have been cached")

    monkeypatch.setattr(cli, "_stages", lambda: [_Score()])

    def _no_backend(mode):
        raise llm_mod.LlmError("Ollama isn't running.")

    monkeypatch.setattr(llm_mod, "make_client", _no_backend)

    assert cli._execute(job, jsonl=True) == 0
    assert _result_lines(capsys)[0]["ok"] is True
