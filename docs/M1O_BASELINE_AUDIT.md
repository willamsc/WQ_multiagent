# M1-O baseline audit (O0)

Inspected actual source before implementation; `uv run --offline pytest -q`:
**61 passed**. No business implementation changed in O0.

## Existing storage

`Repository.initialize()` in `persistence.py` owns core schema v1 (`PRAGMA
user_version=1`), repeatable CREATE IF NOT EXISTS; versions >1 are rejected.
Tables/keys: research_runs(id), alphas(id, UNIQUE fingerprint), sim_jobs(id,
run_id FK, UNIQUE alpha_id FK), job_attempts(id, job_id FK), sim_results(job_id
PK/FK), research_events(id INTEGER PK, run_id FK, nullable job_id FK).
SQLite autoindexes cover PK/UNIQUE; no explicit run/event index yet.
Foreign keys and WAL are enabled on business connections.

`research_events` already supplies run_id, job_id, event_type, payload_json,
at (UTC seconds), and monotonic integer id for timestamp ties. It is the
required audit, not an optional trace. No existing audit_events table.

## Transactions and facts

Controller admission: one BEGIN IMMEDIATE contains CREATED check, GENERATED,
validation, VALIDATED/rejection, dedup, budget reservation, alpha/job insertion,
PENDING/RESERVED/SUBMITTING. Each submit attempt records INTENT and attempt count
before external calls. Receipt, reservation-to-debit settlement, SUBMITTED and
POLLING share a transaction. UNKNOWN holds the reservation for ambiguous submit;
rate-limit exhaustion releases it. Save-result transaction includes result row,
COMPLETED job and audit. Run terminal transition is a separate transaction.

Audit payloads include generated candidate, fingerprint, duplicate alpha link,
terminal reason. Missing details: generator timing/failure, individual poll
responses, local result-write retries/durations (currently log-only), explicit
budget deltas and gate timing. Existing state facts can explain terminal budgets
and stored gate, but historical intermediate budgets must be marked derived.

## Contracts and interfaces

Domain has ResearchConfig, Signal, AlphaCandidate, SimulationRequest,
SubmissionReceipt(remote_id), SimulationResult(metrics/checks/raw), SimulationJob,
ResearchRun. Job enum: PENDING, RESERVED, SUBMITTING, SUBMITTED, POLLING,
COMPLETED, FAILED, UNKNOWN. Run: CREATED, RUNNING, COMPLETED, REJECTED, FAILED.
Only internal mock API exists; no platform fields/IDs may be invented.
Generator is called by CLI composition before Controller. Validator and metrics
gate are Controller operations. Mock poll returns RUNNING/COMPLETED/FAILED.

`Repository.report(run_id)` queries on a writable connection and returns run,
jobs/results, ordered events, attempts and summary. CLI always initializes schema,
including research show. Observatory must not reuse that initialization path:
its reader will use URI mode=ro + query_only and a consistent read transaction.

## Incremental plan

O1 reuses existing facts/audit for all old runs. No duplicate audit table.
O2 adds one optional `observability_traces` table lazily when light/full tracing
is enabled. Core user_version remains 1; the additive extension has its own row
schema_version=1 and CREATE IF NOT EXISTS entry. Old M1 files are readable without
migration; no tables/columns renamed, no data rebuilt. Trace buffer/flush is bounded
and isolated from business transactions. Core runs with no injected trace sink
retain the exact existing behavior and audit path; the UI is never required.

O3/O4 add read-only diagnostic CLI and self-contained offline HTML, no HTTP service.
All public summaries will use allowlists, credential redaction, truncation and
resolvable evidence references. Derived graph/budget annotations remain explicit.
