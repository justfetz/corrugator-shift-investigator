'use strict';
const $ = id => document.getElementById(id);
let config, sessionId = null, latest = null;
// ---- The thread (issue #63): one answer card that fills in place, earlier answers one line each below
// it, newest on top. In memory only; permalinks are #20. An entry is made at submit and keeps the scope
// it was asked under, so a card reads the same after the rail has moved on. Nothing here disables a
// control while a question is out: a second question simply becomes the open card.
const thread=[];
let openEntry=null, entrySeq=0, sessionEpoch=0, sessionOwner=0, paintSeq=0, railOpen=false;
const now=()=>typeof performance!=='undefined'&&performance.now?performance.now():Date.now();
const PROVIDERS={openai:{name:'OpenAI',header:'X-OpenAI-Key'},anthropic:{name:'Anthropic',header:'X-Anthropic-Key'}};
function provider(){ return PROVIDERS[$('mode').value]||null; }
function isLive(){ return provider()!==null; }
function liveModes(){ // modes the server's own key enables; older servers report only the OpenAI flag
 if(!config)return [];
 if(Array.isArray(config.live_modes))return config.live_modes.filter(m=>PROVIDERS[m]);
 return config.live_model_available?['openai']:[];
}
// "Ask the analyst" runs the agent chosen under "Agent and advanced", or else the one the server's own key
// offers (Anthropic first). Null when neither exists: the button is hidden and every question is offline.
function analystMode(){ const chosen=$('mode').value; if(PROVIDERS[chosen])return chosen; const modes=liveModes(); return modes.includes('anthropic')?'anthropic':(modes[0]||null); }
function syncKeyLabel(){
 const p=provider();
 $('key-label').hidden=!p||!!(config&&config.public);
 if(p)$('key-label-text').textContent=`${p.name} API key (optional if configured on server)`;
}
function liveHelp(){ const p=provider(); return `${p.name} receives your question, recent context and synthetic evidence. Live calls incur charges. Your key is used for this request only.`; }
const NOTE_LINE=/ - reported note \(synthetic, unverified\): /;
function message(label, paragraphs, cls='') {
 const e=node('div',undefined,'message '+cls);if(label)e.append(node('strong',label));
 let notes=null; // consecutive shift-note lines fold into one disclosure instead of a dump
 for(const p of paragraphs){
  if(NOTE_LINE.test(p)){ if(!notes){notes=node('details',undefined,'note-group');notes.append(node('summary'));e.append(notes);} notes.append(node('p',p)); notes.children[0].textContent=`${notes.children.length-1} reported note${notes.children.length-1===1?'':'s'} (synthetic, unverified — observations, not causes)`; continue; }
  notes=null; e.append(node('p',p));
 }
 $('messages').append(e);
}

