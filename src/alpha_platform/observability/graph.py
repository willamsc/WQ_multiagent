"""Business topology only. Every displayed graph state is a derived view."""

NODES = (
    "run_admission",
    "generator",
    "validator",
    "dedup_budget",
    "queue",
    "submit",
    "poll",
    "metrics_gate",
    "result_persist",
    "run_terminal",
)
EVENT_NODES = {
    "CREATED": "run_admission",
    "GENERATED": "generator",
    "VALIDATED": "validator",
    "DUPLICATE": "dedup_budget",
    "BUDGET_EXHAUSTED": "dedup_budget",
    "PENDING": "queue",
    "RESERVED": "dedup_budget",
    "SUBMITTING": "submit",
    "SUBMIT_INTENT": "submit",
    "SUBMITTED": "submit",
    "RATE_LIMIT": "submit",
    "POLLING": "poll",
    "POLL_INTENT": "poll",
    "FETCH_INTENT": "poll",
}


def event_node(kind, job_id, reason):
    if kind == "COMPLETED":
        return "result_persist" if job_id else "run_terminal"
    if kind == "REJECTED" and reason.startswith("VALIDATION"):
        return "validator"
    if kind in {"FAILED", "UNKNOWN"} and job_id:
        return "submit"  # The reader resolves polling failures from preceding facts.
    return EVENT_NODES.get(kind, "run_terminal")


def build_graph(events, jobs, run_status, anomalies):
    nodes = {
        node: {"id": node, "status": "NOT_VISITED", "derived": True, "evidence_refs": []}
        for node in NODES
    }
    trace_nodes = {}
    for event in events:
        node = nodes[event["node"]]
        # Trace cannot override audit/business facts. It only fills otherwise absent detail.
        if event["source_kind"] == "TRACE":
            trace_nodes[event["node"]] = event
            continue
        node["status"] = event["outcome"]
        node["evidence_refs"].append(event["source_ref"])
    for name, event in trace_nodes.items():
        if nodes[name]["status"] == "NOT_VISITED":
            nodes[name].update(status=event["outcome"], evidence_refs=[event["source_ref"]])
    for anomaly in anomalies:
        name = anomaly.get("node")
        if name in nodes:
            nodes[name]["status"] = anomaly.get("node_status", "FAILED")
            nodes[name]["evidence_refs"].extend(anomaly["evidence_refs"])
    for job in jobs:
        ref = job["source_ref"]
        if job["status"] in {"SUBMITTED", "POLLING", "COMPLETED"}:
            nodes["submit"].update(status="COMPLETED")
            nodes["submit"]["evidence_refs"].append(ref)
        if job["status"] == "POLLING":
            nodes["poll"].update(status="RUNNING")
            nodes["poll"]["evidence_refs"].append(ref)
        if job["status"] == "UNKNOWN":
            node = "poll" if job["remote_id_known"] else "submit"
            nodes[node].update(status="UNKNOWN")
            nodes[node]["evidence_refs"].append(ref)
        if job["result_present"]:
            for name in ("poll", "metrics_gate", "result_persist"):
                nodes[name].update(status="COMPLETED")
                nodes[name]["evidence_refs"].append(job["result_ref"])
    if run_status in {"COMPLETED", "FAILED", "REJECTED"}:
        nodes["run_terminal"]["status"] = run_status
    for node in nodes.values():
        node["evidence_refs"] = sorted(set(node["evidence_refs"]))
    expected = [
        {"from": a, "to": b, "derived": True, "kind": "expected"} for a, b in zip(NODES, NODES[1:])
    ]
    path = []
    for event in events:
        if event["source_kind"] != "TRACE" and (not path or path[-1] != event["node"]):
            path.append(event["node"])
    actual = [
        {
            "from": a,
            "to": b,
            "derived": True,
            "kind": "observed",
            "evidence_refs": sorted(set(nodes[a]["evidence_refs"] + nodes[b]["evidence_refs"])),
        }
        for a, b in dict.fromkeys(zip(path, path[1:]))
        if a != b
    ]
    return list(nodes.values()), expected + actual
