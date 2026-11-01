import ast

from ..domain import AlphaCandidate, ResearchConfig, Signal
from ..validation import ValidationError, validate


def validate_proposal(proposal, sources, policy):
    """Deterministic syntax/metadata constraints, not a scientific truth validator."""
    if not proposal.evidence_refs:
        raise ValidationError("PROPOSAL_EVIDENCE_REQUIRED")
    fields = {k: v["type"] for k, v in sources["field_metadata"].items()}
    cfg = ResearchConfig(fields=fields, operators=policy.operators, **sources["settings"])
    union = set()
    for expression in proposal.expression_suggestions:
        try:
            tree = ast.parse(expression, mode="eval")
        except (SyntaxError, RecursionError) as exc:
            raise ValidationError("PROPOSAL_EXPRESSION_INVALID") from exc
        used = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in fields
        }
        if used - set(proposal.field_ids):
            raise ValidationError("PROPOSAL_UNDECLARED_FIELDS")
        validate(AlphaCandidate(expression, Signal("formal proposal", tuple(sorted(used)))), cfg)
        union.update(used)
    if union != set(proposal.field_ids):
        raise ValidationError("PROPOSAL_UNUSED_FIELDS")


def repairable(review):
    return bool(review.findings) and all(
        f.severity != "blocking"
        and f.category in {"mechanism", "expression", "clarity", "evidence_reference"}
        for f in review.findings
    )
