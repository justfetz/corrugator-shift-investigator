'use strict';
// The /evals page (issue #26): the golden set from /evals.json, the score and latency trend from the
// shipped history, and a Run that executes a case on this server. The only requests this script makes
// are GET /evals.json, GET /api/config (for the POST token) and POST /api/evals/run, which runs the
// offline planner or replays a recorded fixture. Nothing here can reach a model.
const $ = id => document.getElementById(id);
let board=null, token=null, typeFilter='all', runningAll=false;
const views={};      // case id -> {details, body, built, boxes}
const chips={};      // run id -> the outcome chip in the case's summary line
const TYPE_HELP={routing:'the planner chose the right tools with the right arguments',faithfulness:'every number in the answer came out of a tool row',grounding:'every finding cites evidence that exists; nothing was dropped',refusal:'out-of-scope, out-of-date and out-of-tool requests are refused',value:'a tool’s numbers equal a second, plain computation over the records',investigation:'a multi-turn question finds the seeded story and asserts no cause the evidence lacks',data_quality:'a corrupt record is flagged, excluded and reported, never averaged in'};
function svgNode(tag, attrs, text) { const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e; }
const ms=v=>`${Math.round(v)} ms`;
const runLabel=run=>run.mode==='offline'?'offline':run.provider?`${run.provider} replay`:'live';
function setChip(id,result,when){
 const chip=chips[id]; if(!chip)return;
 const label=chip.dataset.label;
 if(!result){chip.className='outcome none';chip.textContent=`${label} · not in the last tracked run`;return;}
 chip.className='outcome '+result.outcome;
 chip.textContent=`${label} · ${result.outcome}`+(result.outcome==='skip'?'':` · ${ms(result.ms)}`);
 chip.title=`${when}: ${result.outcome}${result.note?' — '+result.note:''}`;
}

// Every POST carries the workbench token; a 403 refreshes it once (the instance restarted) and resends.
async function post(body){
 const send=()=>fetch('/api/evals/run',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token||''},body:JSON.stringify(body)});
 let response=await send();
 if(response.status===403){const fresh=await fetch('/api/config');if(fresh.ok){token=(await fresh.json()).token;response=await send();}}
 return response;
}

// ---- Score: the last tracked run in one line, then pass / total per case type; "Run all" adds this server's own run.
function renderSummary(){
 const c=board.counts, last=board.last_run, both=c.runs-c.cases;
 const parts=[board.badge||'no tracked run yet',`${c.cases} cases, ${c.runs} runs`+(both>0?` (${both} cases run both offline and against a recording)`:'')];
 if(last)parts.push(`last tracked run ${String(last.ran_at).replace('T',' ').replace('Z',' UTC')}${last.commit?' at commit '+last.commit:''}`);
 parts.push(`dataset ${String(board.dataset_hash).slice(0,12)}`+(board.golden_as_of?` (the year ending ${board.golden_as_of}; the demo's own window moves nightly)`:''));
 $('summary').textContent=parts.join(' · ');
}
function tally(results){ // run id -> result  =>  type -> [pass, total]
 const out={};
 for(const c of board.cases)for(const run of c.runs){const r=results[run.id]; if(!r)continue; const t=out[c.type]||(out[c.type]=[0,0]); t[1]++; if(r.outcome==='pass')t[0]++;}
 return out;
}
function renderScore(now){
 const last={}; for(const c of board.cases)for(const run of c.runs)if(run.last)last[run.id]=run.last;
 const tracked=tally(last), fresh=now?tally(now):null;
 const head=node('thead'), hr=node('tr'); for(const label of ['Type','What it checks','Cases','Last tracked run','This server, now']){const th=node('th',label);th.scope='col';hr.append(th);} head.append(hr);
 const body=node('tbody'), cell=t=>t?`${t[0]}/${t[1]}`:'—', sum=[0,0,0,0,0];
 for(const type of board.types){
  const count=board.counts.by_type[type]||0; if(!count)continue;
  const tr=node('tr'); tr.dataset.type=type;
  tr.append(node('td',type),node('td',TYPE_HELP[type]||''),node('td',String(count)),node('td',cell(tracked[type])),node('td',fresh?cell(fresh[type]):'—'));
  sum[0]+=count; for(const [i,t] of [[1,tracked[type]],[3,fresh&&fresh[type]]])if(t){sum[i]+=t[0];sum[i+1]+=t[1];}
  body.append(tr);
 }
 const total=node('tr',undefined,'total'); total.append(node('td','all'),node('td','runs: every case offline, plus its recorded live variant'),node('td',String(sum[0])),node('td',sum[2]?`${sum[1]}/${sum[2]}`:'—'),node('td',sum[4]?`${sum[3]}/${sum[4]}`:'—'));
 body.append(total);
 $('score-table').replaceChildren(head,body);
}
async function runAll(){
 if(runningAll||!board)return;
 runningAll=true;$('run-all').disabled=true;$('run-status').textContent=`Running ${board.counts.runs} runs on this server: the offline planner, and a replay for each recording…`;
 try{
  const response=await post({all:true}), data=await response.json();
  if(!response.ok)throw new Error(data.error||'The run failed');
  const results={}; for(const row of data.rows){results[row.id]=row;setChip(row.id,row,'this server, '+data.ran_at);}
  renderScore(results);
  const failed=data.rows.filter(r=>r.outcome==='fail').map(r=>r.id);
  $('run-status').textContent=`This server, ${String(data.ran_at).replace('T',' ').replace('Z',' UTC')}: ${data.passed}/${data.total} passed`+(data.failed?`, ${data.failed} failed (${failed.slice(0,4).join(', ')})`:'')+(data.skipped?`, ${data.skipped} skipped`:'')
   +(data.latency_ms&&data.latency_ms.p50!==null?` · offline p50 ${ms(data.latency_ms.p50)}, p95 ${ms(data.latency_ms.p95)}`:'')+' · no model called'+(data.cached?' · this is the run from under a minute ago; the whole set is repeated at most once a minute':'');
 }catch(error){$('run-status').textContent=error.message;}
 finally{runningAll=false;$('run-all').disabled=false;}
}

