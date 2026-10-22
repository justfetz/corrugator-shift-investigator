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
function message(label, paragraphs, cls='') { const e=node('div',undefined,'message '+cls);e.append(node('strong',label));for(const p of paragraphs)e.append(node('p',p));$('messages').append(e);$('messages').scrollTop=$('messages').scrollHeight; }

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
 document.querySelectorAll('[data-question]').forEach(b=>b.disabled=busy||ranged);
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
function showChart(chart) {
 if(chart.type==='calendar_heatmap')return showHeatmap(chart);
 const wrap=node('div',undefined,'chart');wrap.append(node('h3',chart.title || `Shift ${chart.shift} · ${chart.unit === 'minutes' ? 'Recorded downtime' : chart.unit === 'ft/min' ? 'Wet-end speed (bars) / grade target (marks)' : 'Dry-end rejects'}`));
 if(chart.coverage!=='complete')wrap.append(node('p','Incomplete data: observed values only.','warning-note'));
 if(chart.unit==='ft/min')wrap.append(node('p','Value = ft/min / % of target. Hover or focus a value for grade and target details. 100% meets target; elapsed time includes downtime.','muted'));
 const rows=chart.rows; const max=Math.max(1,...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r.target_fpm)||0)));
 const height=rows.length*32+25; const svg=svgNode('svg',{viewBox:`0 0 690 ${height}`,role:'img','aria-label':`Shift ${chart.shift} ${chart.unit} chart`});
 rows.forEach((r,i)=>{const y=i*32+8;svg.append(svgNode('text',{x:0,y:y+16},String(r[chart.x])));svg.append(svgNode('rect',{x:140,y,width:Math.max(0,Number(r[chart.y]))/max*360,height:21,class:chart.coverage==='complete'?'bar':'incomplete'}));if(r.target_fpm){const x=140+r.target_fpm/max*360;svg.append(svgNode('line',{x1:x,x2:x,y1:y-2,y2:y+23,class:'target'}));}const detail=r.target_fpm?`${r[chart.x]} / ${r.grade||'grade-weighted'}: ${Number(r[chart.y]).toFixed(1)} ft/min; target ${r.target_fpm.toFixed(1)}; ${r.speed_to_target_pct.toFixed(1)}% of target. Includes downtime.`:`${r[chart.x]}: ${Number(r[chart.y]).toFixed(1)} ${chart.unit}`;const value=svgNode('text',{x:515,y:y+16,tabindex:0,'aria-label':detail},r.target_fpm?`${Number(r[chart.y]).toFixed(1)} / ${r.speed_to_target_pct.toFixed(1)}%`:Number(r[chart.y]).toFixed(1));value.append(svgNode('title',{},detail));svg.append(value);});
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));else wrap.append(svg);$('charts').append(wrap);
}
const labels={setup_id:'Setup',wet_end_id:'Wet-end / paper change',grade:'Grade',start:'Start',end:'End',width_in:'Width (in)',lineal_ft:'Lineal (ft)',actual_fpm:'Speed (ft/min)',target_fpm:'Target (ft/min)',speed_to_target_pct:'% of target',stop_count:'Stops',down_minutes:'Down (min)',rejected_sqft:'Rejects (sq ft)',notes:'Reported note',record_id:'Record',record_type:'Type',rank:'Rank',item:'Item',value:'Value',unit:'Units / order',day:'Production day',shift:'Shift',current_count:'Stops now',previous_count:'Stops before',current_minutes:'Minutes now',previous_minutes:'Minutes before',minutes_change:'Change (min)',speed_rank:'Speed rank',maintenance_rank:'Maint. rank',waste_rank:'Waste rank',valid_setups:'Dry-end setups',paper_changes:'Paper changes',observed_shift_fpm:'Speed (ft/min)',maintenance_pct:'Maintenance %',operator_pct:'Operator %',dry_end_pct:'Dry-end %'};
function showTable(data){
 const titles={get_order_matrix:'Orders and quantities — fragments; demand repeats on continuing orders',get_setup_matrix:'Setup matrix',get_shift_notes:'Shift and downtime notes (synthetic, unverified)',get_rankings:`${data.metric||''} ranking / largest losses first`};
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
const TILE_PRIORITY=['volume_msf','speed_run_fpm','speed_all_fpm','downtime_pct','stops','minutes_per_stop','trim_pct','shear_pct','dry_end_pct','bale_pct','maintenance_pm_pct','operator_pct'];
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
  const trail=node('details',undefined,'prime-trail');trail.append(node('summary','How these tiles were produced'));trail.append(node('p',`Offline question: “${question}”. The same evidence a typed question gets; ask one for charts and the full trail.`,'muted'));trail.append(node('pre',JSON.stringify(entry,null,2)));$('kpis').append(trail);
 }catch(error){ /* the tiles are a convenience; the question flow reports its own errors */ }
}
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
  const d=node('details',undefined,'trace-step'); d.append(node('summary',`${entry.tool} · ${entry.ok?'OK':'error'} · ${entry.elapsed_ms.toFixed(1)} ms`));
  d.append(node('pre',JSON.stringify(entry,null,2)));$('trace').append(d);
 }
 for(const table of result.tables||[])showTable(table);
 for(const chart of result.charts)showChart(chart);
 $('calls').textContent=result.usage.model_calls ? ` · ${result.usage.tool_calls} tool calls · estimated $${result.usage.cost_usd.toFixed(4)} · reserved $${result.usage.reserved_usd.toFixed(2)}` : ` · ${result.usage.tool_calls} tool calls · $0 model cost`;
 $('context').replaceChildren(node('h3','Context carried to the next question'),node('pre',JSON.stringify(result.context,null,2)));
 updateScope();
}
const CONTROL_IDS=['reset','day','day-prev','day-next','period','scope','shift','corrupt','mode','api-key'];
function setBusy(flag){ busy=flag; for(const id of CONTROL_IDS)$(id).disabled=flag; $('submit').disabled=flag; document.querySelectorAll('[data-question]').forEach(b=>b.disabled=flag); const results=document.querySelector?.('.results'); if(results)results.setAttribute('aria-busy',String(flag)); }
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
  message(`Investigator · ${result.mode}`,result.sections);showResult(result);$('status').textContent=`${result.status} · ${result.usage.tool_calls} tool calls`;return {status:result.status,sections:result.sections,production_day:result.production_day,shifts:result.selected_shifts};
 }catch(error){message('Could not complete',[error.message],'error');$('status').textContent='Ready to retry';throw error;}
 finally{setBusy(false);updateScope();}
}
$('ask').addEventListener('submit',event=>{event.preventDefault();ask($('period').value==='day'?$('question').value:`Build ${$('period').value} report`).catch(()=>{});});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{ if(button.dataset.scope){$('scope').value=button.dataset.scope;reset();} $('question').value=button.dataset.question;ask(button.dataset.question).catch(()=>{}); }));
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
 $('submit').disabled=true;renderSegments();
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();$('day').min=config.days[0];$('day').max=config.days[config.days.length-1];
 if(!config.days.includes($('day').value))$('day').value=config.days.includes('2026-09-14')?'2026-09-14':config.days[config.days.length-1];acceptedDay=$('day').value;
 applyPublicMode();updateScope(); $('submit').disabled=false;primeKpis();
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if($('mode').value!=='offline'||$('period').value!=='day')throw new Error('Browser question tool is available only for offline daily investigations');if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;return ask(input.question);}},{signal:lifecycle.signal});}
 }catch(error){$('status').textContent=error.message;}
}
init();
