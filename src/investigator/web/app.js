'use strict';
// While a card of the thread is drawn into its own containers (#86), `slots` maps the card's container ids to them;
// otherwise every id is the page's own element.
let slots = null;
const $ = id => (slots&&slots[id])||document.getElementById(id);
let config, sessionId = null, latest = null;
const PROVIDERS={openai:{name:'OpenAI',header:'X-OpenAI-Key'},anthropic:{name:'Anthropic',header:'X-Anthropic-Key'}};
function provider(){ return PROVIDERS[$('mode').value]||null; }
function isLive(){ return provider()!==null; }
function liveModes(){ // modes the server's own key enables; older servers report only the OpenAI flag
 if(!config)return [];
 if(Array.isArray(config.live_modes))return config.live_modes.filter(m=>PROVIDERS[m]);
 return config.live_model_available?['openai']:[];
}
function syncKeyLabel(){
 const p=provider();
 $('key-label').hidden=!p||!!(config&&config.public);
 if(p)$('key-label-text').textContent=`${p.name} API key (optional if configured on server)`;
}
function liveHelp(){ const p=provider(); return `Every question is answered offline first. “Ask the analyst” on an answer sends ${p.name} your question, recent context and synthetic evidence; live calls incur charges and your key is used for that request only.`; }
const NOTE_LINE=/ - reported note \(synthetic, unverified\): /;
// ---- The answer shape (issue #75). The server's sections are plain text with two markers: `**figure**` and a
// leading `- ` (src/investigator/shapes.py). The headline is the first section with a figure that is not a bullet:
// its figure in display size, the rest of the line (what the figure is held against) in the secondary ink. Bullets
// are a list, each figure bold, the evidence `(query 2 · row 0)` a small link to that query. A short line without
// digits that introduces bullets is a label in small caps. `Where to look` and its bullets, each ending with the
// question that opens that place in the records, go under the charts as links that ask it. A list longer than five
// folds the rest under "more". What a reader sees is the section without its two markers, word for word.
const LOOK_LABEL='Where to look', BULLETS_SHOWN=5;
const SHAPE_REF=/ \(query (\d+)(?: · (row|detail|weekday|flag) (\d+))?\)$/, SHAPE_LOOK=/^(.*) → “([^”]+)”$/;
function plainSection(s){ return (s.startsWith('- ')?s.slice(2):s).replace(/\*\*([^*]+)\*\*/g,'$1'); }
function shapeLabel(s,next){ return !!next&&next.startsWith('- ')&&!s.startsWith('- ')&&!s.includes('**')&&!/\d|[.:;!?]$/.test(s)&&s.length<=48; }
function textOf(e){ return e&&e.children&&e.children.length?[...e.children].map(textOf).join(''):((e&&e.textContent)||''); }   // the stub DOM of the unit tests does not compute textContent from children
function refLink(text,step){ const a=node('a',text,'ref'); a.href=`#query-${step-1}`; a.title='The query and row this figure came from'; a.addEventListener('click',event=>revealQuery(a,event)); return a; }
// The rows behind a receipt (issue #142): each step of the trace names the table and filter it read (`source`,
// datapages.py), and each row it cites its own; the link opens them in the data browser, in a new tab so the card
// thread stays. It carries no text of its own (style.css draws "rows ↗", aria-label names it), so what the card reads
// is still the answer's sections word for word (#65).
function rowsHref(src,part=null,index=null){
 if(!src||typeof src!=='object')return null;
 const urls=part&&src.rows&&src.rows[part], url=urls&&index!==null&&typeof urls[index]==='string'?urls[index]:src.url;
 return typeof url==='string'&&/^\/data\/[a-z_]+(\?|$)/.test(url)?url:null;
}
function rowsLink(href,label='The rows behind this figure, in the data browser'){ const a=node('a',undefined,'rows-link'); a.href=href; a.setAttribute('target','_blank'); a.setAttribute('rel','noopener'); a.setAttribute('aria-label',label); a.title=label; return a; }
// The rows behind paragraph i of a card (#151 review). A section the card shows as the server wrote it, in its place,
// takes the server's own receipt for it (result.receipts: the rows that hold its bold figure). A line the visitor's rates
// rewrote (Money.repriceSections re-ranks a money tool's lines and renumbers "row N" to the re-ranked rows) takes the
// rows of the row it now cites, mapped back to the server's order through the re-priced result's byIndex; a line the
// rates only moved keeps the receipt of the server's line with the same text. Otherwise the cited step's rows.
function receiptHref(rec,p,i){
 const r=rec&&rec.result; if(!r||typeof p!=='string')return null;
 const sections=Array.isArray(r.sections)?r.sections:[], receipts=Array.isArray(r.receipts)&&r.receipts.length===sections.length?r.receipts:null;
 if(receipts&&sections[i]===p)return rowsHref(receipts[i]);
 const ref=SHAPE_REF.exec(p), trace=Array.isArray(r.trace)?r.trace:null, n=ref?Number(ref[1]):0, step=trace&&trace[n-1];
 if(!ref||!step)return null;
 const view=rec.view&&rec.view.repriced&&rec.view.of===r&&rec.view.result, now=view&&Array.isArray(view.trace)?view.trace[n-1]:null, data=now&&now.data;
 let index=ref[3]===undefined?null:Number(ref[3]);
 if(index!==null&&ref[2]==='row'&&data&&Array.isArray(data.byIndex)&&Array.isArray(data.rows)){
  index=data.byIndex.indexOf(data.rows[index]); if(index<0)return null;   // the re-ranked row's place in the server's rows
  return rowsHref(step.source,'row',index);
 }
 const same=sections.indexOf(p);
 if(receipts&&same>=0&&sections.lastIndexOf(p)===same)return rowsHref(receipts[same]);
 return rowsHref(step.source,ref[2]||null,index);
}
// One section's inline content: text spans, the bold figure, the trailing evidence link and the link to its rows
// (`href`, receiptHref). `headline` puts what follows the figure in the secondary ink.
function shapedInline(text,headline=false,href=null){
 const out=[], ref=SHAPE_REF.exec(text); let rest=ref?text.slice(0,ref.index):text, last=0, after=false;
 for(const m of rest.matchAll(/\*\*([^*]+)\*\*/g)){ if(m.index>last)out.push(node('span',rest.slice(last,m.index))); out.push(node('strong',m[1],'figure')); last=m.index+m[0].length; after=true; }
 if(last<rest.length)out.push(node('span',rest.slice(last),headline&&after?'clause':undefined));
 if(ref){
  out.push(node('span',' ')); out.push(refLink(ref[0].trim(),Number(ref[1])));
  if(href)out.push(rowsLink(href));
 }
 return out;
}
// Three to five findings per card (#75): past the fifth bullet of the card's finding lists the rest fold under one
// "N more"; a list left with nothing shown folds with its label.
function foldFindings(e,lists){
 const items=lists.flatMap(l=>[...l.children]); if(items.length<=BULLETS_SHOWN)return;
 const hidden=items.slice(BULLETS_SHOWN), kids=[...e.children], folded=[];
 for(const li of hidden)li.hidden=true;
 for(const l of lists){ if(![...l.children].every(li=>li.hidden))continue; folded.push(l); const at=kids.indexOf(l), label=kids[at-1]; if(label&&label.className==='label')folded.push(label); }
 for(const f of folded)f.hidden=true;
 const more=node('button',`${hidden.length} more`,'secondary more-bullets'); more.type='button'; more.setAttribute('aria-expanded','false');
 more.addEventListener('click',()=>{ const show=hidden.some(li=>li.hidden); for(const x of [...hidden,...folded])x.hidden=!show; more.textContent=show?'Fewer':`${hidden.length} more`; more.setAttribute('aria-expanded',String(show)); });
 const last=lists[lists.length-1]; if(typeof last.after==='function')last.after(more); else e.children.splice(e.children.indexOf(last)+1,0,more);
}
// A where-to-look question is asked in the scope the card answered: a question that moved the controls (#105's
// "yesterday", #101's downtime default: the previous production day, all shifts) points its looks at that day and
// scope, not at the controls it was asked from (review of #111).
function lookScope(rec){
 if(!rec||!rec.scope)return null;
 const c=rec.result&&rec.result.context;
 if(!c||typeof c.production_day!=='string')return rec.scope;
 return {...rec.scope,day:c.production_day,shift:Number(c.shift),scope:c.scope==='day'?'day':'shift',period:c.period?(c.period==='fiscal'?'accounting':c.period):'day'};
}
function lookItem(text,rec){
 const m=SHAPE_LOOK.exec(text), li=node('li');
 if(!m){ li.append(...shapedInline(text)); return li; }
 const asked=lookScope(rec), question=m[2], scope=asked?{...asked,corrupt:!!rec.corrupt}:controls(), a=node('a',`“${question}”`,'look-q');
 a.href=questionUrl(question,scope); a.title='Ask this: a fixed query, no model';
 a.addEventListener('click',event=>{
  if(event&&(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||(event.button&&event.button!==0)))return;   // a modifier click opens the address as the browser would
  if(event&&event.preventDefault)event.preventDefault();
  ask(question,{mode:'offline',title:question,...(rec?{scope:asked||rec.scope,corrupt:rec.corrupt,source:rec}:{})}).catch(()=>{});
 });
 li.append(node('span',m[1]),node('span',' → '),a);
 return li;
}
// A plain line after the findings (the window and its coverage, a tool's note, a caveat) reads under the charts, in the
// secondary ink above where to look, so the chart follows the findings directly; a data-quality warning stays with them.
const STAYS=/data incomplete|withheld|unavailable/i;
function message(label, paragraphs, cls='', rec=null) {
 const e=node('div',undefined,'message '+cls);if(label)e.append(node('strong',label));
 let notes=null, list=null, look=null, headline=false; // consecutive shift-note lines fold into one disclosure instead of a dump
 const lists=[], groups=[], context=[], looks=[];
 paragraphs.forEach((p,i)=>{
  const next=paragraphs[i+1];
  if(NOTE_LINE.test(p)){ list=look=null; if(!notes){notes=node('details',undefined,'note-group');notes.append(node('summary'));e.append(notes);groups.push(notes);} notes.append(node('p',p)); notes.children[0].textContent=`${notes.children.length-1} reported note${notes.children.length-1===1?'':'s'} (synthetic, unverified — observations, not causes)`; return; }
  notes=null;
  if(p===LOOK_LABEL&&next&&next.startsWith('- ')){ list=null; look=node('ul',undefined,'look-list'); return; }   // where to look: under the charts
  if(look&&p.startsWith('- ')){ look.append(lookItem(p.slice(2),rec)); if(!looks.includes(look))looks.push(look); return; }
  look=null;
  if(p.startsWith('- ')){ if(!list){list=node('ul',undefined,'findings');e.append(list);lists.push(list);} const li=node('li');li.append(...shapedInline(p.slice(2),false,receiptHref(rec,p,i)));list.append(li); return; }
  list=null;
  if(shapeLabel(p,next)){ e.append(node('p',p,'label')); return; }
  if(!headline&&p.includes('**')){ headline=true; const h=node('p',undefined,'headline'); h.append(...shapedInline(p,true,receiptHref(rec,p,i))); e.append(h); return; }
  if(lists.length&&!STAYS.test(p)){ context.push(node('p',p,'context-line')); return; }
  e.append(node('p',p));
 });
 foldFindings(e,lists);
 if(context.length||looks.length){
  const box=$('look'); box.replaceChildren(...context);
  if(looks.length)box.append(node('h3',LOOK_LABEL,'label'),...looks);
 }
 if(!lists.length)for(const g of groups)g.open=true;   // no findings (the notes question): the notes are the answer
 $('messages').append(e);
 return e;
}

// ---- Scope: the hidden selects (#day, #period, #shift, #scope, #mode) hold the state the
// request payload reads; the segmented buttons and the sentence are views over them.
const WEEKDAYS=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTHS_LONG=['January','February','March','April','May','June','July','August','September','October','November','December'];
const SHIFT_HOURS={1:'07:00–15:00',2:'15:00–23:00',3:'23:00–07:00 next day'};
const SEGMENTS={period:[['day','Day'],['week','Week'],['month','Month'],['accounting','4-4-5']],shift:[['1','1 · 7–3'],['2','2 · 3–11'],['3','3 · 11–7'],['all','All']]};
const segButtons={period:[],shift:[]};
const utc=iso=>new Date(iso+'T00:00:00Z');
const iso=d=>d.toISOString().slice(0,10);
const addDays=(d,n)=>new Date(d.getTime()+n*86400000);
const dayText=(d,{weekday=true,year=true,long=false}={})=>`${weekday?(long?WEEKDAYS[d.getUTCDay()]:WEEKDAYS[d.getUTCDay()].slice(0,3))+' ':''}${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${year?' '+d.getUTCFullYear():''}`;
function storeDays(){ return config&&Array.isArray(config.days)&&config.days.length?config.days:null; }
function firstStoreDay(){ return (config&&config.first_day)||(storeDays()||[])[0]||null; }
function lastStoreDay(){ return (config&&config.last_day)||(storeDays()||[]).slice(-1)[0]||null; }
// Why a date cannot be selected, or null when it is a production day of the store.
function dayRejection(value, days){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return 'Choose a production day as YYYY-MM-DD.';
 const d=utc(value); if(Number.isNaN(d.getTime()))return 'Choose a production day as YYYY-MM-DD.';
 const first=days&&days.length?days[0]:null, last=days&&days.length?days[days.length-1]:null;
 const range=first&&last?` Production days are Monday to Friday, ${dayText(utc(first),{weekday:false})} to ${dayText(utc(last),{weekday:false})}.`:' Production days are Monday to Friday.';
 if(d.getUTCDay()===0||d.getUTCDay()===6)return `${dayText(d,{long:true})} is a ${WEEKDAYS[d.getUTCDay()]}: the plant is down at weekends, so there are no records.${range}`;
 if(days&&days.length&&!days.includes(value))return `${dayText(d,{long:true})} is outside the record store.${range}`;
 return null;
}
function weekOf(d){ const monday=addDays(d,-((d.getUTCDay()+6)%7)); return [monday,addDays(monday,4)]; }
function spanText(a,b){ return a.getUTCMonth()===b.getUTCMonth()?`Mon ${a.getUTCDate()} – Fri ${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]} ${b.getUTCFullYear()}`:`Mon ${dayText(a,{weekday:false,year:false})} – Fri ${dayText(b,{weekday:false})}`; }
// The plain-English read-back of the selected scope: window, shift, hours and the comparison base.
function scopeSentence(state){
 const {day,period,shift,scope}=state, days=state.days||null, first=state.first_day||(days&&days[0])||null;
 const d=utc(day); if(Number.isNaN(d.getTime()))return 'Choose a production day.';
 const who=scope==='day'?'all three shifts':`shift ${shift}`;
 const before=(start)=>first&&iso(start)<first;
 if(period==='day'){
  let prev=addDays(d,-1); while(prev.getUTCDay()===0||prev.getUTCDay()===6)prev=addDays(prev,-1);
  const hours=scope==='day'?'24 hours from 07:00':SHIFT_HOURS[shift]||'';
  const cmp=before(prev)?'first day in the store, nothing earlier to compare':`compared with ${dayText(prev,{year:false,long:true})}`;
  return `${dayText(d,{long:true})} · ${who} · ${hours} · ${cmp}`;
 }
 if(period==='week'){
  const [a,b]=weekOf(d), [pa,pb]=[addDays(a,-7),addDays(b,-7)];
  const cmp=before(pb)?'no earlier week in the store, comparison withheld':`compared with the week of ${spanText(pa,pb)}`;
  return `Week of ${spanText(a,b)} · ${who} · ${cmp}`;
 }
 if(period==='month'){
  const prev=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()-1,1)), prevEnd=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),0));
  const cmp=before(prevEnd)?'no earlier month in the store, comparison withheld':`compared with ${MONTHS_LONG[prev.getUTCMonth()]} ${prev.getUTCFullYear()}`;
  return `${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()} · ${who} · ${cmp}`;
 }
 return `4-4-5 accounting period containing ${dayText(d,{long:true})} · ${who} · the exact window and its predecessor are shown in the report`;
}
function currentScope(){ return {day:$('day').value,period:$('period').value,shift:Number($('shift').value),scope:$('scope').value,days:storeDays(),first_day:firstStoreDay()}; }
function renderSegments(){
 for(const [group,options] of Object.entries(SEGMENTS)){
  const box=$(group+'-seg'); if(!box)continue; box.replaceChildren(); segButtons[group]=[];
  for(const [value,label] of options){
   const b=node('button',label,'seg-btn'); b.type='button'; b.dataset.value=value; b.setAttribute('aria-pressed','false');
   b.addEventListener('click',()=>{ if(group==='period')selectPeriod(value); else selectShift(value); });
   box.append(b); segButtons[group].push(b);
  }
 }
 syncSegments();
}
function syncSegments(){
 const period=$('period').value, shiftValue=$('scope').value==='day'?'all':$('shift').value;
 for(const b of segButtons.period)b.setAttribute('aria-pressed',String(b.dataset.value===period));
 for(const b of segButtons.shift)b.setAttribute('aria-pressed',String(b.dataset.value===shiftValue));
}
function selectPeriod(value){ if($('period').value===value)return; $('period').value=value; periodChanged(); }
function selectShift(value){
 if(value==='all'){ if($('scope').value==='day')return; $('scope').value='day'; }
 else { if($('scope').value==='shift'&&$('shift').value===value)return; $('scope').value='shift'; $('shift').value=value; }
 rescope();
}
// ---- Addresses (issue #20): every offline view is one URL and the address bar always equals it. The path is
// the controls (/corr/<machine>/<day|week|month|period>/<date>[/shift/<n>]), the query is the question's plan;
// a typed question goes to /q?...&q= and the server redirects to the canonical URL (docs/URLS.md).
const GRAIN={day:'day',week:'week',month:'month',accounting:'period'}, GRAIN_PERIOD={day:'day',week:'week',month:'month',period:'accounting'};
const CANONICAL=/^\/corr\/[^/]+\/(day|week|month|period)\/(\d{4}-\d{2}-\d{2})(?:\/shift\/([123]))?(?:\.json)?$/;
const nav=(typeof window!=='undefined'&&window.history&&window.location)?window:null;
let conversation=[];     // this conversation's earlier views, carried as after= so offline follow-ups need no session
function machine(){ return (config&&config.machine)||'1'; }
// The controls as an address needs them, read when a question is asked: a queued question keeps the scope it was asked under.
function controls(){ return {day:$('day').value,shift:$('shift').value,scope:$('scope').value,period:$('period').value,corrupt:$('corrupt').checked}; }
function scopeUrl(s=controls()){ return `/corr/${machine()}/${GRAIN[s.period]||'day'}/${s.day}`+(s.scope==='shift'?`/shift/${s.shift}`:''); }
function afterParams(p,priors=conversation){ for(const a of priors)p.append('after',a); }
function questionUrl(question,s=controls(),priors=conversation){
 const p=new URLSearchParams([['day',s.day],['shift',String(s.shift)],['scope',s.scope],['period',s.period]]);
 if(s.corrupt)p.append('corrupt','1'); const rates=ratesKey(); if(rates)p.append('rates',rates);   // the visitor's rates price the answer (#31b)
 p.append('q',question); afterParams(p,priors); return '/q?'+p.toString();
}
function reportUrl(s=controls(),priors=conversation){ const p=new URLSearchParams([['measure','report']]); if(s.corrupt)p.append('corrupt','1'); afterParams(p,priors); return scopeUrl(s)+'?'+p.toString(); }
function withQuery(url,extra){ return url+(url.includes('?')?'&':'?')+extra; }
// The query is everything after the first '?': the server writes after= values unencoded, and each holds its own '?'
// (…?measure=fpm&after=/corr/1/day/2026-09-14/shift/2?measure=overview), so splitting on every '?' loses the priors.
function pathOf(url){ const i=url.indexOf('?'); return i<0?url:url.slice(0,i); }
function queryOf(url){ const i=url.indexOf('?'); return new URLSearchParams(i<0?'':url.slice(i+1)); }
function bareUrl(url){ const path=pathOf(url), p=queryOf(url); p.delete('after'); p.delete('rates'); const q=p.toString(); return path+(q?'?'+q:''); }   // a prior view is a question, not its prices
// One parameter of an address put in its canonical place, or removed (docs/URLS.md: rates= after the plan's own
// parameters, before corrupt=, after= and format=). The query is split on its raw '&' only, so an after= value keeps
// its own '?' and its %26 as the server wrote them.
function setParam(url,key,value){
 const i=url.indexOf('?'), path=i<0?url:url.slice(0,i), parts=i<0?[]:url.slice(i+1).split('&').filter(x=>x&&!x.startsWith(key+'='));
 if(value){ const at=parts.findIndex(x=>/^(corrupt|after|format)=/.test(x)); parts.splice(at<0?parts.length:at,0,`${key}=${value}`); }
 return path+(parts.length?'?'+parts.join('&'):'');
}
function paramOf(url,key){ const i=url.indexOf('?'); if(i<0)return null; const hit=url.slice(i+1).split('&').find(x=>x.startsWith(key+'=')); return hit?decodeURIComponent(hit.slice(key.length+1)):null; }
function withRates(url){ return setParam(url,'rates',ratesKey()); }   // the address carries the rates in force, so the link reproduces the page's figures
function ratesOk(url){ const cfg=costCfg(); if(!cfg)return true; try{ Money.parse(paramOf(url,'rates')||'',cfg); return true; }catch(e){ return false; } }
function sameView(a,b){ return typeof a==='string'&&typeof b==='string'&&setParam(a,'rates','')===setParam(b,'rates',''); }
function priorsOf(url){ return queryOf(url).getAll('after'); }
const MAX_AFTER=8;   // the grammar carries at most eight prior views (urls.MAX_AFTER); the agent keeps its own max_turns
function maxPriors(){ const turns=config&&config.demo_guards&&config.demo_guards.max_turns; return Math.min(MAX_AFTER,Math.max(0,(turns||4)-1)); }
function currentAddress(){ return nav?nav.location.pathname+nav.location.search:null; }
// Set the controls from a canonical address; false when the address is not one.
function applyUrl(url){
 const m=CANONICAL.exec(pathOf(url)); if(!m)return false;
 const query=queryOf(url);
 $('period').value=GRAIN_PERIOD[m[1]]; $('day').value=m[2]; acceptedDay=m[2];
 if(m[3]){$('scope').value='shift';$('shift').value=m[3];} else $('scope').value='day';
 $('corrupt').checked=query.get('corrupt')==='1';
 return true;
}
function pushAddress(url,replace=false){ url=withRates(url); if(!nav||currentAddress()===url)return; nav.history[replace?'replaceState':'pushState']({url},'',url); }
function sameDay(url,s){ const m=CANONICAL.exec(pathOf(url)); return !m||m[2]===s.day; }   // a question can name another day (#105); follow-ups share one
function followView(url,follow=true){ conversation=[...priorsOf(url),...(follow?[bareUrl(url)]:[])].slice(-maxPriors()); }   // follow=false: the period report is a view, not a question in the conversation
// Back, forward or a pasted address: the controls follow the URL, and the view becomes the open card of the
// thread. A view this page has shown opens its card again without a request; any other is fetched as JSON from
// the same address and lands in a new card, through the same render entry as an asked question. On a reload, `hint`
// is the stored card for this address (#86): its title and question, rather than the address as the title.
async function restore(url,hint=null){
 if(!applyUrl(url))return false;
 if(ratesOk(url)){   // the rates are the page's own (#31b): an address restored without them, or with others, shows the rates in force
  if(nav&&currentAddress()===url&&withRates(url)!==url)nav.history.replaceState({url:withRates(url)},'',withRates(url));
  url=withRates(url);
 }   // a link whose rates the server refuses is asked as it is, and the card shows the server's reason
 renderQuestionChips(); sessionEpoch++; sessionId=null; $('api-key').value='';   // the scope may have changed under the open card: a fresh server conversation
 const held=thread.find(r=>r.state==='done'&&sameView(r.url,url));
 if(held){ held.epoch=sessionEpoch; openId=held.id; followView(url,!held.report); renderCard(); scheduleFollow(true); updateScope(); $('status').textContent=`${readyText()} · restored`; return true; }
 if(!queryOf(url).has('measure')){ openId=null; conversation=[]; renderCard(); scheduleFollow(true); updateScope(); $('status').textContent=readyText(); primeKpis(); return true; }
 // Nothing is disabled while this loads, so the conversation is this address's own priors from now on: a question
 // asked meanwhile, or a chip link, never carries the previous day's views (the grammar refuses those).
 conversation=priorsOf(url).slice(-maxPriors());
 $('status').textContent='Loading this address…';
 const named=hint&&typeof hint.title==='string'&&typeof hint.question==='string';
 const scope=currentScope(), epoch=sessionEpoch, rec={id:++seq,question:named?hint.question:bareUrl(url),title:named?hint.title:bareUrl(url),scope,answered:null,mode:'offline',report:queryOf(url).get('measure')==='report',corrupt:$('corrupt').checked,epoch,state:'pending',t0:clock(),ms:null,result:null,error:null,note:null,url:null,askUrl:url,analysed:!!(hint&&hint.analysed)};
 thread.push(rec); openId=rec.id; renderCard(); scheduleFollow(true); updateScope(); saveThread();
 try{
  const response=await fetch(url,{headers:{Accept:'application/json'}});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');
  rec.url=result.url||url; if(!named)rec.question=rec.title=bareUrl(rec.url);
  // Only while nothing has moved on since: a scope change or another answer owns the conversation and the address bar.
  if(epoch===sessionEpoch&&openId===rec.id&&currentAddress()===url){ followView(rec.url,!rec.report); const canonical=result.url&&withRates(result.url); if(nav&&canonical&&canonical!==url)nav.history.replaceState({url:canonical},'',canonical); }   // a hand-typed alias (measure=downtime under Week) becomes its one canonical spelling
  land(rec,result); $('status').textContent=readyText();
 }catch(error){ rec.error=error.message; rec.state='error'; settleCard(rec); $('status').textContent='Ready to retry'; }
 finally{ updateScope(); }
 return true;
}
if(nav&&nav.addEventListener)nav.addEventListener('popstate',()=>{restore(currentAddress()).catch(()=>{});});
function periodChanged(){ renderQuestionChips(); rescope(); }
let acceptedDay=null;
function dayChanged(){
 const value=$('day').value, reason=dayRejection(value,storeDays());
 if(reason){ $('day-note').textContent=reason; $('day-note').hidden=false; if(acceptedDay)$('day').value=acceptedDay; updateScope(); return reason; }
 acceptedDay=value; $('day-note').hidden=true; $('day-note').textContent=''; rescope(); return null;
}
function stepDay(direction){
 const days=storeDays(); if(!days)return;
 const index=days.indexOf($('day').value); const next=index<0?(direction>0?0:days.length-1):index+direction;
 if(next<0||next>=days.length)return;
 $('day').value=days[next]; dayChanged();
}
const PERIOD_NOUN={day:'day',week:'week',month:'month',accounting:'4-4-5 period'};
function updateScope(){
 const ranged=$('period').value!=='day', live=isLive();
 $('scope-help').textContent=scopeSentence(currentScope());
 $('mode').disabled=!!(config&&config.public);syncKeyLabel();syncSegments();   // public: the model is Ask the analyst, never a mode (#23)
 const days=storeDays(), index=days?days.indexOf($('day').value):-1;
 $('day-prev').disabled=!days||index<=0; $('day-next').disabled=!days||index<0||index>=days.length-1;   // the ends of the store, never a busy state (#63)
 $('mode-badge').textContent=live?`Synthetic data / ${provider().name} selected`:'Synthetic data / Offline planner';
 const noun=PERIOD_NOUN[$('period').value]||'period';
 if(ranged)$('mode-help').textContent=`Ask about the selected ${noun}: KPIs against the previous one, the downtime Pareto, waste, stops per day, trends, totals by grade, paper consumed, over/under-run, the speed opportunity, what drove speed.${live?' '+liveHelp():' No model calls or API charges.'} Period report builds the output heatmap, daily matrix, recurring downtime and notes without a model.`;
 else $('mode-help').textContent=live?liveHelp():'Ask about performance, setups, rankings or notes within the selected scope. No model calls or API charges.';
 $('question').placeholder=ranged?`Ask about this ${noun}: KPIs, downtime, waste, stops per day, trends, grades`:'Ask about this shift: speed, downtime, waste, setups, orders, notes';
 $('submit').textContent='Ask'; $('report').hidden=!ranged;
 $('analyst').hidden=!analystMode();   // Ask the analyst is the only live path: the agent chosen under "Agent and advanced", or else the one the server's own key enables
 for(const b of chipButtons){ b.hidden=ranged&&b.dataset.period==='day'; chipHref(b); }   // the per-shift matrices have no period form
}
// A canned question is a link to its address in the current scope (/q?...&q=, with the conversation's after=): copy it or
// open it in a new tab, or click it here.
function chipHref(b){ b.href=questionUrl(b.dataset.question,{...controls(),...(b.dataset.scope?{scope:b.dataset.scope}:{})}); }

