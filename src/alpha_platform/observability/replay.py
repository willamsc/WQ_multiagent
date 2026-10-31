"""R1: deterministic recorded-event ordering; no final state is used as history."""

import json
from collections import Counter, defaultdict
from copy import deepcopy

from .graph import NODES
from .safety import safe_text, sanitize

NODE_LABELS = dict(
    zip(
        NODES,
        (
            "控制器与接纳",
            "候选生成",
            "表达式校验",
            "去重与预算",
            "任务队列",
            "提交模拟",
            "查询与读取结果",
            "指标门控",
            "结果保存",
            "研究结束",
        ),
    )
)
EVENT_LABELS = {
    "CREATED": "研究已创建",
    "GENERATED": "候选已生成",
    "VALIDATED": "校验通过",
    "PENDING": "任务已入队",
    "RESERVED": "预算已预留",
    "SUBMITTING": "开始提交模拟",
    "SUBMIT_INTENT": "提交意图已记录",
    "SUBMITTED": "模拟回执已确认",
    "POLLING": "进入查询阶段",
    "POLL_INTENT": "查询意图已记录",
    "FETCH_INTENT": "开始读取结果",
    "RATE_LIMIT": "本次请求受到限流",
    "DUPLICATE": "重复候选被拒绝",
    "BUDGET_EXHAUSTED": "可用预算不足",
    "REJECTED": "研究被拒绝",
    "FAILED": "记录了执行失败",
    "UNKNOWN": "远程执行状态未知",
    "COMPLETED": "完成记录",
    "NODE_STARTED": "诊断：节点调用开始",
    "NODE_COMPLETED": "诊断：节点调用结束",
    "NODE_FAILED": "诊断：节点调用异常",
}
REASON_LABELS = {
    "VALIDATION": "表达式未通过本地校验",
    "DUPLICATE": "相同表达式和配置已有记录",
    "BUDGET_EXHAUSTED": "预算不足，未提交模拟",
    "REMOTE_UNKNOWN": "无法确认远程执行结果",
    "REMOTE_FAILED": "模拟返回失败",
    "RATE_LIMIT": "限流尝试次数耗尽",
    "PERSISTENCE_FAILURE": "结果写入失败，需人工检查",
    "INTERNAL": "内部处理未完成",
    "NODE_EXCEPTION": "诊断记录了调用异常（不等同于研究终态）",
    "MOCK_GATE_PASS": "模拟指标达到本地示例门槛",
    "MOCK_GATE_FAIL": "模拟指标未达到本地示例门槛",
}


def _bucket(event):
    time = event.get("occurred_at")
    return time[:19] if isinstance(time, str) and len(time) >= 19 else None


