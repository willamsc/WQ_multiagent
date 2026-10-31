"""R2: pure prefix reducer. Final database rows are deliberately not an input."""

from copy import deepcopy

from .graph import NODES


def initial_state():
    return {
        "ordinal": 0,
        "active_node": None,
        "node_states": {
            node: {
                "state": "NOT_OBSERVED_YET",
                "basis": "UNKNOWN",
                "refs": [],
                "source_kind": None,
                "changed_at": 0,
            }
            for node in NODES
        },
        "run": {"state": "UNKNOWN", "basis": "UNKNOWN", "refs": []},
        "jobs": {},
        "submit_intents": {},
        "budget": {
            "total": None,
            "reserved": None,
            "used": None,
            "basis": "UNKNOWN",
            "refs": [],
            "last_delta": None,
        },
        "anomalies_at_frame": [],
        "data_gaps": [],
        "transitions": [],
    }


def apply_frame(previous, frame):
    if frame["ordinal"] <= previous["ordinal"]:
        return deepcopy(previous)
    state = deepcopy(previous)
    state["ordinal"] = frame["ordinal"]
    state["active_node"] = frame["node_id"]
    state["transitions"] = []
    refs = frame["evidence_refs"]
    source, kind, node = frame["source_kind"], frame["event_kind"], frame["node_id"]
    payload, job_id = frame["payload_summary"], frame["subject"].get("job_id")
    reason = payload.get("reason_code")

    def gap(message):
        if message not in state["data_gaps"]:
            state["data_gaps"].append(message)

    def set_node(name, value, basis="RECORDED", inference=None):
        current = state["node_states"][name]
        if (
            source == "TRACE"
            and current["source_kind"] == "AUDIT"
            and current["basis"] == "RECORDED"
        ):
            # A later-displayed trace cannot undo a stronger audit state in an ambiguous tie.
            return
        if (
            basis == "INFERRED"
            and current["basis"] == "RECORDED"
            and current["state"] not in {"ACTIVE", "WAITING", "NOT_OBSERVED_YET"}
        ):
            return
        state["node_states"][name] = {
            "state": value,
            "basis": basis,
            "refs": refs,
            "source_kind": source,
            "changed_at": frame["ordinal"],
            **({"inference_reason": inference} if inference else {}),
        }

    def set_run(value):
        state["run"] = {"state": value, "basis": "RECORDED", "refs": refs}

    def set_job(value):
        if job_id:
            prior = state["jobs"].get(job_id, {})
            state["jobs"][job_id] = {**prior, "state": value, "basis": "RECORDED", "refs": refs}

    def anomaly(code, target=node, basis="RECORDED"):
        item = {
            "code": code,
            "node_id": target,
            "basis": basis,
            "evidence_refs": refs,
            "first_seen_ordinal": frame["ordinal"],
        }
        if not any(
            a["code"] == code and a["node_id"] == target for a in state["anomalies_at_frame"]
        ):
            state["anomalies_at_frame"].append(item)

    def budget_delta(reserved, used):
        budget = state["budget"]
        for key, delta in (("reserved", reserved), ("used", used)):
            if budget[key] is not None:
                updated = budget[key] + delta
                budget[key] = updated if updated >= 0 else None
                if updated < 0:
                    gap("预算审计前缀不完整或冲突，绝对金额未知")
        budget["basis"] = "INFERRED"
        budget["last_delta"] = {"reserved": reserved, "used": used}
        budget["refs"] = list(dict.fromkeys([*budget["refs"], *refs]))[-4:]
        budget["inference_reason"] = "按 M1 v1 审计路径推导；不是原始预算快照"

    def frozen_polling():
        if state["run"]["state"] == "FAILED" and any(
            j["state"] == "POLLING" for j in state["jobs"].values()
        ):
            anomaly("RUN_JOB_STATE_MISMATCH_MANUAL_REVIEW", "result_persist", "INFERRED")
            original = previous["node_states"]["poll"]
            state["node_states"]["poll"] = {
                "state": "UNKNOWN",
                "basis": "INFERRED",
                "source_kind": "DERIVED",
                "refs": list(
                    dict.fromkeys(
                        [
                            *state["run"]["refs"],
                            *[
                                ref
                                for j in state["jobs"].values()
                                if j["state"] == "POLLING"
                                for ref in j["refs"]
                            ],
                        ]
                    )
                ),
                "changed_at": original["changed_at"]
                if original.get("source_kind") == "DERIVED"
                else frame["ordinal"],
                "inference_reason": "研究已失败，查询中是保留的任务状态，不证明调用仍在执行；需人工核查",
            }

    if frame["evidence_level"] == "UNKNOWN":
        gap("证据缺失：本帧不用于确认历史状态")
        return state
    if frame["effect"] == "NO_KNOWN_EFFECT":
        gap("未支持的历史事件：不推导成功状态")
        return state
    if previous["active_node"] and previous["active_node"] != node:
        state["transitions"] = [
            {
                "from": previous["active_node"],
                "to": node,
                "basis": "INFERRED",
                "refs": refs,
                "inference_reason": "仅为稳定展示次序，非确认的调用因果",
            }
        ]
    else:
        state["transitions"] = []
    if source == "TRACE":
        if kind == "NODE_STARTED":
            set_node(node, "ACTIVE")
        elif kind == "NODE_FAILED":
            set_node(node, "FAILED")
            anomaly("TRACE_NODE_FAILURE")
        elif kind == "NODE_COMPLETED":
            if payload.get("status") == "INVALID":
                set_node(node, "UNKNOWN")
                gap("诊断调用返回了不支持的状态，不能推导为成功")
            else:
                set_node(node, "WAITING" if payload.get("status") == "RUNNING" else "COMPLETED")
        gap("诊断调用的结束不等同于业务状态迁移；没有调用配对标识，未进行开始/结束配对")
        frozen_polling()
        return state

    if kind == "CREATED":
        set_run("CREATED")
        set_node("run_admission", "ACTIVE")
        state["budget"].update(
            reserved=0,
            used=0,
            basis="INFERRED",
            refs=refs,
            inference_reason="由 M1 v1 创建初值规则推导，预算总额未记录在审计中",
        )
    elif kind == "GENERATED":
        set_node("generator", "COMPLETED")
    elif kind == "VALIDATED":
        set_node("validator", "COMPLETED")
    elif kind == "PENDING":
        set_job("PENDING")
        set_node("queue", "WAITING")
        set_node("run_admission", "COMPLETED", "INFERRED", "由入队审计路径推导已获接纳")
        if state["run"]["state"] not in {"COMPLETED", "FAILED", "REJECTED"}:
            state["run"] = {
                "state": "RUNNING",
                "basis": "INFERRED",
                "refs": refs,
                "inference_reason": "由 M1 v1 入队路径推导",
            }
    elif kind == "RESERVED":
        set_job("RESERVED")
        set_node("dedup_budget", "COMPLETED")
        budget_delta(1, 0)
    elif kind == "SUBMITTING":
        set_job("SUBMITTING")
        set_node("submit", "ACTIVE")
        set_node("queue", "COMPLETED", "INFERRED", "由任务进入提交阶段推导已离开队列")
    elif kind == "SUBMIT_INTENT":
        set_node("submit", "ACTIVE")
        if job_id:
            state["submit_intents"][job_id] = state["submit_intents"].get(job_id, 0) + 1
    elif kind == "SUBMITTED":
        set_job("SUBMITTED")
        set_node("submit", "COMPLETED")
        if job_id:
            state["jobs"][job_id]["remote_id_present"] = True
            state["jobs"][job_id]["receipt_basis"] = "INFERRED_FROM_SUBMITTED_AUDIT"
        budget_delta(-1, 1)
    elif kind == "POLLING":
        set_job("POLLING")
        set_node("poll", "WAITING")
    elif kind in {"POLL_INTENT", "FETCH_INTENT"}:
        set_node("poll", "ACTIVE")
    elif kind in {"DUPLICATE", "BUDGET_EXHAUSTED"}:
        set_node("dedup_budget", "REJECTED")
        anomaly(kind, "dedup_budget")
    elif kind == "RATE_LIMIT":
        set_node("submit", "WAITING")
        anomaly("RATE_LIMIT_RECORDED", "submit")
    elif kind == "UNKNOWN":
        set_job("UNKNOWN")
        set_node(node, "UNKNOWN")
        anomaly("REMOTE_EXECUTION_UNKNOWN")
        if job_id and state["jobs"][job_id].get("remote_id_present") is not True:
            # No remote ID is ever fabricated from the final job table.
            state["jobs"][job_id]["remote_id_present"] = None
            gap("截至此帧未观察到回执，不能确认远程标识是否存在")
    elif kind == "COMPLETED":
        if job_id:
            set_job("COMPLETED")
            set_node("result_persist", "COMPLETED")
            set_node("poll", "COMPLETED", "INFERRED", "由 M1 任务完成审计推导查询阶段已结束")
            set_node(
                "metrics_gate",
                "COMPLETED",
                "INFERRED",
                "M1 结果保存前已计算门控；具体值未记录于该审计",
            )
        else:
            set_run("COMPLETED")
            set_node("run_terminal", "COMPLETED")
    elif kind == "REJECTED":
        set_run("REJECTED")
        set_node("run_terminal", "REJECTED")
        set_node("run_admission", "REJECTED", "INFERRED", "研究拒绝审计表明此次候选未获接纳")
        target = "validator" if reason == "VALIDATION" else "dedup_budget"
        if reason in {"VALIDATION", "DUPLICATE", "BUDGET_EXHAUSTED"}:
            set_node(target, "REJECTED")
            anomaly(reason, target)
    elif kind == "FAILED":
        if job_id:
            set_job("FAILED")
            set_node(node, "FAILED")
        else:
            set_run("FAILED")
            set_node("run_terminal", "FAILED")
            if reason == "PERSISTENCE_FAILURE":
                set_node("result_persist", "FAILED", "INFERRED", "终态审计说明结果持久化失败")
                anomaly(reason, "result_persist")
            elif reason == "RATE_LIMIT":
                budget_delta(-1, 0)
                anomaly("RATE_LIMIT_EXHAUSTED", "submit")
            elif reason:
                anomaly(reason)
    frozen_polling()
    return state


def state_delta(before, after):
    """Small root replacements + per-node updates. No JavaScript business reducer is needed."""
    delta = {
        key: value
        for key, value in after.items()
        if key != "node_states" and before.get(key) != value
    }
    nodes = {
        key: value
        for key, value in after["node_states"].items()
        if before["node_states"][key] != value
    }
    if nodes:
        delta["node_states"] = nodes
    return delta


def apply_delta(state, delta):
    result = deepcopy(state)
    for key, value in delta.items():
        if key == "node_states":
            result[key].update(deepcopy(value))
        else:
            result[key] = deepcopy(value)
    return result


def project(frames, ordinal):
    if not 0 <= ordinal <= len(frames):
        raise ValueError("ordinal out of range")
    state = initial_state()
    for frame in frames[:ordinal]:
        state = apply_frame(state, frame)
    return state


def build_deltas(frames):
    state = initial_state()
    deltas = []
    for frame in frames:
        updated = apply_frame(state, frame)
        deltas.append({"ordinal": frame["ordinal"], "delta": state_delta(state, updated)})
        state = updated
    return deltas
