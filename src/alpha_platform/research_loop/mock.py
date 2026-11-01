"""Deterministic research fixtures. PASS is a protocol demonstration, not alpha evidence."""

import asyncio
import json

from ..agent_interop.backends import MockBackend
from ..agent_interop.contracts import Role, canonical

SCENARIOS = (
    "pass",
    "reject",
    "uncertain",
    "repair_pass",
    "repair_exhausted",
    "regression_fail",
    "regression_uncertain",
    "timeout",
    "invalid_json",
    "wrong_version",
    "overreach",
    "unsupported_syntax",
    "evidence_request",
)


class ResearchMockBackend(MockBackend):
    def __init__(self, scenario="pass"):
        super().__init__()
        if scenario not in SCENARIOS:
            raise ValueError("UNKNOWN_RESEARCH_MOCK_SCENARIO")
        self.research_scenario = scenario

    async def execute(self, request):
        scenario = self.research_scenario
        role = request.task.role
        version = request.task.subject.signal_version
        if role == Role.CRITIC:
            if scenario == "timeout":
                await asyncio.sleep(360)
            if scenario == "invalid_json":
                return "not JSON"
            if scenario == "overreach":
                request.context.evidence("generator_private_history")
        response = json.loads(await super().execute(request))
        result = response["result"]
        if role == Role.GENERATOR:
            result["mechanism"] += f"；正式提案版本 {version}"
            if scenario == "unsupported_syntax":
                result["expression_suggestions"] = [result["field_ids"][0] + " + 1"]
        else:
            regression = request.context.content().get("workflow", {}).get("phase") == "regression"
            verdict = "PASS"
            if scenario in {"uncertain", "evidence_request"}:
                verdict = "UNCERTAIN"
            elif scenario == "reject" or scenario == "repair_exhausted":
                verdict = "FAIL"
            elif (
                scenario in {"repair_pass", "regression_fail", "regression_uncertain"}
                and version == 1
            ):
                verdict = "FAIL"
            elif scenario == "regression_fail" and regression:
                verdict = "FAIL"
            elif scenario == "regression_uncertain" and regression:
                verdict = "UNCERTAIN"
            result["verdict"] = verdict
            if verdict == "FAIL":
                result["findings"] = [
                    {
                        "id": "finding-1",
                        "category": "point_in_time" if scenario == "reject" else "mechanism",
                        "severity": "blocking" if scenario == "reject" else "major",
                        "description": "离线夹具缺陷，不是实际研究判断",
                        "evidence_refs": result["evidence_refs"],
                    }
                ]
            if scenario == "evidence_request":
                result["findings"] = [
                    {
                        "id": "request-1",
                        "category": "evidence_request",
                        "severity": "major",
                        "description": "e:additional",
                        "evidence_refs": [],
                    }
                ]
            if scenario == "wrong_version":
                result["signal_version"] += 1
        return canonical(response)