// ---- Trend: one line chart per measure (one axis each), a point per tracked run, the last value written at its point.
const niceCeil=v=>{ if(!(v>0))return 1; const p=10**Math.floor(Math.log10(v)); const m=v/p; return (m<=1?1:m<=2?2:m<=2.5?2.5:m<=5?5:10)*p; };
const narrow=()=>typeof window!=='undefined'&&window.innerWidth&&window.innerWidth<850;
function trendChart(title,caption,runs,key,unit,fixedMax){
 const wrap=node('div',undefined,'chart'); wrap.append(node('h3',title));
 const points=runs.filter(r=>r[key]!==null&&r[key]!==undefined);
 if(!points.length){wrap.append(node('p','No tracked run recorded this yet.','muted'));return wrap;}
 const W=narrow()?420:690, H=200, m={l:44,r:narrow()?62:74,t:16,b:38}, max=fixedMax||niceCeil(Math.max(...points.map(r=>r[key])));
 const x=i=>points.length===1?(m.l+W-m.r)/2:m.l+(W-m.l-m.r)*i/(points.length-1), y=v=>m.t+(H-m.t-m.b)*(1-v/max);
 const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':`${title}: ${points.length} tracked runs, latest ${points[points.length-1][key]} ${unit}`});
 for(const t of [0,.5,1]){svg.append(svgNode('line',{x1:m.l,x2:W-m.r,y1:y(max*t),y2:y(max*t),class:'grid'+(t===0?' base':'')}));svg.append(svgNode('text',{x:m.l-8,y:y(max*t)+4,'text-anchor':'end',class:'tick'},String(max*t)));}
 if(points.length>1)svg.append(svgNode('path',{d:points.map((r,i)=>`${i?'L':'M'}${x(i).toFixed(1)} ${y(r[key]).toFixed(1)}`).join(' '),class:'line'}));
 const every=Math.ceil(points.length/(narrow()?4:8));
 points.forEach((r,i)=>{
  const detail=`${String(r.ran_at).replace('T',' ').replace('Z',' UTC')}${r.commit?', commit '+r.commit:''}: ${r[key]} ${unit}; ${r.passed}/${r.total} passed`;
  const dot=svgNode('circle',{cx:x(i),cy:y(r[key]),r:4,class:'dot',tabindex:0,'aria-label':detail});dot.append(svgNode('title',{},detail));svg.append(dot);
  if(i%every===0||i===points.length-1)svg.append(svgNode('text',{x:x(i),y:H-m.b+18,'text-anchor':i===0?'start':i===points.length-1?'end':'middle',class:'tick'},String(r.ran_at).slice(5,10)));
  if(i===points.length-1)svg.append(svgNode('text',{x:x(i)+10,y:y(r[key])+4,class:'val'},`${r[key]} ${unit}`));
 });
 wrap.append(svg,node('p',caption+(points.length<runs.length?` ${runs.length-points.length} earlier run${runs.length-points.length===1?'':'s'} did not record it.`:''),'muted'));
 return wrap;
}
function renderTrend(){
 const runs=board.history||[];
 $('trend-note').textContent=`${runs.length} tracked run${runs.length===1?'':'s'}`;
 $('trend-charts').replaceChildren(
  trendChart('Pass rate per run','Runs that passed, as a percent of the runs in the set.',runs,'pass_rate_pct','%',100),
  trendChart('Offline latency per run (p50)',`Median time of an offline case, agent turns only; the gate is ${board.thresholds.offline_latency_ms.p50} ms. Comparable between runs on the same machine.`,runs,'p50_ms','ms'));
}

