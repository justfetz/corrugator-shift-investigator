'use strict';
const $ = id => document.getElementById(id);
let config, sessionId = null, latest = null, busy = false;
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
function liveHelp(){ const p=provider(); return `${p.name} receives your question, recent context and synthetic evidence. Live calls incur charges. Your key is used for this request only.`; }
function node(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
const NOTE_LINE=/ - reported note \(synthetic, unverified\): /;
function message(label, paragraphs, cls='') {
 const e=node('div',undefined,'message '+cls);e.append(node('strong',label));
 let notes=null; // consecutive shift-note lines fold into one disclosure instead of a dump
 for(const p of paragraphs){
  if(NOTE_LINE.test(p)){ if(!notes){notes=node('details',undefined,'note-group');notes.append(node('summary'));e.append(notes);} notes.append(node('p',p)); notes.children[0].textContent=`${notes.children.length-1} reported note${notes.children.length-1===1?'':'s'} (synthetic, unverified — observations, not causes)`; continue; }
  notes=null; e.append(node('p',p));
 }
 $('messages').append(e);$('messages').scrollTop=$('messages').scrollHeight;
}

// ---- Scope: the hidden selects (#day, #period, #shift, #scope, #mode) hold the state the
// request payload reads; the segmented buttons and the sentence are views over them.
const WEEKDAYS=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTHS_LONG=['January','February','March','April','May','June','July','August','September','October','November','December'];
const SHIFT_HOURS={1:'07:00–15:00',2:'15:00–23:00',3:'23:00–07:00 next day'};
const SEGMENTS={period:[['day','Day'],['week','Week'],['month','Month'],['accounting','4-4-5']],shift:[['1','1'],['2','2'],['3','3'],['all','All shifts']]};
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
   b.addEventListener('click',()=>{ if(busy)return; if(group==='period')selectPeriod(value); else selectShift(value); });
   box.append(b); segButtons[group].push(b);
  }
 }
 syncSegments();
}
function syncSegments(){
 const period=$('period').value, shiftValue=$('scope').value==='day'?'all':$('shift').value;
 for(const b of segButtons.period){b.setAttribute('aria-pressed',String(b.dataset.value===period));b.disabled=busy;}
 for(const b of segButtons.shift){b.setAttribute('aria-pressed',String(b.dataset.value===shiftValue));b.disabled=busy;}
}
function selectPeriod(value){ if($('period').value===value)return; $('period').value=value; periodChanged(); }
function selectShift(value){
 if(value==='all'){ if($('scope').value==='day')return; $('scope').value='day'; }
 else { if($('scope').value==='shift'&&$('shift').value===value)return; $('scope').value='shift'; $('shift').value=value; }
 reset();
}
function periodChanged(){ if($('period').value!=='day')$('mode').value='offline'; reset(); }
let acceptedDay=null;
function dayChanged(){
 const value=$('day').value, reason=dayRejection(value,storeDays());
 if(reason){ $('day-note').textContent=reason; $('day-note').hidden=false; if(acceptedDay)$('day').value=acceptedDay; updateScope(); return reason; }
 acceptedDay=value; $('day-note').hidden=true; $('day-note').textContent=''; reset(); return null;
}
function stepDay(direction){
 const days=storeDays(); if(!days)return;
 const index=days.indexOf($('day').value); const next=index<0?(direction>0?0:days.length-1):index+direction;
 if(next<0||next>=days.length)return;
 $('day').value=days[next]; dayChanged();
}
function updateScope(){
 const ranged=$('period').value!=='day', live=isLive();
 $('scope-help').textContent=scopeSentence(currentScope());
 $('mode').disabled=busy||ranged;$('question').disabled=ranged;syncKeyLabel();syncSegments();
 const days=storeDays(), index=days?days.indexOf($('day').value):-1;
 $('day-prev').disabled=busy||!days||index<=0; $('day-next').disabled=busy||!days||index<0||index>=days.length-1;
 $('mode-badge').textContent=live?`Synthetic data / ${provider().name} selected`:'Synthetic data / Offline planner';
 if(ranged)$('mode-help').textContent='Build a free deterministic period summary, output heatmap, recurring downtime comparison, daily matrix and notes. The exact date range is shown; missing days are disclosed.';
 else $('mode-help').textContent=live?liveHelp():'Ask about performance, setups, rankings or notes within the selected scope. No model calls or API charges.';
 $('submit').textContent=ranged?'Build period report':'Investigate';
 for(const b of chipButtons)b.disabled=busy||ranged;
}