def build_frames(snapshot, max_events=200):
    if not 1 <= max_events <= 2000:
        raise ValueError("max_events must be between 1 and 2000")
    warnings = ["EVENT_PREFIX_NOT_COMMIT_SNAPSHOT", "NO_SPAN_ID_NO_PAIRING"]
    meta = snapshot.get("replay_input", {})
    warnings.extend(meta.get("warnings", []))
    streams = defaultdict(list)
    seen = set()
    for event in snapshot.get("events", []):
        ref = event.get("source_ref")
        source = event.get("source_kind")
        if not isinstance(ref, str) or source not in {"AUDIT", "TRACE"}:
            warnings.append("INVALID_EVENT_SKIPPED")
            continue
        if ref in seen:
            warnings.append("DUPLICATE_SOURCE_IGNORED:" + ref)
            continue
        seen.add(ref)
        if not isinstance(event.get("sequence"), int) or event.get("node") not in NODES:
            warnings.append("INVALID_EVENT_SKIPPED:" + ref)
            continue
        if not isinstance(event.get("payload"), dict):
            warnings.append("INVALID_EVENT_PAYLOAD:" + ref)
            continue
        streams[source].append(event)
    for stream in streams.values():
        stream.sort(key=lambda e: e["sequence"])
        if any(
            a.get("occurred_at") and b.get("occurred_at") and b["occurred_at"] < a["occurred_at"]
            for a, b in zip(stream, stream[1:])
        ):
            warnings.append("SOURCE_TIME_REGRESSION")
    # Stable merge of two source streams. Row IDs never imply cross-source causality.
    ordered = []
    positions = {source: 0 for source in streams}
    while any(positions[s] < len(stream) for s, stream in streams.items()):
        available = [
            (stream[positions[s]], s) for s, stream in streams.items() if positions[s] < len(stream)
        ]
        event, source = min(
            available,
            key=lambda pair: (_bucket(pair[0]) or "9999", pair[1] != "AUDIT", pair[0]["sequence"]),
        )
        ordered.append(event)
        positions[source] += 1
    buckets = defaultdict(set)
    for event in ordered:
        if _bucket(event):
            buckets[_bucket(event)].add(event["source_kind"])
    mixed = {bucket for bucket, sources in buckets.items() if len(sources) > 1}
    if mixed:
        warnings.append("CROSS_SOURCE_SAME_SECOND_UNCERTAIN")
    if not streams.get("TRACE"):
        warnings.append("NO_OPTIONAL_TRACE")
    if meta.get("read_limited"):
        warnings.append("READ_LIMIT_REACHED")
    count = len(ordered)
    omitted = max(0, count - max_events)
    # Prefer the recent suffix, matching M1-O, but do not project its missing prefix as known.
    selected = ordered[-max_events:]
    if omitted or meta.get("read_limited"):
        warnings.append("EXPORT_TRUNCATED")
    frames = []
    phases = {}
    for ordinal, event in enumerate(selected, 1):
        ref, kind = event["source_ref"], event.get("event_type", "")
        frame_warnings = []
        if _bucket(event) in mixed:
            frame_warnings.append("CROSS_SOURCE_SAME_SECOND_UNCERTAIN")
        if ref not in snapshot.get("evidence", {}):
            frame_warnings.append("MISSING_EVIDENCE")
        if event.get("payload", {}).get("truncated"):
            frame_warnings.append("PAYLOAD_TRUNCATED")
        level = "RECORDED" if ref in snapshot.get("evidence", {}) else "UNKNOWN"
        node_id = event["node"]
        if event["source_kind"] == "AUDIT" and event.get("job_id"):
            job_id = event["job_id"]
            if kind in {"SUBMITTING", "SUBMIT_INTENT", "SUBMITTED"}:
                phases[job_id] = "submit"
            elif kind in {"POLLING", "POLL_INTENT", "FETCH_INTENT"}:
                phases[job_id] = "poll"
            elif kind in {"UNKNOWN", "FAILED"}:
                node_id = phases.get(job_id, "run_terminal")
                if job_id not in phases:
                    frame_warnings.append("UNKNOWN_JOB_PHASE")
        frames.append(
            {
                "schema_version": 1,
                "frame_id": f"frame:{ordinal:06d}",
                "ordinal": ordinal,
                "occurred_at": event.get("occurred_at"),
                "time_precision": (
                    "unknown"
                    if not event.get("occurred_at")
                    else "second"
                    if event["source_kind"] == "AUDIT"
                    else "millisecond"
                ),
                "order_quality": (
                    "UNKNOWN"
                    if not event.get("occurred_at")
                    else "TIE_UNCERTAIN"
                    if frame_warnings and _bucket(event) in mixed
                    else "SOURCE_ORDER_ONLY"
                ),
                "node_id": node_id,
                "event_kind": kind,
                "label_zh": EVENT_LABELS.get(kind, "未支持的历史事件"),
                "effect": ("OBSERVE_RECORDED_EVENT" if kind in EVENT_LABELS else "NO_KNOWN_EFFECT"),
                "subject": {
                    "run_id": snapshot["run_id"],
                    "job_id": event.get("job_id"),
                    "candidate_id": event.get("candidate_id"),
                },
                "evidence_refs": [ref],
                "evidence_level": level,
                "source_kind": event["source_kind"],
                "source_sequence": event["sequence"],
                "payload_summary": sanitize(event["payload"]),
                "warnings": frame_warnings,
                **({"missing_reason": "来源引用未包含在读取快照中"} if level == "UNKNOWN" else {}),
            }
        )
    return frames, {
        "warnings": list(dict.fromkeys(warnings)),
        "input_events": count,
        "omitted_prefix_events": omitted,
        "read_limited": bool(meta.get("read_limited")),
        "source_counts": dict(Counter(e["source_kind"] for e in ordered)),
    }


