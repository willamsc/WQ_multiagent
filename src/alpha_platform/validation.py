"""A deliberately small expression subset, never evaluated as Python."""

import ast
import hashlib
import json
import math
import tokenize
from io import StringIO


class ValidationError(ValueError):
    pass


def validate(candidate, config):
    try:
        source = candidate.expression.strip()
        for token in tokenize.generate_tokens(StringIO(source).readline):
            if token.type == tokenize.COMMENT or (
                token.type == tokenize.NAME and not token.string.isascii()
            ):
                raise ValidationError("unsupported comment or non-ASCII identifier")
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, RecursionError, tokenize.TokenError) as exc:
        raise ValidationError("invalid expression syntax") from exc
    used = set()

    def visit(node):
        if isinstance(node, ast.Name):
            if config.fields.get(node.id) != "MATRIX":
                raise ValidationError("unknown field or unsupported field type")
            used.add(node.id)
        elif isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.keywords
                or node.func.id not in config.operators
                or len(node.args) != config.operators[node.func.id]
            ):
                raise ValidationError("unsupported operator or signature")
            for arg in node.args:
                visit(arg)
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ValidationError("numeric literal must be finite")
            return
        else:
            raise ValidationError("unsupported expression syntax")

    try:
        visit(tree.body)
    except RecursionError as exc:
        raise ValidationError("expression nesting too deep") from exc
    if not used or used != set(candidate.signal.field_ids):
        raise ValidationError("declared fields do not match expression")
    canonical = ast.unparse(tree)
    return canonical, fingerprints(canonical, config)[0]


def fingerprints(canonical, config):
    """Current identity plus legacy v1 identities, so old UNKNOWN jobs stay blocked."""
    payload = {
        "version": 2,
        "expression": canonical,
        "settings": config.settings(),
        "metadata": {"fields": config.fields, "operators": config.operators},
        "adapter": getattr(config, "adapter_identity", "mock-v1"),
    }

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    current = digest(payload)
    legacy = [
        digest({**payload, "version": 1, "scenario": scenario})
        for scenario in ("success", "rate_limit", "unknown", "failed", "delayed", "timeout")
    ]
    return [current, *legacy]
