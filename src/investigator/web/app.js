'use strict';
const $ = id => document.getElementById(id);
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
function message(label, paragraphs, cls='') {
 const e=node('div',undefined,'message '+cls);if(label)e.append(node('strong',label));
 let notes=null; // consecutive shift-note lines fold into one disclosure instead of a dump
 for(const p of paragraphs){
  if(NOTE_LINE.test(p)){ if(!notes){notes=node('details',undefined,'note-group');notes.append(node('summary'));e.append(notes);} notes.append(node('p',p)); notes.children[0].textContent=`${notes.children.length-1} reported note${notes.children.length-1===1?'':'s'} (synthetic, unverified — observations, not causes)`; continue; }
  notes=null; e.append(node('p',p));
 }
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
 if(s.corrupt)p.append('corrupt','1'); p.append('q',question); afterParams(p,priors); return '/q?'+p.toString();
}
function reportUrl(s=controls(),priors=conversation){ const p=new URLSearchParams([['measure','report']]); if(s.corrupt)p.append('corrupt','1'); afterParams(p,priors); return scopeUrl(s)+'?'+p.toString(); }
function withQuery(url,extra){ return url+(url.includes('?')?'&':'?')+extra; }
// The query is everything after the first '?': the server writes after= values unencoded, and each holds its own '?'
// (…?measure=fpm&after=/corr/1/day/2026-09-14/shift/2?measure=overview), so splitting on every '?' loses the priors.
function pathOf(url){ const i=url.indexOf('?'); return i<0?url:url.slice(0,i); }
function queryOf(url){ const i=url.indexOf('?'); return new URLSearchParams(i<0?'':url.slice(i+1)); }
function bareUrl(url){ const path=pathOf(url), p=queryOf(url); p.delete('after'); const q=p.toString(); return path+(q?'?'+q:''); }
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
function pushAddress(url,replace=false){ if(!nav||currentAddress()===url)return; nav.history[replace?'replaceState':'pushState']({url},'',url); }
function followView(url,follow=true){ conversation=[...priorsOf(url),...(follow?[bareUrl(url)]:[])].slice(-maxPriors()); }   // follow=false: the period report is a view, not a question in the conversation
// Back, forward or a pasted address: the controls follow the URL, and the view becomes the open card of the
// thread. A view this page has shown opens its card again without a request; any other is fetched as JSON from
// the same address and lands in a new card, through the same render entry as an asked question.
async function restore(url){
 if(!applyUrl(url))return false;
 renderQuestionChips(); sessionEpoch++; sessionId=null; $('api-key').value='';   // the scope may have changed under the open card: a fresh server conversation
 const held=thread.find(r=>r.state==='done'&&r.url===url);
 if(held){ held.epoch=sessionEpoch; openId=held.id; followView(url,!held.report); renderCard(); updateScope(); $('status').textContent=`${readyText()} · restored`; return true; }
 if(!queryOf(url).has('measure')){ openId=null; conversation=[]; renderCard(); updateScope(); $('status').textContent=readyText(); primeKpis(); return true; }
 // Nothing is disabled while this loads, so the conversation is this address's own priors from now on: a question
 // asked meanwhile, or a chip link, never carries the previous day's views (the grammar refuses those).
 conversation=priorsOf(url).slice(-maxPriors());
 $('status').textContent='Loading this address…';
 const scope=currentScope(), epoch=sessionEpoch, rec={id:++seq,question:bareUrl(url),title:bareUrl(url),scope,answered:null,mode:'offline',report:queryOf(url).get('measure')==='report',corrupt:$('corrupt').checked,epoch,state:'pending',t0:clock(),ms:null,result:null,error:null,note:null,url:null};
 thread.push(rec); openId=rec.id; renderCard(); updateScope();
 try{
  const response=await fetch(url,{headers:{Accept:'application/json'}});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');
  rec.url=result.url||url; rec.question=rec.title=bareUrl(rec.url);
  // Only while nothing has moved on since: a scope change or another answer owns the conversation and the address bar.
  if(epoch===sessionEpoch&&openId===rec.id&&currentAddress()===url){ followView(rec.url,!rec.report); if(nav&&result.url&&result.url!==url)nav.history.replaceState({url:result.url},'',result.url); }   // a hand-typed alias (measure=downtime under Week) becomes its one canonical spelling
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
 if(ranged)$('mode-help').textContent=`Ask about the selected ${noun}: KPIs against the previous one, the downtime Pareto, waste, stops per day, trends, totals by grade, paper consumed, over/under-run, the speed opportunity.${live?' '+liveHelp():' No model calls or API charges.'} Period report builds the output heatmap, daily matrix, recurring downtime and notes without a model.`;
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
 ['Speed to target','Show speed to target',null,null,true],
 ['Dry-end waste','Show dry-end quality waste',null,null,true],
 ['Bond or warp?','Is it bond or warp?',null,null,true],
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
function xAxis(svg,x0,x1,yTop,yBottom,max,unit){
 for(const t of [0,.5,1]){const x=x0+(x1-x0)*t;svg.append(svgNode('line',{x1:x,x2:x,y1:yTop,y2:yBottom,class:'grid'+(t===0?' base':'')}));svg.append(svgNode('text',{x,y:yBottom+16,'text-anchor':t===0?'start':t===1?'end':'middle',class:'tick'},fmtTick(max*t,unit)));}
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
 const all=chart.rows, long=all.length>10; let showAll=!long;
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
   const detail=r.target_fpm?`${r[chart.x]} / ${r.grade||'grade-weighted'}: ${Number(r[chart.y]).toFixed(1)} ft/min; target ${r.target_fpm.toFixed(1)}; ${r.speed_to_target_pct.toFixed(1)}% of target. Includes downtime.`:`${r[chart.x]}: ${Number(r[chart.y]).toFixed(1)} ${chart.unit}`;
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
  labelText(svg,r.category,f.x0-8,y+19,f.chars);
  svg.append(barPath(f.x0,y+6,(v/max)*(f.x1-f.x0),14,i<3?'bar vital':'bar rest'));
  if(prev!==null)svg.append(barPath(f.x0,y+22,(prev/max)*(f.x1-f.x0),6,'bar prev'));
  const detail=`${r.category}: ${fmtVal(v,chart.unit)} over ${r.stops} stops, ${r.share_pct}% of the window, cumulative ${r.cumulative_pct}%; previous window ${prev===null?'unavailable':fmtVal(prev,chart.unit)}.`;
  const t=svgNode('text',{x:f.x1+8,y:y+19,tabindex:0,'aria-label':detail,class:'val'},`${fmtVal(v,'')} · ${Math.round(r.share_pct)}% · cum ${Math.round(r.cumulative_pct)}%`);t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg,legend([['vital','top three'],['rest','the rest'],['prev','previous window']]));
}
// Waste breakdown: the dry-end split as stacked bars (current and previous), then every category paired.
function wasteChart(chart,wrap){
 const byKey=Object.fromEntries(chart.rows.map(r=>[r.category,r])), parts=[['bond','s1'],['warp','s2'],['misalignment','s3'],['moisture','s4']].filter(([k])=>byKey[k]);  // a result without a family (older recordings) stacks the ones it has
 const W=CHART_W(), f=frame(W,2,34), unit=chart.unit;
 const stacks=[['current',r=>r.pct_of_gross],['previous',r=>r.previous_pct_of_gross]].map(([name,get])=>({name,segments:parts.map(([k,cls])=>({k,cls,value:byKey[k]?get(byKey[k]):null}))}));
 const available=stacks.filter(s=>s.segments.every(x=>x.value!==null&&x.value!==undefined));
 if(available.length){
  const max=niceCeil(Math.max(...available.map(s=>s.segments.reduce((a,x)=>a+x.value,0))));
  const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':'dry-end rejects split: bond, warp, misalignment, moisture'});
  xAxis(svg,f.x0,f.x1,f.top,f.top+available.length*34,max,unit);
  available.forEach((s,i)=>{const y=f.top+i*34; let x=f.x0; const total=s.segments.reduce((a,v)=>a+v.value,0);
   labelText(svg,s.name==='current'?'Dry-end now':'Dry-end previous',f.x0-8,y+18,f.chars);
   s.segments.forEach((seg,j)=>{const w=(seg.value/max)*(f.x1-f.x0); const gap=j<s.segments.length-1?2:0;
    const rect=svgNode('rect',{x,y:y+5,width:Math.max(0,w-gap),height:16,class:'seg '+seg.cls});rect.append(svgNode('title',{},`${seg.k}: ${fmtVal(seg.value,unit)}`));svg.append(rect);
    const inside=`${seg.k} ${seg.value.toFixed(2)}`; if(w>inside.length*6.8+14)svg.append(svgNode('text',{x:x+6,y:y+17,class:'inseg '+seg.cls},inside));x+=w;});
   const t=svgNode('text',{x:f.x1+8,y:y+18,tabindex:0,class:'val','aria-label':`${s.name}: dry-end ${fmtVal(total,unit)} = `+s.segments.map(v=>`${v.k} ${fmtVal(v.value,unit)}`).join(', ')},fmtVal(total,unit));svg.append(t);});
  wrap.append(node('p',`Dry-end rejects split by recorded reason; the ${parts.length} segments add to the dry-end total.`,'muted'),svg,legend(parts.map(([k,cls])=>[cls,k])));
 }
 const others=chart.rows.filter(r=>!['bond','warp','misalignment','moisture'].includes(r.category));
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
function showChart(chart) {
 if(chart.type==='calendar_heatmap')return showHeatmap(chart);
 const rows=chart.rows||[], isSpeed=chart.unit==='ft/min'&&rows.some(r=>r.target_fpm), isPareto=rows.length&&rows[0].cumulative_pct!==undefined, isWaste=rows.length&&rows[0].pct_of_gross!==undefined&&rows[0].category!==undefined;
 const title=chart.title||`Shift ${chart.shift} · ${chart.unit==='minutes'?'Recorded downtime':isSpeed?'Wet-end speed (bars) / grade target (marks)':'Dry-end rejects'}`;
 const notes=[];
 if(chart.coverage!=='complete')notes.push({text:'Incomplete data: observed values only.',cls:'warning-note'});
 if(isSpeed)notes.push('Value = ft/min / % of target; the mark is the grade target. Elapsed time includes downtime; hover a value for the grade.');
 const wrap=chartCard(title,notes);
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));
 else if(chart.type==='line')lineChart(chart,wrap);
 else if(chart.type==='range')rangeChart(chart,wrap);
 else if(isPareto)paretoChart(chart,wrap);
 else if(isWaste)wasteChart(chart,wrap);
 else if(chart.y2)pairedChart(chart,wrap);
 else if(isSpeed)speedChart(chart,wrap);
 else barChart(chart,wrap);
 const link=queryLink(chart.source_result_id);if(link)wrap.append(link);
 $('charts').append(wrap);
}
const labels={setup_id:'Setup',wet_end_id:'Wet-end / paper change',grade:'Grade',start:'Start',end:'End',width_in:'Width (in)',lineal_ft:'Lineal (ft)',actual_fpm:'Speed (ft/min)',target_fpm:'Target (ft/min)',speed_to_target_pct:'% of target',stop_count:'Stops',down_minutes:'Down (min)',rejected_sqft:'Rejects (sq ft)',notes:'Reported note',record_id:'Record',record_type:'Type',rank:'Rank',item:'Item',value:'Value',unit:'Units / order',day:'Production day',shift:'Shift',current_count:'Stops now',previous_count:'Stops before',current_minutes:'Minutes now',previous_minutes:'Minutes before',minutes_change:'Change (min)',speed_rank:'Speed rank',maintenance_rank:'Maint. rank',waste_rank:'Waste rank',valid_setups:'Dry-end setups',paper_changes:'Paper changes',observed_shift_fpm:'Speed (ft/min)',maintenance_pct:'Maintenance %',operator_pct:'Operator %',dry_end_pct:'Dry-end %',target_fpm:'Target (ft/min)',wet_ends:'Wet ends',volume_msf:'Volume (MSF)',share_of_volume_pct:'Share %',speed_run_fpm:'Speed w/o stops',speed_elapsed_fpm:'Elapsed speed',setup_stops:'Setup stops',setup_stop_minutes:'Setup stop min',trim_pct:'Trim %',previous_volume_msf:'Prev MSF',delta_volume_msf:'Δ MSF',paper_runs:'Paper runs',msf:'MSF',previous_msf:'Prev MSF',delta_msf:'Δ MSF',share_of_feet_pct:'Share of ft %',lineal_per_run:'Lineal / run',previous_paper_runs:'Prev runs',previous_lineal_ft:'Prev lineal (ft)',delta_lineal_ft:'Δ lineal (ft)',knife:'Knife',lines:'Lines',nominal_cuts:'Nominal cuts',actual_cuts:'Actual cuts',delta_cuts:'Δ cuts',overrun_pct:'Over/under %',nominal_sheets:'Nominal sheets',actual_sheets:'Actual sheets',over_sheets:'Sheets over',under_sheets:'Sheets under',lines_inside_band_pct:'In band %',lines_over_pct:'Over %',lines_under_pct:'Under %',downtime_pct_of_elapsed:'Down % of elapsed',lineal_per_setup:'Lineal / setup',lineal_per_wet_end:'Lineal / paper change',slice_by:'Slice',slice:'Width / crew',label:'Cell',run_minutes:'Run min',typical_fpm:'Typical ft/min',median_setup_fpm:'Median setup ft/min',best_fifth_fpm:'Best fifth ft/min',gap_fpm:'Gap ft/min',typical_pct_of_target:'Typical % target',best_fifth_pct_of_target:'Best fifth % target',opportunity_hours:'Opportunity h',best_fifth_dry_end_pct:'Dry-end % (fast fifth)',rest_dry_end_pct:'Dry-end % (rest)',reject_penalty_points:'Reject penalty pts',status:'Status'};
function showTable(data){
 const titles={get_order_matrix:'Orders and quantities — fragments; demand repeats on continuing orders',get_setup_matrix:'Setup matrix',get_shift_notes:'Shift and downtime notes (synthetic, unverified)',get_rankings:`${data.metric||''} ranking / largest losses first`,get_grade_totals:'Totals by grade',get_overrun:'Over/under-run by knife (nominal vs actual cuts)',get_speed_opportunity:'Speed opportunity: typical vs best fifth, by grade and width / crew',get_rollstock:'Paper consumed (rollstock) by grade and web width'};
 const box=node('details');box.open=data.title==='get_setup_matrix'||data.title==='get_rankings';box.append(node('summary',`${data.shift?'Shift '+data.shift+' / ':''}${titles[data.title]||data.title} (${data.rows.length} rows)`));
 if(data.coverage!=='complete')box.append(node('p','Incomplete evidence. Rankings are withheld; any other rows are observed records only.','warning-note'));
 if(!data.rows.length){box.append(node('p','No eligible rows.'));const link=queryLink(data.source_result_id);if(link)box.append(link);$('tables').append(box);return;}   // an empty table still names its query (#40)
 const omitted=new Set(['source_ids','gross_sqft','observed_lineal_ft','throughput_in','lineal_per_setup','lineal_per_wet_end','setups_above_trim_target']);
 const columns=Object.keys(data.rows[0]).filter(k=>!omitted.has(k)&&!Array.isArray(data.rows[0][k]));
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
function kpiTile(row,withheld){
 const tile=node('div',undefined,'tile'+(row.change&&!withheld?' '+row.change:''));
 tile.append(node('div',row.label,'l'));
 const v=node('div',undefined,'v');v.append(node('span',fmtNumber(row.current,row.unit)));const unit=unitShort(row.unit);if(unit&&row.current!==null)v.append(node('small',unit));tile.append(v);
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
 rows.forEach((row,i)=>{const tile=kpiTile(row,withheld);if(i>=VISIBLE_TILES)tile.className+=' extra';grid.append(tile);});
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

async function primeKpis(){
 const token=++primeToken, question=primeQuestion();
 if(!config||!question||openId!==null||(config.demo_guards&&config.demo_guards.rate_limit_per_min>0))return;   // the tiles are the idle card's content; an asked question owns the card
 try{
  // The scope's tiles are the KPI measure of the scope's own address; the address bar is not moved for them.
  const response=await fetch(withQuery(scopeUrl()+'.json','measure=kpis'+($('corrupt').checked?'&corrupt=1':'')),{headers:{Accept:'application/json'}});
  const result=await response.json();
  if(token!==primeToken||openId!==null||!response.ok||!Array.isArray(result.trace))return;
  const entry=result.trace.find(e=>e.ok&&e.tool==='get_period_kpis');
  if(!entry)return;
  renderKpiTiles(entry.data,`scope tiles · get_period_kpis · ${result.usage.tool_calls} tool call${result.usage.tool_calls===1?'':'s'} · $0 · offline planner`);
  const trail=node('details',undefined,'prime-trail');trail.append(node('summary','How these tiles were produced'));trail.append(node('p',`Offline question: “${question}”, the address ${result.url||''}. The same evidence a typed question gets; ask one for charts and the full trail.`,'muted'));trail.append(traceRow(entry));$('kpis').append(trail);
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
function revealQuery(i){ try{ const q=document.getElementById(`query-${i}`); if(q){q.hidden=false;q.open=true;} }catch(e){} }   // a query folded behind "N more queries" opens when something links to it
function queryLink(resultId){ const i=stepIndex.get(resultId); if(i===undefined)return null; const a=node('a',`query ${i+1} ↑`,'query-link'); a.href=`#query-${i}`; a.title='The query box that produced these rows'; a.addEventListener('click',()=>revealQuery(i)); return a; }
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
 d.append(foot);
 return d;
}
const QUERY_LINES=4;   // steps shown before "N more queries"; with the SQL pane beside them the box stays about 150 px tall. A phone shows one, as the mockup does
function renderQueries(result){
 const box=$('queries'); box.replaceChildren(); stepIndex=new Map();
 const steps=result.trace||[]; if(!steps.length)return;
 steps.forEach((e,i)=>{if(e.result_id)stepIndex.set(e.result_id,i);});
 const total=steps.reduce((sum,e)=>sum+(typeof e.elapsed_ms==='number'?e.elapsed_ms:0),0);
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
// The shift KPI tiles: the numbers of one get_shift_kpis row on seven tiles, one row of the card (#63).
function shiftTiles(entry,result,prefix){
 const r=entry.data.rows[0], incomplete=entry.data.coverage!=='complete', who=prefix?`${entry.data.shift?'Shift '+entry.data.shift:result.period_label} · `:'';
 const n=(v,places)=>Number(v).toLocaleString(undefined,{minimumFractionDigits:places,maximumFractionDigits:places});
 for(const [label,value,unit,places,sub,bad,title] of [
  ['Speed',r.observed_shift_fpm,'ft/min',1,`${n(r.speed_to_target_pct,1)}% of target`,r.speed_to_target_pct<100,'Lineal feet / sum(grade target ft/min × elapsed minutes) × 100. Includes downtime; weighted by grade duration.'],
  ['Maintenance',r.maintenance_pct,'%',2,'target 2.5%',r.maintenance_pct>2.5],
  ['Operator',r.operator_pct,'%',2,'target 2.5%',r.operator_pct>2.5],
  ['Dry-end waste',r.dry_end_pct,'%',2,r.dry_end_pct>1?'above 1%':'flag above 1%',r.dry_end_pct>1],
  ['Trim',r.trim_pct,'%',2,`shear ${n(r.shear_pct,2)}%`,false],
  ['Setups',r.valid_setups,'',0,`${n(r.paper_changes,0)} paper changes`,false,'Dry-end setups; paper changes are the count of distinct wet-end runs, as defined by the plant expert.'],
  ['Throughput',r.throughput_in,'in',1,`${n(r.lineal_per_setup,0)} ft / setup`,false]]){
  const card=node('div',undefined,'metric'+(bad||incomplete?' warning':''));card.append(node('div',who+label,'label'));if(title)card.title=title;
  const v=node('div',undefined,'value');v.append(node('span',incomplete?'Incomplete':n(value,places)));if(unit&&!incomplete)v.append(node('small',unit));card.append(v);
  card.append(node('div',incomplete?`${entry.data.excluded_records?.length||'Missing/invalid'} excluded record(s)`:sub,'sub'+(bad||incomplete?' flag':'')));$('metrics').append(card);
 }
}
function showResult(result) {
 latest=result;
 for(const id of ['heatmaps','kpis','tables','charts','metrics','trace'])$(id).replaceChildren();   // the open card shows one answer's evidence
 renderQueries(result);   // the query box: first below the title line, built in the first paint (#40)
 const kpiEntry=result.trace.find(e=>e.ok&&(e.tool==='get_period_kpis'||e.tool==='compare_periods'));
 if(kpiEntry)renderKpiTiles(kpiEntry.data,`${kpiEntry.tool} · this question`);
 const shiftKpis=result.trace.filter(e=>e.ok&&(e.tool==='get_shift_kpis'||e.tool==='get_shift_overview'||e.tool==='get_period_report'));
 for(const entry of shiftKpis)shiftTiles(entry,result,shiftKpis.length>1);
 for(const table of result.tables||[])showTable(table);
 for(const chart of result.charts)showChart(chart);
 afterPaint(()=>{if(latest!==result)return;result.trace.forEach((entry,i)=>{const row=traceRow(entry);row.id=`step-${i}`;$('trace').append(row);});});   // a newer answer already replaced the evidence: skip
 $('calls').textContent=`${result.status?result.status+' · ':''}${usageText(result.usage)}`;
 const ctx=node('details',undefined,'trace-context');ctx.append(node('summary','Context carried to the next question'));ctx.append(node('pre',JSON.stringify(result.context,null,2)));$('context').replaceChildren(ctx);
 updateScope();
}

// ---- The thread of answer cards (issue #63). One card is open: its title line, query box, summary, charts,
// tiles and exports fill the fixed containers of #card. Earlier answers are one line each under it, newest on
// top, with an Open button. Submitting draws the open card as a skeleton in the same task as the click, and
// the answer fills that card in place; nothing on the page is disabled while a question is out. The thread
// lives in this page's memory; an offline answer's address is in the address bar (issue #20, restore() above).
let thread=[], openId=null, seq=0, sessionEpoch=0;
let queue=Promise.resolve();   // questions go to the server one at a time, in the order asked, so each carries the session the one before it left
const latestRun=new Map();   // session id -> its latest run id: the only run /api/report can still print
const clock=()=>typeof performance!=='undefined'&&performance.now?performance.now():Date.now();
const msText=ms=>ms>=10000?`${(ms/1000).toFixed(1)} s`:`${Math.max(1,Math.round(ms))} ms`;
const IDLE_TEXT='Pick a question on the left or type one above. The answer lands here as one card: the queries that ran, the summary, the charts and the KPI tiles.';
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
 const step=(result.trace||[]).find(e=>e.ok&&e.data&&e.data.window&&e.data.window.label), out={who:null,window:null}; if(!step)return out;
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
function clearCard(){ if(pace)finishReveal(); for(const id of ['queries','messages','charts','heatmaps','kpis','metrics','tables','trace','context'])$(id).replaceChildren(); $('calls').textContent=''; stepIndex=new Map(); }
function skeleton(){   // grey blocks where the query box, the summary and the two charts will land
 const q=node('div',undefined,'skel-block'); for(const w of ['w60','w45','w70','w45'])q.append(node('span',undefined,'skel '+w)); $('queries').append(q);
 const m=node('div',undefined,'skel-block'); for(const w of ['w90','w70'])m.append(node('span',undefined,'skel '+w)); $('messages').append(m);
 $('charts').append(node('div',undefined,'chart skel-chart'),node('div',undefined,'chart skel-chart'));
}
function renderEarlier(){
 const box=$('earlier'); box.replaceChildren();
 for(const rec of thread.filter(r=>r.id!==openId).sort((a,b)=>b.id-a.id)){
  const line=node('article',undefined,'card earlier'+(rec.state==='error'?' failed':''));line.setAttribute('aria-label',`Earlier answer: ${rec.title}`);
  const text=node('div',undefined,'earlier-text');text.append(node('strong',rec.title),node('span',`${entryWho(rec)} · ${rec.state==='pending'?'asking…':rec.state==='error'?'failed':msText(rec.ms)}`));
  const open=node('button','Open','secondary');open.type='button';open.setAttribute('aria-label',`Open the answer to: ${rec.title}`);open.addEventListener('click',()=>{openCard(rec);focusCard();});
  line.append(text,open);box.append(line);
 }
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
function openCard(rec){ openId=rec.id; renderCard(); if(rec.state==='done'&&rec.url&&rec.epoch===sessionEpoch){ followView(rec.url,!rec.report); pushAddress(rec.url); updateScope(); } }
function focusCard(){ try{$('card-title').focus?.();}catch(e){} }   // keyboard focus follows the card when the control that had it goes away
function renderCard(){
 const rec=thread.find(r=>r.id===openId)||null, card=$('card');
 clearCard();
 if(!rec){card.className='card answer idle';card.setAttribute('aria-label','Answer');card.setAttribute('aria-busy','false');$('card-title').textContent='Ask about a shift';paintMeta(null);$('messages').append(node('p',IDLE_TEXT,'empty'));$('card-foot').hidden=true;latest=null;renderEarlier();return;}
 $('card-title').textContent=rec.title;$('card-title').title=rec.question;paintMeta(rec);
 card.setAttribute('aria-label',`Answer: ${rec.title}`);card.setAttribute('aria-busy',String(rec.state==='pending'));
 if(rec.state==='pending'&&rec.live){card.className='card answer streaming';paintLive(rec);$('card-foot').hidden=true;latest=null;}
 else if(rec.state==='pending'){card.className='card answer skeleton';skeleton();$('card-foot').hidden=true;latest=null;}
 else if(rec.state==='error'){card.className='card answer failed';message('Could not complete',[rec.error],'error');if(PROVIDERS[rec.mode])message(null,['The analyst could not answer; no answer was fabricated. The offline answer stands.'],'muted');$('card-foot').hidden=true;latest=null;}
 else if(PROVIDERS[rec.mode]){card.className='card answer';paintAnalyst(rec);$('card-foot').hidden=false;syncPdf();}
 else{
  card.className='card answer';
  if(rec.note)message('Note',[rec.note]);
  const summary=message('',rec.result.sections||[]);showResult(rec.result);
  $('card-foot').hidden=false;syncPdf();
  if(rec.state==='done'&&!rec.revealed){ rec.revealed=true; pendingReveal={summary,t0:rec.t0}; }   // first time this answer is drawn: it is revealed once, reopening shows it settled
 }
 if(rec.state!=='pending')analystOffer(rec);
 renderEarlier();
 if(pendingReveal){ const {summary,t0}=pendingReveal; pendingReveal=null; reveal(summary,t0); }
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
function finishReveal(skipped=false){
 const p=pace; if(!p)return; pace=null; p.done=true;
 for(const q of p.queries)dropClass(q,'pace-wait');
 for(const t of p.typed){ t.el.textContent=t.text; dropClass(t.el,'typing'); }
 for(const e of p.later)dropClass(e,'pace-wait');
 for(const e of p.blooms){ dropClass(e,'pace-wait'); if(skipped)dropClass(e,'bloom'); }   // skipped: the charts show drawn, no growth still to run
 if(p.card)p.card.setAttribute('data-reveal','done');
}
function skipReveal(){ if(pace)finishReveal(true); }
function reveal(summary,submitted){
 if(pace)finishReveal();   // a newer card: the one before is settled first
 if(reducedMotion()||typeof requestAnimationFrame!=='function'||!summary)return;
 const queries=$('queries').children.filter?$('queries').children.filter(e=>/^query\b/.test(e.className||'')):[...$('queries').children].filter(e=>/^query\b/.test(e.className||''));
 const kids=summary.children?[...summary.children]:[];
 const typed=kids.filter(e=>e.tag==='p'||e.tagName==='P').map(el=>({el,text:el.textContent}));
 const later=[...kids.filter(e=>!typed.some(t=>t.el===e)),...[...$('messages').children].filter(e=>e!==summary&&!/\bmessage\b/.test(e.className||''))];   // the note disclosure and the analyst offer arrive with the last word
 const blooms=[...$('charts').children,...$('heatmaps').children];
 const total=typed.reduce((n,t)=>n+t.text.length,0), step=queries.length?Math.min(PACE.query_ms,PACE.queries_ms/queries.length):0;
 // The first figure is due 400 ms after the submit, not after the land: a slow land shortens the wait before the
 // lead chunk (by a frame's margin), and past that point it lands on the reveal's first frame.
 const now=clock(), since=typeof submitted==='number'?now-submitted:0;
 const start=Math.max(0,Math.min(PACE.type_after_ms,queries.length*step,PACE.first_by_ms-PACE.frame_ms-since));
 const p={t0:now,queries,typed,later,blooms,step,start,cps:Math.max(PACE.cps,total/(PACE.type_ms/1000)),lead:typed.length?firstFigureEnd(typed[0].text):0,first:typed.length?sentenceEnd(typed[0].text):0,bloomed:false,done:false,card:$('card')};
 pace=p; p.card.setAttribute('data-reveal','running');
 queries.forEach((q,i)=>{ if(i*step>0)addClass(q,'pace-wait'); });
 for(const t of typed){ t.el.textContent=''; addClass(t.el,'typing'); }
 for(const e of later)addClass(e,'pace-wait');
 for(const e of blooms)addClass(e,'pace-wait');
 const frame=()=>{ if(pace!==p)return; tickReveal(p); if(pace===p)requestAnimationFrame(frame); };
 tickReveal(p); if(pace===p)requestAnimationFrame(frame);
}
function tickReveal(p){
 const t=clock()-p.t0;
 p.queries.forEach((q,i)=>{ if(t>=i*p.step)dropClass(q,'pace-wait'); });
 if(t<p.start)return;
 let budget=Math.max(p.lead,Math.floor((t-p.start)*p.cps/1000)), all=true;
 p.typed.forEach((x,i)=>{
  const n=wordEnd(x.text,budget); budget-=x.text.length;
  if(n>0||i===0){ x.el.textContent=x.text.slice(0,Math.max(0,n)); }
  if(n>=x.text.length)dropClass(x.el,'typing'); else all=false;
  if(i===0&&!p.bloomed&&n>=p.first){ p.bloomed=true; for(const e of p.blooms){ dropClass(e,'pace-wait'); addClass(e,'bloom'); } }
 });
 if(all&&p.queries.every((q,i)=>t>=i*p.step))finishReveal();
}
if(typeof document!=='undefined'&&typeof document.addEventListener==='function'){ document.addEventListener('keydown',skipReveal,true); document.addEventListener('pointerdown',skipReveal,true); }
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
  const box=$('queries'), head=node('div',undefined,'queries-head');
  head.append(node('span','Queries the analyst planned'),node('span',`${live.steps.length} ${live.steps.length===1?'query':'queries'} · running`,'mono')); box.append(head);
  for(const step of live.steps){ const d=node('div',undefined,'query pending'); d.append(node('code',step.tool,'tool'),node('span',`(${argText(step.arguments)})`,'args'),node('span',step.facts,'facts')); box.append(d); }
 }
 const card=message(null,[],'analyst'); card.append(node('p',live.state,'muted analyst-state'));
 for(const [,p] of [...live.paragraphs].sort((a,b)=>a[0]-b[0]))if(!p.hidden)card.append(node('p',`${KIND_LABEL[p.kind]||'Note'}: ${p.text}`,'analyst-p'+(p.final?'':' streaming')));
}
// The finished analyst card. When the analyst's own queries ran, their evidence is the card's; when the plan was
// refused (nothing ran), the notes say so and the offline evidence it was asked from stays on the card.
function paintAnalyst(rec){
 const r=rec.result, ran=(r.trace||[]).some(e=>e.ok), offline=rec.source&&rec.source.result;
 if(ran)showResult(r); else if(offline)showResult(offline);
 const card=message(null,[],'analyst');
 const notes=ran?(r.sections||[]).filter(t=>/^(Investigation stopped|The model narrative|The requested)/.test(t)):(r.sections||[]);
 if(r.narrative_status!=='available'||r.status!=='complete')for(const text of notes)card.append(node('p',text,'analyst-p warning-note'));
 for(const item of r.narrative||[]){
  card.append(node('p',`${KIND_LABEL[item.kind]||'Note'}: ${item.text}`,'analyst-p'));
  const cites=(item.citations||[]).map(c=>{ for(const [rid,i] of stepIndex)c=c.replace(rid,`query ${i+1}`); return c; });
  if(cites.length)card.append(node('p',`Evidence: ${cites.join('; ')}`+((item.charts||[]).length?` · Charts: ${item.charts.join('; ')}`:''),'analyst-cites muted'));
 }
 const first=r.usage&&r.usage.first_token_ms;
 card.append(node('p',`${r.mode} · ${r.status} · ${usageText(r.usage)}`+(typeof first==='number'?` · first words after ${(first/1000).toFixed(1)} s`:''),'muted analyst-state'));
 if(r.narrative_status==='available')card.append(node('p','Model interpretation: review it with the evidence; hypotheses are not proven causes. Every number above was substituted from a query row.','muted'));
}
// Under an offline answer (or a question the offline planner could not route): one analyst round for that question.
function analystOffer(rec){
 const mode=analystMode(); if(!mode||rec.report||PROVIDERS[rec.mode]||rec.analysed||!/^(done|error)$/.test(rec.state))return;
 const row=node('div',undefined,'analyst-row'), b=node('button','Ask the analyst','secondary analyst-ask'); b.type='button';
 b.addEventListener('click',()=>{ rec.analysed=true; ask(rec.question,{mode,title:rec.title,scope:rec.scope,corrupt:rec.corrupt,source:rec}).catch(()=>{}); });
 row.append(b,node('span',`${rec.state==='error'?'The offline planner has no pattern for this question. ':''}${PROVIDERS[mode].name} picks the queries in one call and writes an interpretation under the numbers. A few seconds; ${config&&config.public?"uses this site's key under a spend cap":'uses your key and incurs API charges'}.`,'muted'));
 $('messages').append(row);
}
// The one render entry for an answer, asked or opened from an address: the record takes the result and its card
// (open or one line in the thread) is drawn again. The ms is submit to rendered.
function settleCard(rec){ if(!thread.includes(rec))return; if(openId===rec.id)renderCard(); else rec.revealed=true;   /* landed behind another card: it opens settled (#65) */ rec.ms=clock()-rec.t0; if(openId===rec.id)paintMeta(rec); else{renderEarlier();syncPdf();} }
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
 const rec={id:++seq,question,title:title||question.trim(),scope,answered:null,mode:recMode,report,corrupt,epoch,source:source||null,state:'pending',t0:clock(),ms:null,result:null,error:null,note:null,url:null,live:null};
 thread.push(rec);openId=rec.id;renderCard();   // the skeleton card is in the DOM before this task yields: within one frame of the submit
 $('status').textContent=report?'Preparing validated daily records; the first period report can take a minute…':'Asking…';
 // What the visitor chose is read now; the request itself waits its turn. Two questions out at once would both carry
 // the conversation as it was before either was answered, and the follow-up context would lose one.
 const headers={}, p=PROVIDERS[rec.mode]||null, turn=queue, address={day:scope.day,shift:String(scope.shift),scope:scope.scope,period:scope.period,corrupt}; let release; queue=new Promise(resolve=>{release=resolve;});
 if(p && $('api-key').value && !(config&&config.public))headers[p.header]=$('api-key').value;
 if(p)$('api-key').value='';   // the key is cleared when it is sent; an offline question leaves it for the analyst
 rec.done=(async()=>{
  try{
   await turn;
   const current=epoch===sessionEpoch;   // the scope did not change while this waited: it continues the conversation
   let response;
   if(p){
    const body={question,day:scope.day,shift:scope.shift,corrupt,session_id:current?sessionId:null,mode:rec.mode,scope:scope.scope,period:scope.period};
    const pending=post('/api/ask',body,headers);
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
    response=await fetch(report?reportUrl(address,priors):questionUrl(question,address,priors),{headers:{Accept:'application/json'}});
   }
   const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');
   if(p&&current){
    if(result.session_reset&&sessionId)rec.note='The server restarted since your last question, so this answer started a fresh conversation; earlier follow-up context was not available.';
    sessionId=result.session_id;
   }
   if(!p&&result.url){ rec.url=result.url; if(current){ followView(result.url,!report); if(openId===rec.id)pushAddress(result.url); } }
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
// A scope change starts a new server conversation (the follow-up context belongs to the old scope) but keeps the
// thread: every card carries its own scope in its title line. Only "Clear the thread" empties it.
function rescope(){ sessionEpoch++;sessionId=null;conversation=[];$('api-key').value='';pushAddress(scopeUrl());$('status').textContent=readyText();if(!thread.length)renderCard();updateScope();primeKpis(); }
function reset(){ thread=[];openId=null;renderCard();rescope(); }
$('mode').addEventListener('change',()=>{rescope();const p=provider();syncKeyLabel();$('api-key').value='';$('mode-badge').textContent=p?`Synthetic data / ${p.name} selected`:'Synthetic data / Offline planner';$('mode-help').textContent=p?(config&&config.public?`Public demo: Ask the analyst has ${p.name} choose which bounded tools to run and writes the interpretation with this site's own key under a spend cap; no visitor key is accepted. Ask and the canned questions stay offline.`:`${p.name} receives your question, bounded history and synthetic tool results. Your key passes through this local server to ${p.name} for this request, is not saved, and the field clears on submit. Live calls incur API charges. Ask and the canned questions stay offline.`):'Offline mode uses a rule-based planner for the supported example questions. No model calls or API charges.';});
$('reset').addEventListener('click',reset);$('day').addEventListener('change',dayChanged);$('day-prev').addEventListener('click',()=>stepDay(-1));$('day-next').addEventListener('click',()=>stepDay(1));$('corrupt').addEventListener('change',rescope);$('shift').addEventListener('change',rescope);$('scope').addEventListener('change',rescope);$('period').addEventListener('change',periodChanged);
$('download').addEventListener('click',()=>{if(!latest)return;const url=URL.createObjectURL(new Blob([latest.report_markdown],{type:'text/markdown'}));const a=node('a');a.href=url;a.download=`synthetic-shift-report-${latest.production_day}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
let printing=false;
$('pdf').addEventListener('click',async()=>{if(!latest||printing)return;
 if(latest.url){const a=node('a');a.href=withQuery(latest.url,'format=pdf');a.download=`synthetic-report-${latest.production_day}.pdf`;a.click();return;}   // an addressed answer: the PDF is the same address with format=pdf
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
 renderSegments();renderQuestionChips();renderCard();
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();$('day').min=config.days[0];$('day').max=config.days[config.days.length-1];
 if(!config.days.includes($('day').value))$('day').value=config.days.includes('2026-09-14')?'2026-09-14':config.days[config.days.length-1];acceptedDay=$('day').value;
 applyPublicMode();updateScope();
 // The golden-set score of the last tracked run, where people look: one line linking to /evals (issue #26).
 if(config.evals){$('evals-foot').hidden=false;if(config.evals.badge){$('evals-badge').textContent=config.evals.badge+' →';$('evals-badge').title=`Golden evals, last tracked run ${config.evals.ran_at||''}${config.evals.commit?' at commit '+config.evals.commit:''}. Browse every case and run it yourself.`;$('evals-badge').hidden=false;}}
 const address=currentAddress();
 if(!(address&&CANONICAL.test(pathOf(address))&&await restore(address))){pushAddress(scopeUrl(),true);primeKpis();}
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if($('mode').value!=='offline')throw new Error('Browser question tool is available only for offline investigations');if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;return ask(input.question,{mode:'offline'});}},{signal:lifecycle.signal});}
 }catch(error){$('status').textContent=error.message;}
}
init();
