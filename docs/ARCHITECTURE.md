# M1 implementation decisions

Only M1 is implemented. Development follows README section 9: scaffold → domain →
SQLite → simulation adapter → validator → controller → generator → CLI/tests.

## Boundaries

- `domain.py`: dataclasses and run/job status enums, no I/O.
- `persistence.py`: schema version 1, SQLite transactions, constraints, state transition
  enforcement and report queries. Foreign keys and WAL enabled. Initialization is
  repeatable; newer schema versions are rejected. No ORM or migration framework.
- `simulation.py`: async protocol (`submit`, `poll`, `fetch_result`) and deterministic
  mock scenarios. No real endpoints, credentials or formal Alpha submission method.
- `validation.py`: parse but never execute expressions. Supports MATRIX field names,
  numeric literals and positional operator calls with configured arity. This is a
  small mock language, not a full FastExpr parser or complete type system.
- `workflow.py`: owns validation gates, deduplication, budget reservation, dispatch,
  legal job transitions, result persistence and the configurable mock Sharpe gate.
- `agents.py`: replaceable structured runtime and generator interfaces. The mock
  returns candidate data and cannot access the database or simulation adapter.
- `cli.py`: composition root; JSON config and JSON report output via standard library.

## State and accounting

Research state is separate from job state. M1 research runs move CREATED → RUNNING →
COMPLETED/FAILED, or CREATED → REJECTED. A completed experiment can fail its metric
gate without becoming an execution failure. No func1/func2/critic phases are simulated.

One run handles one candidate. Each accepted mock simulation costs one budget unit.
Queue insertion, deduplication, budget reservation and initial job events share a
`BEGIN IMMEDIATE` transaction. A versioned SHA-256 fingerprint includes canonical
expression, all mock result settings and configured metadata. Fault scenarios, budget,
polling limits and metric gates do not change simulation identity. Fingerprint v2
excludes fault scenarios; duplicate lookup also checks all six historical v1 scenario
hashes so existing UNKNOWN jobs remain protected without rewriting their history. Exact duplicates
are blocked globally (including UNKNOWN/failed jobs); semantic similarity is not claimed.

Every submit attempt has a persisted intent before the adapter is called. All adapter
calls happen outside database transactions. Known mock rate limits guarantee no
acceptance and allow bounded retry. Accepted receipts atomically convert reserved to
used budget. Ambiguous submissions retain reserved budget and become UNKNOWN; no
automatic resubmission. Poll failures retain the receipt and used budget.

Results, COMPLETED state and its audit event commit together. A transient SQLite
failure retries only this local transaction once. A unique result per job and an
already-completed check make repeated completion idempotent. No repeated remote call
or budget debit is needed. Repeated persistence failure leaves a queryable FAILED run
with its job still POLLING; automated recovery is deferred to M4.
This is a frozen manual-review state: terminal runs cannot advance their jobs,
late result writes roll back, and consumed budget is not refunded. Run completion
requires exactly one completed job. UNKNOWN has no outgoing transitions in M1;
there is no supported manual settlement/release command or automated reconciliation.
Use `research show` to inspect the original run, job, attempts and duplicate alpha link.

Run eligibility, generation audit, validation, duplicate decisions and budget admission
are now inside the same immediate transaction. Concurrent callers cannot overwrite
an active/terminal run or create two jobs for a single M1 run.

## Evidence and reporting

Each alpha retains its hypothesis/provenance separately from simulation facts.
Reports are generated from persisted runs, jobs, raw mock output, parsed metrics,
checks, gate decisions and ordered audit events. `research show` works in a new process.
Mock fields, operators, metrics and thresholds are fixtures, never platform facts.
Errors are logged as structured event codes; exceptions/configuration/credentials
are not dumped into logs.

## Explicit limits

Single-process sequential execution, bounded immediate mock retries/polls, no real
network adapter or LLM. No live mode, RAG, embedding download, critic, reservoir,
swarm search, correlation checks, worker leases, remote reconciliation or interrupted
workflow resumption. SQLite initialization is versioned but no v2 migration exists.
M1 provides restart-safe query, not automatic restart-safe execution.

Config uses JSON rather than YAML to avoid a runtime dependency. The default
GBR/TOP700/D1 values are editable research examples, not verified platform metadata.
The `min_sharpe` gate is an illustrative mock gate, not a submission criterion.

## Correctness review follow-up