// ---- Canned questions (the rail's "Ask in one click"). Each is a phrasing the offline planner routes to fixed,
// parameterized tools, and a click always asks offline: no model, no wait. "{period}" is the window word of the
// Window control (week under Day); a fourth entry 'day' marks a question the period tools cannot answer (the
// per-shift matrices), hidden under Week, Month and 4-4-5. The fifth entry puts a question in the short list;
// the rest sit behind "More questions" so the rail fits a 1040-px screen.
const QUESTIONS=[
 ['Why was this shift slow?','Why was this shift slow? Chart downtime by reason.',null,null,true],
 ['Where is the money?','Where is the money?',null,null,true],   // #31b: the shift's losses in dollars under Day, the levers in $/yr under a window
 ['Speed to target','Show speed to target',null,null,true],
 ['Dry-end waste','Show dry-end quality waste',null,null,true],
 ['Bond or warp?','Is it bond or warp?'],   // under a window the same waste breakdown as "Dry-end waste": in the fold, so the money question fits the rail (#31b)
 ['Costliest reason this {period}','Which downtime reason cost the most minutes this {period}?',null,null,true],
 ['This {period} vs last','How did this {period} go versus the previous one?',null,null,true],
 ['Summary + charts','Summarize performance, downtime and waste. Show supporting charts.',null,null,true],
 ['Setup matrix','Show setup matrix',null,'day'],
 ['Speed trend, 8 weeks','Trend of speed over eight weeks'],
 ['Stops per day this {period}','Stops per day this {period}'],
 ['Read notes','Show shift and downtime notes',null,'day'],
 ['Trim trend, 8 weeks','Trend of trim over eight weeks'],
 ['September vs August','How did September go versus August?'],
 ['Compare shifts','Compare all shifts','day','day'],
 ['Rank losses','Show rankings',null,'day'],
 ['Orders + quantities','Show orders and quantities',null,'day'],
];
const chipButtons=[];
function periodWord(){ return {day:'week',week:'week',month:'month',accounting:'period'}[$('period').value]||'week'; }
function renderQuestionChips(){
 const short=$('examples'), more=$('examples-more'); short.replaceChildren(); more.replaceChildren(); chipButtons.length=0;
 const word=periodWord();
 for(const [label,text,scope,period,primary] of QUESTIONS){
  const question=text.replaceAll('{period}',word), shown=label.replaceAll('{period}',word);
  const b=node('a',shown,'canned'); b.setAttribute('role','button'); b.dataset.question=question; if(scope)b.dataset.scope=scope; if(period)b.dataset.period=period; b.title=question; chipHref(b);
  b.addEventListener('click',event=>{
   if(event&&(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||(event.button&&event.button!==0)))return;   // a modifier click opens the link as the browser would (new tab, window)
   if(event&&event.preventDefault)event.preventDefault();
   if(b.dataset.scope&&$('scope').value!==b.dataset.scope){$('scope').value=b.dataset.scope;rescope();} const sheet=railOpen(); closeRail(); ask(question,{mode:'offline',title:shown}).catch(()=>{}); if(sheet)focusCard(); });
  (primary?short:more).append(b); chipButtons.push(b);
 }
}
function svgNode(tag, attrs, text) { const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e; }
const HEAT_RGB=[8,127,117], LIGHT_SURFACE=[252,252,251];
const heatLuminance=alpha=>HEAT_RGB.map((c,i)=>(alpha*c+(1-alpha)*LIGHT_SURFACE[i])/255).map(v=>v<=.03928?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
function showHeatmap(chart) {
 const wrap=node('div',undefined,'chart heatmap');wrap.append(node('h3','Output heatmap'),node('p',chart.title,'muted'));
 wrap.append(node('p','Darker cells mean more estimated good square feet. Hover or focus a day for output, downtime, KPIs and the largest recorded downtime contributor.','muted'));
 if(chart.coverage!=='complete')wrap.append(node('p','This period is incomplete. Missing or invalid days are hatched and excluded from color scaling.','warning-note'));
 const available=chart.rows.filter(r=>r.available&&Number.isFinite(r.estimated_good_sqft));
 const values=available.map(r=>r.estimated_good_sqft), min=Math.min(...values), max=Math.max(...values);
 const weekdays=node('div',undefined,'heatmap-weekdays');for(const label of ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'])weekdays.append(node('span',label));wrap.append(weekdays);
 const grid=node('div',undefined,'heatmap-grid');const first=new Date(chart.rows[0].day+'T00:00:00Z').getUTCDay();for(let i=0;i<first;i++)grid.append(node('span',undefined,'heatmap-blank'));
 const tip=node('p','Choose a day.','heatmap-tip');tip.setAttribute('aria-live','polite');
 for(const row of chart.rows){
  const alpha=row.available?.22+(max===min?1:(row.estimated_good_sqft-min)/(max-min))*.78:0;
  // The day number is black on light cells and white on deep ones: the flip is where the teal blended over the
  // light surface crosses relative luminance 0.179, the point where black and white both read at 4.58:1 (#36).
  const cell=node('button',String(Number(row.day.slice(-2))),'heatmap-cell'+(row.available?(heatLuminance(alpha)<.179?' deep':''):' missing'));cell.type='button';
  // Assign the CSS property directly: style attributes are blocked by our CSP.
  if(row.available)cell.style.backgroundColor=`rgba(8, 127, 117, ${alpha.toFixed(3)})`;
  const detail=row.available?`${row.day}: estimated good ${row.estimated_good_sqft.toLocaleString(undefined,{maximumFractionDigits:0})} sq ft; gross ${row.gross_sqft.toLocaleString(undefined,{maximumFractionDigits:0})} sq ft; speed ${row.speed_to_target_pct.toFixed(1)}% of target; downtime ${row.downtime_pct.toFixed(2)}%; maintenance ${row.maintenance_pct.toFixed(2)}%; operator ${row.operator_pct.toFixed(2)}%; dry-end ${row.dry_end_pct.toFixed(2)}%; trim ${row.trim_pct.toFixed(2)}%; shear ${row.shear_pct.toFixed(2)}%; ${row.paper_changes} paper changes; heavy hitter ${row.heavy_hitter}, ${row.heavy_hitter_minutes.toFixed(1)} minutes across ${row.heavy_hitter_stops} stops.`:`${row.day}: no complete synthetic record available.`;
  const tooltip=row.available?detail+` Dry-end setups: ${row.valid_setups}.`:detail;
  cell.title=tooltip;cell.setAttribute('aria-label',tooltip);for(const event of ['mouseenter','focus','click'])cell.addEventListener(event,()=>{tip.textContent=tooltip;});grid.append(cell);
 }
 wrap.append(grid);const legend=node('div',undefined,'heatmap-legend');legend.append(node('span','Lower output'),node('span',undefined,'heatmap-scale'),node('span','Higher output'));wrap.append(legend,tip);const link=queryLink(chart.source_result_id);if(link)wrap.append(link);$('heatmaps').append(wrap);
}
// ---- Charts: plain SVG, one axis per chart, text in ink tokens, series color on the marks only.
// Every chart is drawn from a tool result's rows; the same rows sit in the evidence tables.
const CHART_W=()=>(typeof window!=='undefined'&&window.innerWidth&&window.innerWidth<850)?420:470;   // two charts sit side by side in the card (#63), so the box is half the card wide
const niceCeil=v=>{ if(!(v>0))return 1; const p=10**Math.floor(Math.log10(v)); const m=v/p; return (m<=1?1:m<=2?2:m<=2.5?2.5:m<=5?5:10)*p; };
const fmtTick=(v,unit)=>v>=1000?`${(v/1000).toLocaleString(undefined,{maximumFractionDigits:1})}K`:Number.isInteger(v)?String(v):v.toFixed(v<1?2:1);
const fmtVal=(v,unit)=>v===null||v===undefined?'unavailable':`${Number(v).toLocaleString(undefined,{maximumFractionDigits:Math.abs(v)>=100?0:Math.abs(v)>=10?1:2})}${unit&&unit!=='count'?' '+unitShort(unit):''}`;
const shortDay=iso=>{ const d=utc(iso); return Number.isNaN(d.getTime())?String(iso):`${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
function barPath(x,y,w,h,cls,r=4){ // square at the baseline, 4px rounded data end
 w=Math.max(0,w); r=Math.min(r,w,h/2);
 const d=w<=0?`M${x} ${y}h0v${h}h0Z`:`M${x} ${y}h${w-r}a${r} ${r} 0 0 1 ${r} ${r}v${h-2*r}a${r} ${r} 0 0 1 ${-r} ${r}H${x}Z`;
 return svgNode('path',{d,class:cls});
}
function chartCard(title,notes=[]){ const wrap=node('div',undefined,'chart');wrap.append(node('h3',title));for(const n of notes)if(n)wrap.append(node('p',n.text||n,n.cls||'muted'));return wrap; }
function legend(items){ const box=node('div',undefined,'lg');for(const [cls,label] of items){const item=node('span');item.append(node('i',undefined,'sw '+cls),node('span',label));box.append(item);}return box; }
// Horizontal axis along the bottom: hairline gridlines at 0, ½ and the nice maximum, labels beneath.
function xAxis(svg,x0,x1,yTop,yBottom,max,unit,tick=fmtTick){
 for(const t of [0,.5,1]){const x=x0+(x1-x0)*t;svg.append(svgNode('line',{x1:x,x2:x,y1:yTop,y2:yBottom,class:'grid'+(t===0?' base':'')}));svg.append(svgNode('text',{x,y:yBottom+16,'text-anchor':t===0?'start':t===1?'end':'middle',class:'tick'},tick(max*t,unit)));}
}
function frame(W,rows,rowH,top=8,wideValues=false){ const H=top+rows*rowH+28, narrow=W<450; const labelW=narrow?110:138, valueW=wideValues?(narrow?130:150):(narrow?96:104); return {W,H,labelW,valueW,x0:labelW+8,x1:W-valueW-8,top,chars:narrow?14:19}; }   // 420 on a phone, 470 in the card's two-up
function labelText(svg,text,x,y,maxChars){ const t=String(text); svg.append(svgNode('text',{x,y,'text-anchor':'end',class:'lbl'},t.length>maxChars?t.slice(0,maxChars-1)+'…':t)); }
// Generic single-series horizontal bars (downtime by reason, dry-end rejects, stops per day).
function barChart(chart,wrap){
 const rows=chart.rows, W=CHART_W(), f=frame(W,rows.length,30), max=niceCeil(Math.max(...rows.map(r=>Number(r[chart.y])||0)));
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`${chart.title||'Shift '+chart.shift} ${chart.unit} chart`});
 xAxis(svg,f.x0,f.x1,f.top,f.top+rows.length*30,max,chart.unit);
 rows.forEach((r,i)=>{const y=f.top+i*30, v=Number(r[chart.y])||0, label=r.weekday?`${r.weekday} ${shortDay(r[chart.x])}`:r[chart.x];
  labelText(svg,label,f.x0-8,y+19,f.chars);
  svg.append(barPath(f.x0,y+7,(v/max)*(f.x1-f.x0),16,chart.coverage==='complete'?(chart.unit==='minutes'?'bar cur minutes':'bar cur'):'bar incomplete'));
  const detail=`${label}: ${fmtVal(v,chart.unit)}`+(r.minutes_per_stop!==undefined?`; ${fmtVal(r.minutes,'min')}, ${fmtVal(r.minutes_per_stop,'min/stop')}`:'');
  const t=svgNode('text',{x:f.x1+8,y:y+19,tabindex:0,'aria-label':detail,class:'val'},fmtVal(v,chart.unit));t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg);
}
// Wet-end speed: bars with the grade-target mark; long lists show the five best and five worst by % of target.
function speedChart(chart,wrap){
 // grades read slowest first against their targets, as the answer's bullets do (#75); the rows themselves are the tool's
 const all=chart.x==='grade'?[...chart.rows].sort((a,b)=>(a.speed_to_target_pct??0)-(b.speed_to_target_pct??0)):chart.rows, long=all.length>10; let showAll=!long;
 const box=node('div',undefined,'speed-box'); wrap.append(box);
 const rh=long?22:30, bh=long?12:16, by=(rh-bh)/2, ty=rh/2+4;   // a long list is drawn on tighter rows so the card keeps its tiles above the fold (#63)
 const draw=()=>{
  box.replaceChildren();
  let rows=all, divider=-1;
  if(!showAll){const sorted=[...all].sort((a,b)=>(b.speed_to_target_pct??0)-(a.speed_to_target_pct??0));rows=[...sorted.slice(0,5),...sorted.slice(-5)];divider=5;}
  const W=CHART_W(), f=frame(W,rows.length+(divider>0?1:0),rh), max=niceCeil(Math.max(...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r.target_fpm)||0))));
  const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`Shift ${chart.shift} ${chart.unit} chart`});
  xAxis(svg,f.x0,f.x1,f.top,f.top+(rows.length+(divider>0?1:0))*rh,max,chart.unit);
  let slot=0;
  rows.forEach((r,i)=>{
   if(i===divider){svg.append(svgNode('text',{x:f.x0,y:f.top+slot*rh+ty,class:'tick'},`… ${all.length-10} more between them …`));slot++;}
   const y=f.top+slot*rh; slot++;
   labelText(svg,r[chart.x],f.x0-8,y+ty,f.chars);
   svg.append(barPath(f.x0,y+by,(Number(r[chart.y])/max)*(f.x1-f.x0),bh,chart.coverage==='complete'?'bar cur':'bar incomplete'));
   if(r.target_fpm){const x=f.x0+r.target_fpm/max*(f.x1-f.x0);svg.append(svgNode('line',{x1:x,x2:x,y1:y+by-3,y2:y+by+bh+3,class:'target'}));}
   const detail=r.target_fpm?`${chart.x==='grade'?r.grade:`${r[chart.x]} / ${r.grade||'grade-weighted'}`}: ${Number(r[chart.y]).toFixed(1)} ft/min; target ${r.target_fpm.toFixed(1)}; ${r.speed_to_target_pct.toFixed(1)}% of target. ${chart.y==='speed_run_fpm'?'Without stops.':'Includes downtime.'}`:`${r[chart.x]}: ${Number(r[chart.y]).toFixed(1)} ${chart.unit}`;
   const value=svgNode('text',{x:f.x1+8,y:y+ty,tabindex:0,'aria-label':detail,class:'val'},r.target_fpm?`${Number(r[chart.y]).toFixed(1)} / ${r.speed_to_target_pct.toFixed(1)}%`:Number(r[chart.y]).toFixed(1));value.append(svgNode('title',{},detail));svg.append(value);
  });
  box.append(svg);
  if(long){const toggle=node('button',showAll?'Best and worst five':`Show all ${all.length}`,'secondary more');toggle.type='button';toggle.addEventListener('click',()=>{showAll=!showAll;draw();});box.append(toggle);}
 };
 draw();
}
// Paired bars: the current window beside the previous one. KPI tables mix units, so each of those rows
// is scaled to its own larger value and the numbers carry the comparison; same-unit results share one axis.
function pairedChart(chart,wrap){
 const rows=chart.rows, perRow=String(chart.unit).startsWith('per row'), W=CHART_W(), f=frame(W,rows.length,38,8,true);
 const unitOf=r=>perRow?(r.unit||''):chart.unit;
 const shared=perRow?null:niceCeil(Math.max(...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r[chart.y2])||0))));
 if(perRow)wrap.append(node('p','Each row is scaled to its own larger value: compare the two bars within a row and read the numbers across rows. The tiles above carry the same values with their deltas.','muted'));
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':chart.title||'paired bars'});
 if(shared!==null)xAxis(svg,f.x0,f.x1,f.top,f.top+rows.length*38,shared,chart.unit);
 rows.forEach((r,i)=>{const y=f.top+i*38, cur=Number(r[chart.y]), prev=r[chart.y2]===null||r[chart.y2]===undefined?null:Number(r[chart.y2]);
  const max=shared??niceCeil(Math.max(Math.abs(cur)||0,Math.abs(prev)||0));
  labelText(svg,r[chart.x],f.x0-8,y+20,f.chars);
  svg.append(barPath(f.x0,y+6,(Math.max(0,cur)/max)*(f.x1-f.x0),14,'bar cur'));
  if(prev!==null)svg.append(barPath(f.x0,y+22,(Math.max(0,prev)/max)*(f.x1-f.x0),8,'bar prev'));
  const detail=`${r[chart.x]}: ${fmtVal(cur,unitOf(r))} now; previous ${prev===null?'unavailable':fmtVal(prev,unitOf(r))}`+(r.change?`; ${r.change}`:'');
  const t=svgNode('text',{x:f.x1+8,y:y+20,tabindex:0,'aria-label':detail,class:'val small'},`${fmtVal(cur,'')} vs ${prev===null?'n/a':fmtVal(prev,'')}${unitShort(unitOf(r))?' '+unitShort(unitOf(r)):''}`);t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg,legend([['cur','current window'],['prev','previous window']]));
}
// Pareto: minutes (or stops) by category in the tool's order, the top three in color, share and cumulative share as text.
function paretoChart(chart,wrap){
 const rows=chart.rows, W=CHART_W(), f=frame(W,rows.length,36,8,true), key=chart.y, prevKey=chart.y2;   // wide value column: minutes, share and cumulative share
 const max=niceCeil(Math.max(...rows.map(r=>Math.max(Number(r[key])||0,Number(r[prevKey])||0))));
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':chart.title||'downtime Pareto'});
 xAxis(svg,f.x0,f.x1,f.top,f.top+rows.length*36,max,chart.unit);
 rows.forEach((r,i)=>{const y=f.top+i*36, v=Number(r[key])||0, prev=r[prevKey]===null||r[prevKey]===undefined?null:Number(r[prevKey]);
  labelText(svg,stopLabel(r.category),f.x0-8,y+19,f.chars);
  svg.append(barPath(f.x0,y+6,(v/max)*(f.x1-f.x0),14,i<3?'bar vital':'bar rest'));
  if(prev!==null)svg.append(barPath(f.x0,y+22,(prev/max)*(f.x1-f.x0),6,'bar prev'));
  const detail=`${stopLabel(r.category)}: ${fmtVal(v,chart.unit)} over ${r.stops} stops, ${r.share_pct}% of the window, cumulative ${r.cumulative_pct}%; previous window ${prev===null?'unavailable':fmtVal(prev,chart.unit)}.`;
  const t=svgNode('text',{x:f.x1+8,y:y+19,tabindex:0,'aria-label':detail,class:'val'},`${fmtVal(v,'')} · ${Math.round(r.share_pct)}% · cum ${Math.round(r.cumulative_pct)}%`);t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg,legend([['vital','top three'],['rest','the rest'],['prev','previous window']]));
}
// The planned stop reads as both its reason and its kind wherever it shows (owner, 2026-11-12), never a bare code.
function stopLabel(name){return ['Planned maintenance','Scheduled','PM'].includes(name)?'Planned maintenance (scheduled)':name;}
// Waste breakdown: the dry-end split as stacked bars (current and previous), then every category paired.
function wasteChart(chart,wrap){
 const byKey=Object.fromEntries(chart.rows.map(r=>[r.category,r])), parts=[['bond','s1'],['warp','s2'],['misalignment','s3'],['moisture','s4'],['other','s5']].filter(([k])=>byKey[k]);  // the five dry-end families; a result without one (an older recording) stacks the ones it has
 const W=CHART_W(), f=frame(W,2,34), unit=chart.unit;
 const stacks=[['current',r=>r.pct_of_gross],['previous',r=>r.previous_pct_of_gross]].map(([name,get])=>({name,segments:parts.map(([k,cls])=>({k,cls,value:byKey[k]?get(byKey[k]):null}))}));
 const available=stacks.filter(s=>s.segments.every(x=>x.value!==null&&x.value!==undefined));
 if(available.length){
  const max=niceCeil(Math.max(...available.map(s=>s.segments.reduce((a,x)=>a+x.value,0))));
  const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':'dry-end rejects split: '+parts.map(([k])=>k).join(', ')});
  xAxis(svg,f.x0,f.x1,f.top,f.top+available.length*34,max,unit);
  available.forEach((s,i)=>{const y=f.top+i*34; let x=f.x0; const total=s.segments.reduce((a,v)=>a+v.value,0);
   labelText(svg,s.name==='current'?'Dry-end now':'Dry-end previous',f.x0-8,y+18,f.chars);
   s.segments.forEach((seg,j)=>{const w=(seg.value/max)*(f.x1-f.x0); const gap=j<s.segments.length-1?2:0;
    const rect=svgNode('rect',{x,y:y+5,width:Math.max(0,w-gap),height:16,class:'seg '+seg.cls});rect.append(svgNode('title',{},`${seg.k}: ${fmtVal(seg.value,unit)}`));svg.append(rect);
    const inside=`${seg.k} ${seg.value.toFixed(2)}`; if(w>inside.length*6.8+14)svg.append(svgNode('text',{x:x+6,y:y+17,class:'inseg '+seg.cls},inside));x+=w;});
   const t=svgNode('text',{x:f.x1+8,y:y+18,tabindex:0,class:'val','aria-label':`${s.name}: dry-end ${fmtVal(total,unit)} = `+s.segments.map(v=>`${v.k} ${fmtVal(v.value,unit)}`).join(', ')},fmtVal(total,unit));svg.append(t);});
  wrap.append(node('p',`Dry-end rejects split by recorded reason; the ${parts.length} segments add to the dry-end total.`,'muted'),svg,legend(parts.map(([k,cls])=>[cls,k])));
 }
 const others=chart.rows.filter(r=>!['bond','warp','misalignment','moisture','other'].includes(r.category));
 if(others.length){const sub=node('div',undefined,'subchart');sub.append(node('h4','All waste categories, % of gross'));pairedChart({...chart,rows:others},sub);wrap.append(sub);}
}
// Weekly trend: a 2px line with 8px markers; partial weeks are hollow and labelled; the last value is written at its point.
function lineChart(chart,wrap){
 const rows=chart.rows.filter(r=>r[chart.y]!==null&&r[chart.y]!==undefined); if(!rows.length){wrap.append(node('p','No weeks with records in this window.','muted'));return;}
 const W=CHART_W(), H=240, m={l:W<500?44:56,r:W<500?48:70,t:16,b:40}, values=rows.map(r=>Number(r[chart.y]));
 let lo=Math.min(...values), hi=Math.max(...values); if(lo===hi){lo-=1;hi+=1;} const pad=(hi-lo)*.25; lo=Math.max(0,lo-pad); hi=hi+pad;
 const x=i=>rows.length===1?(m.l+W-m.r)/2:m.l+(W-m.l-m.r)*i/(rows.length-1), y=v=>m.t+(H-m.t-m.b)*(1-(v-lo)/(hi-lo));
 const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':chart.title||'weekly trend'});
 for(const t of [0,1/3,2/3,1]){const v=lo+(hi-lo)*t;svg.append(svgNode('line',{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v),class:'grid'}));svg.append(svgNode('text',{x:m.l-8,y:y(v)+4,'text-anchor':'end',class:'tick'},fmtTick(Math.round(v*100)/100,chart.unit)));}
 svg.append(svgNode('path',{d:rows.map((r,i)=>`${i?'L':'M'}${x(i).toFixed(1)} ${y(Number(r[chart.y])).toFixed(1)}`).join(' '),class:'line'}));
 rows.forEach((r,i)=>{const v=Number(r[chart.y]), detail=`week of ${r[chart.x]}: ${fmtVal(v,chart.unit)}${r.partial?` (partial week: ${r.production_days} production days)`:''}`;
  const dot=svgNode('circle',{cx:x(i),cy:y(v),r:4,class:'dot'+(r.partial?' partial':''),tabindex:0,'aria-label':detail});dot.append(svgNode('title',{},detail));svg.append(dot);
  svg.append(svgNode('text',{x:x(i),y:H-m.b+18,'text-anchor':'middle',class:'tick'},shortDay(r[chart.x])));
  if(r.partial)svg.append(svgNode('text',{x:x(i),y:H-m.b+32,'text-anchor':'middle',class:'tick'},'partial'));
  if(i===rows.length-1)svg.append(svgNode('text',{x:x(i)+10,y:y(v)+4,class:'val'},fmtVal(v,'')));});
 wrap.append(svg,node('p',`${chart.unit}; Monday–Friday production weeks ending with the selected week. Hollow markers are partial weeks.`,'muted'));
}
// Over/under-run: one horizontal bullet per knife. Zero (nominal) sits mid-axis, the +-band is shaded
// behind, the bar runs from zero to the knife's percent; inside the band reads as the series color.
function rangeChart(chart,wrap){
 const rows=chart.rows, band=Number(chart.band_pct)||2, W=CHART_W(), f=frame(W,rows.length,34,8,true);
 const span=niceCeil(Math.max(band*1.5,...rows.map(r=>Math.abs(Number(r[chart.y])||0))));
 const x=v=>f.x0+(f.x1-f.x0)*(v+span)/(2*span), yEnd=f.top+rows.length*34;
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':chart.title||'over/under-run by knife'});
 svg.append(svgNode('rect',{x:x(-band),y:f.top,width:x(band)-x(-band),height:yEnd-f.top,class:'band'}));
 for(const v of [-span,-band,0,band,span]){svg.append(svgNode('line',{x1:x(v),x2:x(v),y1:f.top,y2:yEnd,class:'grid'+(v===0?' base':'')}));
  svg.append(svgNode('text',{x:x(v),y:yEnd+16,'text-anchor':'middle',class:'tick'},`${v>0?'+':''}${v}%`));}
 rows.forEach((r,i)=>{const y=f.top+i*34, v=Number(r[chart.y])||0, inside=Math.abs(v)<=band;
  labelText(svg,r[chart.x],f.x0-8,y+20,f.chars);
  const x0=Math.min(x(0),x(v)), w=Math.abs(x(v)-x(0));
  svg.append(svgNode('rect',{x:x0,y:y+8,width:Math.max(w,2),height:14,rx:3,class:'bar '+(inside?'cur':'out')}));
  svg.append(svgNode('circle',{cx:x(v),cy:y+15,r:5,class:'dot'+(inside?'':' out')}));
  const detail=`${r[chart.x]}: ${r.actual_cuts.toLocaleString()} actual vs ${r.nominal_cuts.toLocaleString()} nominal cuts (${v>0?'+':''}${v.toFixed(2)}%); ${r.over_sheets.toLocaleString()} sheets over, ${r.under_sheets.toLocaleString()} under; ${r.lines_inside_band_pct}% of ${r.lines} lines inside the ±${band}% band.`;
  const t=svgNode('text',{x:f.x1+8,y:y+20,tabindex:0,'aria-label':detail,class:'val small'},`${v>0?'+':''}${v.toFixed(2)}% · ${Math.round(r.lines_inside_band_pct)}% in band`);t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg,legend([['cur','inside the band'],['out','outside the band'],['band','±'+band+'% band']]),
  node('p',`Actual cuts against nominal cuts, summed per knife over the window. Positive is overrun, negative is underrun. The band is a demo setting; the plant's acceptance range is an open input.`,'muted'));
}
// Speed variance (#92): a waterfall from the reference speed through each bucket to the speed with stops. The two ends
// are level marks, not bars, so the axis spans the levels instead of starting at zero; a loss steps the level left in
// the warm series, an offset steps it right in the cool one; hairline connectors carry the level from row to row.
function waterfallChart(chart,wrap){
 const rows=chart.rows, start=Number(chart.start), end=Number(chart.end), rh=34, n=rows.length+2, W=CHART_W(), f=frame(W,n,rh,8,true);
 const levels=[start]; for(const r of rows)levels.push(levels[levels.length-1]-(Number(r[chart.y])||0));
 const all=[...levels,end], span=(Math.max(...all)-Math.min(...all))||1, lo=Math.max(0,Math.min(...all)-span*.2), hi=Math.max(...all)+span*.2;
 const x=v=>f.x0+(v-lo)/(hi-lo)*(f.x1-f.x0), yEnd=f.top+n*rh, total=Number(chart.gap??start-end);   // the tool's own gap, never one computed here
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':chart.title||'speed gap by contributor'});
 for(const t of [0,.5,1]){const v=lo+(hi-lo)*t, gx=x(v);svg.append(svgNode('line',{x1:gx,x2:gx,y1:f.top,y2:yEnd,class:'grid'}));
  svg.append(svgNode('text',{x:gx,y:yEnd+16,'text-anchor':t===0?'start':t===1?'end':'middle',class:'tick'},fmtTick(Math.round(v*10)/10,chart.unit)));}
 const value=(y,text,detail)=>{const t=svgNode('text',{x:f.x1+8,y:y+21,tabindex:0,'aria-label':detail,class:'val small'},text);t.append(svgNode('title',{},detail));svg.append(t);};
 const level=(i,v,label)=>{const y=f.top+i*rh;labelText(svg,label,f.x0-8,y+21,f.chars);
  svg.append(svgNode('line',{x1:x(v),x2:x(v),y1:y+6,y2:y+rh-6,class:'level'}));svg.append(svgNode('circle',{cx:x(v),cy:y+rh/2,r:4,class:'dot level'}));
  value(y,`${v.toFixed(1)} ft/min`,`${label}: ${v.toFixed(1)} ft/min`);};
 level(0,start,chart.start_label||'Reference speed');
 rows.forEach((r,i)=>{const y=f.top+(i+1)*rh, a=levels[i], b=levels[i+1], g=Number(r[chart.y])||0, loss=g>0;
  svg.append(svgNode('line',{x1:x(a),x2:x(a),y1:y-rh/2+4,y2:y+8,class:'connector'}));
  labelText(svg,r[chart.x],f.x0-8,y+21,f.chars);
  const rect=svgNode('rect',{x:Math.min(x(a),x(b)),y:y+9,width:Math.max(Math.abs(x(b)-x(a)),2),height:16,rx:3,class:'bar '+(loss?'loss':'offset')});
  const share=r.share_of_gap_pct===null||r.share_of_gap_pct===undefined?'':` · ${Math.round(r.share_of_gap_pct)}%`;
  const detail=`${r[chart.x]}: ${loss?'−':'+'}${Math.abs(g).toFixed(1)} ft/min${share?`, ${r.share_of_gap_pct}% of the gap`:''}`+(r.top_item?`; largest ${r.top_item}, ${Math.abs(r.top_item_fpm).toFixed(1)} ft/min`:'')+'.';
  rect.append(svgNode('title',{},detail));svg.append(rect);
  value(y,`${loss?'−':'+'}${Math.abs(g).toFixed(1)}${share}`,detail);});
 svg.append(svgNode('line',{x1:x(levels[levels.length-1]),x2:x(levels[levels.length-1]),y1:f.top+(n-1)*rh-rh/2+4,y2:f.top+(n-1)*rh+8,class:'connector'}));
 level(n-1,end,chart.end_label||'Speed with stops');
 wrap.append(node('p',`${total>=0?'Losses':'Gains'} of ${Math.abs(total).toFixed(1)} ft/min from the reference to the speed with stops. The axis spans the speeds and does not start at zero: the two ends are levels, the bars between them are the buckets.`,'muted'),
  svg,legend([['loss','lowers speed'],['offset','raises speed'],['level','speed level']]));
}
function showChart(chart) {
 if(chart.type==='calendar_heatmap')return showHeatmap(chart);
 const mode=chartUnits(chart);
 if(mode){ const wrap=chartCard(chart.title||'Priced rows',chart.coverage!=='complete'?[{text:'Incomplete data: observed values only.',cls:'warning-note'}]:[]); pricedChart(chart,mode,wrap); const link=queryLink(chart.source_result_id);if(link)wrap.append(link); $('charts').append(wrap); return; }   // #31b: in another unit than its own, a priced chart is ranked bars of that unit
 const rows=chart.rows||[], isSpeed=chart.unit==='ft/min'&&rows.some(r=>r.target_fpm), isPareto=rows.length&&rows[0].cumulative_pct!==undefined, isWaste=rows.length&&rows[0].pct_of_gross!==undefined&&rows[0].category!==undefined;
 const title=chart.title||`Shift ${chart.shift} · ${chart.unit==='minutes'?'Recorded downtime':isSpeed?'Wet-end speed (bars) / grade target (marks)':'Dry-end rejects'}`;
 const notes=[];
 if(chart.coverage!=='complete')notes.push({text:'Incomplete data: observed values only.',cls:'warning-note'});
 if(isSpeed)notes.push(chart.y==='speed_run_fpm'?'Value = ft/min without stops / % of target; the mark is the grade target.':'Value = ft/min / % of target; the mark is the grade target. Elapsed time includes downtime; hover a value for the grade.');
 const wrap=chartCard(title,notes);
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));
 else if(chart.type==='line')lineChart(chart,wrap);
 else if(chart.type==='range')rangeChart(chart,wrap);
 else if(chart.type==='waterfall')waterfallChart(chart,wrap);
 else if(isPareto)paretoChart(chart,wrap);
 else if(isWaste)wasteChart(chart,wrap);
 else if(chart.y2)pairedChart(chart,wrap);
 else if(isSpeed)speedChart(chart,wrap);
 else barChart(chart,wrap);
 const link=queryLink(chart.source_result_id);if(link)wrap.append(link);
 $('charts').append(wrap);
}
const labels={setup_id:'Setup',wet_end_id:'Wet-end / paper change',grade:'Grade',start:'Start',end:'End',width_in:'Width (in)',lineal_ft:'Lineal (ft)',actual_fpm:'Speed (ft/min)',target_fpm:'Target (ft/min)',speed_to_target_pct:'% of target',stop_count:'Stops',down_minutes:'Down (min)',rejected_sqft:'Rejects (sq ft)',notes:'Reported note',record_id:'Record',record_type:'Type',rank:'Rank',item:'Item',value:'Value',unit:'Units / order',day:'Production day',shift:'Shift',current_count:'Stops now',previous_count:'Stops before',current_minutes:'Minutes now',previous_minutes:'Minutes before',minutes_change:'Change (min)',speed_rank:'Speed rank',maintenance_rank:'Maint. rank',waste_rank:'Waste rank',valid_setups:'Dry-end setups',paper_changes:'Paper changes',observed_shift_fpm:'Speed (ft/min)',maintenance_pct:'Maintenance %',operator_pct:'Operator %',dry_end_pct:'Dry-end %',target_fpm:'Target (ft/min)',wet_ends:'Wet ends',volume_msf:'Volume (MSF)',share_of_volume_pct:'Share %',speed_run_fpm:'ft/min, no stops',speed_elapsed_fpm:'Elapsed speed',setup_stops:'Setup stops',setup_stop_minutes:'Setup stop min',trim_pct:'Trim %',previous_volume_msf:'Prev MSF',delta_volume_msf:'Δ MSF vs previous',paper_runs:'Paper runs',msf:'MSF',previous_msf:'Prev MSF',delta_msf:'Δ MSF',share_of_feet_pct:'Share of ft %',lineal_per_run:'Lineal / run',previous_paper_runs:'Prev runs',previous_lineal_ft:'Prev lineal (ft)',delta_lineal_ft:'Δ lineal (ft)',knife:'Knife',lines:'Lines',nominal_cuts:'Nominal cuts',actual_cuts:'Actual cuts',delta_cuts:'Δ cuts',overrun_pct:'Over/under %',nominal_sheets:'Nominal sheets',actual_sheets:'Actual sheets',over_sheets:'Sheets over',under_sheets:'Sheets under',lines_inside_band_pct:'In band %',lines_over_pct:'Over %',lines_under_pct:'Under %',downtime_pct_of_elapsed:'Down % of elapsed',lineal_per_setup:'Lineal / setup',lineal_per_wet_end:'Lineal / paper change',slice_by:'Slice',slice:'Width / crew',label:'Cell',run_minutes:'Run min',typical_fpm:'Typical ft/min',median_setup_fpm:'Median setup ft/min',best_fifth_fpm:'Best fifth ft/min',gap_fpm:'Gap ft/min',typical_pct_of_target:'Typical % target',best_fifth_pct_of_target:'Best fifth % target',opportunity_hours:'Opportunity h',best_fifth_dry_end_pct:'Dry-end % (fast fifth)',rest_dry_end_pct:'Dry-end % (rest)',reject_penalty_points:'Reject penalty pts',status:'Status',bucket:'Bucket',gap_lineal_ft:'Lineal ft',share_of_gap_pct:'Share of gap %',current_loss_fpm:'Loss ft/min (this)',reference_loss_fpm:'Loss ft/min (reference)',top_item:'Largest reason / grade',top_item_fpm:'Largest ft/min',usd:'$ machine time',usd_per_year:'$ / yr',pct_of_scheduled:'% of scheduled min',minutes_per_stop:'Min / stop',scheduled_minutes:'Scheduled min',band:'Band',band_text:'Against the standard',control:'Lens',control_note:'Why',pct_of_yield:'% of yield',running_fpm:'Running ft/min',baseline_fpm:'Grade baseline ft/min',under_baseline:'Under baseline',speed_to_baseline_pct:'% of baseline',previous_stops:'Prev stops',previous_pct_of_scheduled:'Prev % of scheduled'};
// A table whose answer is a shape (#75) shows the columns the shape's readers compare; the rest stay in "What came back".
const TABLE_COLUMNS={get_grade_totals:['grade','volume_msf','share_of_volume_pct','speed_run_fpm','speed_to_target_pct','setups','trim_pct','dry_end_pct','delta_volume_msf'],
 get_speed_variance:['label','gap_fpm','share_of_gap_pct','gap_lineal_ft','current_loss_fpm','reference_loss_fpm','top_item','top_item_fpm','usd','usd_per_year']};
