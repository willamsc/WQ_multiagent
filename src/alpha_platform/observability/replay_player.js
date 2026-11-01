/* Offline presentation only: Python has already produced all business state deltas. */
'use strict';
(() => {
  const data = JSON.parse(document.getElementById('replay-data').textContent);
  const $ = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const labels = Object.fromEntries(data.topology.nodes.map(n => [n.id, n.label_zh]));
  const states = {NOT_OBSERVED_YET:'尚未观察到',ACTIVE:'进行中',COMPLETED:'已完成',REJECTED:'已拒绝',
    FAILED:'失败',WAITING:'等待中',UNKNOWN:'信息不足／状态未知',CREATED:'已创建',RUNNING:'进行中',
    PENDING:'待执行',RESERVED:'已预留',SUBMITTING:'提交中',SUBMITTED:'已确认回执',POLLING:'查询中'};
  const basis = {RECORDED:'已记录',INFERRED:'推导',UNKNOWN:'信息不足'};
  const warningText = {
    EVENT_PREFIX_NOT_COMMIT_SNAPSHOT:'帧是历史事件前缀投影，不是每次事务提交的数据库截图。',
    NO_SPAN_ID_NO_PAIRING:'历史诊断追踪没有调用配对标识；没有猜测开始／结束配对或补造耗时。',
    CROSS_SOURCE_SAME_SECOND_UNCERTAIN:'审计只有秒级精度：同秒跨源事件只有稳定展示次序，真实先后不确定。',
    NO_OPTIONAL_TRACE:'没有可用的诊断追踪：可能未开启、已过期或进程结束前缓冲丢失，无法复原详细调用。',
    EXPORT_TRUNCATED:'部分事件未导出：当前回放不完整。被省略的前缀不会被填成成功。',
    READ_LIMIT_REACHED:'历史读取达到上限，可能缺少更早的证据。',
    SOURCE_TIME_REGRESSION:'来源内部时间戳发生回退，仍按该来源的记录序号展示。',
    NO_REPLAY_EVENTS:'没有足够的历史事件，无法逐步回放。可查看独立最终查询快照。',
    MISSING_EVIDENCE:'证据缺失：该事件不能用于确认历史状态。',
    PAYLOAD_TRUNCATED:'原始摘要已截断，未记录的部分无法回放。',
    MALFORMED_TRACE_SKIPPED:'已跳过格式异常的诊断追踪，未将其当作成功。',
    MALFORMED_AUDIT_PAYLOAD:'审计内容格式异常，相关解释或输入输出可能缺失。',
    INVALID_EVENT_SKIPPED:'已跳过不能识别的事件。',INVALID_EVENT_PAYLOAD:'事件摘要格式异常。',
    DUPLICATE_SOURCE_IGNORED:'重复来源只保留一次，没有重复计数。',
    UNKNOWN_JOB_PHASE:'缺少更早的任务阶段证据，不能判断异常发生在提交还是查询阶段。',
    SYNTHETIC_QA_FIXTURE:'这是人工构造的交互／容量测试夹具，不代表真实研究历史。'};
  const reasons = {VALIDATION:'本地表达式校验未通过',DUPLICATE:'已有相同表达式和设置的记录',
    BUDGET_EXHAUSTED:'预算不足，未提交模拟',REMOTE_UNKNOWN:'远程执行结果未知，不能确认是否被接受',
    REMOTE_FAILED:'模拟执行返回失败',RATE_LIMIT:'限流尝试次数耗尽',PERSISTENCE_FAILURE:'结果写入失败，需人工检查',
    INTERNAL:'内部处理未完成',NODE_EXCEPTION:'诊断记录了节点调用异常',
    MOCK_GATE_PASS:'达到本地模拟门槛',MOCK_GATE_FAIL:'未达到本地模拟门槛',
    LOCAL_RESEARCH_GATE_PASS:'达到本地研究门槛，不代表平台提交获批',LOCAL_RESEARCH_GATE_FAIL:'未达到本地研究门槛',
    TRACE_NODE_FAILURE:'诊断调用曾发生异常，不代表研究最终失败',RATE_LIMIT_RECORDED:'已记录一次限流',
    RATE_LIMIT_EXHAUSTED:'限流尝试次数耗尽',REMOTE_EXECUTION_UNKNOWN:'远程执行未知，不会自动重发',
    RUN_JOB_STATE_MISMATCH_MANUAL_REVIEW:'研究失败，但任务仍处于查询状态；仅供人工核查，不自动恢复'};
  const textState = value => states[value] || '信息不足';
  const warn = code => warningText[code.split(':')[0]] || '未知提示代码：' + code;
  const reason = code => reasons[code] || '未知错误代码：' + code;
  const publicText = value => String(value).replaceAll('[REDACTED]', '[已脱敏]').replaceAll('[truncated]', '[已截断]');
  const empty = element => element.replaceChildren();
  function element(tag, text, className) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = publicText(text);
    if (className) e.className = className;
    return e;
  }
  function list(target, items, fallback) {
    empty($(target));
    (items.length ? items : [fallback]).forEach(text => $(target).append(element('li', text)));
  }
  let cursor = 0, timer = null, playing = false, selected = null;
  const count = data.frames.length;
  function stateAt(position) {
    const state = clone(data.initial_state);
    for (let i=0; i<position; i++) {
      for (const [key,value] of Object.entries(data.frame_states[i].delta)) {
        if (key === 'node_states') Object.assign(state.node_states, clone(value));
        else state[key] = clone(value);
      }
    }
    return state;
  }
  const isError = f => ['FAILED','UNKNOWN','REJECTED','DUPLICATE','BUDGET_EXHAUSTED','RATE_LIMIT','NODE_FAILED'].includes(f.event_kind);
  const abnormal = data.frames.filter(isError).map(f => f.ordinal);
  function pause() {playing=false; if(timer!==null)clearTimeout(timer); timer=null; updateControls();}
  function updateControls() {
    $('play').textContent = playing ? '暂停' : cursor===count && count ? '重播' : '播放';
    $('play').setAttribute('aria-pressed', String(playing));
    $('play').disabled = count===0;
    $('home').disabled = $('prev').disabled = cursor===0;
    $('next').disabled = $('end').disabled = cursor===count;
    $('anomaly-prev').disabled = !abnormal.some(n=>n<cursor);
    $('anomaly-next').disabled = !abnormal.some(n=>n>cursor);
    $('cursor').value=String(cursor);
    $('frame-counter').textContent='帧 '+cursor+' / '+count;
  }
  function schedule() {
    if(timer!==null)clearTimeout(timer);
    if(!playing)return;
    timer=setTimeout(()=>{
      timer=null;
      if(!playing)return;
      if(cursor<count){cursor++;selected=null;render();}
      if(cursor===count)pause();else schedule();
    },800/Number($('speed').value));
  }
  function seek(position) {pause();cursor=Math.min(count,Math.max(0,Number(position)));selected=null;render();}
  function toggle() {
    if(playing){pause();return;}
    if(!count)return;
    if(cursor===count){cursor=0;selected=null;render();}
    playing=true;updateControls();schedule();
  }
  function evidencePreview(ref) {
    empty($('evidence-preview'));
    const record=data.evidence[ref];
    $('evidence-preview').append(element('p',record && !record.missing_reason ? '来源摘要（已脱敏）' : '证据缺失，无法确认原始内容'));
    const details=element('details');details.open=true;
    details.append(element('summary','证据技术详情'),element('pre',JSON.stringify({source_ref:ref,...record},null,2)));
    $('evidence-preview').append(details);
  }
  function renderPayload(frame) {
    empty($('payload'));
    if(!frame){$('payload').append(element('p','尚未选择历史事件；输入输出未采集时无法回放。','muted'));return;}
    const p=frame.payload_summary, fields=element('dl',undefined,'fields');
    const add=(label,value)=>{fields.append(element('dt',label),element('dd',value));};
    if(p.expression!==undefined)add('表达式摘要',p.expression===null?'未采集，无法回放':p.expression);
    if(p.field_ids)add('涉及字段',Array.isArray(p.field_ids)&&p.field_ids.every(v=>typeof v==='string')?p.field_ids.join('、'):'字段摘要格式异常，无法复原');
    if(p.reason_code)add('原因说明',reason(p.reason_code));
    if(p.duration_ms!==undefined)add('记录的单条调用耗时',p.duration_ms+' 毫秒（未猜测配对）');
    if(p.status)add('记录的返回状态',textState(p.status));
    if(p.gate)add('记录的门控结论',p.gate==='PASS'?'达到示例门槛':'未达到示例门槛');
    if(p.attempt_number!==undefined)add('本次提交尝试序号',p.attempt_number);
    if(p.budget_delta)add('按规则推导的预算变化','预留 '+p.budget_delta.reserved+'；已消耗 '+p.budget_delta.used);
    if(!fields.children.length)add('输入输出','未采集，无法回放；可展开技术详情核对已有字段。');
    $('payload').append(fields);
  }
  function renderTimeline() {
    empty($('timeline'));
    const visible=data.frames.filter(f=>(!$('errors-only').checked || isError(f)) &&
      (!$('node-filter').value || f.node_id===$('node-filter').value));
    const found=visible.findIndex(f=>f.ordinal>=cursor), start=Math.max(0,(found<0?visible.length:found)-35);
    const windowed=visible.slice(start,start+100);
    $('timeline-note').textContent=visible.length ? '列表筛选不改变投影；当前显示 '+windowed.length+' / '+visible.length+' 条。' : '没有符合条件的历史事件。';
    for(const f of windowed){
      const b=element('button',undefined,'event'+(f.ordinal===cursor?' current':'')+(isError(f)?' error':''));
      b.dataset.ordinal=String(f.ordinal);b.setAttribute('aria-label','跳到第 '+f.ordinal+' 帧：'+f.label_zh);
      b.append(element('strong',f.ordinal+' · '+f.label_zh),element('small',labels[f.node_id]+' · '+(f.occurred_at||'时间未记录')+'（UTC）'));
      if(f.order_quality==='TIE_UNCERTAIN')b.append(element('small','同秒跨源顺序不确定','uncertain'));
      b.addEventListener('click',()=>seek(f.ordinal));$('timeline').append(b);
    }
  }
  function render() {
    const state=stateAt(cursor), frame=cursor?data.frames[cursor-1]:null;
    const nodeId=selected || state.active_node;
    $('run-state').textContent='历史研究状态：'+textState(state.run.state)+'（'+basis[state.run.basis]+'）';
    const jobs=Object.values(state.jobs);
    $('job-state').textContent='历史任务状态：'+(jobs.length?jobs.map(j=>textState(j.state)).join('、'):'尚未观察到');
    $('budget-state').textContent='历史预算：预留 '+(state.budget.reserved??'未知')+' / 已消耗 '+(state.budget.used??'未知')+'（'+basis[state.budget.basis]+'；总额未知）';
    empty($('graph'));
    data.topology.nodes.forEach((n,index)=>{
      if(index)$('graph').append(element('span','→','arrow'));
      const s=state.node_states[n.id], b=element('button',undefined,'node '+s.state+(n.id===state.active_node?' current':'')+(n.id===nodeId?' selected':''));
      b.dataset.node=n.id;b.setAttribute('aria-label','检查节点：'+n.label_zh);
      if(n.id===state.active_node)b.setAttribute('aria-current','step');
      b.append(element('strong',n.label_zh),element('span',textState(s.state)),element('small',basis[s.basis]));
      b.addEventListener('click',()=>{selected=n.id;render();});$('graph').append(b);
    });
    $('frame-title').textContent=frame?'第 '+cursor+' 帧 · '+frame.label_zh:'初始视图';
    empty($('frame-meta'));
    if(frame){
      $('frame-meta').append(element('span',basis[frame.evidence_level],'badge'),
        element('span',frame.time_precision==='second'?'秒级时间':frame.time_precision==='millisecond'?'毫秒级时间':'时间未记录','badge'),
        element('span',frame.order_quality==='TIE_UNCERTAIN'?'跨源顺序不确定':'仅确认来源内部顺序','badge uncertain'));
    }
    $('frame-description').textContent=frame ? (frame.occurred_at||'未记录时间')+'（UTC）。这是观测事件，不是重新执行。' :
      count?'尚未应用任何历史事件。所有节点均为“尚未观察到”，不代表实际尚未发生。':'没有足够的历史事件，无法逐步回放。';
    empty($('node-inspector'));
    const node=nodeId?state.node_states[nodeId]:null;
    $('node-inspector').append(element('strong',nodeId?'检查节点：'+labels[nodeId]:'请选择节点或历史事件'));
    if(node)$('node-inspector').append(element('p',textState(node.state)+' · '+basis[node.basis]),
      element('p',node.inference_reason || (node.refs.length?'最近变更在第 '+node.changed_at+' 帧。':'未采集，无法回放。'),'muted'));
    renderPayload(frame);
    const delta=cursor?data.frame_states[cursor-1].delta:{};
    const changes=Object.entries(delta.node_states||{}).map(([id,value])=>labels[id]+' → '+textState(value.state)+'（'+basis[value.basis]+'）');
    if(delta.run)changes.push('研究状态 → '+textState(delta.run.state));
    if(delta.jobs)changes.push('任务状态证据已更新');
    if(delta.budget)changes.push('预算推导已更新；不是历史预算原始快照');
    list('diff',changes,'本帧没有可确认的业务状态变化。');
    list('anomalies',state.anomalies_at_frame.map(a=>'第 '+a.first_seen_ordinal+' 帧：'+reason(a.code)),'截至当前游标未观察到异常。');
    list('gaps',[...state.data_gaps,...(frame?frame.warnings.map(warn):[])],'当前帧没有额外证据缺口提示。');
    empty($('evidence'));empty($('evidence-preview'));
    const refs=[...new Set([...(frame?frame.evidence_refs:[]),...(node?node.refs:[])])];
    refs.forEach((ref,i)=>{const b=element('button','查看证据 '+(i+1));b.dataset.ref=ref;
      b.addEventListener('click',()=>evidencePreview(ref));$('evidence').append(b);});
    if(!refs.length)$('evidence').append(element('p','尚未观察到可引用的历史证据。','muted'));
    $('technical').textContent=JSON.stringify({frame,node_id:nodeId,node_state:node,projected_state:state},null,2);
    renderTimeline();updateControls();
  }
  $('run-id').textContent='研究标识：'+data.run_id+'　／　回放仅涵盖已导出的证据';
  list('warnings',data.warnings.map(warn),'没有额外导出警告。');
  for(const n of data.topology.nodes){const o=element('option',n.label_zh);o.value=n.id;$('node-filter').append(o);}
  $('cursor').max=String(count);
  $('home').addEventListener('click',()=>seek(0));$('prev').addEventListener('click',()=>seek(cursor-1));
  $('next').addEventListener('click',()=>seek(cursor+1));$('end').addEventListener('click',()=>seek(count));
  $('play').addEventListener('click',toggle);$('cursor').addEventListener('input',e=>seek(e.target.value));
  $('speed').addEventListener('change',()=>{if(playing)schedule();});
  $('anomaly-prev').addEventListener('click',()=>{const n=abnormal.filter(n=>n<cursor).at(-1);if(n!==undefined)seek(n);});
  $('anomaly-next').addEventListener('click',()=>{const n=abnormal.find(n=>n>cursor);if(n!==undefined)seek(n);});
  $('errors-only').addEventListener('change',renderTimeline);$('node-filter').addEventListener('change',renderTimeline);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});window.addEventListener('pagehide',pause);
  document.addEventListener('keydown',e=>{if(['INPUT','SELECT','BUTTON'].includes(e.target.tagName))return;
    if(e.code==='Space'){e.preventDefault();toggle();}else if(e.key==='ArrowRight')seek(cursor+1);
    else if(e.key==='ArrowLeft')seek(cursor-1);else if(e.key==='Home')seek(0);else if(e.key==='End')seek(count);});
  const final=data.final_snapshot;
  $('final-summary').append(element('p','最终研究状态：'+textState(final.run.status)+'；最终任务状态：'+
    (final.jobs.map(j=>textState(j.status)).join('、')||'无任务')),
    element('p','最终预算总额 '+final.run.budget.total+'，预留 '+final.run.budget.reserved+'，已消耗 '+final.run.budget.used+'。'));
  $('final-technical').textContent=JSON.stringify({final_snapshot:final,final_comparison:data.final_comparison,limits:data.limits},null,2);
  window.replayStudio=Object.freeze({seek,stateAt,getState:()=>stateAt(cursor),getCursor:()=>cursor,
    isPlaying:()=>playing,getFrameCount:()=>count,getBundle:()=>clone(data),pause});
  render();
})();