The controller validates adapter receipts and finite JSON result shapes before
accounting or gating. Malformed receipts preserve a reservation as UNKNOWN;
malformed results/statuses retain the accepted receipt and consumed budget as UNKNOWN.
Mock now tracks polling progress by remote ID and rejects unknown/uncompleted result
lookups. Three malformed-response scenarios supplement the original six scenarios.
These contracts describe the internal mock API only.

Validator explicitly rejects comments, non-ASCII identifiers (Python would silently
normalize some identifiers), non-finite literals, unsupported AST nodes and excessive
recursive nesting. Supported syntax is normalized without algebraic rewrites; numeric
literal semantics are Python AST semantics, not a claim of FastExpr equivalence.

## M1-O read model and diagnostic tracing

`observability/queries.py` reads a committed SQLite snapshot using a distinct read-only
connection. It maps existing audit IDs to stable event/evidence references; no new
authoritative state is maintained. `graph.py` derives M1 business topology/statuses;
`diagnostics.py` exports bounded evidence bundles; `html_report.py` escapes data into
a standalone HTML inspector. The CLI observe branch never constructs Repository,
Controller or Adapter and never initializes schema. No HTTP service was added.

Optional trace is injected into Controller as a sink, with no observability import
in the business module. Spans observe outer invocations and never decide policy.
`off` supplies no sink; light/full use a bounded buffer and a separate post-business
flush. Trace errors are isolated; required research_events audit remains transactional.
Generator instrumentation stays at CLI composition, not inside Agent Runtime.

The only additive table is observability_traces, with a run FK and (run_id,id) index.
Its schema_version=1 is independent of PRAGMA user_version (core remains v1).
CREATE IF NOT EXISTS in TraceSink.flush is the optional extension's initialization
entry. Existing databases are not rebuilt/migrated on reads. Retention touches only
optional traces. These choices preserve all original M1 schema/status/budget tests.

Audit is second-resolution; optional trace has millisecond UTC timestamps. Ordering
uses timestamps/source/monotonic IDs and cannot establish precise cross-source timing
within an audit second. Graph/anomaly/budget-delta inferences carry derived=true.
Late result recovery, reconciliation, HTTP hosting and M2+ nodes are excluded.
Operational details and bounds are documented in OBSERVATORY.md.

## M1-O.1 historical replay

R0 baseline is documented in M1O1_REPLAY_BASELINE.md (86 original tests passed).
No workflow/persistence/domain/simulation business behavior or schema was changed.
`ObservatoryReader.snapshot(..., for_replay=True)` supplies the same committed read
transaction, preserving all source-capped events and explicit input limits. This
opt-in path validates malformed trace fields and reports gaps; old commands retain
their default path and JSON meaning.

`replay.build_frames` preserves source ID order, merges sources deterministically,
marks cross-source same-second ties and never pairs spans without IDs.
`projector` is an immutable prefix reducer whose inputs contain no final business
rows. It projects native Run/Job states separately from node observations and
marks rule-based budgets/gate/frozen-call observations INFERRED. Final rows belong
only to the bundle's independent final_snapshot and comparison.

`build_replay_bundle` shares event/state/evidence data for JSON and HTML; byte
trimming rebuilds the surviving suffix from UNKNOWN. `replay_html.py` embeds safely
escaped JSON and a hashed constant local `replay_player.js`. JavaScript applies
Python's compact state deltas, not a second business reducer. The list is windowed
to 100 buttons. No timer can mutate business data, and a paused page has no playback
timer. CLI observe replay is entirely read-only apart from protected artifact writes.

Existing samples are re-exported without Mock execution via --replay-existing.
`scripts/check_replay_browser.py` uses stdlib CDP solely for local, temporary QA;
it validates real interactions, every source frame against Python, offline/XSS,
empty-history and synthetic 2000-frame capacity. This is not a product server.
See REPLAY_STUDIO.md for source precision, missing history and limits.

## M2-A Agent interoperability foundation

The legacy M1 agents.py/Controller/simulation/state/budget path remains unchanged.
New agent_interop contracts separate Role, Runtime and Backend. Generator and Critic
have distinct strict versioned results; execution errors never become research verdicts.
ContextBuilder selects approved immutable public inputs, with role/subject/version/scopes
included in the digest. Runtime reconstructs policy before dispatch, validates all
response binding fields and checks role schema/evidence references before persistence.

