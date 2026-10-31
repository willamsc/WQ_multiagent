# M1-O Research Observatory

Observatory reads M1 SQLite facts and audit evidence. It does not run simulations,
change Run/Job status, settle budgets, retry UNKNOWN or call an Agent. No service
starts automatically. The human viewer is a single offline HTML file.

## Usage (from project root)

```powershell
uv run --offline alpha-platform research run --mode mock --config configs/sample-gbr.json --trace light
uv run --offline alpha-platform observe show --run-id RUN_ID
uv run --offline alpha-platform observe timeline --run-id RUN_ID --node submit --format json
uv run --offline alpha-platform observe graph --run-id RUN_ID
uv run --offline alpha-platform observe inspect --run-id RUN_ID --source-ref audit:42
uv run --offline alpha-platform observe export --run-id RUN_ID --output diagnostic.json
uv run --offline alpha-platform observe report --run-id RUN_ID --output run.html
```

Use `--db PATH` **before** the subcommand to select a database. `research show` is
the original detailed M1 report; `observe` is the bounded public read model.
`--job-id`, `--candidate-id`, `--node`, `--errors` filter the timeline without
recomputing graph/business facts from an incomplete path. JSON exports include
schema_version=1, graph, event sources, anomalies and a resolvable evidence map.
`inspect` follows a source_ref in the same bounded snapshot. Export can be read
directly by Codex/another diagnostic agent; it never invokes one.

Open `run.html` directly in a browser. Node/evidence links open the inspector;
timeline filters work locally, without CDN/network. With JavaScript disabled,
the entire report and manual `<details>` inspector remain readable.

## Modes and isolation

| Mode | Core M1 audit | Optional diagnostic data |
|---|---|---|
| off (default) | Always on, unchanged transactions | No sink/table creation or trace writes |
| light | Always on | Node start/completion/failure, duration, reason codes, poll status, IDs |
| full | Always on | Light fields plus allowlisted expression excerpts, bounded and redacted |

Use light for routine debugging. Full does not capture arbitrary raw adapter
responses, credentials, exception text/stacks, hidden model reasoning or global
environment snapshots. Only safe exception class names are included.

The Controller accepts an optional injected sink without importing the observatory.
CLI instruments generator composition and flushes diagnostics after business work.
Trace emit/flush failure produces at most a bounded warning and does not roll back
business facts. Mandatory audit failures still abort their business transaction.
Trace records are diagnostics of invocations, not copies of domain transition events;
business counts/status never derive from optional trace counts.

## Compatibility and limits

- Core schema stays v1. An additive `observability_traces` extension is initialized
  lazily on first trace flush; each row has schema_version=1. Old M1 databases are
  readable with no schema migration. No duplicate audit/snapshot/artifact tables.
- Reader uses `mode=ro`, `query_only`, a read transaction and a 2-second busy timeout.
  It does not initialize/migrate the database. Missing run returns RUN_NOT_FOUND.
- Business facts outrank audit, which outranks optional trace. Graph nodes/edges,
  anomaly rules and historical budget deltas are explicitly derived. Missing evidence
  is labelled; missing trace never implies business failure. Original audit has
  second-resolution timestamps; UTC ties use source and monotonic row ID. Relative
  timing across audit and trace within the same second is approximate.
- FAILED Run + POLLING Job displays both statuses, consumed budget and manual-review
  anomaly. UNKNOWN without receipt shows remote execution unknown, not a fabricated
  remote ID. No recovery/refund action is supplied.
- Pre-admission events can have null candidate/job IDs. Rejected candidates have
  no stored alpha ID. Duplicate evidence preserves the original alpha ID.
- No exact node timings/poll responses can be recovered for old runs with trace off.
  The graph is a business topology, not a Python runtime call graph/profiler.

## Size, privacy and retention

- Default last 200 events; `--max-events` accepts 1–2000. Reader caps audit rows and
  attempts at 2000, traces at 1000, jobs at 100. Limits are intended for single-run M1.
- JSON evidence bundle defaults to 262144 bytes; `--max-bytes` accepts 8192–4194304.
  Oversized bundles trim timeline/reference sets and declare EXPORT_TRUNCATED. If a
  valid evidence bundle still cannot fit, the command fails rather than writing an
  invalid/truncated JSON file. The HTML layout adds bounded markup to that bundle.
- Public text summaries default to 320 characters, collections/depth are bounded.
  Trace strings are capped at 160 (light) / 2048 (full), each payload at 4096 bytes.
  A per-run sink buffers at most 256 events. Truncation and buffer loss are marked.
- Trace flush retains only the most recent 10000 trace rows and at most 7 days of
  traces. This policy deletes **only optional traces**, never business/audit rows.
  Without another flush, expired rows are not automatically deleted in the background.
- Keys/credential-like text and values of credential-named environment variables
  are redacted. Raw adapter responses/auth headers are not exported. HTML escapes
  all data; the only script is a constant local filter/inspector implementation.
- Redaction is conservative and may hide ordinary text containing credential words.
  Arbitrary unlabelled secrets cannot be identified perfectly; keep credentials out
  of expressions and other research content. Original M1 storage is not rewritten.

## Samples

`examples/observatory/` contains six offline JSON/HTML pairs: success,
validation-rejected, duplicate, budget-exhausted, unknown and persistence-failure.
`manifest.json` maps names to real mock run IDs. They are examples, not real research.

To recreate the examples in a new directory:

```powershell
uv run --offline python scripts/generate_observatory_samples.py --output examples/observatory-new
```

The generator refuses to overwrite an existing sample database. Browser profiles,
cache, databases and temporary files are kept under the project. No embedding/model
download, HTTP server, real WorldQuant API, M1.5 or M2–M4 feature is needed.
