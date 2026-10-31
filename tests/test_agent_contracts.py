from dataclasses import asdict

import pytest

from alpha_platform.agent_interop.contracts import (
    ContractError,
    ResourceLimits,
    Subject,
    Verdict,
    parse_proposal,
    parse_review,
    strict_json,
)


def proposal():
    return {
        "schema_version": 1,
        "signal_id": "s",
        "signal_version": 1,
        "economic_hypothesis": "mock hypothesis",
        "mechanism": "mock mechanism",
        "field_ids": ["mock_value"],
        "expression_suggestions": ["mock_rank(mock_value)"],
        "evidence_refs": ["e:1"],
        "risks": ["unverified"],
    }


def review(verdict="UNCERTAIN"):
    return {
        "schema_version": 1,
        "signal_id": "s",
        "signal_version": 1,
        "verdict": verdict,
        "findings": [],
        "evidence_refs": ["e:1"],
    }


def test_roles_have_distinct_strict_contracts():
    parsed = parse_proposal(proposal(), Subject("s", 1), ["mock_value"], ["e:1"])
    assert parsed.signal_version == 1
    assert parse_review(review(), Subject("s", 1), ["e:1"]).verdict == Verdict.UNCERTAIN
    with pytest.raises(ContractError):
        parse_proposal(review(), Subject("s", 1), ["mock_value"], ["e:1"])
    with pytest.raises(ContractError):
        parse_review(proposal(), Subject("s", 1), ["e:1"])
    with pytest.raises(ContractError):
        parse_proposal(
            {**proposal(), "signal_version": True}, Subject("s", 1), ["mock_value"], ["e:1"]
        )


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', "not json", "{} trailing"])
def test_strict_json_rejects_ambiguous_or_malformed_output(raw):
    with pytest.raises(ContractError):
        strict_json(raw, 100)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 0, -1, 301])
def test_resource_limits_are_bounded(value):
    with pytest.raises(ContractError):
        ResourceLimits(timeout_seconds=value)


def test_foreign_version_and_forged_evidence_are_rejected():
    for values in (
        {"signal_version": 2},
        {"field_ids": ["unknown"]},
        {"evidence_refs": ["unapproved"]},
        {"extra": "no"},
    ):
        with pytest.raises(ContractError):
            parse_proposal({**proposal(), **values}, Subject("s", 1), ["mock_value"], ["e:1"])
    result = review("PASS")
    result["findings"] = [
        {
            "id": "f1",
            "category": "timing",
            "severity": "blocking",
            "description": "unresolved",
            "evidence_refs": ["e:1"],
        }
    ]
    with pytest.raises(ContractError):
        parse_review(result, Subject("s", 1), ["e:1"])
    assert asdict(parse_review(review("PASS"), Subject("s", 1), ["e:1"]))["verdict"] == "PASS"
