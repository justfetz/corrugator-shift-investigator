'use strict';
// The grading page (issue #25), local workbench only. It lists every narrative the golden set's recorded runs
// and scripted analyst rounds write (GET /api/evals/narratives: a replay, never a model call), and saves the
// owner's grade of one (POST /api/evals/grade) to evals/judge_calibration.json. The judge's own scores are not
// fetched here, so the owner's grades are not anchored on them.
const $ = id => document.getElementById(id);
function el(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
let data=null, token=null, show='todo';
const forms={};      // narrative id -> {box, state, ...controls}

async function post(body){
 const send=()=>fetch('/api/evals/grade',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token||''},body:JSON.stringify(body)});
 let response=await send();
 if(response.status===403){const fresh=await fetch('/api/config');if(fresh.ok){token=(await fresh.json()).token;response=await send();}}
 return response;
}

function current(item){ return Boolean(item.grade)&&!item.stale; }
// `graded` is every saved grade, the same count /evals shows; `set_aside` the ones whose text has changed since or
// cannot be replayed now, which the agreement leaves out.
function renderProgress(){
 const total=data.narratives.length;
 $('progress').textContent=`${data.graded} graded · target ${data.target_text||data.target} · ${total} narrative${total===1?'':'s'} to choose from`
  +(data.set_aside?` · ${data.set_aside} set aside (text changed, or not replayable now)`:'')
  +(data.graded-data.set_aside>=data.target?' · enough to read the agreement: python3 evals/run.py --judge --track':'');
 $('file-note').textContent=data.file;
}
function renderRubric(){
 const ol=$('rubric'); ol.replaceChildren();
 for(const line of data.rubric){const li=el('li'); li.append(el('strong',line.name+': '),el('span',line.criterion)); const steps=el('ul'); for(const s of line.steps)steps.append(el('li',s,'muted')); li.append(steps); ol.append(li);}
}

function setVerdict(form,verdict){
 form.state.verdict=verdict;
 for(const b of form.verdicts.children)b.setAttribute('aria-pressed',String(b.dataset.value===verdict));
 if(verdict==='good')for(const box of Object.values(form.checks))box.checked=true;   // a good answer passes every line; untick one if not
 form.note.placeholder=verdict==='bad'?'Why is it bad? Required: the reason is what fixes the rubric.':'Why? Required, for a good answer too: what makes it good.';
}
async function save(item,form){
 if(!form.state.verdict){form.status.textContent='Choose good or bad first.';return;}
 const body={id:item.id,digest:item.digest,verdict:form.state.verdict,note:form.note.value};
 for(const [key,box] of Object.entries(form.checks))body[key]=box.checked;
 form.button.disabled=true; form.status.textContent='Saving…';
 try{
  const response=await post(body), out=await response.json();
  if(!response.ok)throw new Error(out.error||'The grade was not saved');
  item.grade=out.saved; item.stale=false; data.graded=out.graded; data.set_aside=out.set_aside;
  if(form.staleNote)form.staleNote.hidden=true;   // the new grade is of the current text
  // The card stays in view under "To grade" until the filter is next applied, so "Saved" does not vanish under the cursor.
  form.status.textContent=`Saved ${String(out.saved.graded_at).replace('T',' ').replace('Z',' UTC')}: ${out.saved.verdict}.`;
  form.box.classList.add('graded');
  renderProgress();
 }catch(error){form.status.textContent=error.message;}
 finally{form.button.disabled=false;}
}
function buildItem(item){
 const box=el('article',undefined,'grade-item'); box.id='n-'+item.id;
 const head=el('div',undefined,'case-sum'); head.append(el('code',item.id,'case-id'),el('span',item.source,'type-chip'));
 box.append(head,el('p',`“${item.question}”`,'case-question'),el('p',item.scope,'mono case-setup'));
 const prose=el('div',undefined,'grade-narrative'); for(const line of item.narrative)prose.append(el('p',line)); box.append(prose);
 const evidence=el('details',undefined,'trace-json'); evidence.append(el('summary',`The tool results it was written from (${item.evidence.length})`));
 for(const line of item.evidence)evidence.append(el('pre',line,'grade-evidence')); box.append(evidence);
 const form={box,state:{verdict:null},checks:{}};
 const verdicts=el('div',undefined,'seg'); verdicts.setAttribute('role','group'); verdicts.setAttribute('aria-label','Verdict on '+item.id);
 for(const [value,label] of [['good','Good'],['bad','Bad']]){const b=el('button',label,'seg-btn'); b.type='button'; b.dataset.value=value; b.setAttribute('aria-pressed','false'); b.addEventListener('click',()=>setVerdict(form,value)); verdicts.append(b);}
 form.verdicts=verdicts;
 const lines=el('fieldset',undefined,'grade-lines'); lines.append(el('legend','Each rubric line: ticked passes, unticked fails'));
 for(const rule of data.rubric){
  const label=el('label',undefined,'grade-line'), box2=document.createElement('input'); box2.type='checkbox'; box2.checked=true; box2.name=rule.key;
  label.append(box2,el('span',` ${rule.name}: ${rule.criterion}`)); lines.append(label); form.checks[rule.key]=box2;
 }
 const note=document.createElement('textarea'); note.rows=2; note.maxLength=500; note.className='grade-note'; note.setAttribute('aria-label','Reason for '+item.id); form.note=note;
 const actions=el('div',undefined,'grade-actions'), button=el('button','Save grade'); button.type='button'; form.button=button; form.status=el('span','','muted');
 button.addEventListener('click',()=>save(item,form)); actions.append(verdicts,button,form.status);
 box.append(lines,note,actions);
 if(item.grade){
  note.value=item.grade.note||''; setVerdict(form,item.grade.verdict);
  for(const [key,b] of Object.entries(form.checks))b.checked=item.grade[key]!==false;   // after the verdict, which ticks every line for "good"
  if(item.stale){form.staleNote=el('p',`You graded an earlier version of this text on ${String(item.grade.graded_at).slice(0,10)}; it has changed since, so grade it again.`,'warning-note'); box.insertBefore(form.staleNote,lines);}
  else box.classList.add('graded');
 }else setVerdict(form,null);
 forms[item.id]=form;
 return box;
}
function applyShow(){
 for(const item of data.narratives)forms[item.id].box.hidden=show==='todo'&&current(item);
 for(const b of $('show-filter').children)b.setAttribute('aria-pressed',String(b.dataset.value===show));
}
async function init(){
 try{
  const [listResponse,configResponse]=await Promise.all([fetch('/api/evals/narratives'),fetch('/api/config')]);
  if(!listResponse.ok)throw new Error('Grading runs on the local workbench only (python -m investigator.server).');
  data=await listResponse.json(); token=configResponse.ok?(await configResponse.json()).token:null;
  renderProgress(); renderRubric();
  $('items').replaceChildren(...data.narratives.map(buildItem));
  for(const b of $('show-filter').children)b.addEventListener('click',()=>{show=b.dataset.value;applyShow();});
  applyShow();
 }catch(error){$('progress').textContent=error.message;}
}
const ready=init();