def build_replay_bundle(snapshot, max_events=200, max_bytes=262144):
    """R3: HTML and JSON share this bounded, evidence-closed pure builder."""
    from .diagnostics import references
    from .projector import build_deltas, initial_state, project

    if not 8192 <= max_bytes <= 4 * 1024 * 1024:
        raise ValueError("max_bytes must be between 8192 and 4194304")
    frames, meta = build_frames(snapshot, max_events)
    final = {
        "label_zh": "最终查询快照（不是历史帧）",
        "run": sanitize(deepcopy(snapshot["run"])),
        "jobs": [sanitize(j) for j in snapshot.get("jobs", [])],
        "attempts": [sanitize(a) for a in snapshot.get("attempts", [])[:20]],
    }
    warnings = list(meta["warnings"])
    payload_truncated = any("PAYLOAD_TRUNCATED" in frame["warnings"] for frame in frames)
    if payload_truncated:
        warnings.append("PAYLOAD_TRUNCATED")
    if not frames:
        warnings.append("NO_REPLAY_EVENTS")
    byte_omitted = 0
    while True:
        # Trimming always rebuilds from UNKNOWN; no dropped-prefix state is smuggled in.
        frames = [
            {**frame, "ordinal": i, "frame_id": f"frame:{i:06d}"}
            for i, frame in enumerate(frames, 1)
        ]
        states = build_deltas(frames)
        projected = project(frames, len(frames))
        comparison = []
        for subject, historical, current, ref in [
            ("run", projected["run"]["state"], final["run"]["status"], final["run"]["source_ref"]),
            *[
                (
                    f"job:{j['job_id']}",
                    projected["jobs"].get(j["job_id"], {}).get("state"),
                    j["status"],
                    j["source_ref"],
                )
                for j in final["jobs"]
            ],
            *[
                (
                    f"budget:{key}",
                    projected["budget"][key],
                    final["run"]["budget"][key],
                    final["run"]["source_ref"],
                )
                for key in ("reserved", "used")
            ],
        ]:
            comparison.append(
                {
                    "subject": subject,
                    "projected": historical if historical is not None else "UNKNOWN",
                    "final": current,
                    "derived": True,
                    "evidence_refs": [ref],
                    "result": (
                        "NOT_RECONSTRUCTABLE"
                        if historical in {None, "UNKNOWN"}
                        else "MATCH"
                        if historical == current
                        else "DIFFERENT"
                    ),
                }
            )
        bundle = {
            "schema_version": 1,
            "run_id": safe_text(snapshot["run_id"], 100),
            "generated_at": snapshot.get("snapshot_generated_at"),
            "locale": "zh-CN",
            "mode": "HISTORICAL_REPLAY",
            "initial_state": initial_state(),
            "frames": frames,
            "frame_states": states,
            "projection_encoding": "ROOT_REPLACEMENTS_AND_NODE_UPDATES_V1",
            "topology": {
                "nodes": [{"id": n, "label_zh": NODE_LABELS[n]} for n in NODES],
                "edges": [
                    {"from": a, "to": b, "kind": "EXPECTED", "derived": True}
                    for a, b in zip(NODES, NODES[1:])
                ],
            },
            "final_snapshot": final,
            "final_comparison": comparison,
            "warnings": list(dict.fromkeys(warnings)),
            "limits": {
                "truncated": bool(
                    meta["omitted_prefix_events"] or byte_omitted or meta["read_limited"]
                ),
                "event_limit": max_events,
                "byte_limit": max_bytes,
                "input_events": meta["input_events"],
                "omitted_prefix_events": meta["omitted_prefix_events"] + byte_omitted,
                "omitted_source_sequence_ranges": _omitted_ranges(snapshot, frames),
                "read_limited": meta["read_limited"],
                "payload_truncated": payload_truncated,
            },
        }
        refs = references(bundle)
        evidence = snapshot.get("evidence", {})
        bundle["evidence"] = {
            ref: {
                "source_kind": evidence[ref].get("source_kind", "UNKNOWN"),
                "summary": sanitize(evidence[ref].get("summary", {})),
            }
            if ref in evidence
            else {
                "source_kind": "UNKNOWN",
                "summary": None,
                "missing_reason": "读取快照没有该来源，不能确认对应历史状态",
            }
            for ref in sorted(refs)
        }
        # Evidence summaries may reference related source rows, too.
        while extra := references(bundle) - bundle["evidence"].keys():
            for ref in sorted(extra):
                bundle["evidence"][ref] = (
                    {
                        "source_kind": evidence[ref].get("source_kind", "UNKNOWN"),
                        "summary": sanitize(evidence[ref].get("summary", {})),
                    }
                    if ref in evidence
                    else {
                        "source_kind": "UNKNOWN",
                        "summary": None,
                        "missing_reason": "关联证据未包含在读取快照中",
                    }
                )
        if len(encode_replay(bundle).encode("utf-8")) <= max_bytes:
            return bundle
        if not frames:
            raise ValueError(
                "byte budget too small for replay evidence bundle; increase --max-bytes"
            )
        remove = max(1, len(frames) // 4)
        frames = frames[remove:]
        byte_omitted += remove
        warnings.append("EXPORT_TRUNCATED")
        if not frames:
            warnings.append("NO_REPLAY_EVENTS")


def _omitted_ranges(snapshot, frames):
    kept = {ref for f in frames for ref in f["evidence_refs"]}
    sources = defaultdict(list)
    for event in snapshot.get("events", []):
        if event.get("source_ref") not in kept and isinstance(event.get("sequence"), int):
            sources[event.get("source_kind", "UNKNOWN")].append(event["sequence"])
    return [
        {
            "source_kind": source,
            "min_sequence": min(ids),
            "max_sequence": max(ids),
            "count": len(ids),
        }
        for source, ids in sorted(sources.items())
    ]


def encode_replay(bundle):
    return json.dumps(
        bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