// ---- Scope: the hidden selects (#day, #period, #shift, #scope, #mode) hold the state the
// request payload reads; the segmented buttons and the sentence are views over them.
const WEEKDAYS=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTHS_LONG=['January','February','March','April','May','June','July','August','September','October','November','December'];
const SHIFT_HOURS={1:'07:00–15:00',2:'15:00–23:00',3:'23:00–07:00 next day'};
const SEGMENTS={period:[['day','Day'],['week','Week'],['month','Month'],['accounting','4-4-5']],shift:[['1','1 · 7–3'],['2','2 · 3–11'],['3','3 · 11–7'],['all','All']]};
const SEGMENT_NAMES={1:'Shift 1, 07:00 to 15:00',2:'Shift 2, 15:00 to 23:00',3:'Shift 3, 23:00 to 07:00 the next day',all:'All three shifts'};
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
   if(group==='shift')b.setAttribute('aria-label',SEGMENT_NAMES[value]);
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
// What a card's title line and an earlier answer's line say about the scope it was asked under.
function whoText(scope){ return scope.scope==='day'?'All shifts':`Shift ${scope.shift}`; }
function windowText(scope){
 const d=utc(scope.day); if(Number.isNaN(d.getTime()))return String(scope.day);
 if(scope.period==='day')return dayText(d);
 if(scope.period==='week'){const [a,b]=weekOf(d);return `Week of ${spanText(a,b)}`;}
 if(scope.period==='month')return `${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
 return `4-4-5 period containing ${dayText(d)}`;
}
function scopeLabel(scope){ return `${whoText(scope)} · ${windowText(scope)}`; }
// A question can name its own window or shift ("How did September go versus August?" with the rail on a
// day): the card then says what the first windowed query actually ran over, in the tool's own words.
function railRange(scope){
 const d=utc(scope.day); if(Number.isNaN(d.getTime()))return null;
 if(scope.period==='day')return [scope.day,scope.day];
 if(scope.period==='week'){const [a,b]=weekOf(d);return [iso(a),iso(b)];}
 if(scope.period==='month')return [iso(new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),1))),iso(new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)))];
 return null;
}
function answeredScope(scope,result){
 const step=(result.trace||[]).find(e=>e.ok&&e.data&&e.data.window&&e.data.window.label), out={who:null,window:null}; if(!step)return out;
 const w=step.data.window, period=w.period==='fiscal'?'accounting':w.period, range=railRange(scope);
 if(period!==scope.period||(range?(w.start!==range[0]||w.end!==range[1]):w.anchor!==scope.day))out.window=w.label;
 const who=step.data.shift===null?'All shifts':typeof step.data.shift==='number'?`Shift ${step.data.shift}`:null;
 if(who&&who!==whoText(scope))out.who=who;
 return out;
}
function entryWho(entry){ return (entry.answered&&entry.answered.who)||whoText(entry.scope); }
function entryLabel(entry){ return `${entryWho(entry)} · ${(entry.answered&&entry.answered.window)||windowText(entry.scope)}`; }
function askNoun(){ return $('period').value!=='day'?(PERIOD_NOUN[$('period').value]||'period'):$('scope').value==='day'?'day':'shift'; }
function updateScope(){
 const ranged=$('period').value!=='day', live=isLive(), scope=currentScope();
 $('scope-help').textContent=scopeSentence(scope); $('scope-chip').textContent=scopeLabel(scope);
 syncKeyLabel();syncSegments();
 const days=storeDays(), index=days?days.indexOf($('day').value):-1;
 $('day-prev').disabled=!days||index<=0; $('day-next').disabled=!days||index<0||index>=days.length-1;   // the ends of the store; nothing waits on a request
 $('mode-badge').textContent=live?`Synthetic data / ${provider().name} selected`:'Synthetic data / Offline planner';
 const noun=PERIOD_NOUN[$('period').value]||'period';
 if(ranged)$('mode-help').textContent=`Ask about the selected ${noun}: KPIs against the previous one, the downtime Pareto, waste, stops per day, trends, totals by grade, over/under-run, the speed opportunity.${live?' '+liveHelp():' No model calls or API charges.'} Period report builds the output heatmap, daily matrix, recurring downtime and notes without a model.`;
 else $('mode-help').textContent=live?liveHelp():'Ask about performance, setups, rankings or notes within the selected scope. No model calls or API charges.';
 $('submit').textContent='Ask'; $('report').hidden=!ranged;
 const analyst=analystMode(); $('analyst').hidden=!analyst;
 if(analyst)$('analyst').title=`${PROVIDERS[analyst].name} chooses the queries and writes the interpretation; every number still comes from the queries. It takes several seconds and ${config&&config.public?'uses this site’s own key under a spend cap':'incurs API charges'}.`;
 $('question').placeholder=ranged?`Ask about this ${noun}: KPIs against the previous one, downtime, waste, trends, grades`:`Ask about this ${askNoun()}: speed, downtime, waste, setups, notes, orders`;
 for(const b of chipButtons)b.disabled=ranged&&b.dataset.period==='day';   // the per-shift matrices have no period form
 if(!openEntry){ $('card-question').textContent=`Ask about this ${askNoun()}`; $('status').textContent=`${scopeLabel(scope)} · no question yet`; }
 renderThread();   // an earlier answer's line names its window once the rail has moved off it
}

// ---- Example questions, grouped by intent. Daily chips run the day tools for the selected scope; the
// period chips are the phrasings the offline planner routes to the record-store tools (docs/WORKBENCH.md).
// "{period}" is the window word of the Period control (week under Day, as before); a fourth entry 'day' marks
// a question the period tools cannot answer (the per-shift matrices), disabled under Week, Month and 4-4-5.
const QUESTION_GROUPS=[
 ['Speed',[['Speed to target','Show speed to target'],['Setup matrix','Show setup matrix',null,'day'],['Speed trend, 8 weeks','Trend of speed over eight weeks']]],
 ['Downtime',[['Why was this shift slow?','Why was this shift slow? Chart downtime by reason.'],['Costliest reason this {period}','Which downtime reason cost the most minutes this {period}?'],['Stops per day this {period}','Stops per day this {period}'],['Read notes','Show shift and downtime notes',null,'day']]],
 ['Waste',[['Dry-end waste','Show dry-end quality waste'],['Bond or warp?','Is it bond or warp?'],['Trim trend, 8 weeks','Trend of trim over eight weeks']]],
 ['Compare',[['Summary + charts','Summarize performance, downtime and waste. Show supporting charts.'],['This {period} vs last','How did this {period} go versus the previous one?'],['September vs August','How did September go versus August?'],['Compare shifts','Compare all shifts','day','day'],['Rank losses','Show rankings',null,'day']]],
 ['Orders',[['Orders + quantities','Show orders and quantities',null,'day']]],
];
const chipButtons=[];
function periodWord(){ return {day:'week',week:'week',month:'month',accounting:'period'}[$('period').value]||'week'; }
function renderQuestionChips(){
 const box=$('examples'); if(!box)return; box.replaceChildren(); chipButtons.length=0;
 const word=periodWord();
 for(const [intent,chips] of QUESTION_GROUPS){
  const group=node('div',undefined,'chip-group'); group.append(node('h4',intent));
  const row=node('div',undefined,'chips');
  for(const [label,text,scope,period] of chips){
   const question=text.replaceAll('{period}',word);
   const b=node('button',label.replaceAll('{period}',word)); b.type='button'; b.dataset.question=question; if(scope)b.dataset.scope=scope; if(period)b.dataset.period=period; b.title=question;
   b.addEventListener('click',()=>{ if(b.disabled)return; if(b.dataset.scope&&$('scope').value!==b.dataset.scope){$('scope').value=b.dataset.scope;rescope();} setRail(false); $('question').value=question; ask(question).catch(()=>{}); });   // a canned question is a fixed query: always the offline planner
   row.append(b); chipButtons.push(b);
  }
  group.append(row); box.append(group);
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
// The viewBox width follows where the chart is drawn: 420 on a phone, 480 for each of two charts side by
// side in a desktop card (issue #63: both charts above the fold), 690 for a chart with the card to itself.
let chartPair=false;
function wideWidth(){ try{return typeof matchMedia==='function'&&matchMedia('(min-width:1320px)').matches;}catch(e){return false;} }
const CHART_W=()=>(typeof window!=='undefined'&&window.innerWidth&&window.innerWidth<850)?420:chartPair?480:690;
const ROW_H=()=>chartPair?26:30;
const niceCeil=v=>{ if(!(v>0))return 1; const p=10**Math.floor(Math.log10(v)); const m=v/p; return (m<=1?1:m<=2?2:m<=2.5?2.5:m<=5?5:10)*p; };
const fmtTick=(v,unit)=>v>=1000?`${(v/1000).toLocaleString(undefined,{maximumFractionDigits:1})}K`:Number.isInteger(v)?String(v):v.toFixed(v<1?2:1);
const fmtVal=(v,unit)=>v===null||v===undefined?'unavailable':`${Number(v).toLocaleString(undefined,{maximumFractionDigits:Math.abs(v)>=100?0:Math.abs(v)>=10?1:2})}${unit&&unit!=='count'?' '+unitShort(unit):''}`;
const shortDay=iso=>{ const d=utc(iso); return Number.isNaN(d.getTime())?String(iso):`${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
function barPath(x,y,w,h,cls,r=4){ // square at the baseline, 4px rounded data end
 w=Math.max(0,w); r=Math.min(r,w,h/2);
 const d=w<=0?`M${x} ${y}h0v${h}h0Z`:`M${x} ${y}h${w-r}a${r} ${r} 0 0 1 ${r} ${r}v${h-2*r}a${r} ${r} 0 0 1 ${-r} ${r}H${x}Z`;
 return svgNode('path',{d,class:cls});
}
function chartCard(title,notes=[]){ const wrap=node('div',undefined,'chart'), head=node('div',undefined,'chart-head');head.append(node('h3',title));wrap.append(head);wrap.head=head;for(const n of notes)if(n)wrap.append(node('p',n.text||n,n.cls||'muted'));return wrap; }   // the head row takes the link back to the query (#63)
function legend(items){ const box=node('div',undefined,'lg');for(const [cls,label] of items){const item=node('span');item.append(node('i',undefined,'sw '+cls),node('span',label));box.append(item);}return box; }
// Horizontal axis along the bottom: hairline gridlines at 0, ½ and the nice maximum, labels beneath.
function xAxis(svg,x0,x1,yTop,yBottom,max,unit){
 for(const t of [0,.5,1]){const x=x0+(x1-x0)*t;svg.append(svgNode('line',{x1:x,x2:x,y1:yTop,y2:yBottom,class:'grid'+(t===0?' base':'')}));svg.append(svgNode('text',{x,y:yBottom+16,'text-anchor':t===0?'start':t===1?'end':'middle',class:'tick'},fmtTick(max*t,unit)));}
}
function frame(W,rows,rowH,top=8,wideValues=false){ const H=top+rows*rowH+28; const labelW=W<440?110:W<500?138:160, valueW=wideValues?(W<500?130:215):(W<500?96:150); return {W,H,labelW,valueW,x0:labelW+8,x1:W-valueW-8,top,chars:W<440?14:W<500?19:21}; }
function labelText(svg,text,x,y,maxChars){ const t=String(text); svg.append(svgNode('text',{x,y,'text-anchor':'end',class:'lbl'},t.length>maxChars?t.slice(0,maxChars-1)+'…':t)); }
// Generic single-series horizontal bars (downtime by reason, dry-end rejects, stops per day).
function barChart(chart,wrap){
 const rows=chart.rows, W=CHART_W(), rh=ROW_H(), f=frame(W,rows.length,rh), max=niceCeil(Math.max(...rows.map(r=>Number(r[chart.y])||0)));
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`${chart.title||'Shift '+chart.shift} ${chart.unit} chart`});
 xAxis(svg,f.x0,f.x1,f.top,f.top+rows.length*rh,max,chart.unit);
 rows.forEach((r,i)=>{const y=f.top+i*rh, v=Number(r[chart.y])||0, label=r.weekday?`${r.weekday} ${shortDay(r[chart.x])}`:r[chart.x];
  labelText(svg,label,f.x0-8,y+rh/2+4,f.chars);
  svg.append(barPath(f.x0,y+(rh-16)/2,(v/max)*(f.x1-f.x0),16,chart.coverage==='complete'?'bar cur':'bar incomplete'));
  const detail=`${label}: ${fmtVal(v,chart.unit)}`+(r.minutes_per_stop!==undefined?`; ${fmtVal(r.minutes,'min')}, ${fmtVal(r.minutes_per_stop,'min/stop')}`:'');
  const t=svgNode('text',{x:f.x1+8,y:y+rh/2+4,tabindex:0,'aria-label':detail,class:'val'},fmtVal(v,chart.unit));t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg);
}
// Wet-end speed: bars with the grade-target mark; long lists show the best and worst by % of target: five
// of each with the card to itself, three of each beside another chart, the rest behind "Show all".
function speedChart(chart,wrap){
 const all=chart.rows, W=CHART_W(), rh=ROW_H(), keep=chartPair?3:5, word=keep===3?'three':'five', long=all.length>2*keep; let showAll=!long;
 const box=node('div'); wrap.append(box);
 const draw=()=>{
  box.replaceChildren();
  let rows=all, divider=-1;
  if(!showAll){const sorted=[...all].sort((a,b)=>(b.speed_to_target_pct??0)-(a.speed_to_target_pct??0));rows=[...sorted.slice(0,keep),...sorted.slice(-keep)];divider=keep;}
  const slots=rows.length+(divider>0?1:0), f=frame(W,slots,rh), max=niceCeil(Math.max(...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r.target_fpm)||0))));
  const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`Shift ${chart.shift} ${chart.unit} chart`});
  xAxis(svg,f.x0,f.x1,f.top,f.top+slots*rh,max,chart.unit);
  let slot=0;
  rows.forEach((r,i)=>{
   if(i===divider){svg.append(svgNode('text',{x:f.x0,y:f.top+slot*rh+rh/2+4,class:'tick'},W<500?`… ${all.length-2*keep} more in between …`:`… ${all.length-2*keep} more between the best ${word} and the worst ${word} …`));slot++;}
   const y=f.top+slot*rh; slot++;
   labelText(svg,r[chart.x],f.x0-8,y+rh/2+4,f.chars);
   svg.append(barPath(f.x0,y+(rh-16)/2,(Number(r[chart.y])/max)*(f.x1-f.x0),16,chart.coverage==='complete'?'bar cur':'bar incomplete'));
   if(r.target_fpm){const x=f.x0+r.target_fpm/max*(f.x1-f.x0);svg.append(svgNode('line',{x1:x,x2:x,y1:y+(rh-22)/2,y2:y+(rh+22)/2,class:'target'}));}
   const detail=r.target_fpm?`${r[chart.x]} / ${r.grade||'grade-weighted'}: ${Number(r[chart.y]).toFixed(1)} ft/min; target ${r.target_fpm.toFixed(1)}; ${r.speed_to_target_pct.toFixed(1)}% of target. Includes downtime.`:`${r[chart.x]}: ${Number(r[chart.y]).toFixed(1)} ${chart.unit}`;
   const value=svgNode('text',{x:f.x1+8,y:y+rh/2+4,tabindex:0,'aria-label':detail,class:'val'},r.target_fpm?`${Number(r[chart.y]).toFixed(1)} / ${r.speed_to_target_pct.toFixed(1)}%`:Number(r[chart.y]).toFixed(1));value.append(svgNode('title',{},detail));svg.append(value);
  });
  box.append(svg);
  if(long){const toggle=node('button',showAll?`Show the ${word} best and ${word} worst`:`Show all ${all.length}`,'secondary more');toggle.type='button';toggle.addEventListener('click',()=>{showAll=!showAll;draw();});box.append(toggle);}
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
 const rows=chart.rows, W=CHART_W(), f=frame(W,rows.length,36), key=chart.y, prevKey=chart.y2;
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
 if(isSpeed)notes.push(chartPair?'ft/min / % of grade target; elapsed time includes stops.':'Value = ft/min / % of target. Hover or focus a value for grade and target details. 100% meets target; elapsed time includes downtime.');
 const wrap=chartCard(title,notes);
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));
 else if(chart.type==='line')lineChart(chart,wrap);
 else if(chart.type==='range')rangeChart(chart,wrap);
 else if(isPareto)paretoChart(chart,wrap);
 else if(isWaste)wasteChart(chart,wrap);
 else if(chart.y2)pairedChart(chart,wrap);
 else if(isSpeed)speedChart(chart,wrap);
 else barChart(chart,wrap);
 const link=queryLink(chart.source_result_id);if(link)wrap.head.append(link);
 $('charts').append(wrap);
}
const labels={setup_id:'Setup',wet_end_id:'Wet-end / paper change',grade:'Grade',start:'Start',end:'End',width_in:'Width (in)',lineal_ft:'Lineal (ft)',actual_fpm:'Speed (ft/min)',target_fpm:'Target (ft/min)',speed_to_target_pct:'% of target',stop_count:'Stops',down_minutes:'Down (min)',rejected_sqft:'Rejects (sq ft)',notes:'Reported note',record_id:'Record',record_type:'Type',rank:'Rank',item:'Item',value:'Value',unit:'Units / order',day:'Production day',shift:'Shift',current_count:'Stops now',previous_count:'Stops before',current_minutes:'Minutes now',previous_minutes:'Minutes before',minutes_change:'Change (min)',speed_rank:'Speed rank',maintenance_rank:'Maint. rank',waste_rank:'Waste rank',valid_setups:'Dry-end setups',paper_changes:'Paper changes',observed_shift_fpm:'Speed (ft/min)',maintenance_pct:'Maintenance %',operator_pct:'Operator %',dry_end_pct:'Dry-end %',target_fpm:'Target (ft/min)',wet_ends:'Wet ends',volume_msf:'Volume (MSF)',share_of_volume_pct:'Share %',speed_run_fpm:'Speed w/o stops',speed_elapsed_fpm:'Elapsed speed',setup_stops:'Setup stops',setup_stop_minutes:'Setup stop min',trim_pct:'Trim %',previous_volume_msf:'Prev MSF',delta_volume_msf:'Δ MSF',knife:'Knife',lines:'Lines',nominal_cuts:'Nominal cuts',actual_cuts:'Actual cuts',delta_cuts:'Δ cuts',overrun_pct:'Over/under %',nominal_sheets:'Nominal sheets',actual_sheets:'Actual sheets',over_sheets:'Sheets over',under_sheets:'Sheets under',lines_inside_band_pct:'In band %',lines_over_pct:'Over %',lines_under_pct:'Under %',downtime_pct_of_elapsed:'Down % of elapsed',lineal_per_setup:'Lineal / setup',lineal_per_wet_end:'Lineal / paper change',slice_by:'Slice',slice:'Width / crew',label:'Cell',run_minutes:'Run min',typical_fpm:'Typical ft/min',median_setup_fpm:'Median setup ft/min',best_fifth_fpm:'Best fifth ft/min',gap_fpm:'Gap ft/min',typical_pct_of_target:'Typical % target',best_fifth_pct_of_target:'Best fifth % target',opportunity_hours:'Opportunity h',best_fifth_dry_end_pct:'Dry-end % (fast fifth)',rest_dry_end_pct:'Dry-end % (rest)',reject_penalty_points:'Reject penalty pts',status:'Status'};
function showTable(data){
 const titles={get_order_matrix:'Orders and quantities — fragments; demand repeats on continuing orders',get_setup_matrix:'Setup matrix',get_shift_notes:'Shift and downtime notes (synthetic, unverified)',get_rankings:`${data.metric||''} ranking / largest losses first`,get_grade_totals:'Totals by grade',get_overrun:'Over/under-run by knife (nominal vs actual cuts)',get_speed_opportunity:'Speed opportunity: typical vs best fifth, by grade and width / crew'};
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
 if(!config||!question||openEntry||(config.demo_guards&&config.demo_guards.rate_limit_per_min>0))return;
 try{
  const response=await post('/api/ask',{question,day:$('day').value,shift:Number($('shift').value),corrupt:$('corrupt').checked,session_id:null,mode:'offline',scope:$('scope').value,period:$('period').value});
  const result=await response.json();
  if(token!==primeToken||openEntry||!response.ok||!Array.isArray(result.trace))return;   // a question was asked meanwhile: the card is its answer now
  const entry=result.trace.find(e=>e.ok&&e.tool==='get_period_kpis');
  if(!entry)return;
  renderKpiTiles(entry.data,`scope tiles · get_period_kpis · ${result.usage.tool_calls} tool call${result.usage.tool_calls===1?'':'s'} · $0 · offline planner`);
  const trail=node('details',undefined,'prime-trail');trail.append(node('summary','How these tiles were produced'));trail.append(node('p',`Offline question: “${question}”. The same evidence a typed question gets; ask one for charts and the full trail.`,'muted'));trail.append(traceRow(entry));$('kpis').append(trail);
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
let revealQueries=()=>{};   // unfolds the query lines behind "N more queries"; set by renderQueries for the answer on screen
function queryLink(resultId){ const i=stepIndex.get(resultId); if(i===undefined)return null; const a=node('a',`query ${i+1} ↑`,'query-link'); a.href=`#query-${i}`; a.title='The query box that produced these rows'; a.addEventListener('click',()=>revealQueries()); return a; }
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
const VISIBLE_QUERIES=3;
function renderQueries(result){
 const box=$('queries'); box.replaceChildren(); stepIndex=new Map(); revealQueries=()=>{};
 const steps=result.trace||[]; if(!steps.length)return;
 steps.forEach((e,i)=>{if(e.result_id)stepIndex.set(e.result_id,i);});
 const total=steps.reduce((sum,e)=>sum+(typeof e.elapsed_ms==='number'?e.elapsed_ms:0),0);   // the count and the wall time are on the card's title line; the server's own time is in the foot
 // Step 1 open; the rest, and all on a phone, one line with the SQL on tap. The first three lines show (one on
 // a phone) and the others wait behind "N more queries", so the card's charts stay above the fold (#63).
 const phone=phoneWidth(), visible=phone?1:VISIBLE_QUERIES, boxes=steps.map((entry,i)=>{const d=queryBox(entry,i);d.open=!phone&&i===0;d.hidden=i>=visible;box.append(d);return d;});
 const foot=node('div',undefined,'queries-foot');
 const all=node('button',undefined,'secondary');all.type='button';
 const label=()=>{const more=boxes.filter(b=>b.hidden).length;all.textContent=more?`${more} more ${more===1?'query':'queries'}`:boxes.every(b=>b.open)?'Collapse all':'Expand all';};
 revealQueries=()=>{for(const b of boxes)b.hidden=false;label();};
 all.addEventListener('click',()=>{if(boxes.some(b=>b.hidden))return revealQueries();const open=!boxes.every(b=>b.open);for(const b of boxes)b.open=open;label();});label();foot.append(all);
 const sqls=steps.map((e,i)=>[e,i,sqlText(e)]).filter(x=>x[2]);
 if(sqls.length){const copy=node('button','Copy all SQL','secondary');copy.type='button';const text=sqls.map(([e,i,sql])=>`-- query ${i+1}: ${e.tool}(${argText(e.arguments)})\n${sql}`).join('\n\n');copy.dataset.copy=text;copy.addEventListener('click',()=>copyText(text,copy));foot.append(copy);}
 foot.append(node('span',`${steps.length} ${steps.length===1?'query':'queries'} · ${total.toFixed(0)} ms on the server. Fixed, parameterized queries. Visitors cannot edit them.`,'queries-note'));
 box.append(foot);
}
function usageText(usage){ return usage.model_calls?`${usage.tool_calls} tool calls · ${usage.model_calls} model call${usage.model_calls===1?'':'s'} · estimated $${Number(usage.cost_usd||0).toFixed(4)}`+(usage.reserved_usd!==undefined?` · reserved $${Number(usage.reserved_usd).toFixed(2)}`:''):`${usage.tool_calls} tool call${usage.tool_calls===1?'':'s'} · $0 model cost`; }
// Paint the answer, tables and charts first; the trace table (the largest part of the page)
// is built after that paint so it never delays it (issue #33). A requestAnimationFrame callback
// runs *before* its frame paints, so the work is queued for the frame after: two frames deep.
// Without a frame scheduler (tests), it renders synchronously.
function afterPaint(f){if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(f));else f();}
// ---- Shift KPI tiles (issue #63): the eleven numbers of a shift's KPI row on six tiles, one row under the
// charts. Each tile leads with one number and carries its companion beneath; every value is the tool's own.
// The thresholds only decide the warning edge, as they did before; the tile prints no number a tool did not return.
function shiftTiles(entry,result,prefix){
 const r=entry.data.rows[0], incomplete=entry.data.coverage!=='complete', who=entry.data.shift?'Shift '+entry.data.shift:result.period_label;
 const fixed=(v,places)=>Number(v).toFixed(places), count=v=>Number(v).toLocaleString(undefined,{maximumFractionDigits:0});
 const tiles=[
  ['Speed',fixed(r.observed_shift_fpm,1),'ft/min',`${fixed(r.speed_to_target_pct,1)}% of target`,r.speed_to_target_pct<100,'Speed / target = lineal feet / sum(grade target ft/min × elapsed minutes) × 100. Includes downtime; weighted by grade duration.'],
  ['Maintenance',fixed(r.maintenance_pct,2),'%',`operator ${fixed(r.operator_pct,2)}%`,r.maintenance_pct>2.5||r.operator_pct>2.5,'Recorded stop time by kind, as a share of the shift. The tile is edged when either is above its target.'],
  ['Trim',fixed(r.trim_pct,2),'%',`shear ${fixed(r.shear_pct,2)}%`,false,'Side trim and shear waste, % of gross area.'],
  ['Dry-end waste',fixed(r.dry_end_pct,2),'%',r.dry_end_pct>1?'above its flag':'within its flag',r.dry_end_pct>1,'Dry-end rejects, % of gross area. The summary line states the flag.'],
  ['Dry-end setups',count(r.valid_setups),'',`${count(r.paper_changes)} paper changes`,false,'Paper changes: the count of distinct wet-end runs, as defined by the plant expert.'],
  ['Throughput',fixed(r.throughput_in,1),'in',`${count(r.lineal_per_setup)} ft lineal / setup`,false,''],
 ];
 for(const [label,value,unit,sub,bad,title] of tiles){
  const card=node('div',undefined,'metric'+(bad||incomplete?' warning':''));
  card.append(node('div',prefix?`${who} · ${label}`:label,'label'));
  const v=node('div',undefined,'value');v.append(node('span',incomplete?'Incomplete':value));if(unit&&!incomplete)v.append(node('small',unit,unit==='%'?'pct':undefined));card.append(v);
  card.append(node('div',incomplete?`${entry.data.excluded_records?.length||'Missing/invalid'} excluded record(s)`:sub,'sub'));
  if(title)card.title=title;
  $('metrics').append(card);
 }
}
function showResult(result) {
 latest=result; $('foot-note').textContent='';   // the exports sit on the card's footer line, which only a finished answer shows
 for(const id of ['heatmaps','kpis','tables','charts','metrics','trace'])$(id).replaceChildren();   // each answer replaces the last one's evidence
 renderQueries(result);   // the query box: first below the question, built in the first paint (#40)
 const kpiEntry=result.trace.find(e=>e.ok&&(e.tool==='get_period_kpis'||e.tool==='compare_periods'));
 if(kpiEntry)renderKpiTiles(kpiEntry.data,`${kpiEntry.tool} · this question`);
 const shiftRows=result.trace.filter(e=>e.ok&&(e.tool==='get_shift_kpis'||e.tool==='get_shift_overview'||e.tool==='get_period_report'));
 for(const entry of shiftRows)shiftTiles(entry,result,shiftRows.length>1);   // all shifts: one row of tiles per shift, each named
 const plain=result.charts.filter(c=>c.type!=='calendar_heatmap').length;
 chartPair=plain>=2&&wideWidth(); $('charts').className=plain>=2?'pair':'';   // two charts or more sit side by side on a desktop (#63)
 for(const table of result.tables||[])showTable(table);
 for(const chart of result.charts)showChart(chart);
 const paint=++paintSeq;   // one trace per paint: a card reopened before its frames ran does not build the rows twice
 afterPaint(()=>{if(latest!==result||paint!==paintSeq)return;result.trace.forEach((entry,i)=>{const row=traceRow(entry);row.id=`step-${i}`;$('trace').append(row);});});   // a newer answer already replaced the evidence: skip
 $('calls').textContent=' · '+usageText(result.usage);
 const ctx=node('details',undefined,'trace-context');ctx.append(node('summary','Context carried to the next question'));ctx.append(node('pre',JSON.stringify(result.context,null,2)));$('context').replaceChildren(ctx);
}

// ---- The answer card and the thread (issue #63). The card is one element with four states: idle (no
// question yet; the scope tiles), skeleton (asked, nothing back yet), done and failed. Submitting paints
// the skeleton in the same task as the click, and the answer fills that same card when it arrives.
const EMPTY_TEXT='Type a question above or pick one on the left. An answer shows its queries first, then the summary, the charts and the numbers.';
function clearEvidence(){
 latest=null; stepIndex=new Map(); revealQueries=()=>{};
 for(const id of ['queries','messages','charts','heatmaps','kpis','metrics','tables','trace','context'])$(id).replaceChildren();
 $('calls').textContent=''; $('foot-note').textContent=''; $('execution').open=false;
}
function entryMeta(entry){
 const parts=[entryLabel(entry)]; if(entry.corrupt)parts.push('bad trim record included'); parts.push(entry.modeWord);
 if(entry.state==='pending')parts.push(entry.report?'building the report; the first one can take a minute…':'querying…');
 else if(entry.state==='failed')parts.push('could not complete');
 else{
  const r=entry.result, n=(r.trace||[]).length, u=r.usage||{};
  if(r.status&&r.status!=='complete')parts.push(r.status);
  parts.push(`${n} ${n===1?'query':'queries'}`);
  if(u.model_calls)parts.push(`${u.model_calls} model call${u.model_calls===1?'':'s'} · est. $${Number(u.cost_usd||0).toFixed(4)}`);
  if(entry.ms!==null)parts.push(`${Math.round(entry.ms)} ms`);
 }
 return parts.join(' · ');
}
// The title line: <question> · Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · 93 ms. The ms is the wall
// time in this browser from submit to the rendered card; the server's own time is on the tooltip.
function paintStatus(){
 const entry=openEntry; if(!entry)return;
 $('status').textContent=entryMeta(entry);
 if(entry.state!=='done'||entry.ms===null){$('status').title='';return;}
 const r=entry.result, server=typeof r.elapsed_ms==='number'?r.elapsed_ms:(r.trace||[]).reduce((sum,e)=>sum+(typeof e.elapsed_ms==='number'?e.elapsed_ms:0),0);
 $('status').title=`${Math.round(entry.ms)} ms from submit to the rendered card in this browser; ${server.toFixed(0)} ms of it in the queries on the server. Planner: ${r.mode}.`;
}
function paintCard(){
 const entry=openEntry, card=$('card');
 clearEvidence();
 if(!entry){
  card.className='card answer idle'; card.setAttribute('aria-busy','false'); card.setAttribute('aria-label','Answer'); $('status').title='';
  $('messages').append(node('p',EMPTY_TEXT,'empty')); updateScope(); return;   // updateScope writes the idle title for the selected scope
 }
 card.setAttribute('aria-label',`Answer: ${entry.question}`); $('card-question').textContent=entry.question;
 if(entry.state==='pending'){ card.className='card answer skeleton'; card.setAttribute('aria-busy','true'); paintStatus(); return; }
 card.setAttribute('aria-busy','false');
 if(entry.state==='failed'){ card.className='card answer failed'; message('Could not complete',[entry.error],'error'); paintStatus(); return; }
 card.className='card answer done';
 if(entry.note)message('Note',[entry.note]);
 message(null,entry.result.sections||[]);
 showResult(entry.result); paintStatus();
}
// Earlier answers, newest on top, one line each: <question> · Shift 2 · 52 ms · Open. The window is named
// only when it is not the one the rail shows now. Rebuilding the lines keeps keyboard focus on the Open
// button it was on.
function entryLine(entry){
 const current=currentScope(), parts=[entryWho(entry)];
 if(entry.answered&&entry.answered.window)parts.push(entry.answered.window);
 else if(entry.scope.day!==current.day||entry.scope.period!==current.period)parts.push(windowText(entry.scope));
 parts.push(entry.state==='pending'?'querying…':entry.state==='failed'?'could not complete':`${Math.round(entry.ms??0)} ms`);
 return parts.join(' · ');
}
function renderThread(){
 let focused=null, again=null; try{focused=document.activeElement&&document.activeElement.dataset?document.activeElement.dataset.entry:null;}catch(e){}
 $('thread').replaceChildren(...thread.filter(entry=>entry!==openEntry).map(entry=>{
  const row=node('article',undefined,'card earlier'+(entry.state==='failed'?' failed':'')); row.setAttribute('aria-label',`Earlier answer: ${entry.question}`);
  const open=node('button','Open','secondary open'); open.type='button'; open.dataset.entry=String(entry.id); open.setAttribute('aria-label',`Open the answer to: ${entry.question}`); open.addEventListener('click',()=>openCard(entry,true));
  if(focused&&focused===open.dataset.entry)again=open;
  row.append(node('span',entry.question,'q'),node('span',entryLine(entry),'meta'),open); return row;
 }));
 if(again)again.focus?.();
}
function toCard(){ try{if(window.scrollY>0)window.scrollTo({top:0});}catch(e){} }   // the open card is always at the top of the main column
function openCard(entry,focus=false){ openEntry=entry; primeToken++; paintCard(); renderThread(); toCard(); if(focus)$('card-question').focus?.(); }   // from an Open button, focus follows to the card it opened
function setRail(open){ railOpen=!!open; try{document.body.classList.toggle('rail-open',railOpen);}catch(e){} $('rail-toggle').setAttribute('aria-expanded',String(railOpen)); $('rail-toggle').textContent=railOpen?'Close':'Questions'; }
// Ask is the deterministic path: the offline planner, whatever agent is chosen. "Ask the analyst" passes the
// live agent as `mode`. Either way the request is the same /api/ask payload as before.
async function ask(question,{report=false,mode='offline'}={}) {
 if(typeof question!=='string'||!question.trim())throw new Error('Enter a question');
 const live=report?null:(PROVIDERS[mode]||null), epoch=sessionEpoch;
 const entry={id:++entrySeq,question:question.trim(),scope:{day:$('day').value,period:$('period').value,shift:Number($('shift').value),scope:$('scope').value},corrupt:$('corrupt').checked,mode:live?mode:'offline',modeWord:live?live.name:'offline',report,epoch,state:'pending',started:now(),ms:null,result:null,error:null,note:null,answered:null};
 // The same question, still out, asked again (a double click): it is the card already waiting, not a second
 // request. Nothing is disabled to prevent it, so this is what keeps a second paid round from going out.
 const twin=thread.find(e=>e.state==='pending'&&e.epoch===epoch&&e.question===entry.question&&e.mode===entry.mode&&e.report===report&&e.corrupt===entry.corrupt&&['day','period','shift','scope'].every(k=>e.scope[k]===entry.scope[k]));
 if(twin){ entrySeq--; if(openEntry!==twin)openCard(twin); return twin.done; }
 entry.done=new Promise((resolve,reject)=>{entry.settle={resolve,reject};}); entry.done.catch(()=>{});
 thread.unshift(entry); openEntry=entry; primeToken++;
 paintCard(); renderThread(); toCard();   // the skeleton card is in the DOM before this function first yields: the same task as the click
 try{
  if(!config){await boot;if(!config)throw new Error('Workbench unavailable');}
  const headers={};
  if(live && $('api-key').value && !(config&&config.public))headers[live.header]=$('api-key').value;
  if(live)$('api-key').value='';   // the key is cleared when it is sent; an offline question leaves it for the analyst
  const body={question,day:entry.scope.day,shift:entry.scope.shift,corrupt:entry.corrupt,session_id:sessionId,mode:live?mode:'offline',scope:entry.scope.scope,period:entry.scope.period};
  if(report)body.report=true;
  const sent=post('/api/ask',body,headers);
  for(const p of Object.values(PROVIDERS))delete headers[p.header];
  const response=await sent;
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');
  if(result.session_reset&&body.session_id)entry.note='The server restarted since your last question, so this answer started a fresh conversation; earlier follow-up context was not available.';
  // The conversation follows the newest question of the current scope, whichever answer lands last; an
  // answer that arrives after the rail has moved on belongs to a conversation that is over.
  if(epoch===sessionEpoch&&entry.id>sessionOwner){sessionId=result.session_id;sessionOwner=entry.id;}
  entry.result=result; entry.answered=answeredScope(entry.scope,result); entry.state='done';
  if(openEntry===entry)paintCard();
  entry.ms=now()-entry.started;
  const brief={status:result.status,sections:result.sections,production_day:result.production_day,shifts:result.selected_shifts};
  entry.settle.resolve(brief); return brief;
 }catch(error){
  entry.state='failed'; entry.error=error.message; entry.ms=now()-entry.started;
  if(openEntry===entry)paintCard();
  entry.settle.reject(error); throw error;
 }finally{ if(openEntry===entry)paintStatus(); renderThread(); }
}
function submitQuestion(mode){ const question=$('question').value; if(!question.trim()){$('question').focus?.();return;} ask(question,mode?{mode}:{}).catch(()=>{}); }
$('ask').addEventListener('submit',event=>{event.preventDefault();submitQuestion(null);});
$('analyst').addEventListener('click',()=>{const mode=analystMode();if(mode)submitQuestion(mode);});
$('report').addEventListener('click',()=>{ setRail(false); $('question').value=''; ask(`Build ${PERIOD_NOUN[$('period').value]||'period'} report`,{report:true}).catch(()=>{}); });
$('rail-toggle').addEventListener('click',()=>setRail(!railOpen));
$('came-back').addEventListener('click',()=>{$('execution').open=true;});
// A scope change starts a fresh conversation and puts the open answer on the thread with the scope it was
// asked under; the card goes back to idle for the new scope. "Clear answers" also empties the thread.
function rescope(){ sessionId=null; sessionEpoch++; sessionOwner=0; $('api-key').value=''; openEntry=null; paintCard(); primeKpis(); }
function reset(){ thread.length=0; rescope(); }
$('mode').addEventListener('change',()=>{const p=provider();$('api-key').value='';updateScope();$('mode-help').textContent=p?(config&&config.public?`Public demo: Ask the analyst has ${p.name} choose which bounded tools to run and write the interpretation with this site's own key under a spend cap; no visitor key is accepted.`:`Ask the analyst sends ${p.name} your question, bounded history and synthetic tool results. Your key passes through this local server to ${p.name} for this request, is not saved, and the field clears on submit. Live calls incur API charges. Ask and the canned questions stay offline.`):'Offline mode uses a rule-based planner for the supported example questions. No model calls or API charges.';});
$('reset').addEventListener('click',reset);$('day').addEventListener('change',dayChanged);$('day-prev').addEventListener('click',()=>stepDay(-1));$('day-next').addEventListener('click',()=>stepDay(1));$('corrupt').addEventListener('change',rescope);$('shift').addEventListener('change',rescope);$('scope').addEventListener('change',rescope);$('period').addEventListener('change',periodChanged);
$('download').addEventListener('click',()=>{if(!latest)return;const url=URL.createObjectURL(new Blob([latest.report_markdown],{type:'text/markdown'}));const a=node('a');a.href=url;a.download=`synthetic-shift-report-${latest.production_day}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
let pdfPending=false;   // one PDF at a time, without disabling the control
$('pdf').addEventListener('click',async()=>{if(!latest||pdfPending)return;const result=latest;pdfPending=true;$('foot-note').textContent='Building the PDF…';try{const response=await post('/api/report',{session_id:result.session_id,run_id:result.run_id});if(!response.ok){const error=await response.json();throw new Error(error.error||'Report unavailable');}const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download=`synthetic-report-${result.production_day}.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);if(latest===result)$('foot-note').textContent='';}catch(error){if(latest===result)$('foot-note').textContent=error.message;}finally{pdfPending=false;}});
function applyPublicMode(){
 // Hosted synthetic demo: no visitor keys, ever. A live option stays only when the server's own key for that provider and the cap are enabled.
 if(!config.public)return;
 $('key-label').hidden=true;$('api-key').value='';
 const modes=liveModes();
 for(const [value,entry] of Object.entries(PROVIDERS)){
  const option=$('mode').querySelector?.(`option[value="${value}"]`);
  if(!option)continue;
  if(modes.includes(value))option.textContent=`${entry.name} / server key`;else option.remove();
 }
 if(!modes.length){$('mode').value='offline';$('mode').disabled=true;$('mode-help').textContent='Public demo: the free offline planner answers the supported questions from synthetic records. No model calls, no keys.';}
 else{
  // Offline is the default even when the site has a key (issue #42): it answers in tens of
  // milliseconds; the model path takes seconds and spends the site's budget. The model is a choice.
  if(!modes.includes($('mode').value))$('mode').value='offline';
  const live=modes.includes('anthropic')?'anthropic':modes[0];
  $('mode-help').textContent=`Public demo: Ask answers instantly with fixed queries over synthetic records. Ask the analyst has ${PROVIDERS[live]?.name||'the model'} pick the tools and write the interpretation; that takes several seconds and uses this site's own key under a spend cap. Every number comes from the queries either way; no visitor key is accepted.`;
 }
 $('mode-badge').textContent=modes.length?'Synthetic data / Public demo · model available':'Synthetic data / Public demo';
}
async function init(){
 renderSegments();renderQuestionChips();
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();$('day').min=config.days[0];$('day').max=config.days[config.days.length-1];
 if(!config.days.includes($('day').value))$('day').value=config.days.includes('2026-09-14')?'2026-09-14':config.days[config.days.length-1];acceptedDay=$('day').value;
 applyPublicMode();updateScope();primeKpis();
 // The golden-set score of the last tracked run, where people look: one line linking to /evals (issue #26).
 if(config.evals){$('evals-foot').hidden=false;if(config.evals.badge){$('evals-badge').textContent=config.evals.badge+' →';$('evals-badge').title=`Golden evals, last tracked run ${config.evals.ran_at||''}${config.evals.commit?' at commit '+config.evals.commit:''}. Browse every case and run it yourself.`;$('evals-badge').hidden=false;}}
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;return ask(input.question);}},{signal:lifecycle.signal});}   // ask() is the offline planner whatever agent is chosen
 }catch(error){$('status').textContent=error.message;}
}
const boot=init();   // a question asked before /api/config has answered waits for it instead of finding a disabled button
