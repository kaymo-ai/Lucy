import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vertex_client import build_config, collect_results


class FakeResponse:
    def __init__(self, text):
        self.text = text


def test_config_requests_json_against_the_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}
    cfg = build_config(schema, system_text="preamble")
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"] == schema


def test_config_carries_the_shared_preamble_as_system_instruction():
    cfg = build_config({"type": "object"}, system_text="camp vocabulary")
    assert cfg["system_instruction"] == "camp vocabulary"


def test_results_are_keyed_by_job_id_not_position():
    """Concurrent completion means arrival order carries no meaning."""
    out = collect_results([
        ("entity-42", FakeResponse('{"name": "Doris"}'), None),
        ("entity-7", FakeResponse('{"name": "Boris"}'), None),
    ])
    assert out["entity-42"] == {"name": "Doris"}
    assert out["entity-7"] == {"name": "Boris"}


def test_failed_jobs_map_to_none_rather_than_vanishing():
    out = collect_results([
        ("entity-1", FakeResponse('{"name": "Doris"}'), None),
        ("entity-2", None, RuntimeError("quota exceeded")),
    ])
    assert out["entity-1"] == {"name": "Doris"}
    assert out["entity-2"] is None


def test_unparseable_output_maps_to_none_not_a_crash():
    out = collect_results([("entity-3", FakeResponse("not json"), None)])
    assert out["entity-3"] is None


class ExplodingResponse:
    """A safety-blocked or MAX_TOKENS response: touching .text raises."""

    @property
    def text(self):
        raise ValueError("Response has no valid candidate")


def test_a_response_that_raises_on_text_costs_one_job_not_the_pass():
    out = collect_results([
        ("entity-1", ExplodingResponse(), None),
        ("entity-2", FakeResponse('{"name": "Doris"}'), None),
    ])
    assert out["entity-1"] is None
    assert out["entity-2"] == {"name": "Doris"}


def test_expired_credentials_abort_the_pass(monkeypatch):
    import pytest
    import vertex_client
    # An auth refresh failure is total: every job would fail the same way,
    # and mapping it to None reported three whole passes as empty results
    # while the model was never reached (2026-08-11). It must abort loudly,
    # on the first job, with the fix in the message.
    class RefreshError(Exception):
        pass

    class FakeModels:
        def generate_content(self, **_kwargs):
            raise RefreshError("Reauthentication is needed.")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(vertex_client, "client", lambda: FakeClient())
    sleeps = []
    monkeypatch.setattr(vertex_client.time, "sleep", sleeps.append)
    with pytest.raises(SystemExit) as exc:
        vertex_client.run_all([("job-0", "hello")], "system", {"type": "OBJECT"})
    assert "gcloud auth application-default login" in str(exc.value)
    assert sleeps == []      # no backoff against a failure that cannot heal