// ---- Example questions, grouped by intent. Daily chips run the day tools for the selected scope; the
// period chips are the phrasings the offline planner routes to the record-store tools (docs/WORKBENCH.md).
const QUESTION_GROUPS=[
 ['Speed',[['Speed to target','Show speed to target'],['Setup matrix','Show setup matrix'],['Speed trend, 8 weeks','Trend of speed over eight weeks']]],
 ['Downtime',[['Why was this shift slow?','Why was this shift slow? Chart downtime by reason.'],['Costliest reason this week','Which downtime reason cost the most minutes this week?'],['Stops per day this week','Stops per day this week'],['Read notes','Show shift and downtime notes']]],
 ['Waste',[['Dry-end waste','Show dry-end quality waste'],['Bond or warp?','Is it bond or warp?'],['Trim trend, 8 weeks','Trend of trim over eight weeks']]],
 ['Compare',[['Summary + charts','Summarize performance, downtime and waste. Show supporting charts.'],['This week vs last','How did this week go versus the previous one?'],['September vs August','How did September go versus August?'],['Compare shifts','Compare all shifts','day'],['Rank losses','Show rankings']]],
 ['Orders',[['Orders + quantities','Show orders and quantities']]],
];
const chipButtons=[];
function renderQuestionChips(){
 const box=$('examples'); if(!box)return; box.replaceChildren(); chipButtons.length=0;
 for(const [intent,chips] of QUESTION_GROUPS){
  const group=node('div',undefined,'chip-group'); group.append(node('h4',intent));
  const row=node('div',undefined,'chips');
  for(const [label,question,scope] of chips){
   const b=node('button',label); b.type='button'; b.dataset.question=question; if(scope)b.dataset.scope=scope; b.title=question;
   b.addEventListener('click',()=>{ if(busy)return; if(b.dataset.scope){$('scope').value=b.dataset.scope;reset();} $('question').value=question; ask(question).catch(()=>{}); });
   row.append(b); chipButtons.push(b);
  }
  group.append(row); box.append(group);
 }
}
function svgNode(tag, attrs, text) { const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e; }
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
  const cell=node('button',String(Number(row.day.slice(-2))),'heatmap-cell'+(row.available?'':' missing'));cell.type='button';
  // Assign the CSS property directly: style attributes are blocked by our CSP.
  if(row.available){const density=max===min?1:(row.estimated_good_sqft-min)/(max-min);cell.style.backgroundColor=`rgba(8, 127, 117, ${(.22+density*.78).toFixed(3)})`;}
  const detail=row.available?`${row.day}: estimated good ${row.estimated_good_sqft.toLocaleString(undefined,{maximumFractionDigits:0})} sq ft; gross ${row.gross_sqft.toLocaleString(undefined,{maximumFractionDigits:0})} sq ft; speed ${row.speed_to_target_pct.toFixed(1)}% of target; downtime ${row.downtime_pct.toFixed(2)}%; maintenance ${row.maintenance_pct.toFixed(2)}%; operator ${row.operator_pct.toFixed(2)}%; dry-end ${row.dry_end_pct.toFixed(2)}%; trim ${row.trim_pct.toFixed(2)}%; shear ${row.shear_pct.toFixed(2)}%; ${row.paper_changes} paper changes; heavy hitter ${row.heavy_hitter}, ${row.heavy_hitter_minutes.toFixed(1)} minutes across ${row.heavy_hitter_stops} stops.`:`${row.day}: no complete synthetic record available.`;
  const tooltip=row.available?detail+` Dry-end setups: ${row.valid_setups}.`:detail;
  cell.title=tooltip;cell.setAttribute('aria-label',tooltip);for(const event of ['mouseenter','focus','click'])cell.addEventListener(event,()=>{tip.textContent=tooltip;});grid.append(cell);
 }
 wrap.append(grid);const legend=node('div',undefined,'heatmap-legend');legend.append(node('span','Lower output'),node('span',undefined,'heatmap-scale'),node('span','Higher output'));wrap.append(legend,tip);$('heatmaps').append(wrap);
}
// ---- Charts: plain SVG, one axis per chart, text in ink tokens, series color on the marks only.
// Every chart is drawn from a tool result's rows; the same rows sit in the evidence tables.
const CHART_W=()=>(typeof window!=='undefined'&&window.innerWidth&&window.innerWidth<850)?420:690;
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
function frame(W,rows,rowH,top=8,wideValues=false){ const H=top+rows*rowH+28; const labelW=W<500?110:160, valueW=wideValues?(W<500?130:215):(W<500?96:150); return {W,H,labelW,valueW,x0:labelW+8,x1:W-valueW-8,top,chars:W<500?14:21}; }
function labelText(svg,text,x,y,maxChars){ const t=String(text); svg.append(svgNode('text',{x,y,'text-anchor':'end',class:'lbl'},t.length>maxChars?t.slice(0,maxChars-1)+'…':t)); }
// Generic single-series horizontal bars (downtime by reason, dry-end rejects, stops per day).
function barChart(chart,wrap){
 const rows=chart.rows, W=CHART_W(), f=frame(W,rows.length,30), max=niceCeil(Math.max(...rows.map(r=>Number(r[chart.y])||0)));
 const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`${chart.title||'Shift '+chart.shift} ${chart.unit} chart`});
 xAxis(svg,f.x0,f.x1,f.top,f.top+rows.length*30,max,chart.unit);
 rows.forEach((r,i)=>{const y=f.top+i*30, v=Number(r[chart.y])||0, label=r.weekday?`${r.weekday} ${shortDay(r[chart.x])}`:r[chart.x];
  labelText(svg,label,f.x0-8,y+19,f.chars);
  svg.append(barPath(f.x0,y+7,(v/max)*(f.x1-f.x0),16,chart.coverage==='complete'?'bar cur':'bar incomplete'));
  const detail=`${label}: ${fmtVal(v,chart.unit)}`+(r.minutes_per_stop!==undefined?`; ${fmtVal(r.minutes,'min')}, ${fmtVal(r.minutes_per_stop,'min/stop')}`:'');
  const t=svgNode('text',{x:f.x1+8,y:y+19,tabindex:0,'aria-label':detail,class:'val'},fmtVal(v,chart.unit));t.append(svgNode('title',{},detail));svg.append(t);});
 wrap.append(svg);
}
// Wet-end speed: bars with the grade-target mark; long lists show the five best and five worst by % of target.
function speedChart(chart,wrap){
 const all=chart.rows, long=all.length>10; let showAll=!long;
 const box=node('div'); wrap.append(box);
 const draw=()=>{
  box.replaceChildren();
  let rows=all, divider=-1;
  if(!showAll){const sorted=[...all].sort((a,b)=>(b.speed_to_target_pct??0)-(a.speed_to_target_pct??0));rows=[...sorted.slice(0,5),...sorted.slice(-5)];divider=5;}
  const W=CHART_W(), f=frame(W,rows.length+(divider>0?1:0),30), max=niceCeil(Math.max(...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r.target_fpm)||0))));
  const svg=svgNode('svg',{viewBox:`0 0 ${f.W} ${f.H}`,role:'img','aria-label':`Shift ${chart.shift} ${chart.unit} chart`});
  xAxis(svg,f.x0,f.x1,f.top,f.top+(rows.length+(divider>0?1:0))*30,max,chart.unit);
  let slot=0;
  rows.forEach((r,i)=>{
   if(i===divider){svg.append(svgNode('text',{x:f.x0,y:f.top+slot*30+19,class:'tick'},`… ${all.length-10} more between the best five and the worst five …`));slot++;}
   const y=f.top+slot*30; slot++;
   labelText(svg,r[chart.x],f.x0-8,y+19,f.chars);
   svg.append(barPath(f.x0,y+7,(Number(r[chart.y])/max)*(f.x1-f.x0),16,chart.coverage==='complete'?'bar cur':'bar incomplete'));
   if(r.target_fpm){const x=f.x0+r.target_fpm/max*(f.x1-f.x0);svg.append(svgNode('line',{x1:x,x2:x,y1:y+4,y2:y+26,class:'target'}));}
   const detail=r.target_fpm?`${r[chart.x]} / ${r.grade||'grade-weighted'}: ${Number(r[chart.y]).toFixed(1)} ft/min; target ${r.target_fpm.toFixed(1)}; ${r.speed_to_target_pct.toFixed(1)}% of target. Includes downtime.`:`${r[chart.x]}: ${Number(r[chart.y]).toFixed(1)} ${chart.unit}`;
   const value=svgNode('text',{x:f.x1+8,y:y+19,tabindex:0,'aria-label':detail,class:'val'},r.target_fpm?`${Number(r[chart.y]).toFixed(1)} / ${r.speed_to_target_pct.toFixed(1)}%`:Number(r[chart.y]).toFixed(1));value.append(svgNode('title',{},detail));svg.append(value);
  });
  box.append(svg);
  if(long){const toggle=node('button',showAll?'Show the five best and five worst':`Show all ${all.length}`,'secondary more');toggle.type='button';toggle.addEventListener('click',()=>{showAll=!showAll;draw();});box.append(toggle);}
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
 const byKey=Object.fromEntries(chart.rows.map(r=>[r.category,r])), parts=[['bond','s1'],['warp','s2'],['misalignment','s3'],['moisture','s4']];
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
    const inside=`${seg.k} ${seg.value.toFixed(2)}`; if(w>inside.length*6.8+14)svg.append(svgNode('text',{x:x+6,y:y+17,class:'inseg'},inside));x+=w;});
   const t=svgNode('text',{x:f.x1+8,y:y+18,tabindex:0,class:'val','aria-label':`${s.name}: dry-end ${fmtVal(total,unit)} = `+s.segments.map(v=>`${v.k} ${fmtVal(v.value,unit)}`).join(', ')},fmtVal(total,unit));svg.append(t);});
  wrap.append(node('p','Dry-end rejects split by recorded reason; the four segments add to the dry-end total.','muted'),svg,legend([['s1','bond'],['s2','warp'],['s3','misalignment'],['s4','moisture']]));
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
 if(isSpeed)notes.push('Value = ft/min / % of target. Hover or focus a value for grade and target details. 100% meets target; elapsed time includes downtime.');
 const wrap=chartCard(title,notes);
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));
 else if(chart.type==='line')lineChart(chart,wrap);
 else if(chart.type==='range')rangeChart(chart,wrap);
 else if(isPareto)paretoChart(chart,wrap);
 else if(isWaste)wasteChart(chart,wrap);
 else if(chart.y2)pairedChart(chart,wrap);
 else if(isSpeed)speedChart(chart,wrap);
 else barChart(chart,wrap);
 $('charts').append(wrap);
}
const labels={setup_id:'Setup',wet_end_id:'Wet-end / paper change',grade:'Grade',start:'Start',end:'End',width_in:'Width (in)',lineal_ft:'Lineal (ft)',actual_fpm:'Speed (ft/min)',target_fpm:'Target (ft/min)',speed_to_target_pct:'% of target',stop_count:'Stops',down_minutes:'Down (min)',rejected_sqft:'Rejects (sq ft)',notes:'Reported note',record_id:'Record',record_type:'Type',rank:'Rank',item:'Item',value:'Value',unit:'Units / order',day:'Production day',shift:'Shift',current_count:'Stops now',previous_count:'Stops before',current_minutes:'Minutes now',previous_minutes:'Minutes before',minutes_change:'Change (min)',speed_rank:'Speed rank',maintenance_rank:'Maint. rank',waste_rank:'Waste rank',valid_setups:'Dry-end setups',paper_changes:'Paper changes',observed_shift_fpm:'Speed (ft/min)',maintenance_pct:'Maintenance %',operator_pct:'Operator %',dry_end_pct:'Dry-end %',target_fpm:'Target (ft/min)',wet_ends:'Wet ends',volume_msf:'Volume (MSF)',share_of_volume_pct:'Share %',speed_run_fpm:'Speed w/o stops',speed_elapsed_fpm:'Elapsed speed',setup_stops:'Setup stops',setup_stop_minutes:'Setup stop min',trim_pct:'Trim %',previous_volume_msf:'Prev MSF',delta_volume_msf:'Δ MSF',knife:'Knife',lines:'Lines',nominal_cuts:'Nominal cuts',actual_cuts:'Actual cuts',delta_cuts:'Δ cuts',overrun_pct:'Over/under %',nominal_sheets:'Nominal sheets',actual_sheets:'Actual sheets',over_sheets:'Sheets over',under_sheets:'Sheets under',lines_inside_band_pct:'In band %',lines_over_pct:'Over %',lines_under_pct:'Under %',downtime_pct_of_elapsed:'Down % of elapsed',lineal_per_setup:'Lineal / setup',lineal_per_wet_end:'Lineal / paper change'};
function showTable(data){
 const titles={get_order_matrix:'Orders and quantities — fragments; demand repeats on continuing orders',get_setup_matrix:'Setup matrix',get_shift_notes:'Shift and downtime notes (synthetic, unverified)',get_rankings:`${data.metric||''} ranking / largest losses first`,get_grade_totals:'Totals by grade',get_overrun:'Over/under-run by knife (nominal vs actual cuts)'};
 const box=node('details');box.open=data.title==='get_setup_matrix'||data.title==='get_rankings';box.append(node('summary',`${data.shift?'Shift '+data.shift+' / ':''}${titles[data.title]||data.title} (${data.rows.length} rows)`));
 if(data.coverage!=='complete')box.append(node('p','Incomplete evidence. Rankings are withheld; any other rows are observed records only.','warning-note'));
 if(!data.rows.length){box.append(node('p','No eligible rows.'));$('tables').append(box);return;}
 const omitted=new Set(['source_ids','gross_sqft','observed_lineal_ft','throughput_in','lineal_per_setup','lineal_per_wet_end','setups_above_trim_target']);
 const columns=Object.keys(data.rows[0]).filter(k=>!omitted.has(k)&&!Array.isArray(data.rows[0][k]));
 const wrap=node('div',undefined,'table-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',titles[data.title]||data.title);const table=node('table'),head=node('thead'),tr=node('tr');
 for(const key of columns){const th=node('th',labels[key]||key.replaceAll('_',' '));th.scope='col';tr.append(th);}head.append(tr);table.append(head);const body=node('tbody');
 for(const row of data.rows){const tr=node('tr');for(const key of columns){const value=row[key];tr.append(node('td',value===null?'Unavailable':typeof value==='number'?Number.isInteger(value)?String(value):value.toFixed(2):String(value??'')));}body.append(tr);}table.append(body);wrap.append(table);box.append(wrap);$('tables').append(box);
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
function primeQuestion(){ return {day:'How did this shift go compared with the previous one?',week:'How did this week go versus the previous one?',month:'Compare this month to last'}[$('period').value]||null; }
async function primeKpis(){
 const token=++primeToken, question=primeQuestion();
 if(!config||!question||busy||(config.demo_guards&&config.demo_guards.rate_limit_per_min>0))return;
 try{
  const response=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':config.token},body:JSON.stringify({question,day:$('day').value,shift:Number($('shift').value),corrupt:$('corrupt').checked,session_id:null,mode:'offline',scope:$('scope').value,period:'day'})});
  const result=await response.json();
  if(token!==primeToken||!response.ok||!Array.isArray(result.trace))return;
  const entry=result.trace.find(e=>e.ok&&e.tool==='get_period_kpis');
  if(!entry)return;
  if(!latest)$('metrics').replaceChildren();
  renderKpiTiles(entry.data,`scope tiles · get_period_kpis · ${result.usage.tool_calls} tool call${result.usage.tool_calls===1?'':'s'} · $0 · offline planner`);
  const trail=node('details',undefined,'prime-trail');trail.append(node('summary','How these tiles were produced'));trail.append(node('p',`Offline question: “${question}”. The same evidence a typed question gets; ask one for charts and the full trail.`,'muted'));trail.append(traceRow(entry));$('kpis').append(trail);
 }catch(error){ /* the tiles are a convenience; the question flow reports its own errors */ }
}

// ---- The tool trail: one row per tool call. Arguments, row count, coverage and timing on the row;
// the exact SQL with its bound values and the full result JSON behind disclosures.
const compactJson=value=>JSON.stringify(value,(k,v)=>v===null?null:v)||'';
function traceRow(entry){
 const d=node('details',undefined,'trace-step '+(entry.ok?'ok':'failed')), data=entry.ok?entry.data:null;
 const rows=data?Array.isArray(data.rows)?data.rows.length:null:null;
 const sum=node('summary',undefined,'trace-sum');
 sum.append(node('code',entry.tool,'tool'));
 sum.append(node('span',compactJson(entry.arguments||{}),'args mono'));
 const facts=[];
 if(rows!==null)facts.push(`${rows} row${rows===1?'':'s'}`);
 if(data&&data.coverage)facts.push(data.coverage);
 if(typeof entry.elapsed_ms==='number')facts.push(`${entry.elapsed_ms.toFixed(1)} ms`);
 facts.push(entry.ok?'OK':'error');
 sum.append(node('span',facts.join(' · '),'facts mono'));
 d.append(sum);
 if(!entry.ok){d.append(node('p',`${entry.error?.code||'error'}: ${entry.error?.message||'tool call rejected'}`,'warning-note'));}
 else{
  const meta=node('dl',undefined,'trace-meta');const add=(k,v)=>{if(v===undefined||v===null||v==='')return;meta.append(node('dt',k),node('dd',String(v)));};
  if(data.window)add('Window',`${data.window.label} (${data.window.coverage})`+(data.previous_window?`; previous ${data.previous_window.label} (${data.previous_window.coverage})`:''));
  if(data.shift!==undefined)add('Shift',data.shift===null?'all shifts':data.shift);
  if(data.source_rows)add('Source rows',compactJson(data.source_rows));
  if(data.excluded_records?.length)add('Excluded records',data.excluded_records.map(r=>`${r.record_id}: ${r.error}`).join('; '));
  if(entry.tool==='render_chart')add('Chart',`${data.type}; x=${data.x}; y=${data.y}${data.y2?'; y2='+data.y2:''}; unit=${data.unit}; from ${data.source_result_id}`);
  if(data.dataset_version)add('Dataset',String(data.dataset_version).slice(0,16)+'…');
  if(meta.children.length)d.append(meta);
  const sql=data.trace?.sql||data.daily_sql; const params=data.trace?.parameters;
  if(sql){const q=node('details',undefined,'trace-sql');q.append(node('summary','SQL and bound values'));q.append(node('pre',String(sql).trim()+(params?`\n\n-- bound values\n${JSON.stringify(params)}`:'')));d.append(q);}
  else if(entry.tool!=='render_chart'){d.append(node('p','No SQL: this tool composes other results.','muted'));}
 }
 const full=node('details',undefined,'trace-json');full.append(node('summary','Full result JSON'));full.append(node('pre',JSON.stringify(entry,null,2)));d.append(full);
 return d;
}
function usageText(usage){ return usage.model_calls?`${usage.tool_calls} tool calls · ${usage.model_calls} model call${usage.model_calls===1?'':'s'} · estimated $${Number(usage.cost_usd||0).toFixed(4)}`+(usage.reserved_usd!==undefined?` · reserved $${Number(usage.reserved_usd).toFixed(2)}`:''):`${usage.tool_calls} tool call${usage.tool_calls===1?'':'s'} · $0 model cost`; }
function showResult(result) {
 $('heatmaps').replaceChildren();
 latest=result; $('download').disabled=false; $('pdf').disabled=false; $('tables').replaceChildren(); $('charts').replaceChildren(); $('metrics').replaceChildren(); $('trace').replaceChildren();
 const kpiEntry=result.trace.find(e=>e.ok&&(e.tool==='get_period_kpis'||e.tool==='compare_periods'));
 if(kpiEntry)renderKpiTiles(kpiEntry.data,`${kpiEntry.tool} · this question`);
 for(const entry of result.trace) {
   if(entry.ok && (entry.tool==='get_shift_kpis'||entry.tool==='get_shift_overview'||entry.tool==='get_period_report')){
   const r=entry.data.rows[0], incomplete=entry.data.coverage!=='complete';
   for(const [label,value,unit,bad] of [['Speed',r.observed_shift_fpm,'ft/min',false],['Speed / target',r.speed_to_target_pct,'%',r.speed_to_target_pct<100],['Dry-end setups',r.valid_setups,'',false],['Paper changes',r.paper_changes,'',false],['Maintenance',r.maintenance_pct,'%',r.maintenance_pct>2.5],['Operator',r.operator_pct,'%',r.operator_pct>2.5],['Dry-end waste',r.dry_end_pct,'%',r.dry_end_pct>1],['Trim',r.trim_pct,'%',false],['Shear',r.shear_pct,'%',false],['Throughput',r.throughput_in,'in',false],['Lineal / setup',r.lineal_per_setup,'ft',false]]){
    const card=node('div',undefined,'metric '+(bad||incomplete?'warning':''));card.append(node('div',`${entry.data.shift?'Shift '+entry.data.shift:result.period_label} · ${label}`,'label'));if(label==='Speed / target')card.title='Lineal feet / sum(grade target ft/min × elapsed minutes) × 100. Includes downtime; weighted by grade duration.';if(label==='Paper changes')card.title='Count of distinct wet-end runs, as defined by the plant expert.';card.append(node('div',incomplete?'Incomplete':`${Number(value).toFixed(1)} ${unit}`,'value'));if(incomplete)card.append(node('small',`${entry.data.excluded_records?.length||'Missing/invalid'} excluded record(s)`));$('metrics').append(card);
   }
  }
  $('trace').append(traceRow(entry));
 }
 for(const table of result.tables||[])showTable(table);
 for(const chart of result.charts)showChart(chart);
 $('calls').textContent=' · '+usageText(result.usage);
 const ctx=node('details',undefined,'trace-context');ctx.append(node('summary','Context carried to the next question'));ctx.append(node('pre',JSON.stringify(result.context,null,2)));$('context').replaceChildren(ctx);
 updateScope();
}
const CONTROL_IDS=['reset','day','day-prev','day-next','period','scope','shift','corrupt','mode','api-key'];
function setBusy(flag){ busy=flag; for(const id of CONTROL_IDS)$(id).disabled=flag; $('submit').disabled=flag; for(const b of chipButtons)b.disabled=flag; const results=document.querySelector?.('.results'); if(results)results.setAttribute('aria-busy',String(flag)); }
async function ask(question) {
 if(busy)throw new Error('An investigation is already running');
 if(typeof question!=='string'||!question.trim())throw new Error('Enter a question');
 setBusy(true);$('status').textContent=$('period').value==='day'?'Querying evidence…':'Preparing validated daily records; the first period report can take a minute…';
 const empty=$('messages').querySelector('.empty');if(empty)empty.remove();message('You',[question],'user');
 try{
  const headers={'Content-Type':'application/json','X-Workbench-Token':config.token};
  const p=provider();
  if(p && $('api-key').value && !(config&&config.public))headers[p.header]=$('api-key').value;
  $('api-key').value='';
  const pending=fetch('/api/ask',{method:'POST',headers,body:JSON.stringify({question,day:$('day').value,shift:Number($('shift').value),corrupt:$('corrupt').checked,session_id:sessionId,mode:$('mode').value,scope:$('scope').value,period:$('period').value})});
  for(const entry of Object.values(PROVIDERS))delete headers[entry.header];
  const response=await pending;
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');sessionId=result.session_id;
  message(`Investigator · ${result.mode}`,result.sections);showResult(result);$('status').textContent=`${result.status} · ${usageText(result.usage)}`;return {status:result.status,sections:result.sections,production_day:result.production_day,shifts:result.selected_shifts};
 }catch(error){message('Could not complete',[error.message],'error');$('status').textContent='Ready to retry';throw error;}
 finally{setBusy(false);updateScope();}
}
$('ask').addEventListener('submit',event=>{event.preventDefault();ask($('period').value==='day'?$('question').value:`Build ${$('period').value} report`).catch(()=>{});});
function reset(){$('heatmaps').replaceChildren();$('kpis').replaceChildren();$('api-key').value='';sessionId=null;latest=null;$('messages').replaceChildren(node('p','Conversation reset. Choose a question.','empty'));$('tables').replaceChildren();$('metrics').replaceChildren();$('charts').replaceChildren();$('trace').replaceChildren();$('context').replaceChildren();$('calls').textContent='';$('download').disabled=true;$('pdf').disabled=true;$('status').textContent=isLive()?`Ready · ${provider().name} calls incur charges`:'Ready · no API costs';updateScope();primeKpis();}
$('mode').addEventListener('change',()=>{reset();const p=provider();syncKeyLabel();$('api-key').value='';$('mode-badge').textContent=p?`Synthetic data / ${p.name} selected`:'Synthetic data / Offline planner';$('mode-help').textContent=p?(config&&config.public?`Public demo: ${p.name} chooses which bounded tools to run and writes the interpretation with this site's own key under a spend cap; no visitor key is accepted.`:`${p.name} receives your question, bounded history and synthetic tool results. Your key passes through this local server to ${p.name} for this request, is not saved, and the field clears on submit. Live calls incur API charges.`):'Offline mode uses a rule-based planner for the supported example questions. No model calls or API charges.';});
$('reset').addEventListener('click',reset);$('day').addEventListener('change',dayChanged);$('day-prev').addEventListener('click',()=>stepDay(-1));$('day-next').addEventListener('click',()=>stepDay(1));$('corrupt').addEventListener('change',reset);$('shift').addEventListener('change',reset);$('scope').addEventListener('change',reset);$('period').addEventListener('change',periodChanged);
$('download').addEventListener('click',()=>{if(!latest)return;const url=URL.createObjectURL(new Blob([latest.report_markdown],{type:'text/markdown'}));const a=node('a');a.href=url;a.download=`synthetic-shift-report-${latest.production_day}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('pdf').addEventListener('click',async()=>{if(!latest||busy)return;$('pdf').disabled=true;try{const response=await fetch('/api/report',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':config.token},body:JSON.stringify({session_id:latest.session_id,run_id:latest.run_id})});if(!response.ok){const error=await response.json();throw new Error(error.error||'Report unavailable');}const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download=`synthetic-report-${latest.production_day}.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){$('status').textContent=error.message;}finally{$('pdf').disabled=!latest;}});
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
 else{if($('period').value==='day')$('mode').value=modes.includes('anthropic')?'anthropic':modes[0];$('mode-help').textContent=`Public demo: the model (${PROVIDERS[$('mode').value]?.name||'server'}) chooses which bounded tools to run and writes the interpretation; every number is calculated by fixed queries and inserted by code. Uses this site's own key under a spend cap; no visitor key is accepted. Switch to Offline to see the same tools without a model.`;}
 $('mode-badge').textContent=modes.length?'Synthetic data / Public demo · model on':'Synthetic data / Public demo';
}
async function init(){
 $('submit').disabled=true;renderSegments();renderQuestionChips();
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();$('day').min=config.days[0];$('day').max=config.days[config.days.length-1];
 if(!config.days.includes($('day').value))$('day').value=config.days.includes('2026-09-14')?'2026-09-14':config.days[config.days.length-1];acceptedDay=$('day').value;
 applyPublicMode();updateScope(); $('submit').disabled=false;primeKpis();
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if($('mode').value!=='offline'||$('period').value!=='day')throw new Error('Browser question tool is available only for offline daily investigations');if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;return ask(input.question);}},{signal:lifecycle.signal});}
 }catch(error){$('status').textContent=error.message;}
}
init();
