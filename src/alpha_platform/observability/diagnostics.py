"""Deterministic evidence bundles with an explicit byte budget."""

import json
import os
import tempfile
from pathlib import Path


def references(value):
    refs = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_ref", "candidate_ref", "result_ref"} and isinstance(item, str):
                refs.add(item)
            elif key in {"evidence_refs", "refs"}:
                refs.update(item)
            else:
                refs.update(references(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(references(item))
    return refs


def encode(snapshot, max_bytes=262144):
    if not 8192 <= max_bytes <= 4 * 1024 * 1024:
        raise ValueError("max_bytes must be between 8192 and 4194304")
    # All values have already been projected/redacted by the reader.
    report = json.loads(json.dumps(snapshot, allow_nan=False))

    def pack():
        return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)

    if len(pack().encode("utf-8")) > max_bytes:
        report["events"] = report["events"][-16:]
        report["attempts"] = report["attempts"][-8:]
        for entry in [*report["nodes"], *report["edges"], *report["anomalies"]]:
            if "evidence_refs" in entry:
                entry["evidence_refs"] = entry["evidence_refs"][-4:]
        report["evidence_gaps"].append(
            "EXPORT_TRUNCATED: byte budget; use source refs to drill down"
        )
        report["truncated"] = True
        evidence = report.pop("evidence")
        refs = references(report)
        report["evidence"] = {key: evidence[key] for key in sorted(refs) if key in evidence}
    result = pack()
    if len(result.encode("utf-8")) > max_bytes:
        raise ValueError("byte budget too small for evidence bundle; increase --max-bytes")
    if references({k: v for k, v in report.items() if k != "evidence"}) - report["evidence"].keys():
        raise ValueError("unresolved evidence reference")
    return result


def write_artifact(output, content, database):
    target = Path(output).resolve()
    db = Path(database).resolve()
    if target in {db, Path(str(db) + "-wal"), Path(str(db) + "-shm")}:
        raise ValueError("output must not overwrite the database or its sidecars")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=".observatory-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp = Path(handle.name)
            handle.write(content)
        os.replace(temp, target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
