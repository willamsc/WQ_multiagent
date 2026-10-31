from copy import deepcopy

from alpha_platform.observability.projector import (
    apply_delta,
    apply_frame,
    build_deltas,
    initial_state,
    project,
)
from alpha_platform.observability.replay import build_frames


def frames_for(kinds):
    events = []
    for index, (kind, node, job, payload) in enumerate(kinds, 1):
        events.append(
            {
                "source_kind": "AUDIT",
                "sequence": index,
                "source_ref": f"audit:{index}",
                "node": node,
                "event_type": kind,
                "job_id": job,
                "occurred_at": "2026-10-31T08:00:00.000Z",
                "payload": payload,
            }
        )
    return build_frames(
        {"run_id": "r", "events": events, "evidence": {e["source_ref"]: {} for e in events}}
    )[0]


def test_prefix_does_not_leak_future_job_run_budget_or_anomalies():
    frames = frames_for(
        [
            ("CREATED", "run_admission", None, {}),
            ("GENERATED", "generator", None, {}),
            ("RESERVED", "dedup_budget", "j", {}),
            ("SUBMITTED", "submit", "j", {}),
            ("POLLING", "poll", "j", {}),
            ("FAILED", "run_terminal", None, {"reason_code": "PERSISTENCE_FAILURE"}),
        ]
    )
    assert project(frames, 0) == initial_state()
    early = project(frames, 1)
    assert early["run"]["state"] == "CREATED" and not early["jobs"]
    assert early["budget"]["used"] == 0 and early["budget"]["total"] is None
    assert early["budget"]["basis"] == "INFERRED"
    assert not early["anomalies_at_frame"]
    assert early["node_states"]["result_persist"]["state"] == "NOT_OBSERVED_YET"
    end = project(frames, len(frames))
    assert end["run"]["state"] == "FAILED" and end["jobs"]["j"]["state"] == "POLLING"
    assert end["node_states"]["poll"]["state"] == "UNKNOWN"
    assert end["node_states"]["poll"]["basis"] == "INFERRED"
    assert end["budget"]["used"] == 1 and end["budget"]["reserved"] == 0
    assert any(
        a["code"] == "RUN_JOB_STATE_MISMATCH_MANUAL_REVIEW" for a in end["anomalies_at_frame"]
    )
    assert not project(frames, len(frames) - 1)["anomalies_at_frame"]


def test_jump_linear_deltas_and_duplicate_application_are_equivalent():
    frames = frames_for(
        [
            ("CREATED", "run_admission", None, {}),
            ("RESERVED", "dedup_budget", "j", {}),
            ("UNKNOWN", "submit", "j", {}),
        ]
    )
    deltas = build_deltas(frames)
    state = initial_state()
    for index, change in enumerate(deltas, 1):
        state = apply_delta(state, change["delta"])
        assert state == project(frames, index)
    assert apply_frame(state, frames[-1]) == state
    assert project(frames, 1) == project(frames, 1)
    assert state["budget"]["reserved"] == 1 and state["budget"]["used"] == 0
    assert state["jobs"]["j"]["remote_id_present"] is None


def test_missing_prefix_and_missing_evidence_do_not_create_known_budget():
    frames = frames_for([("SUBMITTED", "submit", "j", {})])
    projected = project(frames, 1)
    assert projected["budget"]["used"] is None and projected["budget"]["reserved"] is None
    frames[0]["evidence_level"] = "UNKNOWN"
    projected = project(frames, 1)
    assert not projected["jobs"]
    assert projected["data_gaps"]


def test_reducer_does_not_mutate_input_and_lower_trace_does_not_undo_audit():
    frames = frames_for([("SUBMITTING", "submit", "j", {}), ("UNKNOWN", "submit", "j", {})])
    baseline = initial_state()
    copy = deepcopy(baseline)
    result = apply_frame(apply_frame(baseline, frames[0]), frames[1])
    assert baseline == copy
    trace = {**frames[1], "ordinal": 3, "source_kind": "TRACE", "event_kind": "NODE_COMPLETED"}
    updated = apply_frame(result, trace)
    assert updated["node_states"]["submit"]["state"] == "UNKNOWN"
    assert updated["jobs"]["j"]["state"] == "UNKNOWN"
