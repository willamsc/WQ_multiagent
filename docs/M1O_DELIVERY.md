# M1-O delivery

Implemented O0 → O5 incrementally against the real M1 source. No live API,
M1.5/M2+, forum ingestion, RAG, model download or new dependencies/frameworks.

## Files and scope

- `docs/M1O_BASELINE_AUDIT.md`: pre-change schema/transaction/contract audit;
  original 61-test baseline passed.
- `src/alpha_platform/observability/queries.py`, `graph.py`: read-only committed
  snapshot, normalized events, stable ordering, graph, anomalies, evidence refs.
- `observability/safety.py`: allowlisted summaries, bounded recursive sanitization,
  credential-like text/environment-secret redaction.
- `observability/traces.py`: off/light/full sink, one optional additive trace table,
  bounded buffering/payloads, post-business flush, seven-day/10000-row retention.
- `observability/diagnostics.py`: schema v1 JSON bundles, byte budgets, evidence
  reference closure, atomic artifact output, database-sidecar overwrite protection.
- `observability/html_report.py`: standalone escaped HTML, graph, local timeline
  filters, expandable evidence inspector; no server/CDN/LLM.
- `workflow.py`: optional injected outer-node spans; no observatory import, no
  budget/status/dedup policy change, mandatory audit transactions preserved.
- `cli.py`: generator span and independent optional flush; observe commands use
  only the read model, never initialize/migrate/write the business database.
- `tests/test_observatory.py`: 25 new cases, including fault injection and privacy.
- `scripts/generate_observatory_samples.py`: six reproducible offline scenarios.
- `README.md`, `docs/ARCHITECTURE.md`, `docs/OBSERVATORY.md`, `.gitignore`: operation,
  design decisions, limits and project-local browser profile exclusion.

## Validation

- Full pytest: **86 passed**, including the original 61 cases.
- `uv run --offline ruff check .`: **All checks passed**.
- `uv run --offline ruff format --check .`: **27 files already formatted**.
- Tests verify old runs without traces, rejection, dedup/budget, UNKNOWN, FAILED +
  POLLING, result-write failure, optional emit/SQLite/flush failure isolation,
  mandatory audit rollback, generator failure, HTML injection, secret redaction,
  trace/payload/JSON bounds, stable ties, missing evidence, read-only consistency,
  cross-process CLI and protected output paths.
- Existing Chrome headless rendered the offline desktop report. Its isolated
  profile and preview image are under this project; no browser/model downloaded.

## Examples

Open `examples/observatory/success.html` or `persistence-failure.html` directly.
The directory also contains validation-rejected, duplicate, budget-exhausted,
unknown HTML/JSON pairs, `manifest.json` with actual mock run IDs, and a desktop
`success-preview.png`. Example database `samples.sqlite3` is a local ignored artifact;
the generation script recreates all cases in a new output directory.

## Remaining limits

Offline reports only; no HTTP viewer or real-time stream. Trace flush occurs at
the end of a run, so a killed process can lose its optional buffer. Original audit
remains the durable evidence. Old audit timestamps have second precision; exact
cross-source ordering within a second is unavailable. Historical timings/retries
without trace cannot be reconstructed. Reader/export size limits may omit older
detail and explicitly mark gaps; an insufficient byte budget fails cleanly.

UNKNOWN and FAILED + POLLING remain frozen; no reconciliation/recovery/refund
command. Core schema stays v1; optional extension schema is versioned separately.
Redaction is conservative, not a classifier for arbitrary unlabelled secrets.
Original M1 business storage is not rewritten. No research facts are inferred
from optional diagnostics as authoritative state.