function showTable(data){
 const titles={get_order_matrix:'Orders and quantities — fragments; demand repeats on continuing orders',get_setup_matrix:'Setup matrix',get_shift_notes:'Shift and downtime notes (synthetic, unverified)',get_rankings:`${data.metric||''} ranking / largest losses first`,get_grade_totals:'All grade totals',get_overrun:'Over/under-run by knife (nominal vs actual cuts)',get_speed_opportunity:'Speed opportunity: typical vs best fifth, by grade and width / crew',get_rollstock:'Paper consumed (rollstock) by grade and web width',get_speed_variance:'Speed gap by contributor: stops, setups, running under baseline, grade mix and planned maintenance (schedule-driven), external stops'};
 const box=node('details');box.open=data.title==='get_setup_matrix'||data.title==='get_rankings';box.append(node('summary',`${data.shift?'Shift '+data.shift+' / ':''}${titles[data.title]||data.title} (${data.rows.length} rows)`));
 if(data.coverage!=='complete')box.append(node('p','Incomplete evidence. Rankings are withheld; any other rows are observed records only.','warning-note'));
 if(!data.rows.length){box.append(node('p','No eligible rows.'));const link=queryLink(data.source_result_id);if(link)box.append(link);$('tables').append(box);return;}   // an empty table still names its query (#40)
 const omitted=new Set(['source_ids','gross_sqft','observed_lineal_ft','throughput_in','lineal_per_setup','lineal_per_wet_end','setups_above_trim_target','price_terms','usd_basis']);
 const columns=TABLE_COLUMNS[data.title]||Object.keys(data.rows[0]).filter(k=>!omitted.has(k)&&!Array.isArray(data.rows[0][k]));
 const wrap=node('div',undefined,'table-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',titles[data.title]||data.title);const table=node('table'),head=node('thead'),tr=node('tr');
 for(const key of columns){const th=node('th',labels[key]||key.replaceAll('_',' '));th.scope='col';tr.append(th);}head.append(tr);table.append(head);const body=node('tbody');
 for(const row of data.rows){const tr=node('tr');for(const key of columns){const value=row[key];tr.append(node('td',value===null?'Unavailable':typeof value==='number'?Number.isInteger(value)?String(value):value.toFixed(2):String(value??'')));}body.append(tr);}table.append(body);wrap.append(table);box.append(wrap);const link=queryLink(data.source_result_id);if(link)box.append(link);$('tables').append(box);
}

