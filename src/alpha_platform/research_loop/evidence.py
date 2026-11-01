"""Small deterministic gateway over an explicitly approved in-memory catalog only."""

from dataclasses import asdict

from ..agent_interop.contracts import Role, canonical, identifier


class EvidenceGateway:
    def __init__(self, store, catalog):
        self.store = store
        self.catalog = {item["ref"]: item for item in catalog}

    def request(self, study_id, outcome, phase, requested_ref):
        identifier(requested_ref)
        if outcome.role != Role.CRITIC or getattr(outcome.result, "verdict", None) != "UNCERTAIN":
            raise PermissionError("EVIDENCE_REQUEST_ROLE_DENIED")
        store = self.store
        with store.ledger.transaction():
            store._confirmed(study_id, outcome, phase)
            existing = store.db.execute(
                "SELECT response_json FROM signal_evidence_requests WHERE task_id=? AND requested_ref=?",
                (outcome.task_id, requested_ref),
            ).fetchone()
            if existing:
                return existing[0]
            study = store.db.execute(
                "SELECT * FROM signal_studies WHERE study_id=?", (study_id,)
            ).fetchone()
            if study["current_version"] != outcome.subject.signal_version:
                raise ValueError("EVIDENCE_REQUEST_VERSION_MISMATCH")
            import json

            limit = json.loads(study["policy_json"])["max_evidence_requests"]
            if study["used_evidence"] >= limit:
                store.event(study_id, "EVIDENCE_LIMIT_REACHED", task_id=outcome.task_id)
                return None
            item = self.catalog.get(requested_ref)
            response = {
                "status": "AVAILABLE" if item else "UNAVAILABLE",
                "scope": "provided_evidence",
                "subject": asdict(outcome.subject),
                "evidence": item,
                "independently_verified": False,
            }
            store.db.execute(
                "INSERT INTO signal_evidence_requests(study_id,task_id,attempt_id,version,requested_ref,response_json) VALUES(?,?,?,?,?,?)",
                (
                    study_id,
                    outcome.task_id,
                    outcome.attempt_id,
                    outcome.subject.signal_version,
                    requested_ref,
                    canonical(response),
                ),
            )
            store.db.execute(
                "UPDATE signal_studies SET used_evidence=used_evidence+1 WHERE study_id=?",
                (study_id,),
            )
            store.event(
                study_id,
                "EVIDENCE_REQUESTED",
                task_id=outcome.task_id,
                requested_ref=requested_ref,
                response_status=response["status"],
            )
            return canonical(response)