// ---- The narrative judge (issue #25): the newest judged run, scored per rubric line, beside its agreement with the
// owner's grades. Read from the board; the page never runs the judge. The grading link shows on the local workbench only.
const pct=v=>v===null||v===undefined?'—':`${Math.round(v)}%`;
function agreeCell(r){ return r&&r.n?`${r.agree}/${r.n} · ${pct(r.pct)} · κ ${r.kappa===null?'undefined':r.kappa.toFixed(2)}`:'—'; }
function renderJudge(isPublic){
 const j=board.judge; if(!j){$('judge-summary').textContent='This build carries no narrative judge.';return;}
 $('grade-link').hidden=Boolean(isPublic);
 const a=j.agreement, last=j.last;
 const parts=[last?`Last judged run ${String(last.ran_at).replace('T',' ').replace('Z',' UTC')}${last.commit?' at commit '+last.commit:''}: ${last.metric} with ${last.model}, ${last.judged} of ${last.narratives} narratives scored`
   +(last.cost_per_case_usd!==null&&last.cost_per_case_usd!==undefined?`, $${Number(last.cost_per_case_usd).toFixed(4)} per judged case`:'')+(last.rubric_current?'':' (an earlier wording of the rubric)')
  :'No judged run is tracked yet: the owner runs python3 evals/run.py --judge --track with the judge model’s key'];
 parts.push(a.matched?`agreement with the owner over ${a.matched} narrative${a.matched===1?'':'s'} graded as the same text (bar ${j.bar_pct}% per line)`
   +(a.unmatched?`; ${a.unmatched} grade${a.unmatched===1?'':'s'} set aside (changed text or not judged)`:'')
  :`${j.graded} owner grade${j.graded===1?'':'s'} saved (target ${j.target})${last&&j.graded?', none of the text the judge scored':''}; no agreement measured yet`);
 $('judge-summary').textContent=parts.join(' · ');
 const head=node('thead'), hr=node('tr'); for(const label of ['Rubric line','What it asks','Judge passes','Agreement with the owner']){const th=node('th',label);th.scope='col';hr.append(th);} head.append(hr);
 const body=node('tbody');
 for(const rule of j.rubric){
  const c=last&&last.criteria?last.criteria[rule.key]:null, tr=node('tr'); tr.dataset.key=rule.key;
  tr.append(node('td',rule.name),node('td',rule.criterion),node('td',c?`${c.pass}/${c.pass+c.fail}`+(c.error?` (${c.error} unscored)`:''):'—'),node('td',agreeCell(a.criteria[rule.key])));
  body.append(tr);
 }
 const total=node('tr',undefined,'total'); total.append(node('td','overall'),node('td','good or bad, against all four lines passing'),node('td','—'),node('td',agreeCell(a.overall))); body.append(total);
 $('judge-table').replaceChildren(head,body);
 if(!j.rows.length){$('judge-detail').hidden=true;return;}
 $('judge-detail').hidden=false; $('judge-rows-title').textContent=`Judged narratives (${j.rows.length}): a score per rubric line, and the judge’s reason for each failed line`;
 const rh=node('thead'), rr=node('tr'); for(const label of ['Narrative',...j.rubric.map(r=>r.name),'Why a line failed']){const th=node('th',label);th.scope='col';rr.append(th);} rh.append(rr);
 const rb=node('tbody');
 for(const row of j.rows){
  const tr=node('tr'); tr.append(node('td',row.id));
  for(const rule of j.rubric){const v=row.scores[rule.key]; const td=node('td',v===null||v===undefined?'—':String(v),'judge-score s'+v); if(row.reasons)td.title=row.reasons[rule.key]||''; tr.append(td);}
  const failed=j.rubric.filter(r=>row.scores[r.key]!==1);   // the judge's reasons are model prose: the hosted board leaves them out
  tr.append(node('td',row.reasons?failed.map(r=>`${r.name}: ${row.reasons[r.key]||'not scored'}`).join(' · '):failed.length?failed.map(r=>r.name).join(' · ')+' (reasons on the local workbench)':''));
  rb.append(tr);
 }
 $('judge-rows').replaceChildren(rh,rb);
}