// ---- KPI strip: dashboard-style tiles from a get_period_kpis / compare_periods envelope.
// Twelve tiles visible in a fixed priority, the rest behind "more"; the delta chip is colored by the
// tool's own `change` label (better/worse/same/neutral) and reads "withheld" when the comparison is.
const TILE_PRIORITY=['volume_msf','msf_per_day','speed_run_fpm','speed_all_fpm','downtime_pct','stops','minutes_per_stop','trim_pct','shear_pct','dry_end_pct','bale_pct','maintenance_pm_pct','operator_pct'];
const VISIBLE_TILES=12;
const UNIT_SHORT={'% of gross':'%','% of scheduled minutes':'%',count:'','min/stop':'min/stop'};
const unitShort=unit=>UNIT_SHORT[unit]??unit;
function fmtNumber(value,unit,signed=false){
 if(value===null||value===undefined)return 'unavailable';
 let places=Number.isInteger(value)?0:/MSF|ft\/min|^ft$|^min$/.test(unit)?1:2;
 if(signed&&value!==0&&Math.abs(value)<0.5*10**-places)places=Math.min(4,places+2); // a nonzero delta never reads as ±0
 const text=Math.abs(value).toLocaleString(undefined,{minimumFractionDigits:places,maximumFractionDigits:places});
 return (signed?(value>0?'+':value<0?'−':'±'):(value<0?'−':''))+text;
}
function deltaChip(row,withheld){
 const chip=node('span',undefined,'delta');
 if(withheld||row.delta===null){chip.className+=' withheld';chip.textContent='withheld';chip.title='Deltas are withheld unless both windows are complete; missing days are not zero production.';return chip;}
 const change=row.change||'neutral', arrow=row.delta>0?'▲':row.delta<0?'▼':'→';
 chip.className+=' '+change;
 const unit=unitShort(row.unit), pts=unit==='%'?' pts':unit?' '+unit:'';
 chip.textContent=`${arrow} ${fmtNumber(row.delta,row.unit,true)}${pts}`;
 chip.title=change==='neutral'?'A count: context, not a score.':`${change} — ${row.better} is better for ${row.label.toLowerCase()}`;
 return chip;
}
function kpiTile(row,withheld,priced=null){   // priced: the tool's tile_money entry for this tile's metric (#31b)
 const tile=node('div',undefined,'tile'+(row.change&&!withheld?' '+row.change:''));
 tile.append(node('div',row.label,'l'));
 const v=node('div',undefined,'v');v.append(node('span',fmtNumber(row.current,row.unit)));const unit=unitShort(row.unit);if(unit&&row.current!==null)v.append(node('small',unit));tile.append(v);
 moneyOnTile(tile,v,`${fmtNumber(row.current,row.unit)}${unit?' '+unit:''}`,priced,'this window');   // #31b: the tile's loss in dollars, $/window and $/yr
 const foot=node('div',undefined,'f');foot.append(deltaChip(row,withheld));
 foot.append(node('span',row.previous===null||row.previous===undefined?'prev unavailable':`prev ${fmtNumber(row.previous,row.unit)}${unit?' '+unit:''}`,'prev'));tile.append(foot);
 tile.title=`${row.label}: ${row.unit}. ${row.better==='neutral'?'A count, not a score.':row.better+' is better.'}`;
 return tile;
}
function orderedTiles(rows){ const rank=k=>{const i=TILE_PRIORITY.indexOf(k);return i<0?TILE_PRIORITY.length:i;}; return rows.map((r,i)=>[rank(r.metric),i,r]).sort((a,b)=>a[0]-b[0]||a[1]-b[1]).map(x=>x[2]); }
function renderKpiTiles(data,caption){
 const box=$('kpis');box.replaceChildren();
 const withheld=data.comparison!=='complete';
 const head=node('div',undefined,'kpis-head');
 head.append(node('strong',`${data.window.label}${data.previous_window?' vs '+data.previous_window.label:''} · ${data.shift?'shift '+data.shift:'all shifts'}`));
 if(data.equal_production_days===false)head.append(node('span',`${data.production_days_current} vs ${data.production_days_previous} production days: compare MSF/day and the ratio tiles, not raw totals`,'warning-note'));
 if(caption)head.append(node('span',caption,'muted mono'));box.append(head);
 if(withheld)box.append(node('p',`Deltas withheld: ${String(data.comparison||'').replace(/^withheld:\s*/,'')||'a window is incomplete or unavailable'}. Missing days are not zero production.`,'warning-note'));
 const grid=node('div',undefined,'kpi-grid');const rows=orderedTiles(data.rows);
 rows.forEach((row,i)=>{const tile=kpiTile(row,withheld,data.coverage==='complete'||data.window&&data.window.coverage==='complete'?tileOf(data,row.metric):null);if(i>=VISIBLE_TILES)tile.className+=' extra';grid.append(tile);});
 box.append(grid);
 if(rows.length>VISIBLE_TILES){const more=node('button',`Show ${rows.length-VISIBLE_TILES} more`,'secondary more');more.type='button';more.setAttribute('aria-expanded','false');
  more.addEventListener('click',()=>{const open=grid.className.includes('expanded');grid.className=open?'kpi-grid':'kpi-grid expanded';more.setAttribute('aria-expanded',String(!open));more.textContent=open?`Show ${rows.length-VISIBLE_TILES} more`:'Show fewer';});box.append(more);}
 return grid;
}
// On load and after every scope change, the tiles for the selected scope come from one free offline
// get_period_kpis call (the offline planner; no model, no key). Skipped when a per-visitor rate limit
// is on, so a visitor's rounds are not spent on priming, and for the 4-4-5 view (its report has no tool).
let primeToken=0;
function primeQuestion(){ return {day:'How did this shift go compared with the previous one?',week:'How did this week go versus the previous one?',month:'Compare this month to last',accounting:'Compare this period to the previous period'}[$('period').value]||null; }
// Every POST goes through here. A 403 means the token we hold is not the one this
// server instance expects (it restarted, or a second instance answered): refresh
// /api/config once and resend, instead of showing "Missing session token" until
// the visitor reloads the page.
async function post(path,body,extraHeaders={}){
 const extra={...extraHeaders};   // snapshot: the caller clears a visitor key from its object right after calling, and the resend must carry it too
 const send=()=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':config.token,...extra},body:JSON.stringify(body)});
 let response=await send();
 if(response.status===403){
  const fresh=await fetch('/api/config');
  if(fresh.ok){config=Object.assign(config||{},await fresh.json());response=await send();}
 }
 return response;
}

// The idle card's tiles, drawn again when a rate or the unit changes: the tool's tile dollars re-priced at the rates in force.
function paintPrimed(){
 if(!primed||openId!==null)return;
 const {entry,question,result}=primed, cfg=costCfg(); $('kpis').replaceChildren();
 const data=cfg&&ratesKey()&&Money.priced(entry.data)?Money.reprice(entry.tool,entry.data,ratesNow(),cfg):entry.data;
 renderKpiTiles(data,`scope tiles · get_period_kpis · ${result.usage.tool_calls} tool call${result.usage.tool_calls===1?'':'s'} · $0 · offline planner`);
 const trail=node('details',undefined,'prime-trail');trail.append(node('summary','How these tiles were produced'));trail.append(node('p',`Offline question: “${question}”, the address ${result.url||''}. The same evidence a typed question gets; ask one for charts and the full trail.`,'muted'));trail.append(traceRow(entry));$('kpis').append(trail);
}
async function primeKpis(){
 const token=++primeToken, question=primeQuestion(); primed=null;
 if(!config||!question||openId!==null||(config.demo_guards&&config.demo_guards.rate_limit_per_min>0))return;   // the tiles are the idle card's content; an asked question owns the card
 try{
  // The scope's tiles are the KPI measure of the scope's own address; the address bar is not moved for them.
  const response=await fetch(withQuery(scopeUrl()+'.json','measure=kpis'+($('corrupt').checked?'&corrupt=1':'')),{headers:{Accept:'application/json'}});
  const result=await response.json();
  if(token!==primeToken||openId!==null||!response.ok||!Array.isArray(result.trace))return;
  const entry=result.trace.find(e=>e.ok&&e.tool==='get_period_kpis');
  if(!entry)return;
  primed={entry,question,result}; paintPrimed();
  scheduleFollow();
 }catch(error){ /* the tiles are a convenience; the question flow reports its own errors */ }
}

