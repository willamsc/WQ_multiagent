import asyncio
import hashlib
import json
from copy import deepcopy

import pytest

from alpha_platform.cli import execute, main
from alpha_platform.domain import ResearchConfig
from alpha_platform.observability.diagnostics import references
from alpha_platform.observability.projector import project
from alpha_platform.observability.queries import ObservatoryReader
from alpha_platform.observability.replay import build_replay_bundle, encode_replay
from alpha_platform.observability.traces import TraceSink
from alpha_platform.persistence import Repository


@pytest.fixture
def replay_env(tmp_path):
    path = tmp_path / "replay.sqlite3"
    repo = Repository(path)
    repo.initialize()
    yield repo, path
    repo.close()


def read(path, run_id):
    reader = ObservatoryReader(path)
    try:
        return reader.snapshot(run_id, for_replay=True)
    finally:
        reader.close()


def test_bundle_uses_history_not_business_snapshot_and_is_stable(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "full")))
    raw = read(path, report["id"])
    first = build_replay_bundle(raw)
    modified = deepcopy(raw)
    modified["run"]["status"] = "FAILED"
    modified["run"]["budget"]["used"] = 999
    second = build_replay_bundle(modified)
    assert first["frames"] == second["frames"]
    assert first["frame_states"] == second["frame_states"]
    assert first["initial_state"]["run"]["state"] == "UNKNOWN"
    assert first["initial_state"]["jobs"] == {}
    assert encode_replay(first) == encode_replay(build_replay_bundle(deepcopy(raw)))
    assert references(first) <= first["evidence"].keys()
    assert project(first["frames"], 1)["run"]["state"] != "COMPLETED"


@pytest.mark.parametrize("limit", [1, 200, 2000])
def test_event_limits_never_import_dropped_prefix_state(replay_env, limit):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig()))
    bundle = build_replay_bundle(read(path, report["id"]), max_events=limit)
    assert len(bundle["frames"]) <= limit
    if limit == 1:
        assert bundle["limits"]["truncated"]
        assert bundle["limits"]["omitted_prefix_events"] == 12
        assert bundle["limits"]["omitted_source_sequence_ranges"]
        assert project(bundle["frames"], 1)["budget"]["used"] is None
        assert (
            project(bundle["frames"], 1)["node_states"]["generator"]["state"] == "NOT_OBSERVED_YET"
        )


def test_empty_history_keeps_separate_final_snapshot_without_fabricating_frames(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig()))
    with repo.transaction():
        repo.db.execute("DELETE FROM research_events")
    bundle = build_replay_bundle(read(path, report["id"]))
    assert bundle["frames"] == bundle["frame_states"] == []
    assert bundle["final_snapshot"]["run"]["status"] == "COMPLETED"
    assert "NO_REPLAY_EVENTS" in bundle["warnings"]


def test_polluted_optional_trace_is_skipped_not_rendered_as_success(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "light")))
    with repo.transaction():
        repo.db.execute("UPDATE observability_traces SET node='fake-node' WHERE id=1")
        repo.db.execute("UPDATE observability_traces SET payload_json='invalid json' WHERE id=2")
        repo.db.execute("UPDATE observability_traces SET payload_json='[]' WHERE id=3")
        repo.db.execute(
            "UPDATE observability_traces SET payload_json=? WHERE id=4",
            (json.dumps({"summary": "x" * 10000}),),
        )
    raw = read(path, report["id"])
    # Invalid JSON decodes as {}, so require explicit invalid-json recognition in the reader.
    bundle = build_replay_bundle(raw)
    assert len([w for w in bundle["warnings"] if w.startswith("MALFORMED_TRACE_SKIPPED")]) == 4
    assert all(f["node_id"] != "fake-node" for f in bundle["frames"])


