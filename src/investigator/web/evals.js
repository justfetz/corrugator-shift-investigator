'use strict';
const $=id=>document.getElementById(id);
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
let config=null, listing=null, busy=false;
const TYPE_LABEL={routing:'Routing',faithfulness:'Faithfulness',grounding:'Grounding',refusal:'Refusal'};

function renderCase(c){
 const box=node('article',undefined,'case');box.id='case-'+c.id;
 const head=node('div',undefined,'case-head');
 const title=node('div',undefined,'case-title');title.append(node('span',c.id,'case-id'),node('span',c.mode==='live'?'live · '+(c.recording?`recorded ${c.recording.provider} answer`:'no recording yet'):'offline · free','case-mode'));
 head.append(title);
 const run=node('button','Run');run.type='button';run.dataset.case=c.id;run.disabled=c.mode==='live'&&!c.recording;run.addEventListener('click',()=>runCases({case_id:c.id}));head.append(run);
 box.append(head);
 const q=node('p',undefined,'case-question');q.append(node('span','Question: ','muted'));if(c.turns){q.append(c.turns.map((t,i)=>`${i+1}. "${t}"`).join('  '));}else q.append(`"${c.question}"`);
 q.append(node('span',` · ${c.day}, shift ${c.shift}, ${c.scope==='day'?'all shifts':'selected shift only'}${c.corrupt?', with a corrupt record':''}`,'muted'));
 box.append(q);
 if(c.note)box.append(node('p',c.note,'case-note'));
 const ul=node('ul',undefined,'expectations');for(const e of c.expectations)ul.append(node('li',e));box.append(ul);
 box.append(node('div',undefined,'verdicts'));
 return box;
}

function renderReport(r){
 const box=$('case-'+r.id);if(!box)return;const v=box.querySelector('.verdicts');v.replaceChildren();
 box.classList.remove('pass','fail','skip');box.classList.add(r.outcome);
 const line=node('div',undefined,'verdict-line');line.append(node('strong',r.outcome.toUpperCase()),node('span',' · '+(r.note||''),'muted'));v.append(line);
 if(r.checks.length){const ul=node('ul',undefined,'checks');for(const c of r.checks){const li=node('li',undefined,c.ok?'ok':'bad');li.append(node('span',c.ok?'✓':'✗','mark'),node('span',c.check,'name'));if(c.detail)li.append(node('span',' — '+c.detail,'muted'));ul.append(li);}v.append(ul);}
 if(r.answer){
  const d=node('details');d.append(node('summary',`What the agent answered · ${r.answer.trace.length} tool calls · ${r.answer.charts} chart(s) · $${Number(r.answer.usage.cost_usd||0).toFixed(r.answer.usage.model_calls?4:2)}`));
  const t=node('table',undefined,'trace-table');const tb=node('tbody');for(const s of r.answer.trace){const tr=node('tr',undefined,s.ok?'':'rejected');tr.append(node('td',String(s.step)),node('td',s.tool,'tool'),node('td',JSON.stringify(s.arguments),'args'),node('td',s.ok?`${s.rows??'-'} rows`:'rejected'),node('td',s.elapsed_ms.toFixed(1)+' ms','ms'));tb.append(tr);}t.append(tb);const sc=node('div',undefined,'trace-scroll');sc.append(t);d.append(sc);
  for(const p of r.answer.narrative||[]){d.append(node('p',`${p.kind}: ${p.text}`+(p.citations.length?'  [cites '+p.citations.join('; ')+']':''),'narrative'));}
  for(const s of r.answer.sections)d.append(node('p',s,'section'));
  v.append(d);
 }
}

function renderScore(reports){
 const s=$('score');s.hidden=false;s.replaceChildren();
 const tot={};for(const r of reports){const t=tot[r.type]||(tot[r.type]={pass:0,fail:0,skip:0});t[r.outcome]++;}
 const parts=[];for(const type of listing.types){const t=tot[type];if(!t)continue;parts.push(`${TYPE_LABEL[type]} ${t.pass}/${t.pass+t.fail}${t.skip?` (${t.skip} not recorded)`:''}`);}
 const failed=reports.filter(r=>r.outcome==='fail').length,skipped=reports.filter(r=>r.outcome==='skip').length;
 s.append(node('div',parts.join('   ·   '),'score-line'));s.append(node('div',`${reports.length-failed-skipped} passed, ${failed} failed, ${skipped} skipped`,failed?'score-bad':'score-ok'));
}

async function runCases(body){
 if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);$('run-status').textContent=body.all?'Running every case on the server…':`Running ${body.case_id}…`;
 try{
  const response=await fetch('/api/evals/run',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':config.token},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');
  for(const r of result.reports)renderReport(r);
  if(body.all)renderScore(result.reports);
  $('run-status').textContent=body.all?`Done · ${result.reports.length} cases · no model calls`:`Done · ${body.case_id} · no model calls`;
  if(!body.all)$('case-'+body.case_id).scrollIntoView({block:'nearest'});
 }catch(e){$('run-status').textContent=e.message;}
 finally{busy=false;document.querySelectorAll('button').forEach(b=>{b.disabled=false;});document.querySelectorAll('button[data-case]').forEach(b=>{const c=listing.cases.find(x=>x.id===b.dataset.case);if(c&&c.mode==='live'&&!c.recording)b.disabled=true;});}
}

async function init(){
 const [cfg,lst]=await Promise.all([fetch('/api/config').then(r=>r.json()),fetch('/api/evals').then(r=>r.json())]);
 config=cfg;listing=lst;
 for(const inv of lst.invariants){const li=node('li');li.append(node('strong',inv.check),node('span',' — '+inv.meaning));$('invariant-list').append(li);}
 const groups=$('cases');
 for(const type of lst.types){
  const cases=lst.cases.filter(c=>c.type===type);if(!cases.length)continue;
  const sec=node('div',undefined,'type-group');sec.append(node('h2',`${TYPE_LABEL[type]} · ${cases.length} cases`));
  for(const c of cases)sec.append(renderCase(c));groups.append(sec);
 }
 $('run-all').addEventListener('click',()=>runCases({all:true}));
}
init().catch(e=>{$('run-status').textContent='Could not load the golden cases: '+e.message;});
