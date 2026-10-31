# M1 targeted correctness review

Reviewed actual source and tests; baseline: 30 tests passed. Scope remains M1;
no live API, M1.5/M2 work, framework or dependency added. Locations below refer
to the reviewed/fixed source. P0 items were addressed before P1 items.

## Confirmed defects and fixes

| Priority / finding | Location | Evidence and fix | Regression |
|---|---|---|---|
| P0 BUG — concurrent run admission | `src/alpha_platform/workflow.py:22` | CREATED/config check previously preceded the write transaction. Two callers could pass it, overwrite a completed/active run as REJECTED, or admit two different candidates against one run. Check, generation event, validation and admission now share BEGIN IMMEDIATE; budget update also requires CREATED. | `tests/test_review.py:47`: two connections/threads, same run with same/different candidates and different runs with same candidate; one submission and one debit only. |
| P0 BUG — UNKNOWN dedup bypass | `src/alpha_platform/validation.py:60`, `src/alpha_platform/workflow.py:33` | Fingerprint included fault scenario, so timeout → success could queue an identical simulation while the first remained UNKNOWN. v2 excludes fault scenario; lookup also includes historical v1 hashes for all six original scenarios. No old records rewritten. | `tests/test_review.py:109`: current and historical timeout identity both block a success-scenario duplicate; original reservation, job and attempt remain intact. |
| P1 BUG — terminal run/job writes | `src/alpha_platform/persistence.py:104`, `:124`, `:144` | save_result could complete a POLLING job after its run had FAILED; finish could complete a run whose job was still POLLING. Job transitions now require an active run and transaction; run completion requires exactly one completed job. Late writes roll back, including their result row. | `tests/test_review.py:132`, `:150`: actual persistent-write failure followed by late result; premature completion from a live polling adapter. Existing completion idempotence test still passes. |
| P1 BUG — unchecked adapter return data | `src/alpha_platform/simulation.py:14`, `:23`, `src/alpha_platform/workflow.py:83`, `:145` | None/empty receipt crashed outside submit handling; malformed/missing Sharpe could raise outside error handling; NaN/Infinity/bool could produce invalid gates or persisted nonstandard JSON. Validate receipt and finite JSON result before settlement/gating. Invalid receipt → UNKNOWN + reservation; invalid result → UNKNOWN + retained receipt/debit. | `tests/test_review.py:162`, `:189`, `:209`: malformed receipt/result, missing/string/bool/non-finite metrics, non-JSON raw data, invalid poll status. |
| P1 BUG — silent validator rewriting | `src/alpha_platform/validation.py:15` | Python AST silently discarded comments, normalized Unicode lookalike identifiers, and accepted overflowing float literals. These could pass a whitelist despite undeclared syntax or reach submission as non-finite numbers. Explicit rejection precedes canonicalization; supported AST roundtrip is tested. | `tests/test_review.py:227`, `:233`: comments, lookalikes, overflow and unsupported FastExpr constructs; supported expression retains its AST and stable fingerprint. |
| P1 BUG — mock remote lifecycle | `src/alpha_platform/simulation.py:63`, `:99` | Global poll count made a second delayed task complete early. Unknown IDs and unfinished/failed tasks could return successful results. Poll progress is now per remote ID; unknown/uncompleted result lookups are rejected. | `tests/test_review.py:246`: interleaved delayed tasks, result fetch before completion, unknown-ID poll/fetch. |

## Passing checks

| Finding | Location | Result |
|---|---|---|
| PASS — job state graph | `src/alpha_platform/persistence.py:13`, `:124` | M1 path is PENDING → RESERVED → SUBMITTING → SUBMITTED → POLLING → COMPLETED; only SUBMITTING/POLLING can enter UNKNOWN, and only SUBMITTING/POLLING can fail. Terminal job states have no outgoing edges. Active run required. |
| PASS — research graph | `src/alpha_platform/persistence.py:104`, `src/alpha_platform/workflow.py:22` | CREATED → RUNNING via admitted job; CREATED → REJECTED/FAILED; RUNNING → COMPLETED/FAILED. A metric gate FAIL still means completed execution. No terminal run reopen. |
| PASS — budget accounting | `src/alpha_platform/workflow.py:43`, `:109`, `:123` | One reservation at admission; accepted receipt converts it to one debit; definitive rate-limit exhaustion releases it; ambiguous submit holds it; accepted remote failure/result loss does not refund it. Constraints forbid negative/excess totals. Local result retries do not resubmit/debit. Concurrent and transaction-failure tests pass. |
| PASS — UNKNOWN traceability | `src/alpha_platform/persistence.py:162`, `src/alpha_platform/workflow.py:92` | Original run/config/job/expression, submit intent, attempt outcome and original alpha linkage remain queryable. No automatic UNKNOWN retry or release. |
| PASS — simulation isolation | `src/alpha_platform/simulation.py:50`, `src/alpha_platform/workflow.py:81` | Internal async contract documents three poll states and exceptions; remote calls remain outside SQLite transactions. Existing success/rate-limit/delay/timeout/remote-failure modes plus three malformed-return modes are tested. |

## Remaining risks (no new recovery features implemented)

- RISK — `workflow.py:165`, `persistence.py:124`: FAILED run + POLLING job after
  persistent local write failure is a frozen, auditable manual-review condition.
  Consumed budget stays consumed. Late writes are now refused. There is no supported
  reopen/replay command; adding recovery policy belongs outside this review.
- RISK — `workflow.py:92`, `persistence.py:162`: UNKNOWN can be inspected but M1 has
  no manual reconciliation/settlement/release CLI. For a submit timeout without a
  receipt, no remote ID exists; automatic lookup or resubmission is not claimed.
  Inspect the original run and duplicate alpha link; do not clear its fingerprint
  or reserved budget just to retry.
- RISK — `workflow.py:109`, `:167`: receipt persistence and final run completion
  can still be interrupted by process death/database outage. Durable intents prevent
  blind retry but do not provide automatic restart recovery or remote reconciliation.
- RISK — `validation.py:15`, `:60`: subset grammar only; no full FastExpr semantics,
  operator argument type inference or economic equivalence. Numeric literal semantics
  follow Python AST; metadata/settings changes intentionally change identity. No real
  platform metadata or interface was assumed or accessed.

## Final validation

- `uv run --offline pytest -q`: **61 passed** (30 baseline + 31 regression cases).
- `uv run --offline ruff check .`: **All checks passed**.
- `uv run --offline ruff format --check .`: **14 files already formatted**.
- pytest required the existing elevation for project-local temporary-directory access;
  all test databases remained inside the project. No download or external API call.