def test_json_export_is_readonly_and_legacy_commands_still_work(replay_env, tmp_path, capsys):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig()))
    before = list(repo.db.iterdump())
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    output = tmp_path / "replay.json"
    assert (
        main(
            [
                "--db",
                str(path),
                "observe",
                "replay",
                "--run-id",
                report["id"],
                "--format",
                "json",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "HISTORICAL_REPLAY"
    capsys.readouterr()
    assert main(["--db", str(path), "observe", "show", "--run-id", report["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["status"] == "COMPLETED"
    assert list(repo.db.iterdump()) == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
    assert (
        main(
            [
                "--db",
                str(path),
                "observe",
                "replay",
                "--run-id",
                report["id"],
                "--output",
                str(path),
            ]
        )
        == 1
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_byte_budget_and_missing_reference_are_explicit(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "full")))
    raw = read(path, report["id"])
    ref = next(e["source_ref"] for e in raw["events"] if e["source_kind"] == "AUDIT")
    raw["evidence"].pop(ref)
    bundle = build_replay_bundle(raw, max_bytes=16000)
    assert len(encode_replay(bundle).encode()) <= 16000
    assert bundle["limits"]["truncated"]
    assert references(bundle) <= bundle["evidence"].keys()
    full = build_replay_bundle(raw, max_bytes=4194304)
    assert full["evidence"][ref]["missing_reason"]
    with pytest.raises(ValueError):
        build_replay_bundle(raw, max_bytes=8191)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": {"bad": "type"}},
        {"duration_ms": float("nan")},
        {"attempt_number": True},
        {"duration_ms": -1},
        {"gate": "UNKNOWN"},
        {"remote_ref_present": "yes"},
        {"duration_ms": 10**1000},
        {"attempt_number": 10**1000},
    ],
)
def test_polluted_trace_field_types_are_skipped(replay_env, payload):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "light")))
    with repo.transaction():
        repo.db.execute(
            "UPDATE observability_traces SET payload_json=? WHERE id=1", (json.dumps(payload),)
        )
    bundle = build_replay_bundle(read(path, report["id"]))
    assert "MALFORMED_TRACE_SKIPPED:trace:1" in bundle["warnings"]
    assert all("trace:1" not in f["evidence_refs"] for f in bundle["frames"])


def test_corrupted_audit_payload_keeps_recorded_kind_but_declares_gap(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig()))
    with repo.transaction():
        repo.db.execute(
            "UPDATE research_events SET payload_json='invalid' WHERE event_type='GENERATED'"
        )
    bundle = build_replay_bundle(read(path, report["id"]))
    assert any(w.startswith("MALFORMED_AUDIT_PAYLOAD") for w in bundle["warnings"])
    generated = next(f for f in bundle["frames"] if f["event_kind"] == "GENERATED")
    assert generated["payload_summary"]["expression"] is None


def test_capacity_2000_frames_and_min_max_byte_budgets(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig()))
    raw = read(path, report["id"])
    raw["events"] = []
    for index in range(2000):
        ref = f"audit:fixture:{index}"
        raw["events"].append(
            {
                "source_kind": "AUDIT",
                "sequence": index,
                "source_ref": ref,
                "node": "poll",
                "event_type": "POLL_INTENT",
                "payload": {},
                "occurred_at": "2026-10-31T00:00:00.000Z",
            }
        )
        raw["evidence"][ref] = {"source_kind": "AUDIT", "summary": {}}
    bundle = build_replay_bundle(raw, max_events=2000, max_bytes=4194304)
    assert len(bundle["frames"]) == 2000
    assert not bundle["limits"]["truncated"]
    assert len(encode_replay(bundle).encode()) <= 4194304
    assert project(bundle["frames"], 1999)["ordinal"] == 1999
    try:
        small = build_replay_bundle(raw, max_events=2000, max_bytes=8192)
    except ValueError as exc:
        assert "byte budget" in str(exc)
    else:
        assert small["limits"]["truncated"] and "EXPORT_TRUNCATED" in small["warnings"]
        assert len(encode_replay(small).encode()) <= 8192


def test_absent_trace_table_and_empty_history_are_read_only(replay_env):
    repo, path = replay_env
    report = asyncio.run(execute(repo, ResearchConfig()))
    assert not repo.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='observability_traces'"
    ).fetchone()
    before = list(repo.db.iterdump())
    bundle = build_replay_bundle(read(path, report["id"]))
    assert "NO_OPTIONAL_TRACE" in bundle["warnings"]
    assert list(repo.db.iterdump()) == before
