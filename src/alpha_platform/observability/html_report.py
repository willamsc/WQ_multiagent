"""Standalone offline HTML. Untrusted data is escaped text, never executable script."""

import html
import json


def render(report):
    def esc(value):
        return html.escape(str(value), quote=True)

    def pretty(value):
        return esc(json.dumps(value, ensure_ascii=False, indent=2))

    nodes = '<span class="arrow" aria-hidden="true">→</span>'.join(
        f'<a class="node {esc(n["status"])}" href="#graph-{esc(n["id"])}">'
        f"<strong>{esc(n['id'])}</strong><span>{esc(n['status'])}</span></a>"
        for n in report["nodes"]
    )
    graph_details = "".join(
        f'<details id="graph-{esc(n["id"])}"><summary>{esc(n["id"])} · derived</summary>'
        f"<pre>{pretty(n)}</pre></details>"
        for n in report["nodes"]
    )
    evidence = "".join(
        f'<details id="{esc(ref)}"><summary>{esc(ref)} · {esc(item["source_kind"])}</summary>'
        f"<pre>{pretty(item['summary'])}</pre></details>"
        for ref, item in report["evidence"].items()
    )
    rows = ""
    for event in report["events"]:
        ref = event["source_ref"]
        rows += (
            f'<tr data-node="{esc(event["node"])}" data-job="{esc(event["job_id"] or "")}" '
            f'data-outcome="{esc(event["outcome"])}"><td>{esc(event["occurred_at"])}</td>'
            f"<td>{esc(event['node'])}</td><td>{esc(event['event_type'])}"
            f"<br><small>{esc(event['source_kind'])}</small></td>"
            f"<td>{esc(event['outcome'])}</td>"
            f'<td><a href="#{esc(ref)}">{esc(ref)}</a></td></tr>'
        )
    anomalies = (
        "".join(
            f"<li><strong>{esc(a['code'])}</strong> · derived observation · "
            + " ".join(f'<a href="#{esc(ref)}">{esc(ref)}</a>' for ref in a["evidence_refs"])
            + "</li>"
            for a in report["anomalies"]
        )
        or "<li>No detected anomalies.</li>"
    )
    node_options = "".join(f"<option>{esc(n['id'])}</option>" for n in report["nodes"])
    job_options = "".join(f"<option>{esc(j['job_id'])}</option>" for j in report["jobs"])
    # Script is a constant. No report value is ever interpolated into JavaScript.
    script = """document.querySelectorAll('select,input').forEach(x=>x.addEventListener('change',()=>{
const node=document.getElementById('node').value,job=document.getElementById('job').value;
const errors=document.getElementById('errors').checked;
document.querySelectorAll('tbody tr').forEach(r=>{r.hidden=(node&&r.dataset.node!==node)||
(job&&r.dataset.job!==job)||(errors&&!['FAILED','REJECTED','UNKNOWN'].includes(r.dataset.outcome));});
}));
document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',()=>{
let e=document.getElementById(decodeURIComponent(a.hash.slice(1)));
while(e){if(e.tagName==='DETAILS')e.open=true;e=e.parentElement;}
}));"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';
script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Research Observatory · {esc(report["run_id"])}</title><style>
:root{{font:15px/1.55 system-ui,sans-serif;color:#dce7f5;background:#101722}}
body{{max-width:1250px;margin:auto;padding:28px}}h1{{font-size:26px}}h2{{font-size:18px}}
small,.muted{{color:#9aafc7}}header,section{{border:1px solid #33455b;padding:20px;
border-radius:12px;margin-bottom:18px;background:#172232}}.overview{{display:flex;gap:22px;flex-wrap:wrap}}
.graph{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}.node{{border:1px solid #54708c;padding:12px;
border-radius:8px;display:flex;flex-direction:column;text-decoration:none;color:inherit}}
.COMPLETED{{border-color:#40bfa1}}.FAILED,.REJECTED{{border-color:#f18d8d}}
.UNKNOWN,.RUNNING{{border-color:#e6b65f}}.NOT_VISITED,.INFERRED{{opacity:.6;border-style:dashed}}
a{{color:#88c5ff}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:10px;text-align:left;
border-bottom:1px solid #33455b;overflow-wrap:anywhere}}.scroll{{overflow-x:auto}}
details{{padding:8px;border-bottom:1px solid #33455b}}summary{{cursor:pointer;overflow-wrap:anywhere}}
select{{max-width:250px;margin:5px;padding:6px}}@media(max-width:650px){{body{{padding:10px}}
section,header{{padding:12px}}table{{min-width:650px}}}}
</style></head><body><header><div class="muted">M1-O · OFFLINE · READ ONLY</div>
<h1>Research Observatory</h1><div>Run {esc(report["run_id"])}</div>
<p class="overview"><strong>STATUS {esc(report["run"]["status"])}</strong>
<span>PHASE {esc(report["run"]["phase"])}</span><span>BUDGET total / reserved / used:
{esc(report["run"]["budget"]["total"])} / {esc(report["run"]["budget"]["reserved"])} /
{esc(report["run"]["budget"]["used"])}</span></p>
<details><summary>Run / candidate / job facts</summary><pre>{pretty(report["run"])}</pre>
<pre>{pretty(report["jobs"])}</pre></details></header>
<section><h2>Execution graph</h2><p class="muted">Expected M1 topology; node states and edges are
derived from evidence. Dashed nodes were not observed or are inferred. No recovery controls.</p>
<div class="graph">{nodes}</div><details><summary>Graph evidence and observed edges</summary>
{graph_details}<pre>{pretty(report["edges"])}</pre></details></section>
<section><h2>Anomalies / evidence coverage</h2><ul>{anomalies}</ul>
<pre>{pretty(report["evidence_gaps"])}</pre><p>{esc(report["summary"])}</p></section>
<section><h2>Timeline</h2><label>Node <select id="node"><option value="">All</option>{node_options}</select></label>
<label>Job <select id="job"><option value="">All</option>{job_options}</select></label>
<label><input type="checkbox" id="errors"> Errors / rejected / unknown</label>
<div class="scroll"><table><thead><tr><th>UTC</th><th>Node</th><th>Action / source</th>
<th>Outcome</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>Artifact inspector</h2><p class="muted">Follow an evidence link. Summaries are allowlisted,
redacted and bounded; full raw responses are omitted.</p>{evidence}</section>
<footer><pre>{pretty(report["limitations"])}</pre><small>Snapshot {esc(report["snapshot_generated_at"])}</small></footer>
<script>{script}</script></body></html>"""