// The tool trail row (traceRow), node(), compactJson, dedent and sqlText live in trail.js, shared with the /evals page.
let stepIndex=new Map();   // result_id -> step number of the latest answer, for the links from charts, tables and render_chart steps (#40)
// ---- The query box (issue #40): the compact trace at the top of the answer, Datasette-style. One block
// per tool step in order: tool(arguments) · rows · ms, then the exact parameterized SQL with its bound
// values and a copy button. A few hundred bytes per step, so it is built with the answer, in the first
// paint; the full per-step result ("What came back") stays below and is built after the paint (#33).
// Read-only by design: the tools are parameterized (docs/AGENT_CONTRACT.md), so visitors cannot edit
// and re-run the SQL here; the copy button is how someone re-runs it themselves.
function argText(args){ return Object.entries(args||{}).filter(([,v])=>v!==null&&v!==undefined).map(([k,v])=>`${k}=${k==='result_id'&&stepIndex.has(v)?`query ${stepIndex.get(v)+1}`:typeof v==='object'?compactJson(v):String(v)}`).join(', '); }   // a result id reads as the query it names
function stepFacts(entry){ const data=entry.ok?entry.data:null, rows=data&&Array.isArray(data.rows)?data.rows.length:null, facts=[]; if(rows!==null)facts.push(`${rows} row${rows===1?'':'s'}`); if(typeof entry.elapsed_ms==='number')facts.push(`${entry.elapsed_ms.toFixed(1)} ms`); if(!entry.ok)facts.push('error'); return facts; }
function phoneWidth(){ try{return typeof matchMedia==='function'&&matchMedia('(max-width:850px)').matches;}catch(e){return false;} }
function copyText(text,button){
 const done=label=>{const was=button.textContent;button.textContent=label;setTimeout(()=>{button.textContent=was;},1500);};
 try{ if(typeof navigator!=='undefined'&&navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(text).then(()=>done('Copied'),()=>done('Copy failed')); else done('Copy unavailable'); }catch(e){done('Copy failed');}
}
function revealStep(i){ try{ $('execution').open=true; const step=document.getElementById(`step-${i}`); if(step)step.open=true; }catch(e){} }
const hrefOf=a=>String((typeof a.getAttribute==='function'?a.getAttribute('href'):a.href)||'');
// A query folded behind "N more queries" opens when something links to it. The box is found from the link's own href
// when it is clicked, so a card that moved up into the thread (its ids prefixed, #86) opens its own box.
// The click stays in the page: following the fragment would fire popstate, and restore() would redraw the card (#75).
function revealQuery(link,event){ try{ const q=document.getElementById(hrefOf(link).replace(/^#/,'')); if(!q)return; if(event&&event.preventDefault)event.preventDefault(); q.hidden=false;q.open=true;const box=q.parentElement;if(box&&/\bcollapsed\b/.test(box.className||''))openQueries(box,true); if(typeof q.scrollIntoView==='function')q.scrollIntoView({block:'nearest'}); }catch(e){} }
// The query box is one line under the answer (#75): `3 queries · 23 ms · Show SQL`. The button, or an evidence link,
// opens it to the list of steps with the open step's SQL beside it.
function openQueries(box,open){ box.className=open?'queries':'queries collapsed'; const b=[...box.children].find(e=>e.className==='queries-line'), toggle=b&&[...b.children].find(e=>e.tag==='button'||e.tagName==='BUTTON'); if(toggle){toggle.textContent=open?'Hide SQL':'Show SQL';toggle.setAttribute('aria-expanded',String(open));} }
function queryLink(resultId){ const i=stepIndex.get(resultId); if(i===undefined)return null; const a=node('a',`query ${i+1} ↑`,'query-link'); a.href=`#query-${i}`; a.title='The query box that produced these rows'; a.addEventListener('click',event=>revealQuery(a,event)); return a; }
function queryBox(entry,i){
 const d=node('details',undefined,'query '+(entry.ok?'ok':'failed')); d.id=`query-${i}`;
 const sum=node('summary',undefined,'query-sum');
 sum.append(node('code',entry.tool,'tool'),node('span',`(${argText(entry.arguments)})`,'args'),node('span','· '+stepFacts(entry).join(' · '),'facts'));
 d.append(sum);
 const sql=sqlText(entry);
 if(sql)d.append(node('pre',sql,'sql'));
 else if(!entry.ok)d.append(node('p',`${entry.error?.code||'error'}: ${entry.error?.message||'tool call rejected'}`,'warning-note'));
 else if(entry.tool==='render_chart'){ const p=node('p',undefined,'muted'); const src=queryLink(entry.arguments?.result_id); p.append('No SQL: draws the rows of '); p.append(src||'an earlier query'); p.append('.'); d.append(p); }
 else d.append(node('p','No SQL: this tool composes other results.','muted'));
 const foot=node('div',undefined,'query-foot');
 if(sql){const copy=node('button','Copy SQL','secondary copy');copy.type='button';copy.dataset.copy=sql;copy.addEventListener('click',()=>copyText(sql,copy));foot.append(copy);}
 const back=node('a','What came back ↓','came-back');back.href=`#step-${i}`;back.title='The full result of this step, below the charts';back.addEventListener('click',()=>revealStep(i));foot.append(back);
 const rows=rowsHref(entry.source);   // the table and filter this step read, in the data browser (#142)
 if(rows){ const a=node('a',`Rows: ${entry.source.table} ↗`,'came-back rows-step'); a.href=rows; a.setAttribute('target','_blank'); a.setAttribute('rel','noopener'); a.title='The rows this query read, with their schema'; foot.append(a); }
 for(const more of (rows&&Array.isArray(entry.source.also)?entry.source.also:[])){ const href=rowsHref(more); if(!href)continue; const a=node('a',`${more.label==='previous window'?'Previous window':'Also'}: ${more.table} ↗`,'came-back rows-step'); a.href=href; a.setAttribute('target','_blank'); a.setAttribute('rel','noopener'); a.title=`The rows this query also read: ${more.label}`; foot.append(a); }   // the comparison window, the shift record of the notes
 d.append(foot);
 return d;
}
const QUERY_LINES=4;   // steps shown before "N more queries"; with the SQL pane beside them the box stays about 150 px tall. A phone shows one, as the mockup does
function renderQueries(result){
 const box=$('queries'); box.replaceChildren(); stepIndex=new Map();
 const steps=result.trace||[]; if(!steps.length)return;
 steps.forEach((e,i)=>{if(e.result_id)stepIndex.set(e.result_id,i);});
 const total=steps.reduce((sum,e)=>sum+(typeof e.elapsed_ms==='number'?e.elapsed_ms:0),0);
 const line=node('div',undefined,'queries-line'), toggle=node('button','Show SQL','secondary');toggle.type='button';toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls','queries');
 toggle.addEventListener('click',()=>{ const owner=line.parentElement||box; openQueries(owner,/\bcollapsed\b/.test(owner.className)); });   // the box this line is in: the open card's, or an earlier card's once it moved up
 line.append(node('span',`${steps.length} ${steps.length===1?'query':'queries'} · ${total.toFixed(0)} ms`,'mono'),toggle);box.append(line);box.className='queries collapsed';
 // Step 1 open (all closed on a phone); one open at a time, so at desktop width its SQL sits in the pane beside the list.
 const phone=phoneWidth(), lines=phone?1:QUERY_LINES, boxes=steps.map((entry,i)=>{const d=queryBox(entry,i);d.open=!phone&&i===0;if(i>=lines)d.hidden=true;d.addEventListener('toggle',()=>{if(d.open)for(const other of boxes)if(other!==d)other.open=false;});box.append(d);return d;});
 const foot=node('div',undefined,'queries-foot');
 if(steps.length>lines){const n=steps.length-lines, more=node('button',`${n} more ${n===1?'query':'queries'}`,'secondary');more.type='button';more.setAttribute('aria-expanded','false');
  more.addEventListener('click',()=>{const show=boxes.slice(lines).some(b=>b.hidden);for(const b of boxes.slice(lines))b.hidden=!show;more.textContent=show?'Show fewer':`${n} more ${n===1?'query':'queries'}`;more.setAttribute('aria-expanded',String(show));});foot.append(more);}
 const sqls=steps.map((e,i)=>[e,i,sqlText(e)]).filter(x=>x[2]);
 if(sqls.length){const copy=node('button','Copy all SQL','secondary');copy.type='button';const text=sqls.map(([e,i,sql])=>`-- query ${i+1}: ${e.tool}(${argText(e.arguments)})\n${sql}`).join('\n\n');copy.dataset.copy=text;copy.addEventListener('click',()=>copyText(text,copy));foot.append(copy);}
 foot.append(node('span',`${steps.length} ${steps.length===1?'query':'queries'} · ${total.toFixed(0)} ms in the tools`,'mono'),node('span','Fixed, parameterized queries. Visitors cannot edit them.','fixed-note'));
 box.append(foot);
}
function usageText(usage){ return usage.model_calls?`${usage.tool_calls} tool calls · ${usage.model_calls} model call${usage.model_calls===1?'':'s'} · estimated $${Number(usage.cost_usd||0).toFixed(4)}`+(usage.reserved_usd!==undefined?` · reserved $${Number(usage.reserved_usd).toFixed(2)}`:''):`${usage.tool_calls} tool call${usage.tool_calls===1?'':'s'} · $0 model cost`; }
// Paint the answer, tables and charts first; the trace table (the largest part of the page)
// is built after that paint so it never delays it (issue #33). A requestAnimationFrame callback
// runs *before* its frame paints, so the work is queued for the frame after: two frames deep.
// Without a frame scheduler (tests), it renders synchronously.
function afterPaint(f){if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(f));else f();}
// The shift KPI tiles: the numbers of one get_shift_kpis row on seven tiles, one row of the card (#63). A tile that
// measures a loss gets a money slot, filled from the card's money answer when it lands (paintTiles, #31b).
let tileSlots=[];
// A row without its band (a period report's) is read against the owner's numbers (targets.json via /api/config), never a
// threshold typed here, in targets.band and targets.band_text's own words: the tile then reads as a banded row would.
const BAND_WORDS=['home run','on target','over target','flag'];
function bandOf(id,value){
 const b=config&&config.targets&&config.targets[id]; if(!b||value===null||value===undefined||b.target===null||b.target===undefined)return null;
 const word=b.home_run!==null&&b.home_run!==undefined&&value<=b.home_run?'home run':value<=b.target?'on target':b.flag!==null&&b.flag!==undefined&&value>b.flag?'flag':'over target';
 const g=v=>`${Number(v)}%`, text=word==='home run'?`home run (${g(b.home_run)} or under)`:word==='on target'?`on target (${g(b.target)} or under)`:
  word==='over target'?`over the ${g(b.target)} target`+(b.flag!==null&&b.flag!==undefined?`, under the ${g(b.flag)} flag`:''):`flag: over ${g(b.flag)}`+(b.target!==b.flag?`, the target is ${g(b.target)}`:'');
 return {word,text};
}
const standard=(id,value)=>(bandOf(id,value)||{}).text||'';
const over=(id,value,...words)=>words.includes((bandOf(id,value)||{}).word);
function shiftTiles(entry,result,prefix){
 const r=entry.data.rows[0], incomplete=entry.data.coverage!=='complete', who=prefix?`${entry.data.shift?'Shift '+entry.data.shift:result.period_label} · `:'';
 const n=(v,places)=>Number(v).toLocaleString(undefined,{minimumFractionDigits:places,maximumFractionDigits:places});
 for(const [label,value,unit,places,sub,bad,title,metric] of [
  ['Speed',r.observed_shift_fpm,'ft/min',1,`${n(r.speed_to_target_pct,1)}% of target`,r.speed_to_target_pct<100,'Lineal feet / sum(grade target ft/min × elapsed minutes) × 100. Includes downtime; weighted by grade duration.','observed_shift_fpm'],
  ['Maintenance',r.maintenance_pct,'%',2,r.maintenance_band_text||standard('maintenance',r.maintenance_pct),r.maintenance_band?r.maintenance_band==='flag':over('maintenance',r.maintenance_pct,'flag'),undefined,'maintenance_pct'],
  ['Operator',r.operator_pct,'%',2,r.operator_band_text||standard('operator',r.operator_pct),r.operator_band?r.operator_band==='flag':over('operator',r.operator_pct,'flag'),undefined,'operator_pct'],
  // the owner's bands ride on the row (issue #101): dry-end waste is a percent of the yield after trim and shear
  ['Dry-end waste',r.dry_end_pct,'%',2,'of yield'+((r.dry_end_band_text||standard('dry_end',r.dry_end_pct))?` · ${r.dry_end_band_text||standard('dry_end',r.dry_end_pct)}`:''),r.dry_end_band?['over target','flag'].includes(r.dry_end_band):over('dry_end',r.dry_end_pct,'over target','flag'),undefined,'dry_end_pct'],
  ['Trim',r.trim_pct,'%',2,`shear ${n(r.shear_pct,2)}%`+(r.trim_band_text?` · ${r.trim_band_text}`:''),r.trim_band==='flag',undefined,'trim_pct'],
  ['Setups',r.valid_setups,'',0,`${n(r.paper_changes,0)} paper changes`,false,'Dry-end setups; paper changes are the count of distinct wet-end runs, as defined by the plant expert.'],
  ['Throughput',r.throughput_in,'in',1,`${n(r.lineal_per_setup,0)} ft / setup`,false]]){
  const card=node('div',undefined,'metric'+(bad||incomplete?' warning':''));card.append(node('div',who+label,'label'));if(title)card.title=title;
  const v=node('div',undefined,'value');v.append(node('span',incomplete?'Incomplete':n(value,places)));if(unit&&!incomplete)v.append(node('small',unit));card.append(v);
  card.append(node('div',incomplete?`${entry.data.excluded_records?.length||'Missing/invalid'} excluded record(s)`:sub,'sub'+(bad||incomplete?' flag':'')));
  if(!incomplete&&metric&&entry.tool!=='get_period_report'){ const line=node('div','','usd'); card.append(line); tileSlots.push({card,v,kids:[...v.children],line,metric,native:`${n(value,places)}${unit?' '+unit:''}`,day:entry.data.production_day,shift:entry.data.shift}); }   // #31b: $/shift and $/yr once the money answer lands (a report's row is a whole window, priced in its panel)
  $('metrics').append(card);
 }
}
function showResult(result) {   // result: at the rates in force (#31b); its trail is what the page's own address returns at them
 latest=result; tileSlots=[];
 for(const id of ['heatmaps','kpis','tables','charts','metrics','trace'])$(id).replaceChildren();   // the open card shows one answer's evidence
 renderQueries(result);   // the query box: first below the title line, built in the first paint (#40)
 const kpiEntry=result.trace.find(e=>e.ok&&(e.tool==='get_period_kpis'||e.tool==='compare_periods'));
 if(kpiEntry)renderKpiTiles(kpiEntry.data,`${kpiEntry.tool} · this question`);
 const shiftKpis=result.trace.filter(e=>e.ok&&(e.tool==='get_shift_kpis'||e.tool==='get_shift_overview'||e.tool==='get_period_report'));
 for(const entry of shiftKpis)shiftTiles(entry,result,shiftKpis.length>1);
 for(const table of result.tables||[])showTable(table);
 for(const chart of result.charts)if(!(costCfg()&&/^\$ (this shift|per year)$/.test(chart.unit||'')))showChart(chart);   // a money tool's own chart is the opportunities panel (#31b): the same rows, ranked, with their formulas and a table
 if(!slots)afterPaint(()=>{if(latest!==result)return;result.trace.forEach((entry,i)=>{const row=traceRow(entry);row.id=`step-${i}`;$('trace').append(row);});});   // a newer answer already replaced the evidence: skip. An earlier card drawn into its own containers has no trail (#86)
 $('calls').textContent=`${result.status?result.status+' · ':''}${usageText(result.usage)}`;
 const ctx=node('details',undefined,'trace-context');ctx.append(node('summary','Context carried to the next question'));ctx.append(node('pre',JSON.stringify(result.context,null,2)));$('context').replaceChildren(ctx);
 updateScope();
}

// ---- Money on the card (issue #31b). The cost model prices the records (src/investigator/cost_model.py); this layer
// puts the dollars on every card and re-prices them in this browser when a visitor changes a rate:
//  - a dollar line under the headline: ≈ $7,587 recorded losses this shift · $5.72M/yr at this rate, a chip per bucket;
//  - the opportunities panel: bars of $/yr by lever, ranked, each bar's formula on hover, and a table flip;
//  - the arithmetic at the bottom: waste sq ft → MSF → paper $; machine minutes → MSF not made → $; trim points → $/yr; the total;
//  - a dollar line on every KPI tile that measures a loss, dollars on every priced bar, and the units toggle minutes · % · $;
//  - the rates panel: each rate with its source; an edit re-prices every card at once, with no request, and rides in the
//    address as rates=, so the link reproduces the page.
// A card's money is its own get_shift_dollars / get_opportunities when it asked one, else the money answer of its scope
// (one GET per scope, shared by the cards of that scope): measure=dollars on the Day grain, measure=money on a window.
// Every dollar is a tool row's quantity times a rate through money.js, the server's formula line for line.
let rateOverrides={}, units='usd', primed=null;
const UNITS=[['min','minutes'],['pct','%'],['usd','$']], UNIT_WORDS={min:'machine minutes',pct:'% of the window',usd:'dollars'};
const companions=new Map();   // a scope's money address -> the promise of its answer
const MONEY_KEY='csi-money';
function costCfg(){ return config&&config.cost_model&&typeof Money!=='undefined'?config.cost_model:null; }
function ratesNow(){ const cfg=costCfg(); return cfg?Money.effective(cfg,rateOverrides):null; }
function ratesKey(){ const cfg=costCfg(); return cfg?Money.format(rateOverrides,cfg):''; }
function unitMode(){ return costCfg()?units:null; }
function askedRates(rec){ const cfg=costCfg(), url=(rec&&(rec.url||rec.askUrl))||''; if(!cfg)return ''; try{ return Money.format(Money.parse(paramOf(url,'rates')||'',cfg),cfg); }catch(e){ return ''; } }
// An answer at the rates in force: verbatim when they are the rates it was priced at; else every priced query re-priced,
// the prose written again from those rows (Money.repriceSections), and the charts drawn from them.
function pricedView(result,asked){
 const cfg=costCfg(), key=ratesKey(), same={result,sections:result?result.sections||[]:[],narrative:result&&result.narrative,repriced:false};
 if(!cfg||!result||asked===key)return same;
 const rates=ratesNow(), trace=result.trace||[], tools=Object.fromEntries(trace.filter(e=>e.result_id).map(e=>[e.result_id,e.tool]));
 const steps=trace.map(e=>e.ok&&e.tool!=='render_chart'&&Money.priced(e.data)?{tool:e.tool,before:e.data,after:Money.reprice(e.tool,e.data,rates,cfg)}:null);
 if(!steps.some(Boolean))return same;
 const sections=Money.repriceSections(result.sections||[],steps,rates,cfg);
 const out={...result,sections,trace:trace.map((e,i)=>steps[i]?{...e,data:steps[i].after}:e.ok&&e.tool==='render_chart'&&Money.priced(e.data)?{...e,data:Money.repriceChart(e.data,tools[e.data.source_result_id],rates,cfg)}:e),charts:(result.charts||[]).map(c=>Money.repriceChart(c,tools[c.source_result_id],rates,cfg)),
  tables:(result.tables||[]).map(t=>Money.repriceChart(t,t.title,rates,cfg))};   // a table is named by its tool (get_speed_opportunity carries dollars)
 if(typeof result.report_markdown==='string')out.report_markdown=[`# Synthetic production investigation: ${result.period_label||result.production_day}`,`${result.mode}. No email delivery.`,...sections,result.limitations].join('\n\n');   // server._markdown, at these rates
 return {result:out,sections,narrative:result.narrative,repriced:true};   // a model's interpretation is not rewritten: see paintAnalyst
}
function viewOf(rec){ const key=ratesKey(); if(!rec.view||rec.view.key!==key||rec.view.of!==rec.result)rec.view={key,of:rec.result,...pricedView(rec.result,askedRates(rec))}; return rec.view; }
// ---- the card's money: its own money queries, or its scope's money answer
// The money answer of the scope the card's answer ran over, never the rail's when the answer says otherwise: a question
// can name its own window ("How did September go versus August?" under Day is about September) or its own day and shift
// (#105), and an analyst's answer has no address of its own (the #31b review), so its queries decide: the first
// windowed query, else the day queries' production_day and shifts (one shift; two of the three as shifts=; else all
// shifts), else the answer's context.
function companionUrl(rec){
 const s=rec.scope, trace=(rec.result&&rec.result.trace)||[], step=trace.find(e=>e.ok&&e.data&&e.data.window&&e.data.window.period&&e.data.window.start);
 let grain=GRAIN[s.period]||'day', day=s.day, shift=s.scope==='shift'?s.shift:null, pair=null;
 const own=rec.url&&CANONICAL.exec(pathOf(rec.url));   // the answer's own address
 const daily=trace.filter(e=>e.ok&&e.tool!=='render_chart'&&e.data&&typeof e.data.production_day==='string'&&!(e.data.window&&e.data.window.period)), ctx=rec.result&&rec.result.context;
 if(own){ grain=own[1]; day=own[2]; shift=own[3]?Number(own[3]):null; const named=!own[3]&&paramOf(rec.url,'shifts'); if(named&&/^[123](,[123])?$/.test(named))pair=named.split(',').map(Number).sort().join(','); }
 else if(daily.length){
  const shifts=[...new Set(daily.map(e=>e.data.shift))], numbered=shifts.filter(x=>typeof x==='number').sort();
  grain='day'; day=daily[0].data.production_day;
  shift=shifts.length===1&&numbered.length===1?numbered[0]:null;
  if(numbered.length===2&&shifts.length===2)pair=numbered.join(',');   // two of the three shifts: their money, not the whole day's
 }
 else if(ctx&&typeof ctx.production_day==='string'&&!step){ grain='day'; day=ctx.production_day; shift=ctx.scope==='shift'&&typeof ctx.shift==='number'?ctx.shift:null; }
 if(step){
  const w=step.data.window, days=storeDays()||[], first=days.find(d=>d>=w.start&&d<=w.end);
  if(first){ grain={day:'day',week:'week',month:'month',fiscal:'period'}[w.period]||grain; day=days.includes(w.anchor)?w.anchor:first; shift=typeof step.data.shift==='number'?step.data.shift:null; }
 }
 return `/corr/${machine()}/${grain}/${day}`+(shift?`/shift/${shift}`:'')+`.json?measure=${grain==='day'?'dollars':'money'}`+(pair&&grain==='day'&&!shift&&!step?`&shifts=${pair}`:'')+(rec.corrupt?'&corrupt=1':'');
}
function moneyOf(rec){
 if(!costCfg()||!rec||rec.state!=='done'||!rec.result)return null;
 const m=rec.money;
 if(m&&!(m.state==='error'&&clock()-m.at>15000))return m;   // a failed money answer is asked again on a later paint, not in a loop
 if(moneyStep(rec.result.trace||[]))return rec.money={state:'ready',own:true,result:rec.result,asked:askedRates(rec),url:rec.url};
 const url=companionUrl(rec), fresh=rec.money={state:'pending',own:false,result:null,asked:'',url:url.replace('.json','')};
 let p=companions.get(url);
 // The money request goes out after the card's first paint (afterPaint: two frames), so it never competes with the
 // offline answer it follows; the promise is shared at once, so the cards of one scope ask once.
 if(!p){ p=new Promise(go=>afterPaint(()=>go(fetch(url,{headers:{Accept:'application/json'}})))).then(async r=>{ const j=await r.json(); if(!r.ok)throw new Error(j.error||'The money answer is unavailable'); return j; }); companions.set(url,p); p.catch(()=>{ if(companions.get(url)===p)companions.delete(url); }); }
 p.then(result=>{ fresh.state='ready'; fresh.result=result; moneyLanded(rec); },error=>{ fresh.state='error'; fresh.error=error.message; fresh.at=clock(); moneyLanded(rec); });
 return fresh;
}
function moneyLanded(rec){ if(!thread.includes(rec))return; if(openId===rec.id&&shownId===rec.id)paintMoney(rec); else renderEarlier(); }
function bucketOf(row){
 const kind=(row.price_terms&&row.price_terms.kind)||row.kind, name=String(row.gap||row.lever||row.category||row.label||row.reason||'');
 if(row.kind==='setup')return 'Setups';
 if(kind==='downtime')return 'Stops'; if(kind==='speed')return 'Speed'; if(kind==='trim')return 'Trim';
 if(/^shear/i.test(name))return 'Shear'; if(/^dry-end/i.test(name))return 'Dry-end'; return 'Other waste';
}
const WHEN={day:'this day',week:'this week',month:'this month',fiscal:'this 4-4-5 period'};
// The one money query a card's money is read from: the day's get_day_dollars when the answer has it (an all-shifts day),
// else its only get_shift_dollars or get_opportunities. Several money queries and no day total (an analyst's plan over
// two shifts) are not added up here: the card takes its scope's money answer instead (moneyOf).
function moneyStep(trace){
 const steps=trace.filter(e=>e.ok&&Money.MONEY_TOOLS.has(e.tool));
 return steps.find(e=>e.tool==='get_day_dollars')||(steps.length===1?steps[0]:null);
}
// The money of a card in one shape for its line, panel and arithmetic. Every figure is a field of the money query
// (cost_model.summary, trim_over_target, _totals; get_day_dollars for an all-shifts day), re-priced as a whole at the
// rates in force; nothing is summed here (the #31b review: no figure is the browser's own).
function moneyFacts(m){
 const cfg=costCfg(), rates=ratesNow(), e=moneyStep(m.result.trace||[]);
 if(!e)return null;
 const d=atRates(m,e), levers=e.tool==='get_opportunities', day=e.tool==='get_day_dollars';
 const excluded=(d.excluded_records||[]).length, dropped=day?d.excluded_shifts||[]:[];
 let warning=null;
 if(dropped.length)warning=`Shift ${dropped.join(' and ')}: data incomplete, not counted in these dollars; they are the complete shifts'.`;
 else if(d.coverage!=='complete')warning=`Incomplete data: ${excluded||'some'} excluded record${excluded===1?'':'s'}. These dollars are the observed records only, not the whole ${levers?'window':day?'day':'shift'}; do not rank them.`;
 const rows=d.rows.map(r=>({name:r.gap||r.lever,bucket:bucketOf(r),usd:r.usd||0,year:r.usd_per_year,minutes:r.machine_minutes||0,share:Money.share(r),
  area:!!(r.price_terms&&Money.PAPER.has(r.price_terms.kind)),control:r.control||r.apart||null,apart:!!r.apart,trim:r.kind==='trim',rows:[r]}));
 const counted=day?(d.shifts||[]).filter(x=>!dropped.includes(x.shift)).map(x=>x.shift):[];
 // each counted shift's own total a year: its get_shift_dollars step beside the day total, re-priced with it
 const own=day?counted.map(n=>(m.result.trace||[]).find(x=>x.ok&&x.tool==='get_shift_dollars'&&x.data&&x.data.shift===n&&x.data.production_day===d.production_day)):[];
 const perShift=day&&own.length&&own.every(Boolean)?own.map(x=>`shift ${x.data.shift} ${Money.short(atRates(m,x).total_usd_per_year)}/yr`).join(' · '):null;
 return {levers,day,data:d,rows,cfg,rates,warning,lens:levers?d.lens||'all':null,
  total:d.total_usd, year:d.total_usd_per_year, paper:d.total_paper_usd, machine:d.total_machine_time_usd, contribution:d.total_contribution_usd,
  apart:d.apart_gaps||0, allIn:d.all_in_usd, allInYear:d.all_in_usd_per_year,   // the all-in total with the gaps apart (the owner, PR #111)
  buckets:(d.buckets||[]).map(b=>[b.bucket,b.usd]),
  when:levers?WHEN[d.window&&d.window.period]||'this window':day?((d.shifts||[]).length===3?'this day':'these shifts'):'this shift',
  who:levers?(d.shift?`Shift ${d.shift}`:'All shifts'):day?(counted.length===3?'All shifts':`Shift${counted.length===1?'':'s'} ${counted.join(' and ')}`):`Shift ${d.shift}`,
  label:levers?(d.window&&d.window.label)||'':(d.production_day||''),
  perShift};
}
// The slot under the headline the dollar line fills (an analyst's card: the top of its interpretation).
function dollarSlot(summary){
 if(!costCfg()||!summary)return null;
 const slot=node('div',undefined,'dollar-line'), kids=summary.children?[...summary.children]:[], head=kids.find(k=>/\bheadline\b/.test(k.className||''));
 if(head&&typeof head.after==='function')head.after(slot);
 else if(head)summary.children.splice(summary.children.indexOf(head)+1,0,slot);
 else if(typeof summary.prepend==='function')summary.prepend(slot); else summary.children.unshift(slot);
 return slot;
}
function paintMoney(rec){
 const m=moneyOf(rec), line=rec.lineEl, panel=$('money'), ledger=$('ledger');
 panel.replaceChildren(); ledger.replaceChildren(); if(line)line.replaceChildren();
 paintTiles(rec,m);
 if(!m)return;
 if(m.state!=='ready'){ if(line)line.append(node('p',m.state==='pending'?'Pricing this card at your rates…':`No dollars for this card: ${m.error}`,'muted dollar-wait')); return; }
 const f=moneyFacts(m); if(!f)return;
 if(line)fillDollarLine(line,f);
 panel.append(opportunityPanel(f,m,rec));
 ledger.append(...arithmetic(f));
}
function fillDollarLine(line,f){
 if(f.warning)line.append(node('p',f.warning,'warning-note'));
 const lead=node('p',undefined,'dollar-lead');
 lead.append(node('span','≈ '),node('strong',Money.money(f.total),'figure'),node('span',` ${f.levers?'in levers':'recorded losses'} ${f.when}`));
 if(f.year!==null&&f.year!==undefined)lead.append(node('span',' · '),node('strong',`${Money.short(f.year)}/yr`,'figure'),node('span',f.when==='this day'?' at this day\'s rate':' at this rate'));
 // the card states both totals (the owner, PR #111): the recorded losses, and all in with planned maintenance and an outage
 if(f.apart)lead.append(node('span',` · ${Money.money(f.allIn)}${f.allInYear!==null&&f.allInYear!==undefined?` (${Money.short(f.allInYear)}/yr)`:''} all in, with the schedule-driven and external downtime`,'all-in'));
 const margin=f.rates.values.margin_usd_msf;   // the line says plainly whether contribution is priced
 lead.append(node('span',margin===null||margin===undefined?` · contribution: ${f.cfg.margin_not_set}`:` · contribution ${Money.money(f.contribution)} at $${Money.rateText(margin)}/MSF, not added`,'contrib'));
 const chips=node('p',undefined,'chips');
 for(const [bucket,usd] of f.buckets.filter(([,usd])=>Money.money(usd)!=='$0')){ const c=node('span',undefined,'chip'); c.append(node('span',bucket+' '),node('strong',Money.money(usd))); c.title=`${bucket}: ${Money.money(usd)} ${f.when}, priced at your rates`; chips.append(c); }
 line.append(lead,chips);
}
function unitValue(r,mode,yearly){ return mode==='min'?r.minutes:mode==='pct'?r.share:yearly&&r.year!==null&&r.year!==undefined?r.year:r.usd; }
function unitLabel(r,mode,yearly){
 const v=unitValue(r,mode,yearly);
 if(v===null||v===undefined)return 'unavailable';
 if(mode==='min')return `${Money.fmt(v,1)} min`;
 if(mode==='pct')return `${Money.fixed(v,2)}% of ${r.area?'gross':'time'}`;
 const year=r.year!==null&&r.year!==undefined;
 return yearly&&year?`${Money.short(r.year)}/yr`:Money.money(r.usd)+(year?` · ${Money.short(r.year)}/yr`:'');
}
function rowFormula(r){ return r.rows[0].usd_basis; }
function rowRecords(r){ const x=r.rows[0]; return x.records||(Array.isArray(x.shifts)?`shift${x.shifts.length===1?'':'s'} ${x.shifts.join(', ')}`:Array.isArray(x.source_ids)?`${x.source_ids.length} records`:''); }
const PANEL_ROWS=8;
// The lens orders the levers before the unit does (issue #101), as the server ranks them: under the supervisor lens the
// controllable levers come first, the schedule-driven ones (side trim, planned maintenance) after them, unranked; under
// the ownership lens side trim, all of it, is first. Within those, the unit shown ranks them.
const APART={'schedule-driven':0,external:1};   // cost_model.APART: what the supervisor lens lists apart, in this order
function lensOrder(rows,lens){
 const apart=r=>r.control in APART?APART[r.control]:2;
 if(lens==='supervisor')return [...rows.filter(r=>r.control==='controllable'),...rows.filter(r=>r.control!=='controllable').sort((a,b)=>apart(a)-apart(b))];
 if(lens==='ownership'){ const i=rows.findIndex(r=>r.trim); return i<0?rows:[rows[i],...rows.filter((_,j)=>j!==i)]; }
 return rows;
}
// The opportunities panel: the card's levers (or gaps) ranked by the unit shown, $/yr in dollars, with the rate and
// formula of each bar on hover and the same rows as a table behind a flip.
function opportunityPanel(f,m,rec){
 const mode=unitMode()||'usd', yearly=true, wrap=node('div',undefined,'chart money-panel');   // every money query has its rows' own $/yr
 wrap.append(node('h3',`${f.levers?'Where the money is':'Where the money went'} · ${f.who} · ${f.label}`));
 wrap.append(node('p',(f.levers?(f.lens==='ownership'?'The total cost of the waste recorded at your rates, side trim first. ':
  f.lens==='supervisor'?'The controllable losses at your rates, ranked; the schedule-driven ones (side trim, planned maintenance), then the external ones (a power outage), after them, unranked. ':
  'Each lever is a gap priced at your rates: the speed of each run under its grade\'s trailing-year speed, side trim over the target, each recorded stop, all shear and dry-end waste. '):f.day?'Each gap of the day, its shifts\' records summed and priced once on the day, at your rates. ':'Each gap of the shift priced at your rates. ')+`${mode==='usd'?'Ranked by dollars a year at this rate':'Ranked by '+UNIT_WORDS[mode]}. Where to look, not what to change.`,'muted'));
 const box=node('div',undefined,'money-box'); wrap.append(box);
 let table=false, all=false;
 const draw=()=>{
  box.replaceChildren();
  const ranked=lensOrder([...f.rows].sort((a,b)=>(unitValue(b,mode,yearly)??-1)-(unitValue(a,mode,yearly)??-1)||(a.name<b.name?-1:a.name>b.name?1:0)),f.lens).sort((a,b)=>a.apart-b.apart);   // a money card's gaps apart go last (PR #111); a stable sort
  const shown=all?ranked:ranked.slice(0,PANEL_ROWS);
  if(table)box.append(moneyTable(ranked,f));
  else box.append(moneyBars(shown,mode,yearly,true));
  const actions=node('div',undefined,'money-actions');
  const flip=node('button',table?'Bars':'Table','secondary'); flip.type='button'; flip.setAttribute('aria-pressed',String(table)); flip.title='The same rows as a table'; flip.addEventListener('click',()=>{table=!table;draw();}); actions.append(flip);
  if(!table&&ranked.length>PANEL_ROWS){ const more=node('button',all?`Top ${PANEL_ROWS}`:`All ${ranked.length}`,'secondary'); more.type='button'; more.addEventListener('click',()=>{all=!all;draw();}); actions.append(more); }
  box.append(actions);
 };
 draw();
 const src=node('p',undefined,'muted money-src');
 const tool=f.levers?'get_opportunities':f.day?'get_day_dollars':'get_shift_dollars';
 if(m.own){ const step=moneyStep(rec.result.trace||[]), link=step?queryLink(step.result_id):null; src.append(`From ${tool}, this answer's own query`); if(link){src.append(' · ');src.append(link);} }
 else{ const a=node('a',f.levers?'Where is the money?':'Where did the money go?','look-q'); a.href=withRates(m.url); a.title='The money answer of this scope: its queries and its prose'; a.addEventListener('click',event=>{ if(event&&(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||(event.button&&event.button!==0)))return; if(event&&event.preventDefault)event.preventDefault(); ask('Where is the money?',{mode:'offline',title:'Where is the money?',scope:rec.scope,corrupt:rec.corrupt,source:rec}).catch(()=>{}); }); src.append(`From ${tool} over this card's scope · `,a); }
 wrap.append(src);
 return wrap;
}
function moneyBars(rows,mode,yearly,wide=false){
 // The panel spans the card, so its bars get a label column wide enough for "Downtime: Knife / slitter jam"; a priced
 // chart in the two-up grid keeps the grid's frame.
 const W=wide?(CHART_W()<450?420:600):CHART_W(), f=wide?{...frame(W,rows.length,28,8,true),...(W>=600?{labelW:214,x0:222,chars:30}:{})}:frame(W,rows.length,28,8,true), max=niceCeil(Math.max(0,...rows.map(r=>Number(unitValue(r,mode,yearly))||0)));
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`levers ranked by ${UNIT_WORDS[mode]}`});
 xAxis(svg,f.x0,f.x1,f.top,f.top+rows.length*28,max,mode,mode==='usd'?v=>Money.short(v):undefined);
 rows.forEach((r,i)=>{ const y=f.top+i*28, v=Number(unitValue(r,mode,yearly))||0;
  labelText(svg,r.name,f.x0-8,y+18,f.chars);
  svg.append(barPath(f.x0,y+6,(v/max)*(f.x1-f.x0),15,'bar priced'));
  const detail=[`${r.name}: ${Money.money(r.usd)}${r.year!==null&&r.year!==undefined?` · ${Money.money(r.year)}/yr at this rate`:''}${r.minutes?` · ${Money.fmt(r.minutes,1)} machine min`:''}${r.control?` · ${r.control}`:''}`,rowFormula(r),rowRecords(r)].filter(Boolean).join('. ')+'.';
  const t=svgNode('text',{x:f.x1+8,y:y+18,tabindex:0,'aria-label':detail,class:'val'},unitLabel(r,mode,yearly)); t.append(svgNode('title',{},detail)); svg.append(t); });
 return svg;
}
function moneyTable(rows,f){
 const wrap=node('div',undefined,'table-scroll'); wrap.tabIndex=0; wrap.setAttribute('role','region'); wrap.setAttribute('aria-label','Levers as a table');
 const table=node('table',undefined,'money-table'), head=node('thead'), tr=node('tr');
 for(const h of [f.levers?'Lever':'Gap','$ '+f.when,'$/yr','Machine min','Share','How it is priced']){ const th=node('th',h); th.scope='col'; tr.append(th); }
 head.append(tr); table.append(head); const body=node('tbody');
 for(const r of rows){ const row=node('tr'); for(const cell of [r.name,Money.money(r.usd),r.year===null||r.year===undefined?'unavailable':Money.money(r.year),Money.fmt(r.minutes,2),r.share===null?'unavailable':`${Money.fixed(r.share,2)}% of ${r.area?'gross':'time'}`,`${rowFormula(r)} · ${rowRecords(r)}`])row.append(node('td',cell)); body.append(row); }
 table.append(body); wrap.append(table); return wrap;
}
// The arithmetic, as the post closes: four short lines with this card's own numbers, each result in bold.
function arithmetic(f){
 const cfg=f.cfg, C=cfg.constants, v=f.rates.values, paper=Money.rateText(v.paper_usd_msf), hour=Money.rateText(v.machine_usd_hour,true);
 const line=(lead,parts)=>{ const p=node('p'); p.append(node('span',lead,'ledger-k')); for(const x of parts)p.append(typeof x==='string'?node('span',x):x); return p; };
 const b=text=>node('strong',text,'figure');
 const d=f.data;   // the money query's own sums (cost_model.summary): the area priced as paper, the machine minutes by kind, the MSF not made
 const out=[node('h3','The arithmetic, at your rates','label')];
 if(f.warning)out.push(node('p',f.warning,'warning-note'));
 out.push(line('Paper ',[`${Money.fmt(d.area_sqft,0)} sq ft of ${f.levers?'trim over target, shear and rejects':'trim, shear, rejects and waste boards'} = `,b(`${Money.fmt(d.area_msf,1)} MSF`),` × $${paper}/MSF = `,b(Money.money(f.paper))]));
 out.push(line('Machine time ',[`${Money.fmt(d.machine_minutes,1)} min (stops ${Money.fmt(d.stop_minutes,1)} · speed gap ${Money.fmt(d.speed_minutes,1)} · waste run time ${Money.fmt(d.waste_run_minutes,1)}) = `,b(`${Money.fmt(d.msf_not_made,1)} MSF not made`),` at ${Money.gText(C.required_fpm)} ft/min (${Money.gText(C.msf_per_minute)} MSF a minute) × $${hour}/hr = `,b(Money.money(f.machine))]));
 out.push(trimLine(f,line,b));
 const total=[`= `,b(Money.money(f.total)),` ${f.when}`];
 if(f.year!==null&&f.year!==undefined)total.push(' · ',b(`${Money.money(f.year)}/yr`),f.when==='this day'?' at this day\'s rate':' at this rate');
 if(f.perShift)total.push(` (each shift at its own rate: ${f.perShift})`);
 total.push(v.margin_usd_msf===null||v.margin_usd_msf===undefined?` · contribution: ${cfg.margin_not_set}`:` · contribution ${Money.money(f.contribution)} at $${Money.rateText(v.margin_usd_msf)}/MSF, beside the losses, not added`);
 out.push(line('Total ',total));
 const prov=node('p',`${Money.provenance(f.rates,cfg)} ${cfg.note}`,'muted provenance'); out.push(prov);
 const change=node('button','Change rates','secondary rates-open'); change.type='button'; change.addEventListener('click',()=>openRates(true)); out.push(change);
 return out;
}
function trimLine(f,line,b){
 const v=f.rates.values, target=Money.rateText(v.trim_target_pct), cfg=f.cfg, C=cfg.constants;
 if(f.levers){
  const r=f.data.rows.find(x=>x.kind==='trim'); if(!r)return line('Side trim ',['no trim lever in this window']);
  const pct=r.price_terms.trim_pct;
  if(!r.quantity)return line('Side trim ',[`${Money.fixed(pct,2)}% of gross, at or under the ${target}% target: `,b('no trim lever'),' this window']);
  return line('Side trim ',[`${Money.fixed(pct,2)}% of gross − ${target}% target = `,b(`${Money.fixed(r.quantity,2)} points over`),' → ',b(`${Money.money(r.usd_per_year)}/yr`),` at the post's volume (points / 100 × ${Money.group(String(C.annual_msf_per_machine))} MSF a year × $${Money.rateText(v.paper_usd_msf)}/MSF)`]);
 }
 const d=f.data; if(d.trim_pct_of_gross===null||d.trim_pct_of_gross===undefined)return line('Side trim ',['no side trim recorded']);   // trim_over_target, the server's
 if(!d.trim_points_over)return line('Side trim ',[`${Money.fixed(d.trim_pct_of_gross,2)}% of gross, at or under the ${target}% target: `,b('no trim lever')]);
 return line('Side trim ',[`${Money.fixed(d.trim_pct_of_gross,2)}% of gross − ${target}% target = `,b(`${Money.fixed(d.trim_points_over,2)} points over`),' → ',b(`${Money.money(d.trim_over_usd_per_year)}/yr`),` at the post's volume (points / 100 × ${Money.group(String(C.annual_msf_per_machine))} MSF a year × $${Money.rateText(v.paper_usd_msf)}/MSF)`]);
}
// ---- dollars on the tiles: the tool's own tile_money (cost_model.shift_tiles / period_tiles), one entry a tile, each the
// pricing calls of the rows it agrees with (the waste breakdown's category, the Pareto's reasons, get_shift_dollars' gaps).
// The step is re-priced with the rest of the answer (pricedView), so a tile never prices a rounded display value.
function tileOf(data,metric){ if(!costCfg()||!data||!Array.isArray(data.tile_money))return null; return data.tile_money.find(t=>(t.metrics||[t.metric]).includes(metric))||null; }
// A money query at the rates in force: verbatim at the rates it was asked at, else re-priced as a whole.
function atRates(m,e){ const cfg=costCfg(), key=ratesKey(); return m.asked===key?e.data:Money.reprice(e.tool,e.data,ratesNow(),cfg); }
// The shift tiles' dollars: the card's money answer's get_shift_dollars for the tile's own day and shift (its tile_money,
// the calls its rows make), so a tile and its chip agree to the cent. Drawn again on every paint, from the tile as first drawn.
function paintTiles(rec,m){
 for(const slot of rec.tiles||[]){
  slot.v.replaceChildren(...slot.kids); slot.line.textContent=''; slot.line.title='';
  if(!m||m.state!=='ready'||!m.result)continue;
  const e=(m.result.trace||[]).find(x=>x.ok&&x.tool==='get_shift_dollars'&&x.data&&x.data.production_day===slot.day&&x.data.shift===slot.shift);
  if(e)moneyOnTile(slot.card,slot.v,slot.native,tileOf(atRates(m,e),slot.metric),'this shift',slot.line);
 }
}
// A tile's money line, and in minutes or dollars its value shown in that unit (the tile's own reading moves to the line).
function moneyOnTile(tile,valueEl,native,priced,when,slot=null){
 if(!priced||priced.usd===null)return;
 const mode=unitMode(), year=priced.usd_per_year===null?null:`${Money.short(priced.usd_per_year)}/yr`;
 let text=[`${Money.money(priced.usd)} ${when}`,year].filter(Boolean).join(' · ');
 if(mode==='usd'){ valueEl.replaceChildren(node('span',Money.money(priced.usd))); text=[year,native].filter(Boolean).join(' · '); }
 else if(mode==='min'){ valueEl.replaceChildren(node('span',Money.fmt(priced.machine_minutes,1)),node('small','min')); text=[Money.money(priced.usd),year,native].filter(Boolean).join(' · '); }
 const line=slot||node('div',text,'usd'); line.textContent=text; line.title=`${priced.what}: ${priced.usd_basis}`; if(!slot)tile.append(line);
}
// ---- the charts in the unit shown: a priced chart in another unit than its own is drawn as ranked bars of that unit
function chartUnits(chart){ const mode=unitMode(); if(!mode||!Money.priced(chart))return null; const u=String(chart.unit||''); if(mode==='min'&&u==='minutes'||mode==='pct'&&u==='% of gross')return null; return mode; }
function pricedChart(chart,mode,wrap){
 const yearly=chart.y==='usd_per_year', label=r=>r.weekday?`${r.weekday} ${shortDay(r[chart.x])}`:String(r[chart.x]??'');
 const rows=chart.rows.filter(r=>r.price_terms).map(r=>({name:label(r),usd:r.usd,year:r.usd_per_year,minutes:r.machine_minutes,share:Money.share(r),area:Money.PAPER.has(r.price_terms.kind),bucket:bucketOf(r),rows:[r]}));
 rows.sort((a,b)=>(unitValue(b,mode,yearly)??-1)-(unitValue(a,mode,yearly)??-1));
 wrap.append(node('p',`${mode==='usd'?'Dollars at your rates':UNIT_WORDS[mode][0].toUpperCase()+UNIT_WORDS[mode].slice(1)}, ranked${mode==='usd'?' by dollars':''}: each bar is the row's own priced quantity${chart.y2?'; the previous window is not priced':''}. Show as % for the chart in its own unit.`,'muted'));
 wrap.append(moneyBars(rows,mode,yearly));
}
// ---- the rail: Show as, the rates and the editor
function renderUnits(){
 const box=$('units-seg'); if(!box)return; box.replaceChildren(); const cfg=costCfg(); $('rates-panel').hidden=!cfg; if(!cfg)return;
 for(const [value,label] of UNITS){ const b=node('button',label,'seg-btn'); b.type='button'; b.dataset.value=value; b.setAttribute('aria-pressed',String(units===value)); b.title=`Show every priced tile and bar in ${UNIT_WORDS[value]}`; b.addEventListener('click',()=>setUnits(value)); box.append(b); }
}
function setUnits(value){ if(units===value||!UNITS.some(u=>u[0]===value))return; units=value; saveMoney(); renderUnits(); repaintMoney(); }
function sourceText(s){ return s==='yours'?'yours':s==='server'?'this server':s; }
function renderRates(){
 const cfg=costCfg(); if(!cfg)return; const rates=ratesNow(), list=$('rates-list'); list.replaceChildren();
 $('rates-reset').textContent=cfg.rates.some(r=>r.source==='server')?"Reset to this server's rates":"Reset to the post's rates";   // clearing your rates returns to the server's, which INVESTIGATOR_RATES may set
 for(const r of cfg.rates){ const v=rates.values[r.id], dd=node('dd',undefined,v===null||v===undefined?'unset':rates.sources[r.id]==='yours'?'yours':undefined);
  dd.append(node('span',v===null||v===undefined?'not set':r.unit==='$/MSF'?`$${Money.rateText(v)}/MSF`:r.unit==='$/hr'?`$${Money.rateText(v,true)}/hr`:`${Money.rateText(v)}%`),node('small',sourceText(rates.sources[r.id]),'src'));
  dd.title=r.note; const dt=node('dt',{paper_usd_msf:'Paper',machine_usd_hour:'Machine',margin_usd_msf:'Margin',trim_target_pct:'Trim'}[r.id]||r.label); dt.title=r.label; list.append(dt,dd); }
 $('rates-note').textContent=ratesKey()?'Assumed; yours ride in the link.':'Assumed, not a plant\'s.';
}
function renderRateFields(){
 const cfg=costCfg(), box=$('rates-fields'); if(!cfg||!box)return; box.replaceChildren(); const rates=ratesNow();
 for(const r of cfg.rates){
  const field=node('label',undefined,'field rate-field'), head=node('span',undefined,'field-label'); head.append(node('span',`${r.label} (${r.unit})`),node('small',r.default===null?'default: not set':`default ${Money.rateText(r.default,r.unit==='$/hr')} · ${r.default_source}`,'src'));
  const input=node('input'); input.type='text'; input.inputMode='decimal'; input.autocomplete='off'; input.dataset.rate=r.id; input.value=rates.values[r.id]===null||rates.values[r.id]===undefined?'':String(rates.values[r.id]); input.placeholder=r.default===null?'not set':String(r.default);
  const why=node('span','','rate-error'); why.setAttribute('role','alert');
  input.addEventListener('input',()=>{ const msg=editRate(r,input.value); why.textContent=msg||''; input.setAttribute('aria-invalid',String(!!msg)); });
  field.append(head,input,why); field.title=r.note; box.append(field);
 }
}
// One rate typed: applied at once when it is a rate the server takes (the same rule), else the reason, and nothing changes.
function editRate(spec,text){
 const cfg=costCfg(), base=Money.base(cfg), value=String(text).trim();
 if(value===''){ delete rateOverrides[spec.id]; applyRates(); return null; }   // empty: back to the server's own rate (a margin back to not set)
 const why=Money.check(spec,value); if(why)return why;
 const n=Number(value); if(base.values[spec.id]===n)delete rateOverrides[spec.id]; else rateOverrides[spec.id]=n;
 applyRates(); return null;
}
function openRates(open){
 const form=$('rates-form'); form.hidden=!open; $('rates-change').setAttribute('aria-expanded',String(open)); $('rates-change').hidden=open;
 if(!open)return;
 renderRateFields();
 if(phoneWidth()&&!railOpen()){ $('rail').className='rail open'; $('rail-toggle').setAttribute('aria-expanded','true'); }   // on a phone the rates live in the rail sheet
 const input=($('rates-fields').children[0]||{children:[]}).children[1]; try{ if(input&&input.focus)input.focus(); }catch(e){}
}
function applyRates(){
 saveMoney(); renderRates();
 const here=currentAddress(); if(nav&&here&&CANONICAL.test(pathOf(here))){ const next=withRates(here); if(next!==here)nav.history.replaceState({url:next},'',next); }
 for(const b of chipButtons)chipHref(b);
 repaintMoney();
}
// Every card again at the rates and unit in force, from what it already holds: no request, no model.
function repaintMoney(){ renderCard(); paintPrimed(); }
function saveMoney(){ const store=tabStore(); if(!store)return; try{ store.setItem(MONEY_KEY,JSON.stringify({units})); }catch(e){} }
function loadMoney(address){
 const cfg=costCfg(); if(!cfg)return;
 const store=tabStore(); try{ const saved=JSON.parse((store&&store.getItem(MONEY_KEY))||'{}'); if(UNITS.some(u=>u[0]===saved.units))units=saved.units; }catch(e){}
 try{ rateOverrides=Money.parse((address&&CANONICAL.test(pathOf(address))&&paramOf(address,'rates'))||'',cfg); }catch(e){ rateOverrides={}; }   // a link's rates are the page's rates (#31b)
 const base=Money.base(cfg); for(const [id,v] of Object.entries(rateOverrides))if(base.values[id]===v)delete rateOverrides[id];
 renderUnits(); renderRates();
}

// ---- The thread of answer cards (issues #63, #86). One card is open: its title line, query box, summary, charts,
// tiles and exports fill the fixed containers of #card, at the bottom of the transcript, right above the ask bar.
// Earlier answers sit above it, newest nearest (in the DOM they follow #card, newest first; the stylesheet stacks
// them oldest at the top). Each keeps its whole card under a title line with an Open button: nothing is collapsed
// when a new question arrives, it only moves up. Submitting draws the open card as a skeleton in the same task as
// the click, and the answer fills that card in place; nothing on the page is disabled while a question is out. An
// offline answer's address is in the address bar (issue #20, restore() above); the thread survives a reload of the
// tab (sessionStorage, replayed offline: saveThread() below).
let thread=[], openId=null, seq=0, sessionEpoch=0;
let queue=Promise.resolve();   // questions go to the server one at a time, in the order asked, so each carries the session the one before it left
const latestRun=new Map();   // session id -> its latest run id: the only run /api/report can still print
const clock=()=>typeof performance!=='undefined'&&performance.now?performance.now():Date.now();
const msText=ms=>ms>=10000?`${(ms/1000).toFixed(1)} s`:`${Math.max(1,Math.round(ms))} ms`;
const IDLE_TEXT='Pick a question on the left or type one in the box below. Each answer lands as one card at the bottom of this thread: the headline and findings, the charts, where to look, the queries behind them and the KPI tiles; earlier answers move up.';
function scopeWho(s){ return s.scope==='day'?'All shifts':`Shift ${s.shift}`; }
function windowText(s){   // "Mon 14 Sep 2026"
 const d=utc(s.day); if(Number.isNaN(d.getTime()))return String(s.day);
 if(s.period==='day')return dayText(d);
 if(s.period==='week'){const [a,b]=weekOf(d);return `week of ${spanText(a,b)}`;}
 if(s.period==='month')return `${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
 return `4-4-5 period of ${dayText(d)}`;
}
function scopeLabel(s){ return `${scopeWho(s)} · ${windowText(s)}`; }   // "Shift 2 · Mon 14 Sep 2026"
// A question can name its own window or shift ("How did September go versus August?" with the rail on a day):
// the card then says what the first windowed query actually ran over, in the tool's own words. The date on the
// card is a number, so it comes from a tool result, not from the rail.
function railRange(s){
 const d=utc(s.day); if(Number.isNaN(d.getTime()))return null;
 if(s.period==='day')return [s.day,s.day];
 if(s.period==='week'){const [a,b]=weekOf(d);return [iso(a),iso(b)];}
 if(s.period==='month')return [iso(new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),1))),iso(new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)))];
 return null;
}
function answeredScope(s,result){
 const steps=(result.trace||[]).filter(e=>e.ok&&e.data), step=steps.find(e=>e.data.window&&e.data.window.label), out={who:null,window:null};
 if(!step){   // a day answer the question moved (#105: "third shift yesterday", "show shift 1"): the day and the one shift its daily tools read
  const days=[...new Set(steps.map(e=>e.data.production_day).filter(d=>typeof d==='string'))], shifts=[...new Set(steps.map(e=>e.data.shift).filter(n=>typeof n==='number'))];
  if(days.length===1&&(s.period!=='day'||days[0]!==s.day))out.window=windowText({...s,period:'day',day:days[0]});
  if(s.scope==='shift'&&shifts.length===1&&shifts[0]!==Number(s.shift))out.who=`Shift ${shifts[0]}`;
  return out;
 }
 const w=step.data.window, period=w.period==='fiscal'?'accounting':w.period, range=railRange(s);
 if(period!==s.period||(range?(w.start!==range[0]||w.end!==range[1]):w.anchor!==s.day))out.window=w.label;
 const who=step.data.shift===null?'All shifts':typeof step.data.shift==='number'?`Shift ${step.data.shift}`:null;
 if(who&&who!==scopeWho(s))out.who=who;
 return out;
}
function entryWho(rec){ return (rec.answered&&rec.answered.who)||scopeWho(rec.scope); }
function entryLabel(rec){ return `${entryWho(rec)} · ${(rec.answered&&rec.answered.window)||windowText(rec.scope)}`; }
function cardMeta(rec){   // Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · 93 ms, or … · analyst · 2 model calls · 1.8 s (#65)
 const analyst=!!PROVIDERS[rec.mode], parts=[entryLabel(rec),analyst?'analyst':rec.mode];
 if(rec.state==='pending')parts.push('asking…');
 else if(rec.state==='error')parts.push('failed');
 else if(analyst){const n=Number(rec.result.usage&&rec.result.usage.model_calls)||0;parts.push(`${n} model ${n===1?'call':'calls'}`);}
 else{const n=(rec.result.trace||[]).length;parts.push(`${n} ${n===1?'query':'queries'}`);}
 if(rec.ms!==null)parts.push(analyst&&rec.ms>=1000?`${(rec.ms/1000).toFixed(1)} s`:msText(rec.ms));   // a model round reads in seconds: analyst · 2 model calls · 1.8 s
 return parts.join(' · ');
}
function clearCard(){ if(pace)finishReveal(); for(const id of ['queries','messages','charts','money','look','heatmaps','kpis','metrics','ledger','tables','trace','context'])$(id).replaceChildren(); $('queries').className='queries'; $('calls').textContent=''; stepIndex=new Map(); }
function skeleton(){   // grey blocks where the headline and findings, the two charts and the one-line query box will land
 const m=node('div',undefined,'skel-block'); for(const w of ['w60','w90','w70','w70'])m.append(node('span',undefined,'skel '+w)); $('messages').append(m);
 const q=node('div',undefined,'skel-block'); q.append(node('span',undefined,'skel w45')); $('queries').append(q);
 $('charts').append(node('div',undefined,'chart skel-chart'),node('div',undefined,'chart skel-chart'));
}
// An earlier card (#86): its title line (title, who · ms, Open) and, once it has settled, its whole card beneath.
// The body is the open card's own nodes moved up when another card took its place (harvest), so nothing is drawn
// twice and open folds stay open; a card that settled while another was open is drawn once into its own containers
// (drawBody). Either way it is kept on the record and only redrawn when what it shows changes (bodyKey).
const BODY_SLOTS=['messages','charts','money','look','queries','heatmaps','kpis','metrics','ledger','tables'];   // in the card's order (#75: the answer, its charts, where to look, then the one-line query box; #31b: the money panel under the charts, the arithmetic under the tiles); the trail and the exports stay with the open card
const settled=rec=>rec.state==='done'||rec.state==='error';
function bodyKey(rec){ const m=moneyOf(rec); return `${rec.state}|${rec.analysed?1:0}|${analystMode()||''}|${m?m.state:''}|${ratesKey()}|${unitMode()||''}`; }   // what changes after a card settles: the analyst offer, its money landing (#31b), the rates and the unit
function entryOf(rec){
 if(rec.entry)return rec.entry;
 const line=node('article',undefined,'card earlier'), text=node('div',undefined,'earlier-text'), title=node('strong'), meta=node('span'), open=node('button','Open','secondary');
 open.type='button';open.title='Make this the open card at the bottom: its address, exports and the full result of each query';open.addEventListener('click',()=>{openCard(rec);focusCard();});
 text.append(title,meta);line.append(text,open);
 return rec.entry={line,text,title,meta,open,body:null,key:null};
}
function setBody(v,body,key){ v.body=body; v.key=key; v.line.replaceChildren(v.text,v.open,...(body?[body]:[])); }
// A card moved into the thread keeps its links: its query boxes' ids and the links to them get the card's own prefix,
// and "What came back" (the per-step data, which stays with the open card) is hidden; Open brings it back.
function relabel(el,prefix){
 for(const child of [...(el.children||[])]){
  if(typeof child.id==='string'&&/^query-\d+$/.test(child.id))child.id=prefix+child.id;
  const href=hrefOf(child); if(/^#query-\d+$/.test(href))child.setAttribute('href','#'+prefix+href.slice(1));
  if(typeof child.className==='string'&&/\bcame-back\b/.test(child.className))child.hidden=true;
  relabel(child,prefix);
 }
}
function bodyShell(){ const body=node('div',undefined,'entry-body'), own={}; for(const id of BODY_SLOTS){ own[id]=node('div',undefined,id); body.append(own[id]); } return {body,own}; }
function harvest(rec,key){
 if(pace)finishReveal();   // a card still revealing settles first: what moves up is the finished answer
 const {body,own}=bodyShell();
 for(const id of BODY_SLOTS)own[id].append(...$(id).children);
 own.queries.className=$('queries').className||'queries';   // the one-line query box stays one line when it moves up (#75)
 relabel(body,`card-${rec.id}-`);
 setBody(entryOf(rec),body,key);
}
function drawBody(rec){
 const {body,own}=bodyShell(), kept={latest,stepIndex};
 slots={...own,trace:node('div'),context:node('div'),calls:node('span')};   // the trail is drawn for the open card only
 try{ paintAnswer(rec); analystOffer(rec); feedbackOffer(rec); }
 finally{ slots=null; latest=kept.latest; stepIndex=kept.stepIndex; }
 relabel(body,`card-${rec.id}-`);
 return body;
}
// Keep the lines that stay where they are attached: re-inserting a drawn card would lose its place for scroll
// anchoring and restart its charts' animation. (The unit tests' DOM stub has no insertBefore and is simply replaced.)
function syncChildren(box,want){
 const have=[...box.children];
 if(have.length===want.length&&have.every((e,i)=>e===want[i]))return;
 if(typeof box.insertBefore!=='function'){box.replaceChildren(...want);return;}
 for(const e of have)if(!want.includes(e))e.remove();
 want.forEach((e,i)=>{ const at=box.children[i]; if(at!==e)box.insertBefore(e,at||null); });
}
function renderEarlier(){
 const lines=[];
 for(const rec of thread.filter(r=>r.id!==openId).sort((a,b)=>b.id-a.id)){
  const v=entryOf(rec);
  if(settled(rec)&&v.key!==bodyKey(rec))setBody(v,drawBody(rec),bodyKey(rec));
  v.line.className='card earlier'+(rec.state==='error'?' failed':'')+(v.body?' drawn':'');v.line.setAttribute('aria-label',`Earlier answer: ${rec.title}`);
  v.title.textContent=rec.title;v.meta.textContent=`${entryWho(rec)} · ${rec.state==='pending'?'asking…':rec.state==='error'?'failed':rec.state==='stored'?'not reloaded: Open loads it':msText(rec.ms)}`;
  v.open.setAttribute('aria-label',`Open the answer to: ${rec.title}`);
  lines.push(v.line);
 }
 syncChildren($('earlier'),lines);
}
// The ms on the title line is the wall time in this browser from submit to the rendered card; the tooltip splits
// out the server's own time in the queries.
function paintMeta(rec){
 const meta=$('card-meta'); meta.textContent=rec?cardMeta(rec):'';
 if(!rec||rec.state!=='done'||rec.ms===null){meta.title='';return;}
 const r=rec.result, server=typeof r.elapsed_ms==='number'?r.elapsed_ms:(r.trace||[]).reduce((sum,e)=>sum+(typeof e.elapsed_ms==='number'?e.elapsed_ms:0),0);
 meta.title=`${Math.round(rec.ms)} ms from submit to the rendered card in this browser; ${server.toFixed(0)} ms of it in the queries on the server. Planner: ${PROVIDERS[rec.mode]?PROVIDERS[rec.mode].name+' analyst, '+r.mode:r.mode}.`;
}
function syncPdf(){ if(latest)$('pdf').hidden=!latest.url&&latestRun.get(latest.session_id)!==latest.run_id; }   // an addressed answer prints from its address; a model's answer only while it is its session's latest run (re-checked when another lands)
// Opening a card: an addressed answer of the current scope puts its address in the bar and the conversation follows
// it, so the next question is a follow-up of the card on screen. A card asked under another scope keeps the rail's
// address: the rail, not the card, is what the address encodes.
function openCard(rec){ if(rec.state==='stored')replayOne(rec);   /* a card kept but not reloaded (#86) loads when it is opened */ openId=rec.id; renderCard(); scheduleFollow(true); if(rec.state==='done'&&rec.url&&rec.epoch===sessionEpoch){ followView(rec.url,!rec.report); pushAddress(rec.url); updateScope(); } }
function focusCard(){ try{$('card-title').focus?.({preventScroll:true});}catch(e){} }   // keyboard focus follows the card when the control that had it goes away; the pane's follow rule does the scrolling (#86)
// A settled answer's own content, into the card's containers: the open card's, or an earlier card's own (#86).
// Returns the summary the reveal paces, for an offline answer.
function paintAnswer(rec){
 if(rec.state==='error'){message('Could not complete',[rec.error],'error');if(PROVIDERS[rec.mode])message(null,['The analyst could not answer; no answer was fabricated. The offline answer stands.'],'muted');return null;}
 if(PROVIDERS[rec.mode]){paintAnalyst(rec);return null;}
 if(rec.note)message('Note',[rec.note]);
 const view=viewOf(rec);   // the answer at the rates in force (#31b)
 const summary=message('',view.sections,'',rec); rec.lineEl=dollarSlot(summary); showResult(view.result); rec.tiles=tileSlots; paintMoney(rec);
 return summary;
}
let shownId=null, shownKey=null;   // the record #card shows now, and the bodyKey of what was drawn for it
function renderCard(){
 const rec=thread.find(r=>r.id===openId)||null, card=$('card');
 if(shownId!==null&&(!rec||rec.id!==shownId)){ const prev=thread.find(r=>r.id===shownId); if(prev&&shownKey!==null)harvest(prev,shownKey); }   // the card that was open moves up whole
 clearCard();
 shownId=rec?rec.id:null; shownKey=rec&&settled(rec)?bodyKey(rec):null;
 if(rec)rec.entry=null;   // it is the open card now, not a line of the thread
 if(!rec){card.className='card answer idle';card.setAttribute('aria-label','Answer');card.setAttribute('aria-busy','false');$('card-title').textContent='Ask about a shift';paintMeta(null);$('messages').append(node('p',IDLE_TEXT,'empty'));$('card-foot').hidden=true;latest=null;renderEarlier();scheduleFollow();return;}
 $('card-title').textContent=rec.title;$('card-title').title=rec.question;paintMeta(rec);
 card.setAttribute('aria-label',`Answer: ${rec.title}`);card.setAttribute('aria-busy',String(rec.state==='pending'));
 if(rec.state==='pending'&&rec.live){card.className='card answer streaming';paintLive(rec);$('card-foot').hidden=true;latest=null;}
 else if(rec.state==='pending'){card.className='card answer skeleton';skeleton();$('card-foot').hidden=true;latest=null;}
 else if(rec.state==='error'){card.className='card answer failed';paintAnswer(rec);$('card-foot').hidden=true;latest=null;}
 else if(PROVIDERS[rec.mode]){card.className='card answer';paintAnswer(rec);$('card-foot').hidden=false;syncPdf();}
 else{
  card.className='card answer';
  const summary=paintAnswer(rec);
  $('card-foot').hidden=false;syncPdf();
  if(rec.state==='done'&&!rec.revealed){ rec.revealed=true; pendingReveal={summary,t0:rec.t0}; }   // first time this answer is drawn: it is revealed once, reopening shows it settled
 }
 if(rec.state!=='pending'){ analystOffer(rec); feedbackOffer(rec); }
 renderEarlier();
 scheduleFollow();
 if(pendingReveal){ const {summary,t0}=pendingReveal; pendingReveal=null; reveal(summary,t0); }
}
// ---- The transcript pane (issue #86). The page itself does not scroll: the rail and the ask bar stay where they are
// and only #transcript scrolls. The open card is the newest, at the bottom. While it fills (skeleton, landed answer,
// reveal steps, streamed narrative), the pane follows it: its bottom sits at the pane's bottom while it fits, and once
// it is taller than the pane its title line stays at the top, so the answer is read from its first word. The rule
// never scrolls up on its own, and never pulls a reader back down: any move up, however small (a trackpad scrolls in
// steps of a few pixels), stops it. Once the reader is more than FOLLOW_SLACK px from the newest content and more has
// arrived, a "↓ new" pill says so; clicking it, or scrolling back down to within FOLLOW_SLACK px, resumes following.
// Asking, or opening a card, always brings the open card into view.
const FOLLOW_SLACK=80, FOLLOW_GAP=12;
let following=true, unseen=false, lastTop=0, followQueued=false, followForce=false, followFresh=false;
function paneOf(){ const p=$('transcript'); return p&&typeof p.scrollTop==='number'&&typeof p.scrollHeight==='number'&&typeof p.clientHeight==='number'?p:null; }   // null in the unit tests' layout-less stub, unless a test gives it numbers
function followTarget(p){
 const card=$('card'), max=Math.max(0,p.scrollHeight-p.clientHeight);
 if(typeof card.offsetTop!=='number'||typeof card.offsetHeight!=='number')return max;
 return Math.max(0,Math.min(max,card.offsetTop-FOLLOW_GAP,card.offsetTop+card.offsetHeight+FOLLOW_GAP-p.clientHeight));
}
function syncPill(){ $('new-pill').hidden=following||!unseen; }
// fresh: the change is at the bottom (the open card), so a reader who scrolled away has something new below.
function follow(force=false,fresh=true){
 const p=paneOf(); if(!p)return;
 if(force){ following=true; unseen=false; }
 const target=followTarget(p);
 if(!following){ if(fresh&&target-p.scrollTop>FOLLOW_SLACK)unseen=true; syncPill(); return; }
 if(force||p.scrollTop<target)p.scrollTop=target;
 lastTop=p.scrollTop; syncPill();
}
// Coalesced to the next frame: layout is read once, where the browser lays out anyway, and never inside the measured
// submit-to-rendered time on the title line. Without a frame scheduler (the unit tests' stub) it runs at once.
function scheduleFollow(force=false,fresh=true){
 followForce=followForce||force; followFresh=followFresh||fresh;
 if(typeof requestAnimationFrame!=='function'){ const f=followForce, n=followFresh; followForce=followFresh=false; follow(f,n); return; }
 if(followQueued)return; followQueued=true;
 requestAnimationFrame(()=>{ followQueued=false; const f=followForce, n=followFresh; followForce=followFresh=false; follow(f,n); });
}
// Only a move up stops the following; the distance to the follow point decides only when it resumes. A scroll event
// is dispatched a frame after the scroll, so by then a card may have landed and moved the follow point: the event of
// our own scroll, or of the browser's scroll anchoring (which moves down when content grows above), is not a move up
// and must not read as the reader leaving. A user's scroll event always runs before the frame's follow step.
function onPaneScroll(){
 const p=paneOf(); if(!p)return;
 const top=p.scrollTop, gap=followTarget(p)-top;
 if(top<lastTop){ following=false; if(gap<=FOLLOW_SLACK)unseen=false; }
 else if(gap<=FOLLOW_SLACK){ following=true; unseen=false; }
 lastTop=top; syncPill();
}
$('transcript').addEventListener('scroll',onPaneScroll,{passive:true});
$('new-pill').addEventListener('click',()=>{ follow(true); try{$('card-title').focus?.({preventScroll:true});}catch(e){} });   // lands on the newest content; focus goes with it, the button having gone
if(typeof window!=='undefined'&&typeof window.addEventListener==='function')window.addEventListener('resize',()=>{ if(following)scheduleFollow(false,false); });
// ---- The ask bar's keys (#86): Enter sends, Shift+Enter is a new line, and "/" focuses the box from anywhere else.
// The box grows with what is typed, up to about six lines.
function fitQuestion(){ const q=$('question'); if(!q.style||typeof q.scrollHeight!=='number')return; q.style.height='auto'; q.style.height=Math.min(q.scrollHeight,148)+'px'; }
$('question').addEventListener('keydown',event=>{ if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){ event.preventDefault(); typed(); } });   // an IME composing a word keeps its Enter
$('question').addEventListener('input',fitQuestion);
function editable(el){ const tag=el&&typeof el.tagName==='string'?el.tagName.toUpperCase():''; return tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||!!(el&&el.isContentEditable); }
function onKey(event){
 skipReveal();   // any key ends a reveal at once (#65)
 if(!event||event.key!=='/'||event.ctrlKey||event.metaKey||event.altKey||editable(event.target))return;
 if(event.preventDefault)event.preventDefault();   // the slash is not typed into the box it focuses
 if(railOpen()){closeRail();}
 try{$('question').focus?.();}catch(e){}
}
// ---- The reveal (issue #65). The answer is complete and in the DOM before this runs: the pacing only changes
// when the parts of it become visible, in the order of the reasoning. The query lines tick in about 80 ms apart;
// the summary's visible paragraphs type word by word at reading speed (faster when the answer is long, so it is all
// there by about 1.5 s), the first figure landing at once; the charts bloom once the first sentence is down; the
// tiles and tables are there from the first frame, like a gauge. Nothing is typed that is not already the text of
// the finished card: each paragraph shows a prefix of its own textContent, and the end state is that text. A click
// or any key ends it at once; prefers-reduced-motion, or a page without a frame scheduler, shows everything at once.
let pendingReveal=null, pace=null;
const PACE={query_ms:80,queries_ms:480,type_after_ms:160,first_by_ms:400,frame_ms:17,cps:50,type_ms:1000};
function reducedMotion(){ try{return typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion: reduce)').matches;}catch(e){return false;} }
function addClass(e,c){ e.className=((e.className||'')+' '+c).trim(); }
function dropClass(e,c){ e.className=(e.className||'').split(' ').filter(x=>x&&x!==c).join(' '); }
// The end of the leading chunk of a paragraph: through its first figure, which lands with the first frame of typing.
// A shift's number is its name, not its figure: "Shift 2: 616.2 ft/min" lands through 616.2.
function firstFigureEnd(text){ const re=/\d[\d,.]*/g; let m; while((m=re.exec(text))&&/\bShift $/.test(text.slice(0,m.index))); if(!m)return 0; const end=m.index+m[0].length; const space=text.slice(end).search(/\s/); return space<0?text.length:end+space; }
function sentenceEnd(text){ const m=/[.!?](\s|$)/.exec(text); return m?m.index+1:text.length; }
function wordEnd(text,n){ if(n>=text.length)return text.length; if(n<=0)return 0; const space=text.slice(n).search(/\s/); return space<0?text.length:n+space; }
// A typed line with structure (a headline's display figure, a bullet's bold figure and evidence link) types as plain
// text and gets its own nodes back the moment its last word is down; the text is the same either way (#75).
function settleTyped(t){ if(t.settled)return; t.settled=true; t.el.textContent=''; t.el.replaceChildren(...t.kids); if(!t.kids.length)t.el.textContent=t.text; dropClass(t.el,'typing'); }
function typedLines(summary){
 const out=[];
 for(const e of summary.children?[...summary.children]:[]){
  const tag=String(e.tag||e.tagName||'').toLowerCase();
  if(e.hidden)continue;   // folded under "more": shown as it is when opened
  if(tag==='p')out.push(e);
  else if(tag==='ul')for(const li of [...e.children])if(!li.hidden)out.push(li);
 }
 return out;
}
function leadEnd(el,text){   // the lead chunk ends after the bold figure when the line has one, else after its first figure
 const kids=el.children?[...el.children]:[]; let at=0;
 for(const k of kids){ at+=textOf(k).length; if(/\bfigure\b/.test(k.className||''))return at; }
 return firstFigureEnd(text);
}
function finishReveal(skipped=false){
 const p=pace; if(!p)return; pace=null; p.done=true;
 for(const q of p.queries)dropClass(q,'pace-wait');
 for(const t of p.typed)settleTyped(t);
 for(const e of p.later)dropClass(e,'pace-wait');
 for(const e of p.blooms){ dropClass(e,'pace-wait'); if(skipped)dropClass(e,'bloom'); }   // skipped: the charts show drawn, no growth still to run
 if(p.card)p.card.setAttribute('data-reveal','done');
 scheduleFollow();   // the last words are down: the pane follows them (#86)
}
function skipReveal(){ if(pace)finishReveal(true); }
function reveal(summary,submitted){
 if(pace)finishReveal();   // a newer card: the one before is settled first
 if(reducedMotion()||typeof requestAnimationFrame!=='function'||!summary)return;
 const queries=$('queries').children.filter?$('queries').children.filter(e=>/^query\b/.test(e.className||'')):[...$('queries').children].filter(e=>/^query\b/.test(e.className||''));
 const kids=summary.children?[...summary.children]:[];
 const typed=typedLines(summary).map(el=>({el,text:textOf(el),kids:el.children?[...el.children]:[],settled:false}));
 const later=[...kids.filter(e=>!typed.some(t=>t.el===e)&&!/^ul$/i.test(e.tag||e.tagName||'')&&!/\bdollar-line\b/.test(e.className||'')),...[...$('messages').children].filter(e=>e!==summary&&!/\bmessage\b/.test(e.className||'')),...$('look').children];   // the note disclosure, a list's "more", where to look and the analyst offer arrive with the last word
 const blooms=[...$('charts').children,...$('heatmaps').children];
 const total=typed.reduce((n,t)=>n+t.text.length,0), step=queries.length?Math.min(PACE.query_ms,PACE.queries_ms/queries.length):0;
 // The first figure is due 400 ms after the submit, not after the land: a slow land shortens the wait before the
 // lead chunk (by a frame's margin), and past that point it lands on the reveal's first frame.
 const now=clock(), since=typeof submitted==='number'?now-submitted:0;
 const start=Math.max(0,Math.min(PACE.type_after_ms,queries.length*step,PACE.first_by_ms-PACE.frame_ms-since));
 const p={t0:now,queries,typed,later,blooms,step,start,cps:Math.max(PACE.cps,total/(PACE.type_ms/1000)),lead:typed.length?leadEnd(typed[0].el,typed[0].text):0,first:typed.length?sentenceEnd(typed[0].text):0,bloomed:false,done:false,card:$('card')};
 pace=p; p.card.setAttribute('data-reveal','running');
 queries.forEach((q,i)=>{ if(i*step>0)addClass(q,'pace-wait'); });
 for(const t of typed){ t.el.replaceChildren(); t.el.textContent=''; addClass(t.el,'typing'); }
 for(const e of later)addClass(e,'pace-wait');
 for(const e of blooms)addClass(e,'pace-wait');
 const frame=()=>{ if(pace!==p)return; tickReveal(p); follow(); if(pace===p)requestAnimationFrame(frame); };   // each step of the reveal is a step of the follow rule (#86): this is a frame already, so no second one
 tickReveal(p); if(pace===p)requestAnimationFrame(frame);
}
function tickReveal(p){
 const t=clock()-p.t0;
 p.queries.forEach((q,i)=>{ if(t>=i*p.step)dropClass(q,'pace-wait'); });
 if(t<p.start)return;
 let budget=Math.max(p.lead,Math.floor((t-p.start)*p.cps/1000)), all=true;
 p.typed.forEach((x,i)=>{
  const n=wordEnd(x.text,budget); budget-=x.text.length;
  if(n>=x.text.length)settleTyped(x);
  else{ all=false; if(n>0||i===0)x.el.textContent=x.text.slice(0,Math.max(0,n)); }
  if(i===0&&!p.bloomed&&n>=p.first){ p.bloomed=true; for(const e of p.blooms){ dropClass(e,'pace-wait'); addClass(e,'bloom'); } }
 });
 if(all&&p.queries.every((q,i)=>t>=i*p.step))finishReveal();
}
if(typeof document!=='undefined'&&typeof document.addEventListener==='function'){ document.addEventListener('keydown',onKey,true); document.addEventListener('pointerdown',skipReveal,true); }
// ---- Ask the analyst (issues #23, #67). The model is opt-in per question: the ask bar's button for the typed
// question, or the button under an offline answer for that answer's question. Either opens its own card. The
// server streams newline-delimited JSON events: start, plan (the queries the model chose, in one call), a step
// as each query starts and finishes, narrative text as the model writes it, a retract for a paragraph that failed
// validation, then done with the full result. Every number in the narrative was substituted from a query row.
const KIND_LABEL={finding:'Finding',hypothesis:'Hypothesis',check:'Check',limitation:'Limitation'};
async function readEvents(response,onEvent){
 const line=text=>{ if(text.trim())onEvent(JSON.parse(text)); };
 if(response.body&&typeof response.body.getReader==='function'&&typeof TextDecoder==='function'){
  const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer='';
  for(;;){ const {value,done}=await reader.read(); if(done)break; buffer+=decoder.decode(value,{stream:true}); let cut; while((cut=buffer.indexOf('\n'))>=0){ line(buffer.slice(0,cut)); buffer=buffer.slice(cut+1); } }
  line(buffer);
 } else for(const text of (await response.text()).split('\n'))line(text);   // no stream reader: the events arrive together
}
// The card while the stream runs: the planned queries with their progress, then the paragraphs as they are written.
function paintLive(rec){
 const live=rec.live; clearCard();
 if(live.steps.length){
  const box=$('queries'), head=node('div',undefined,'queries-head'); box.className='queries';
  head.append(node('span','Queries the analyst planned'),node('span',`${live.steps.length} ${live.steps.length===1?'query':'queries'} · running`,'mono')); box.append(head);
  for(const step of live.steps){ const d=node('div',undefined,'query pending'); d.append(node('code',step.tool,'tool'),node('span',`(${argText(step.arguments)})`,'args'),node('span',step.facts,'facts')); box.append(d); }
 }
 const card=message(null,[],'analyst'); card.append(node('p',live.state,'muted analyst-state'));
 for(const [,p] of [...live.paragraphs].sort((a,b)=>a[0]-b[0]))if(!p.hidden)card.append(node('p',`${KIND_LABEL[p.kind]||'Note'}: ${p.text}`,'analyst-p'+(p.final?'':' streaming')));
 scheduleFollow();   // the streamed narrative is followed like the reveal (#86)
}
// The finished analyst card. When the analyst's own queries ran, their evidence is the card's; when the plan was
// refused (nothing ran), the notes say so and the offline evidence it was asked from stays on the card.
function paintAnalyst(rec){
 const view=viewOf(rec), r=view.result, ran=(r.trace||[]).some(e=>e.ok), offline=rec.source&&rec.source.result;
 if(ran)showResult(r); else if(offline)showResult(pricedView(offline,askedRates(rec.source)).result);
 rec.tiles=tileSlots; const card=message(null,[],'analyst'); rec.lineEl=dollarSlot(card); paintMoney(rec);
 if(r.status==='plan_rejected'){   // issue #78: a refused plan degrades, it does not stop; one quiet line under the offline evidence
  const over=r.plan_rejection, calls=(r.usage&&r.usage.model_calls)||0;
  card.append(node('p',over?`The analyst proposed ${over.proposed} queries; the limit is ${over.limit}, so none ran.`:(r.sections||[]).filter(t=>!/^Investigation stopped/.test(t)).join(' '),'analyst-p muted'));
  const state=node('p',`analyst · plan rejected · ${calls} model call${calls===1?'':'s'}`,'muted analyst-state'); if(r.usage)state.title=usageText(r.usage); card.append(state);
  return;
 }
 const notes=ran?(r.sections||[]).filter(t=>/^(Investigation stopped|The model narrative|The requested)/.test(t)):(r.sections||[]);
 if(r.narrative_status!=='available'||r.status!=='complete')for(const text of notes)card.append(node('p',text,'analyst-p warning-note'));
 // The interpretation in the answer's shape (#75): one bullet per paragraph, its figures bold (each was substituted
 // from a query row by the validator), the evidence small under it.
 const narrative=view.narrative||[], list=narrative.length?node('ul',undefined,'analyst-list'):null; if(list)card.append(list);
 for(const item of narrative){
  const li=node('li',undefined,'analyst-p'); li.append(node('span',`${KIND_LABEL[item.kind]||'Note'}: `,'kind'),...boldFigures(item.text)); list.append(li);
  const cites=(item.citations||[]).map(c=>{ for(const [rid,i] of stepIndex)c=c.replace(rid,`query ${i+1}`); return c; });
  if(cites.length)li.append(node('p',`Evidence: ${cites.join('; ')}`+((item.charts||[]).length?` · Charts: ${item.charts.join('; ')}`:''),'analyst-cites muted'));
 }
 const first=r.usage&&r.usage.first_token_ms;
 card.append(node('p',`${r.mode} · ${r.status} · ${usageText(r.usage)}`+(typeof first==='number'?` · first words after ${(first/1000).toFixed(1)} s`:''),'muted analyst-state'));
 if(r.narrative_status==='available')card.append(node('p','Model interpretation: review it with the evidence; hypotheses are not proven causes. Every number above was substituted from a query row.','muted'));
 // #31b: the evidence re-prices at the visitor's rates; a model's words are not rewritten (a figure moved into another
 // sentence, or a comparison the new figures reverse, would say what no row says), so at other rates the
 // interpretation says which rates it was written at.
 if(view.repriced&&narrative.length){ const cfg=costCfg(), asked=Money.effective(cfg,Money.parse(askedRates(rec),cfg)); card.append(node('p',`Written at the rates it was asked at (${Money.provenance(asked,cfg).replace(/^Rates: /,'').replace(/\.$/,'')}); the evidence above is at your rates. Ask the analyst again for an interpretation at these.`,'warning-note analyst-rates')); }
}
// A validated paragraph's figures in bold: a number with its unit, never a shift's own number ("Shift 2").
function boldFigures(text){
 const out=[], re=/\d[\d,]*(?:\.\d+)?(?:%|\s?(?:ft\/min|min(?:utes)?|MSF|sq ft|stops|hours|ft)\b)?(?![\w-])/g; let last=0, m;
 while((m=re.exec(text))){ if(/\b[Ss]hift $/.test(text.slice(0,m.index))||/[A-Za-z-]$/.test(text.slice(0,m.index)))continue; if(m.index>last)out.push(node('span',text.slice(last,m.index))); out.push(node('strong',m[0],'figure')); last=m.index+m[0].length; }
 if(last<text.length)out.push(node('span',text.slice(last)));
 return out;
}
// Under an offline answer (or a question the offline planner could not route): one analyst round for that question.
function analystOffer(rec){
 const mode=analystMode(); if(!mode||rec.report||PROVIDERS[rec.mode]||rec.analysed||!/^(done|error)$/.test(rec.state))return;
 const row=node('div',undefined,'analyst-row'), b=node('button','Ask the analyst','secondary analyst-ask'); b.type='button';
 b.addEventListener('click',()=>{ rec.analysed=true; ask(rec.question,{mode,title:rec.title,scope:rec.scope,corrupt:rec.corrupt,source:rec}).catch(()=>{}); });
 row.append(b,node('span',`${rec.state==='error'?'The offline planner has no pattern for this question. ':''}${PROVIDERS[mode].name} picks the queries in one call and writes an interpretation under the numbers. A few seconds; ${config&&config.public?"uses this site's key under a spend cap":'uses your key and incurs API charges'}.`,'muted'));
 $('messages').append(row);
}
// ---- The question log and feedback on a card (issue #125). While the server keeps the log (config.question_log: the
// hosted demo), each question this page asks carries a random line id, and the tab a random session id
// (sessionStorage), as two headers; nothing else about the visitor is sent, and a reloaded card replays without them.
// Every answered card, the analyst's included, offers a thumbs up or down once, never required; a thumbs down asks
// "What were you looking for?" (optional, plain text, at most 300 characters). Both POST /api/feedback.
const LOG_SESSION_KEY='csi-log-session', LOG_ID=/^[0-9a-f]{16}$/;
let logSession=null;
function questionLog(){ return !!(config&&config.question_log); }
function randomId(){
 const bytes=new Uint8Array(8);
 try{ globalThis.crypto.getRandomValues(bytes); }catch(e){ for(let i=0;i<bytes.length;i++)bytes[i]=Math.floor(Math.random()*256); }
 return [...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');
}
function logSessionId(){
 if(logSession)return logSession;
 const store=tabStore(); let held=null; try{ held=store&&store.getItem(LOG_SESSION_KEY); }catch(e){ held=null; }
 logSession=LOG_ID.test(held||'')?held:randomId(); try{ if(store)store.setItem(LOG_SESSION_KEY,logSession); }catch(e){ /* blocked storage: one id for this page */ }
 return logSession;
}
function logHeaders(rec){ return questionLog()&&rec.line?{'X-Question-Line':rec.line,'X-Question-Session':logSessionId()}:{}; }
const FEEDBACK_THANKS='Thanks: kept with this question, anonymously.';
function feedbackOffer(rec){
 if(!questionLog()||!rec.line||!/^(done|error)$/.test(rec.state))return;
 rec.feedback=rec.feedback||{vote:null,state:'ask',note:'',draft:''};
 const row=node('div',undefined,'feedback-row'); row.setAttribute('role','group'); row.setAttribute('aria-label','Feedback on this answer');
 rec.feedbackEl=row; paintFeedback(rec); $('messages').append(row);
}
function paintFeedback(rec){
 const f=rec.feedback, row=rec.feedbackEl; if(!row)return;
 const parts=[];
 if(f.state==='ask'){
  parts.push(node('span','Did this answer it?','muted'));
  for(const [vote,face,label] of [['up','👍','Yes, this answered my question'],['down','👎','No, this is not what I was looking for']]){
   const b=node('button',face,'secondary feedback-vote'); b.type='button'; b.title=label; b.setAttribute('aria-label',label);
   b.addEventListener('click',()=>sendVote(rec,vote)); parts.push(b);
  }
 }else if(f.state==='comment'){
  const form=node('form',undefined,'feedback-form'), input=node('input'), send=node('button','Send','secondary'), skip=node('button','No thanks','secondary');
  input.type='text'; input.maxLength=300; input.value=f.draft; input.placeholder='What were you looking for? (optional)';
  input.setAttribute('aria-label','What were you looking for? Optional, plain text, at most 300 characters');
  input.addEventListener('input',()=>{ f.draft=input.value; });
  send.type='submit'; skip.type='button';
  form.addEventListener('submit',event=>{ if(event&&event.preventDefault)event.preventDefault(); sendComment(rec,input.value); });
  skip.addEventListener('click',()=>{ f.state='done'; f.note=FEEDBACK_THANKS; paintFeedback(rec); });
  form.append(input,send,skip); parts.push(node('span','Noted.','muted'),form); rec.feedbackInput=input;
 }
 if(f.note)parts.push(node('span',f.note,f.failed?'warning-note':'muted'));
 row.replaceChildren(...parts);
}
async function sendFeedback(rec,extra){
 const response=await post('/api/feedback',{line:rec.line,session:logSessionId(),card:PROVIDERS[rec.mode]?'analyst':'answer',...extra});
 if(!response.ok){ let error='The feedback was not recorded'; try{ error=(await response.json()).error||error; }catch(e){ /* not JSON */ } throw new Error(error); }
}
function sendVote(rec,vote){
 const f=rec.feedback; if(f.vote)return;   // once per card
 f.vote=vote; f.state=vote==='up'?'done':'comment'; f.note=vote==='up'?FEEDBACK_THANKS:''; f.failed=false; paintFeedback(rec);
 if(vote==='down')try{ rec.feedbackInput.focus?.({preventScroll:true}); }catch(e){ /* the stub has no focus */ }   // the clicked button is gone: the keyboard lands in the box
 return sendFeedback(rec,{vote}).catch(error=>{ f.state='done'; f.note=error.message; f.failed=true; paintFeedback(rec); });
}
function sendComment(rec,text){
 const f=rec.feedback, comment=String(text||'').trim(); f.draft=comment;
 f.state='done'; f.note=FEEDBACK_THANKS; f.failed=false; paintFeedback(rec);
 if(!comment)return Promise.resolve();
 return sendFeedback(rec,{vote:'down',comment}).catch(error=>{ f.state='comment'; f.note=error.message; f.failed=true; paintFeedback(rec); });
}
// The one render entry for an answer, asked or opened from an address: the record takes the result and its card
// (open or one line in the thread) is drawn again. The ms is submit to rendered.
// A card replayed on a reload (#86) keeps the time it was first answered in.
function settleCard(rec){ if(!thread.includes(rec))return; if(openId===rec.id)renderCard(); else rec.revealed=true;   /* landed behind another card: it opens settled (#65) */ rec.ms=typeof rec.keepMs==='number'?rec.keepMs:clock()-rec.t0; if(openId===rec.id)paintMeta(rec); else{renderEarlier();syncPdf();scheduleFollow(false,false);} saveThread(); }
function land(rec,result){
 if(result.session_id)latestRun.set(result.session_id,result.run_id);
 rec.result=result; rec.answered=answeredScope(rec.scope,result); rec.state='done'; settleCard(rec);
}
// Ask is the deterministic path: the offline planner, whatever agent is chosen under "Agent and advanced"; it is a
// GET of the question's address (/q, which the server redirects to the canonical URL), and the answer's URL goes in
// the address bar. "Ask the analyst" is the only live path: it passes the agent as `mode` and POSTs /api/ask, whose
// session carries the follow-up history; a model's answer is not an address. A report is always offline.
async function ask(question,{report=false,mode='offline',title,scope:asked,corrupt:askedCorrupt,source}={}) {
 if(typeof question!=='string'||!question.trim())throw new Error('Enter a question');
 if(!config)throw new Error('The workbench is still loading');
 const scope=asked||currentScope(), epoch=source&&source.epoch!==sessionEpoch?-1:sessionEpoch,   // a card from an earlier scope asks outside the current conversation
  corrupt=askedCorrupt??$('corrupt').checked, recMode=report?'offline':mode;
 // The same question, still out, asked again (a double click): it is the card already waiting, not a second
 // request. Nothing is disabled to prevent it, so this is what keeps a second paid round from going out.
 const twin=thread.find(r=>r.state==='pending'&&r.epoch===epoch&&r.question===question&&r.mode===recMode&&r.report===report&&r.corrupt===corrupt&&['day','period','shift','scope'].every(k=>r.scope[k]===scope[k]));
 if(twin){ if(openId!==twin.id)openCard(twin); return twin.done; }
 const rec={id:++seq,question,title:title||question.trim(),scope,answered:null,mode:recMode,report,corrupt,epoch,source:source||null,state:'pending',t0:clock(),ms:null,result:null,error:null,note:null,url:null,live:null,
  line:questionLog()&&!report?((PROVIDERS[recMode]&&source&&source.line)||randomId()):null};   // the question log's line id (#125): an analyst round asked from a card shares that card's; any other question, a where-to-look follow-up included, is its own
 thread.push(rec);openId=rec.id;renderCard();scheduleFollow(true);   // the skeleton card is in the DOM before this task yields: within one frame of the submit; the pane brings it into view (#86)
 $('status').textContent=report?'Preparing validated daily records; the first period report can take a minute…':'Asking…';
 // What the visitor chose is read now; the request itself waits its turn. Two questions out at once would both carry
 // the conversation as it was before either was answered, and the follow-up context would lose one.
 const headers={}, p=PROVIDERS[rec.mode]||null, turn=queue, address={day:scope.day,shift:String(scope.shift),scope:scope.scope,period:scope.period,corrupt}; let release; queue=new Promise(resolve=>{release=resolve;});
 // The offline address of this question, for a reload of the tab (#86): an analyst's card comes back as the offline answer.
 rec.askUrl=report?reportUrl(address,epoch===sessionEpoch?conversation:[]):questionUrl(question,address,epoch===sessionEpoch?conversation:[]); saveThread();
 if(p && $('api-key').value && !(config&&config.public))headers[p.header]=$('api-key').value;
 if(p)$('api-key').value='';   // the key is cleared when it is sent; an offline question leaves it for the analyst
 rec.done=(async()=>{
  try{
   await turn;
   const current=epoch===sessionEpoch;   // the scope did not change while this waited: it continues the conversation
   let response;
   if(p){
    const body={question,day:scope.day,shift:scope.shift,corrupt,session_id:current?sessionId:null,mode:rec.mode,scope:scope.scope,period:scope.period};
    const rates=paramOf(rec.askUrl||'','rates'); if(rates)body.rates=rates;   // the analyst prices at the rates the card was asked at (#31b)
    const pending=post('/api/ask',body,{...headers,...logHeaders(rec)});
    for(const entry of Object.values(PROVIDERS))delete headers[entry.header];
    response=await pending;
    if(response.ok){   // a live round is a stream (#67); a refusal before it starts is plain JSON
     rec.live={state:'Planning the queries…',steps:[],paragraphs:new Map()}; $('status').textContent='Analyst · planning the queries…';
     let result=null, failure=null;
     const repaint=()=>{ if(openId===rec.id&&rec.state==='pending'){ $('card').className='card answer streaming'; paintLive(rec); } };
     const para=(i,kind)=>{ let q=rec.live.paragraphs.get(i); if(!q){ q={kind,text:'',hidden:false,final:false}; rec.live.paragraphs.set(i,q); } return q; };
     repaint();
     await readEvents(response,event=>{
      const live=rec.live;
      if(event.type==='plan'){ live.steps=event.steps.map(st=>({tool:st.tool,arguments:st.arguments,facts:'· queued'})); live.state=`Running ${event.steps.length} ${event.steps.length===1?'query':'queries'}…`; $('status').textContent='Analyst · running the queries…'; }
      else if(event.type==='step'){ const st=live.steps[event.index]; if(st)st.facts=event.phase==='start'?'· running…':event.ok?`· ${event.rows===null||event.rows===undefined?'done':event.rows+(event.rows===1?' row':' rows')} · ${Number(event.ms).toFixed(1)} ms`:'· refused'; if(event.phase==='finish'&&event.index===live.steps.length-1){ live.state='Writing the interpretation…'; $('status').textContent='Analyst · writing…'; } }
      else if(event.type==='delta')para(event.paragraph,event.kind).text+=event.text;
      else if(event.type==='paragraph'){ const q=para(event.paragraph,event.kind); q.text=event.text; q.final=true; }
      else if(event.type==='retract'){ const q=para(event.paragraph); q.text=''; q.hidden=true; }   // it failed validation: nothing of it stays on screen
      else if(event.type==='done')result=event.result;
      else if(event.type==='error')failure=event.error;
      repaint();
     });
     if(failure)throw new Error(failure);
     if(!result)throw new Error('The analyst stream ended before the answer; nothing was fabricated');
     response={ok:true,json:async()=>result};
    }
   }else{
    const priors=current?conversation:[];
    rec.askUrl=report?reportUrl(address,priors):questionUrl(question,address,priors);
    saveThread();   // the stored address now carries the real priors, not the ones at submit time
    response=await fetch(rec.askUrl,{headers:{Accept:'application/json',...logHeaders(rec)}});   // the ids ride the /q redirect to the answer (#125)
   }
   const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');
   if(p&&current){
    if(result.session_reset&&sessionId)rec.note='The server restarted since your last question, so this answer started a fresh conversation; earlier follow-up context was not available.';
    sessionId=result.session_id;
   }
   if(!p&&result.url){ rec.url=result.url; if(current){ if(sameDay(result.url,scope))followView(result.url,!report); if(openId===rec.id)pushAddress(result.url); } }   // an answer on another day (#105: "last week") is no follow-up of this conversation, whose views share the day
   land(rec,result);
   $('status').textContent=readyText();
   return {status:result.status,sections:result.sections,production_day:result.production_day,shifts:result.selected_shifts,url:result.url};
  }catch(error){rec.error=error.message;rec.state='error';settleCard(rec);$('status').textContent='Ready to retry';throw error;}
  finally{release();updateScope();}
 })();
 return rec.done;
}
function readyText(){ return isLive()?`Ready · ${provider().name} calls incur charges`:'Ready · no API costs'; }
function analystMode(){ if(isLive())return $('mode').value; const modes=liveModes(); return modes.includes('anthropic')?'anthropic':(modes[0]||null); }
function railOpen(){ return /\bopen\b/.test($('rail').className); }
function closeRail(){ $('rail').className='rail'; $('rail-toggle').setAttribute('aria-expanded','false'); }
function typed(options){ const q=$('question').value; if(!q.trim()){$('status').textContent='Type a question first';return;} ask(q,options).catch(()=>{}); }
$('ask').addEventListener('submit',event=>{event.preventDefault();typed();});
$('analyst').addEventListener('click',()=>{ const mode=analystMode(); if(mode)typed({mode}); });
$('report').addEventListener('click',()=>{ const noun=PERIOD_NOUN[$('period').value]||'period'; closeRail(); ask(`Build ${noun} report`,{report:true,title:`${noun[0].toUpperCase()+noun.slice(1)} report`}).catch(()=>{}); });
$('rail-toggle').addEventListener('click',()=>{ const open=!railOpen(); $('rail').className=open?'rail open':'rail'; $('rail-toggle').setAttribute('aria-expanded',String(open)); if(open)$('rail-close').focus?.(); });
$('rail-close').addEventListener('click',()=>{closeRail();$('rail-toggle').focus?.();});
$('rail').addEventListener('keydown',event=>{ if(event.key==='Escape'&&railOpen()){closeRail();$('rail-toggle').focus?.();} });
$('came-back').addEventListener('click',()=>{ $('execution').open=true; });
// The rates panel (#31b): "Change rates" opens the editor in the rail; every edit applies as it is typed.
$('rates-change').addEventListener('click',()=>openRates(true));
$('rates-form').addEventListener('submit',event=>{ if(event&&event.preventDefault)event.preventDefault(); openRates(false); $('rates-change').focus?.(); });
$('rates-form').addEventListener('keydown',event=>{ if(event.key==='Escape'){ openRates(false); $('rates-change').focus?.(); } });
$('rates-reset').addEventListener('click',()=>{ rateOverrides={}; applyRates(); renderRateFields(); });
// A scope change starts a new server conversation (the follow-up context belongs to the old scope) but keeps the
// thread: every card carries its own scope in its title line. Only "Clear the thread" empties it.
function rescope(){ sessionEpoch++;sessionId=null;conversation=[];$('api-key').value='';pushAddress(scopeUrl());$('status').textContent=readyText();if(!thread.length)renderCard();updateScope();primeKpis(); }
function reset(){ thread=[];openId=null;renderCard();scheduleFollow(true);saveThread();rescope(); }
// ---- The thread survives a reload of the tab (#86). Each card is kept as the address that answers it offline (an
// analyst's card as the offline answer it was asked from) with its title line, in sessionStorage: this tab only, never
// sent anywhere. On load the address bar's card is restore()'s, as before (#20); the cards above it are GETs of their
// own addresses, never /api/ask, and land settled, in their order. A model's narrative is not kept or replayed: its
// card comes back as the offline answer, with the analyst button. Each replay is an answer the server counts against
// the visitor's rate limit, so a reload fetches only the newest few (a quarter of that limit, at most REPLAY_MAX); the
// older cards come back as their title lines, and Open loads one.
const THREAD_KEY='csi-thread', THREAD_MAX=24, REPLAY_MAX=8, REPLAYABLE=/^\/(corr\/|q\?)/;   // same-origin paths only
function replayBudget(){ const limit=Number(config&&config.demo_guards&&config.demo_guards.rate_limit_per_min)||0; return limit>0?Math.min(REPLAY_MAX,Math.max(1,Math.floor(limit/4))):REPLAY_MAX; }
function tabStore(){ try{ return typeof sessionStorage!=='undefined'&&sessionStorage?sessionStorage:null; }catch(e){ return null; } }
function replayUrl(r){ return PROVIDERS[r.mode]?((r.source&&r.source.url)||r.askUrl):(r.url||r.askUrl); }
function saveThread(){
 const store=tabStore(); if(!store)return;
 const cards=thread.map(r=>({url:replayUrl(r),title:r.title,question:r.question,scope:{day:r.scope.day,period:r.scope.period,shift:r.scope.shift,scope:r.scope.scope},corrupt:!!r.corrupt,report:!!r.report,analysed:!PROVIDERS[r.mode]&&!!r.analysed,ms:PROVIDERS[r.mode]?null:r.state==='done'?r.ms:typeof r.keepMs==='number'?r.keepMs:null}))
  .filter(c=>typeof c.url==='string'&&REPLAYABLE.test(c.url)).slice(-THREAD_MAX);
 try{ if(cards.length)store.setItem(THREAD_KEY,JSON.stringify(cards)); else store.removeItem(THREAD_KEY); }catch(e){ /* storage full or blocked: the thread is a convenience */ }
}
function storedThread(){
 const store=tabStore(); if(!store)return [];
 try{ const cards=JSON.parse(store.getItem(THREAD_KEY)||'[]'); return Array.isArray(cards)?cards.filter(c=>c&&typeof c.url==='string'&&REPLAYABLE.test(c.url)&&c.scope&&typeof c.scope.day==='string').slice(-THREAD_MAX):[]; }catch(e){ return []; }
}
function sameScope(rec){ const s=currentScope(); return rec.scope.day===s.day&&rec.scope.period===s.period&&rec.scope.scope===s.scope&&Number(rec.scope.shift)===s.shift&&!!rec.corrupt===$('corrupt').checked; }
function replayThread(cards){
 // Newest first, a card costs one round and, with the cost model on, its scope's money answer one more the first time
 // that scope comes up (#31b), unless the card is itself a money answer: the replay stays inside its share of the limit.
 let budget=replayBudget(), from=cards.length; const scopes=new Set();
 for(let i=cards.length-1;i>=0;i--){
  const c=cards[i], own=/[?&]measure=(dollars|money)(&|$)/.test(c.url), key=`${c.scope.period}|${c.scope.day}|${c.scope.scope}|${c.scope.shift}|${!!c.corrupt}`;
  const cost=1+(costCfg()&&!c.report&&!own&&!scopes.has(key)?1:0);
  if(cost>budget)break;
  budget-=cost; from=i; if(cost>1)scopes.add(key);
 }
 cards.forEach((c,i)=>{
  const rec={id:++seq,question:typeof c.question==='string'?c.question:c.url,title:typeof c.title==='string'?c.title:c.url,scope:{day:c.scope.day,period:c.scope.period,shift:Number(c.scope.shift),scope:c.scope.scope},answered:null,mode:'offline',report:!!c.report,corrupt:!!c.corrupt,epoch:-1,source:null,state:'stored',t0:clock(),ms:null,keepMs:typeof c.ms==='number'?c.ms:null,result:null,error:null,note:null,url:null,live:null,revealed:true,analysed:!!c.analysed,askUrl:c.url};
  thread.push(rec);
  if(i>=from)replayOne(rec);
 });
 if(cards.length)renderEarlier();
}
function replayOne(rec){
 rec.state='pending'; rec.t0=clock();
 rec.done=(async()=>{
  try{
   const response=await fetch(rec.askUrl,{headers:{Accept:'application/json'}}), result=await response.json();
   if(!response.ok)throw new Error(result.error||'Request failed');
   rec.url=result.url||null; if(rec.url&&sameScope(rec))rec.epoch=sessionEpoch;   // a card of the rail's scope opens into the conversation, as it did before the reload
   land(rec,result);
   if(openId===rec.id&&rec.url&&rec.epoch===sessionEpoch){ followView(rec.url,!rec.report); pushAddress(rec.url); updateScope(); }   // opened while it loaded
  }catch(error){ rec.error=error.message; rec.state='error'; settleCard(rec); }
 })();
 return rec.done;
}
$('mode').addEventListener('change',()=>{rescope();const p=provider();syncKeyLabel();$('api-key').value='';$('mode-badge').textContent=p?`Synthetic data / ${p.name} selected`:'Synthetic data / Offline planner';$('mode-help').textContent=p?(config&&config.public?`Public demo: Ask the analyst has ${p.name} choose which bounded tools to run and writes the interpretation with this site's own key under a spend cap; no visitor key is accepted. Ask and the canned questions stay offline.`:`${p.name} receives your question, bounded history and synthetic tool results. Your key passes through this local server to ${p.name} for this request, is not saved, and the field clears on submit. Live calls incur API charges. Ask and the canned questions stay offline.`):'Offline mode uses a rule-based planner for the supported example questions. No model calls or API charges.';});
$('reset').addEventListener('click',reset);$('day').addEventListener('change',dayChanged);$('day-prev').addEventListener('click',()=>stepDay(-1));$('day-next').addEventListener('click',()=>stepDay(1));$('corrupt').addEventListener('change',rescope);$('shift').addEventListener('change',rescope);$('scope').addEventListener('change',rescope);$('period').addEventListener('change',periodChanged);
$('download').addEventListener('click',()=>{if(!latest)return;const url=URL.createObjectURL(new Blob([latest.report_markdown],{type:'text/markdown'}));const a=node('a');a.href=url;a.download=`synthetic-shift-report-${latest.production_day}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
let printing=false;
$('pdf').addEventListener('click',async()=>{if(!latest||printing)return;
 if(latest.url){const a=node('a');a.href=withQuery(withRates(latest.url),'format=pdf');a.download=`synthetic-report-${latest.production_day}.pdf`;a.click();return;}   // an addressed answer: the PDF is the same address with format=pdf
 printing=true;try{const response=await post('/api/report',{session_id:latest.session_id,run_id:latest.run_id});if(!response.ok){const error=await response.json();throw new Error(error.error||'Report unavailable');}const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download=`synthetic-report-${latest.production_day}.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){$('status').textContent=error.message;}finally{printing=false;}});
function applyPublicMode(){
 // Hosted synthetic demo: no visitor keys, ever. A live option stays only when the server's own key for that provider and the cap are enabled.
 if(!config.public)return;
 $('key-label').hidden=true;$('api-key').value='';
 const modes=liveModes();
 for(const [value,entry] of Object.entries(PROVIDERS)){
  const option=$('mode').querySelector?.(`option[value="${value}"]`);
  if(option)option.remove();   // the model is a button on each answer, not a mode that sticks (issue #23)
 }
 $('mode').value='offline';$('mode').disabled=true;
 if(!modes.length)$('mode-help').textContent='Public demo: the free offline planner answers the supported questions from synthetic records. No model calls, no keys.';
 else{
  // Every question is answered offline first, in tens of milliseconds (issues #42, #23). The model is
  // a choice made per answer: it takes seconds and spends the site's budget.
  const live=modes.includes('anthropic')?'anthropic':modes[0];
  $('mode-help').textContent=`Public demo: every question is answered at once with fixed queries over synthetic records. “Ask the analyst” on an answer has ${PROVIDERS[live]?.name||'a model'} pick the queries and write an interpretation under the numbers; that takes a few seconds and uses this site's own key under a spend cap. Every number comes from the queries either way; no visitor key is accepted.`;
 }
 $('mode-badge').textContent=modes.length?'Synthetic data / Public demo · model available':'Synthetic data / Public demo';
}
async function init(){
 renderSegments();renderQuestionChips();renderCard();renderUnits();
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();$('day').min=config.days[0];$('day').max=config.days[config.days.length-1];
 // The store is a rolling year (issue #35): the footer names the window this server holds.
 $('dataset-range').textContent=`${config.days.length} synthetic production days (Mon–Fri, ${dayText(utc(config.days[0]),{weekday:false})} – ${dayText(utc(config.days[config.days.length-1]),{weekday:false})})`;
 if(!config.days.includes($('day').value))$('day').value=config.days.includes('2026-09-14')?'2026-09-14':config.days[config.days.length-1];acceptedDay=$('day').value;
 applyPublicMode();loadMoney(currentAddress());updateScope();
 // The golden-set score of the last tracked run, where people look: one line linking to /evals (issue #26).
 if(questionLog())$('log-foot').hidden=false;   // the question log's disclosure (#125)
 if(config.evals){$('evals-foot').hidden=false;if(config.evals.badge){$('evals-badge').textContent=config.evals.badge+' →';$('evals-badge').title=`Golden evals, last tracked run ${config.evals.ran_at||''}${config.evals.commit?' at commit '+config.evals.commit:''}. Browse every case and run it yourself.`;$('evals-badge').hidden=false;}}
 const address=currentAddress(), addressable=!!(address&&CANONICAL.test(pathOf(address))), cards=storedThread();
 let addressed=-1; if(addressable)for(let i=cards.length-1;i>=0;i--)if(sameView(cards[i].url,address)){addressed=i;break;}
 replayThread(cards.filter((c,i)=>i!==addressed));   // the thread above the addressed card comes back first, in its order (#86)
 if(!(addressable&&await restore(address,addressed>=0?cards[addressed]:null))){pushAddress(scopeUrl(),true);primeKpis();}
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if($('mode').value!=='offline')throw new Error('Browser question tool is available only for offline investigations');if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;fitQuestion();return ask(input.question,{mode:'offline'});}},{signal:lifecycle.signal});}
 }catch(error){$('status').textContent=error.message;}
}
init();
