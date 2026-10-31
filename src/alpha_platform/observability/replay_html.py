"""R4: Chinese offline player. Data is escaped JSON; rendering uses safe DOM APIs."""

import base64
import hashlib
from importlib.resources import files

from .replay import encode_replay

MAX_HTML_BYTES = 6 * 1024 * 1024


def render_replay(bundle):
    serialized = encode_replay(bundle)
    for char, replacement in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        serialized = serialized.replace(char, replacement)
    script = files(__package__).joinpath("replay_player.js").read_text(encoding="utf-8")
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    document = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';
script-src 'sha256-__HASH__'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Alpha 研究执行回放</title><style>
:root{font:15px/1.55 'Microsoft YaHei',system-ui,sans-serif;color:#e5edf8;background:#0d1420;
--line:#304258;--muted:#9aafc8;--cyan:#66d6cd;--danger:#ffb1aa;--gold:#f1cd83}
*{box-sizing:border-box}body{max-width:1500px;margin:auto;padding:24px 28px 130px}
header,.panel,.notice{border:1px solid var(--line);border-radius:12px;background:#142133;padding:18px;margin-bottom:16px}
header{border-top:3px solid var(--cyan)}h1{font-size:27px;margin:8px 0}h2{font-size:18px;margin:0 0 14px}
h3{font-size:15px}.eyebrow,.muted,small{color:var(--muted)}.eyebrow{font-size:12px;letter-spacing:1px}
.summary{display:flex;gap:22px;flex-wrap:wrap;margin-top:14px}.notice{background:#28251c;border-color:#6d5d36;color:#f1dbb0}
.notice:empty{display:none}.notice ul{margin:6px 0;padding-left:22px}.graph{display:flex;gap:7px;flex-wrap:wrap;align-items:center}
button,select{font:inherit;border:1px solid #48627d;border-radius:7px;background:#1b2d43;color:#e5edf8;padding:8px 12px;cursor:pointer}
button:hover{background:#29435e}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--cyan);outline-offset:3px}
button:disabled{opacity:.4;cursor:default}.node{min-width:118px;padding:10px;text-align:left;border-left:3px solid #61748a}
.node strong,.node span,.node small{display:block}.node span{font-size:12px}.node small{font-size:11px}
.node.COMPLETED{border-color:#48bda5}.node.FAILED,.node.REJECTED{border-color:var(--danger)}
.node.UNKNOWN,.node.WAITING{border-color:var(--gold)}.node.NOT_OBSERVED_YET{opacity:.6;border-style:dashed}
.node.current{outline:2px solid var(--cyan);background:#214052}.node.selected{box-shadow:inset 0 0 0 1px #b4d9ff}
.arrow{color:#7792af}.workspace{display:grid;grid-template-columns:minmax(280px,36%) 1fr;gap:16px;align-items:start}
.timeline{max-height:580px;overflow:auto;padding-right:3px}.event{display:block;width:100%;text-align:left;margin:6px 0;font-size:13px}
.event strong,.event small{display:block}.event.current{border-color:var(--cyan);background:#214052}.event.error{border-left:3px solid var(--danger)}
.filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}.filters select{max-width:190px;font-size:13px}
.badge{display:inline-block;border:1px solid #52677e;border-radius:5px;padding:1px 7px;font-size:12px;margin-right:6px}
.uncertain{color:var(--gold)}.readout{padding:12px;background:#0e1a2a;border-radius:8px;margin:12px 0}
.fields{display:grid;grid-template-columns:150px 1fr;gap:8px 14px}.fields dt{color:var(--muted)}.fields dd{margin:0;overflow-wrap:anywhere}
pre{font:12px/1.6 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#0d1928;padding:12px;border-radius:6px;max-height:340px;overflow:auto}
details{border-top:1px solid var(--line);padding:10px 0;margin-top:10px}summary{cursor:pointer;color:#b6d1ed}
.controls{position:fixed;bottom:0;left:0;right:0;background:#142133f5;border-top:1px solid #45617c;padding:14px max(20px,calc((100vw - 1444px)/2));backdrop-filter:blur(8px);z-index:10}
.control-row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.primary{background:#175c63;border-color:var(--cyan);min-width:86px}
.scrubber{display:flex;gap:14px;align-items:center;margin-top:10px}.scrubber input{flex:1;accent-color:var(--cyan)}#frame-counter{min-width:120px;font-size:13px}
#evidence button{margin:4px}.diff{color:#a4dccf}.error-text{color:var(--danger)}
@media(max-width:850px){body{padding:12px 12px 190px}.workspace{grid-template-columns:1fr}.timeline{max-height:300px}
.node{min-width:105px}.controls{padding:10px}.control-row{gap:5px}.control-row button{padding:7px 8px}.fields{grid-template-columns:110px 1fr}}
</style></head><body><header><div class="eyebrow">历史事件回放 · 本地只读 · 模拟研究样例不代表真实绩效</div>
<h1>Alpha 研究执行回放</h1><div id="run-id" class="muted"></div>
<div class="summary"><strong id="run-state">历史研究状态：信息不足</strong><span id="job-state">历史任务状态：尚未观察到</span>
<span id="budget-state">历史预算：信息不足</span></div>
<p class="muted">尚未观察到 ≠ 实际尚未发生。此处按证据逐帧投影，最终查询快照在下方独立对照。</p></header>
<div id="warnings" class="notice" role="status"></div>
<section class="panel"><h2>研究工作流</h2><div id="graph" class="graph"></div>
<p class="muted">箭头是预期拓扑，不代表已确认的跨源调用先后；高亮表示当前展示事件的节点。</p></section>
<div class="workspace"><section class="panel"><h2>事件时间线</h2><div class="filters">
<label><input id="errors-only" type="checkbox">仅看异常</label><label>节点 <select id="node-filter" aria-label="筛选时间线节点"><option value="">全部节点</option></select></label></div>
<div id="timeline-note" class="muted"></div><div id="timeline" class="timeline"></div></section>
<section class="panel"><h2 id="frame-title">初始视图</h2><div id="frame-meta"></div><p id="frame-description"></p>
<div id="node-inspector" class="readout"></div><div id="payload"></div>
<h3>本帧变化</h3><ul id="diff" class="diff"></ul>
<h3>截至当前游标的异常与证据缺口</h3><ul id="anomalies"></ul><ul id="gaps"></ul>
<h3>证据检查</h3><div id="evidence"></div><div id="evidence-preview"></div>
<details><summary>技术详情：事件、状态和来源引用</summary><pre id="technical"></pre></details></section></div>
<section class="panel"><details id="final-panel"><summary>最终查询快照与末帧对照（独立信息，不属于当前历史帧）</summary>
<div id="final-summary"></div><p class="muted">最终记录只用于核对，不会补齐历史节点或预算。</p>
<details><summary>最终查询快照技术详情</summary><pre id="final-technical"></pre></details></details></section>
<div class="controls"><div class="control-row">
<button id="home" aria-label="回到开头">回到开头</button><button id="prev" aria-label="上一步">上一步</button>
<button id="play" class="primary" aria-label="播放或暂停" aria-pressed="false">播放</button>
<button id="next" aria-label="下一步">下一步</button><button id="end" aria-label="跳到最后">最后</button>
<label>播放速度 <select id="speed" aria-label="播放速度"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
<button id="anomaly-prev" aria-label="上一个异常">上一个异常</button><button id="anomaly-next" aria-label="下一个异常">下一个异常</button>
<small>固定节奏演示，并非真实等待时间</small></div><div class="scrubber"><input id="cursor" type="range" min="0" value="0" aria-label="回放进度"><span id="frame-counter" aria-live="polite"></span></div></div>
<noscript><p class="notice">需要启用浏览器本地 JavaScript 才能播放；不会连接网络。机器可读回放请查看同源 JSON 文件。</p></noscript>
<script type="application/json" id="replay-data">__DATA__</script><script>__PLAYER__</script></body></html>"""
    # Replace data last so user content cannot become a template token or executable code.
    document = (
        document.replace("__HASH__", digest)
        .replace("__PLAYER__", script)
        .replace("__DATA__", serialized)
    )
    if len(document.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("replay HTML exceeds 6 MiB")
    return document