AgentLedger is a separate SQLite schema v1, refusing the M1 database. It stores only
task/attempt/result/audit foundation records, with transactional current-attempt fencing,
one validated result per task and idempotent settlement. No Seed/Repair/Dispute/Experiment
tables or M2-B loop were added. Allocated Agent attempts do not charge simulation budget.

Configuration selects registered factories only; adapters are fresh per Attempt and
live singleton reuse is blocked. Mock is trusted in-process code with no tool API,
not an OS sandbox. Codex/Claude compatibility adapters provide bounded preflight and
fail-closed execution gates: no verified host isolation means UNSUPPORTED. DSH has only
a registration slot, not a fabricated implementation. JSON cannot enable arbitrary
shell/plugin loading or override these known-real-backend gates.

observability/agent_summary.py exposes read-only safe identity/evidence summaries;
M1 Replay nodes are not changed. CLI adds independent agent run/show/probe/smoke commands.
No shared backend private session, raw output, secrets or hidden reasoning are persisted.
AGENT_INTEROP.md documents logical-vs-physical isolation, cancellation limits and the
actual optional-backend evidence; no real research process was launched in this batch.

## M1.5 manual WQ simulations and rolling multi-simulation batches

`wq/` adds opt-in CLI/config, a stdlib HTTPS boundary, single/multi simulation adapters
and a rolling batch runner. M1 mock commands retain their semantics. Each Alpha uses
its own original Controller Run/Job, admission, reservation, attempt and result.
No schema migration. Live phase is M1.5; complete settings participate in fingerprints.
Backend identity defaults to mock-v1 for legacy configs. Local gate labels distinguish
live evidence from mock metrics and formal platform submission approval.

The adapter coalesces pending submissions into arrays, retaining singleton object tails.
The runner bounds batch work slots and replenishes completed groups. HTTP session I/O
is serialized while accepted remote simulations overlap. Children are joined by AST
and echoed settings, never by order; completed parent and child results are cached.
Live responses omit testPeriod: only that absence is allowed with an explicit evidence
gap; differing values and other missing settings remain rejected. Full requested settings
still bind the fingerprint. Credential-file outer quotes are parsed without unescaping
passwords; environment values remain literal. Both fixes have regression tests.
Receipts retain parent URL and request identity, without credentials/cookies. UNKNOWN
keeps existing budget semantics and halts new group admission; peers finish normally.

Existing audit/Trace/read models/replay are reused, with no invented batch events or
automatic reconciliation. Actual authentication, schema and account-specific batch
quotas require verification. Ordinary authentication has since been verified (201),
and one actual remote Alpha completed. Its original local FAILED/UNKNOWN record remains
frozen: wq inspect only reads the receipt and current metrics, never rewrites history.
See M15_WQ_SIMULATION.md and M15_DELIVERY.md for exact acceptance limits.

## M2-B Generator–Critic research loop

`research_loop/` is a separate Controller-owned domain over the existing Agent Runtime.
It adds immutable main-signal versions, locked independent/regression reviews and final
admission, with bounded repairs, authorized calls, evidence queries and execution time.
No simulation adapter is passed to research agents and no automatic WQ calls occur.

The M2-A Runtime/Backend/AgentLedger contracts remain intact. ContextBuilder gains an
optional strictly validated workflow view for formal repair/regression data, and fixes
sensitive text recursion through dataclass tuples. Independent review of every new
version has no prior review or generator private history. Only after it is committed
PASS may a fresh Critic task see prior formal defects and derived changed-field names.
All prior failed versions must be regressed before approval, enforced again in SQLite.

AgentLedger user_version stays 1; loop_schema v1 tracks additive signal_studies/tasks/
versions/reviews/events/evidence_requests. No migration of M1/M1.5 tables. Transactions,
CAS, unique keys, immutable row triggers and current-result checks prevent replacement
of accepted versions/reviews by duplicate or stale executions. Backend routing is fixed
for the study; no failed-review fallback or automatic retry/resume is offered.

Evidence Gateway only matches exact IDs in an approved structured catalog. It binds
Critic identity/version and quota, never accesses arbitrary files, network, MCP or RAG,
and never turns available evidence into automatic admission. research_summary reuses
read-only consistent snapshots, sanitization, reference closure and safe JSON exports;
study show and observe study share that view. Existing Replay nodes are unchanged.

Mock is trusted in-process protocol validation, not physical isolation. Real backend
execution stays fail closed until host permissions are verified; no framework added.
See M2B_BASELINE_AUDIT.md, M2B_RESEARCH_LOOP.md and M2B_DELIVERY.md.