// ---- Cases: one disclosure per case. Opening it runs the case here and draws expected beside actual.
function setUrl(caseId){
 try{const params=new URLSearchParams(); if(typeFilter!=='all')params.set('type',typeFilter); if(caseId)params.set('case',caseId); const q=params.toString(); window.history.replaceState(null,'','/evals'+(q?'?'+q:''));}catch(e){}
}
function setupText(c){
 const bits=[c.day, c.scope==='day'?'all shifts':`shift ${c.shift}`];
 if(c.period!=='day')bits.push(`${c.period} period`);
 if(c.corrupt)bits.push('one bad trim record injected');
 return bits.join(' · ');
}
function list(items,cls){ const ul=node('ul',undefined,cls); for(const item of items)ul.append(node('li',item)); return ul; }
function renderRun(target,result){
 target.replaceChildren();
 const line=node('p',undefined,'result-line'); line.append(node('span',result.outcome,'outcome '+result.outcome),node('span',`${result.outcome==='skip'?'':ms(result.ms)+' · '}${result.note||''}`,'mono'));
 target.append(line);
 if(result.failures.length)target.append(list(result.failures,'failures'));
 const rec=result.recording;
 if(rec){
  target.append(node('p',`Replay of the recorded ${rec.provider} run (${rec.models.join(', ')||'model unknown'}): ${rec.model_responses} model response${rec.model_responses===1?'':'s'}, recorded status “${rec.recorded_status}”, recorded cost $${Number(rec.recorded_cost_usd||0).toFixed(4)}. No model was called now: the recording’s choices ran through the agent, the tools and the validators again.`,'muted'));
  const chosen=node('details',undefined,'trace-json'); chosen.append(node('summary',`What the model chose, as recorded (${rec.exchanges.length} responses)`));
  const ol=node('ol',undefined,'recorded'); for(const e of rec.exchanges){const li=node('li'); if(!e.calls.length&&!e.text)li.append(node('span',`HTTP ${e.http_status}, no tool call`,'mono')); for(const call of e.calls){const args=compactJson(call.arguments||{});li.append(node('code',call.tool,'tool'),node('span',' '+(args.length>240?args.slice(0,240)+'…':args),'mono'));} if(e.text)li.append(node('p',e.text,'muted')); ol.append(li);}
  chosen.append(ol); target.append(chosen);
 }
 if(!result.turns.length)target.append(node('p',result.outcome==='skip'?'Nothing ran.':'Refused before any tool ran, so there is no trail: the refusal reason above is the whole answer.','muted'));
 result.turns.forEach((turn,i)=>{
  const box=node('div',undefined,'turn');
  if(result.turns.length>1)box.append(node('h4',`Turn ${i+1}: ${turn.question||''}`));
  const calls=turn.trace.length;
  box.append(node('p',`${turn.status} · ${calls} tool call${calls===1?'':'s'} · ${turn.usage.model_calls?turn.usage.model_calls+' recorded model responses replayed':'no model'}`,'mono turn-facts'));
  const lines=turn.sections;   // the evidence lines, and the interpretation when a recording supplied one
  if(lines.length){const answer=node('details',undefined,'trace-json'); answer.append(node('summary',`The answer (${lines.length} line${lines.length===1?'':'s'})`)); for(const text of lines)answer.append(node('p',text)); box.append(answer);}
  for(const entry of turn.trace)box.append(traceRow(entry));
  target.append(box);
 });
}
async function runRow(run,box){
 box.button.disabled=true; box.result.replaceChildren(node('p','Running on this server…','muted'));
 try{
  const response=await post({id:run.id}), data=await response.json();
  if(!response.ok)throw new Error(data.error||'The run failed');
  renderRun(box.result,data); setChip(run.id,data,'this server, '+data.ran_at);
 }catch(error){box.result.replaceChildren(node('p',error.message,'warning-note'));}
 finally{box.button.disabled=false;}
}
function buildCase(c){
 const view=views[c.id]; if(view.built)return null; view.built=true;
 const grid=node('div',undefined,'case-grid'), expected=node('div',undefined,'case-col'), actual=node('div',undefined,'case-col');
 expected.append(node('h3','Expected'),node('p',setupText(c),'mono case-setup'));
 if(c.call)expected.append(node('p','One scripted tool call, no planner: '+c.call));
 else if(c.turns.length>1){expected.append(node('p','Asked in turn:')); const ol=node('ol'); for(const t of c.turns)ol.append(node('li',t)); expected.append(ol);}
 else expected.append(node('p',`“${c.question}”`,'case-question'));
 expected.append(list(c.expected,'expect'));
 if(c.expected_live){expected.append(node('h4','With a model planning (the recorded variant)'),list(c.expected_live,'expect'));}
 if(c.note)expected.append(node('p',c.note,'muted'));
 if(c.known_defect)expected.append(node('p','Known defect: '+c.known_defect,'warning-note'));
 actual.append(node('h3','Actual, on this server'));
 view.boxes=[];
 for(const run of c.runs){
  const box=node('div',undefined,'run-box'), head=node('div',undefined,'run-head'), button=node('button','Run again','secondary'), result=node('div');
  button.type='button'; head.append(node('strong',run.mode==='offline'?(c.call?'Scripted call':'Offline planner'):run.provider?`Recorded ${run.provider} replay`:'Live variant'),button);
  box.append(head,result); actual.append(box);
  const entry={run,button,result}; view.boxes.push(entry);
  button.addEventListener('click',()=>runRow(run,entry));
 }
 grid.append(expected,actual); view.body.append(grid);
 return Promise.all(view.boxes.map(entry=>runRow(entry.run,entry)));
}
function openCase(c){ setUrl(c.id); return buildCase(c); }
function visibleCases(){
 const q=String($('search').value||'').trim().toLowerCase();
 return board.cases.filter(c=>(typeFilter==='all'||c.type===typeFilter)&&(!q||c.id.toLowerCase().includes(q)||String(c.question||c.call||'').toLowerCase().includes(q)));
}
function applyFilter(){
 const shown=new Set(visibleCases().map(c=>c.id));
 for(const c of board.cases)views[c.id].details.hidden=!shown.has(c.id);
 $('case-count').textContent=`${shown.size} of ${board.cases.length} cases`;
 for(const b of $('type-filter').children)b.setAttribute('aria-pressed',String(b.dataset.value===typeFilter));
}
function renderFilter(){
 const box=$('type-filter'); box.replaceChildren();
 for(const [value,label] of [['all',`All ${board.cases.length}`],...board.types.filter(t=>board.counts.by_type[t]).map(t=>[t,`${t.replace('_',' ')} ${board.counts.by_type[t]}`])]){
  const b=node('button',label,'seg-btn'); b.type='button'; b.dataset.value=value;
  b.addEventListener('click',()=>{typeFilter=value;applyFilter();setUrl(null);});
  box.append(b);
 }
 $('search').addEventListener('input',applyFilter);
}
function renderCases(){
 const box=$('cases'); box.replaceChildren();
 for(const c of board.cases){
  const details=node('details',undefined,'case'), summary=node('summary',undefined,'case-sum'), body=node('div',undefined,'case-body');
  details.id='case-'+c.id; details.dataset.type=c.type;
  summary.append(node('code',c.id,'case-id'),node('span',c.type.replace('_',' '),'type-chip'));
  for(const run of c.runs){const chip=node('span',undefined,'outcome');chip.dataset.label=runLabel(run);chips[run.id]=chip;summary.append(chip);setChip(run.id,run.last,'last tracked run');}
  summary.append(node('span',c.call||(c.turns.length>1?`${c.turns.length} turns, ending: ${c.question}`:c.question)||'','case-q'));
  details.append(summary,body);
  views[c.id]={details,body,built:false,boxes:[]};
  details.addEventListener('toggle',()=>details.open?openCase(c):null);
  box.append(details);
 }
}
// The URL is the state: /evals?type=refusal filters, /evals?case=why-slow opens that case and runs it.
function applyUrl(){
 let params; try{params=new URLSearchParams(window.location.search);}catch(e){return null;}
 const type=params.get('type'); if(type&&board.counts.by_type[type])typeFilter=type;
 applyFilter();
 const wanted=params.get('case'); if(!wanted)return null;
 const c=board.cases.find(c=>c.id===wanted||c.runs.some(r=>r.id===wanted)); if(!c)return null;
 const view=views[c.id]; view.details.hidden=false; view.details.open=true;
 try{view.details.scrollIntoView({block:'start'});}catch(e){}
 return openCase(c);
}
async function init(){
 try{
  const [boardResponse,configResponse]=await Promise.all([fetch('/evals.json'),fetch('/api/config')]);
  if(!boardResponse.ok)throw new Error('The golden set is not available on this server.');
  board=await boardResponse.json(); const config=configResponse.ok?await configResponse.json():{}; token=config.token||null;
  renderSummary();renderScore(null);renderTrend();renderJudge(config.public!==false);renderFilter();renderCases();
  $('run-all').addEventListener('click',runAll);
  await applyUrl();
 }catch(error){$('summary').textContent=error.message;}
}
const ready=init();
