from copy import deepcopy

from alpha_platform.observability.replay import build_frames


def record(
    source, sequence, timestamp="2026-10-31T08:00:00.000Z", kind="CREATED", node="run_admission"
):
    return {
        "source_kind": source,
        "sequence": sequence,
        "source_ref": f"{source.lower()}:{sequence}",
        "event_type": kind,
        "node": node,
        "occurred_at": timestamp,
        "payload": {},
    }


def fixture(events):
    return {
        "run_id": "run-1",
        "events": events,
        "evidence": {
            e["source_ref"]: {"source_kind": e["source_kind"], "summary": {}} for e in events
        },
        "run": {"status": "COMPLETED", "budget": {"used": 1}},
    }


def test_source_order_is_stable_but_same_second_is_not_claimed_causal():
    snapshot = fixture(
        [record("TRACE", 3), record("AUDIT", 9), record("TRACE", 1), record("AUDIT", 2)]
    )
    frames, meta = build_frames(snapshot)
    assert [f["source_sequence"] for f in frames] == [2, 9, 1, 3]
    assert all(f["order_quality"] == "TIE_UNCERTAIN" for f in frames)
    assert "CROSS_SOURCE_SAME_SECOND_UNCERTAIN" in meta["warnings"]
    assert frames == build_frames(deepcopy(snapshot))[0]
    assert frames[0]["time_precision"] == "second" and frames[-1]["time_precision"] == "millisecond"


def test_source_row_order_survives_wall_clock_regression():
    frames, meta = build_frames(
        fixture(
            [
                record("AUDIT", 1, "2026-10-31T08:00:05.000Z"),
                record("AUDIT", 2, "2026-10-31T08:00:01.000Z"),
            ]
        )
    )
    assert [f["source_sequence"] for f in frames] == [1, 2]
    assert "SOURCE_TIME_REGRESSION" in meta["warnings"]


def test_no_final_snapshot_events_are_fabricated_and_truncation_is_explicit():
    assert build_frames(fixture([]))[0] == []
    data = fixture([record("AUDIT", i) for i in range(8)])
    frames, meta = build_frames(data, 3)
    assert len(frames) == 3 and frames[0]["source_sequence"] == 5
    assert meta["omitted_prefix_events"] == 5
    assert "EXPORT_TRUNCATED" in meta["warnings"]
    assert "budget" not in frames[0] and "COMPLETED" not in str(frames)


def test_missing_evidence_and_duplicate_records_are_not_silently_accepted():
    event = record("AUDIT", 2, None)
    data = fixture([event, event])
    data["evidence"] = {}
    frames, meta = build_frames(data)
    assert len(frames) == 1
    assert frames[0]["evidence_level"] == "UNKNOWN"
    assert frames[0]["missing_reason"]
    assert frames[0]["time_precision"] == "unknown"
    assert any(w.startswith("DUPLICATE_SOURCE_IGNORED") for w in meta["warnings"])


def test_unknown_job_without_prior_phase_does_not_invent_submission():
    event = {**record("AUDIT", 10, kind="UNKNOWN", node="submit"), "job_id": "j"}
    frames, _ = build_frames(fixture([event]))
    assert frames[0]["node_id"] == "run_terminal"
    assert "UNKNOWN_JOB_PHASE" in frames[0]["warnings"]
