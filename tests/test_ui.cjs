// Unit-level UI checks with a minimal DOM stub; no browser session or visual QA.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync('src/investigator/web/index.html','utf8');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.dataset={};this.disabled=false;this.checked=false;this.textContent='';this.style={};this.events={};this.selectedOptions=[{textContent:'Week ending on date'}];}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(key,value){this[key]=value;}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelector(){return null;}
 click(){}
}
const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
Object.assign(elements.day,{value:'2026-09-14'});elements.period.value='day';elements.shift.value='2';elements.scope.value='shift';elements.mode.value='offline';
const document={getElementById:id=>{assert.ok(elements[id],`Unknown control ${id}`);return elements[id];},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>[]};
const context={document,fetch:async()=>({ok:true,json:async()=>({days:['2026-09-01','2026-09-30'],token:'test'})}),window:{addEventListener(){}},console,setTimeout,URL,Blob,AbortController};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),context);
vm.runInContext('updateScope()',context);assert.equal(elements['scope-help'].textContent,'Monday 14 Sep 2026 · shift 2 · 15:00–23:00 · compared with Friday 11 Sep');
elements.scope.value='day';vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/all three shifts · 24 hours from 07:00 · compared with Friday 11 Sep/);
elements.scope.value='shift';vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/shift 2/);
elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.equal(elements['key-label'].hidden,false);
elements.period.value='month';elements.period.events.change();assert.equal(elements.mode.value,'offline');assert.equal(elements['key-label'].hidden,true);
elements.period.value='week';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,true);assert.equal(elements.mode.disabled,true);
elements.period.value='day';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,false);
// Anthropic locally: the key label and badge follow the selected provider, and the key travels under its own header name.
elements.mode.value='anthropic';vm.runInContext('updateScope()',context);assert.equal(elements['key-label'].hidden,false);assert.match(elements['key-label-text'].textContent,/^Anthropic API key/);assert.match(elements['mode-badge'].textContent,/Anthropic selected/);
elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.match(elements['key-label-text'].textContent,/^OpenAI API key/);
assert.equal(vm.runInContext("JSON.stringify(Object.values(PROVIDERS).map(p=>p.header))",context),'["X-OpenAI-Key","X-Anthropic-Key"]');
// Public demo with only the Anthropic server key: that option is relabelled, the OpenAI option is removed, Anthropic is preselected.
const options={openai:new Element('option'),anthropic:new Element('option')};for(const [v,o] of Object.entries(options)){o.value=v;o.removed=false;o.remove=()=>{o.removed=true;};}
elements.mode.querySelector=sel=>{const v=sel.match(/value="(\w+)"/)[1];return options[v]&&!options[v].removed?options[v]:null;};
vm.runInContext("config={public:true,live_modes:['anthropic'],live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);
assert.equal(elements.mode.value,'anthropic');assert.equal(options.anthropic.textContent,'Anthropic / server key');assert.equal(options.openai.removed,true);assert.equal(elements['key-label'].hidden,true);assert.match(elements['mode-help'].textContent,/Anthropic/);
elements.mode.querySelector=()=>null;
// Public demo: with the server's model the default mode is the model; without it the OpenAI option is gone and offline is forced.
vm.runInContext("config={public:true,live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);assert.equal(elements.mode.value,'openai');assert.equal(elements['key-label'].hidden,true);assert.match(elements['mode-badge'].textContent,/model on/);
elements.mode.value='openai';vm.runInContext("config={public:true,live_model_available:false,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);assert.equal(elements.mode.value,'offline');assert.equal(elements.mode.disabled,true);
vm.runInContext("config={days:['2026-09-01','2026-09-30'],token:'test'}",context);elements.mode.disabled=false;elements.mode.value='offline';
const row={observed_shift_fpm:600,speed_to_target_pct:75,valid_setups:60,paper_changes:30,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,throughput_in:94,lineal_per_setup:4800};
context.result={run_id:'test',session_id:'session',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{shift:2},usage:{tool_calls:1,model_calls:0},trace:[{tool:'get_shift_kpis',ok:true,elapsed_ms:1,data:{shift:2,coverage:'complete',rows:[row]}}],tables:[{title:'get_shift_notes',shift:2,coverage:'complete',rows:[{notes:'<script>untrusted</script>'}]}],charts:[{title:'Output heatmap',type:'calendar_heatmap',coverage:'complete',unit:'estimated good sq ft',rows:[{day:'2026-09-14',available:true,estimated_good_sqft:1000000,gross_sqft:1020000,speed_to_target_pct:75,downtime_pct:4,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,paper_changes:27,valid_setups:60,heavy_hitter:'Upper knife / Knife jam',heavy_hitter_minutes:12,heavy_hitter_stops:3}]},{shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:[{wet_end_id:'W2-01',actual_fpm:600,target_fpm:800,speed_to_target_pct:75,grade:'200-C'}]}]};
const first=context.result.charts[0].rows[0];
context.result.charts[0].rows.push({...first,day:'2026-09-15',estimated_good_sqft:1500000},{...first,day:'2026-09-16',estimated_good_sqft:2000000},{day:'2026-09-17',available:false});
vm.runInContext('showResult(result)',context);assert.equal(elements.metrics.children.length,11);assert.equal(elements.pdf.disabled,false);assert.equal(elements.tables.children.length,1);assert.equal(elements.charts.children.length,1);
const heatmap=elements.heatmaps.children[0];
const cells=heatmap.children.find(e=>e.className==='heatmap-grid').children.filter(e=>e.tag==='button');
assert.deepEqual(cells.slice(0,3).map(e=>e.style.backgroundColor),['rgba(8, 127, 117, 0.220)','rgba(8, 127, 117, 0.610)','rgba(8, 127, 117, 1.000)']);
assert.equal(cells[3].style.backgroundColor,undefined);assert.match(cells[3].className,/missing/);
for(const phrase of ['estimated good','gross','speed','downtime','maintenance','operator','dry-end','trim','shear','27 paper changes','Dry-end setups: 60','heavy hitter Upper knife / Knife jam'])assert.ok(cells[0].title.includes(phrase),phrase);
assert.equal(cells[0]['aria-label'],cells[0].title);
const tip=heatmap.children.find(e=>e.className==='heatmap-tip');
for(const event of ['mouseenter','focus','click']){cells[1].events[event]();assert.equal(tip.textContent,cells[1].title);}
assert.ok(heatmap.children.some(e=>e.className==='heatmap-legend'));
assert.ok(html.indexOf('id="heatmaps"')<html.indexOf('id="metrics"'));
context.result.charts[0].rows=[first];vm.runInContext('showResult(result)',context);
assert.equal(elements.heatmaps.children.length,1);
assert.equal(elements.heatmaps.children[0].children.find(e=>e.className==='heatmap-grid').children.find(e=>e.tag==='button').style.backgroundColor,'rgba(8, 127, 117, 1.000)');
vm.runInContext('reset()',context);assert.equal(elements.pdf.disabled,true);assert.equal(elements.tables.children.length,0);
assert.equal(elements.heatmaps.children.length,0);

// ---- Scope sentence: the plain-English read-back for each period, with the comparison base or its absence.
const days=['2026-08-03','2026-08-04','2026-09-11','2026-09-14','2026-09-30'];
const sentence=state=>vm.runInContext(`scopeSentence(${JSON.stringify(state)})`,context);
assert.equal(sentence({day:'2026-09-14',period:'day',shift:2,scope:'shift',days,first_day:'2026-08-03'}),'Monday 14 Sep 2026 · shift 2 · 15:00–23:00 · compared with Friday 11 Sep');
assert.equal(sentence({day:'2026-09-14',period:'day',shift:3,scope:'day',days,first_day:'2026-08-03'}),'Monday 14 Sep 2026 · all three shifts · 24 hours from 07:00 · compared with Friday 11 Sep');
assert.equal(sentence({day:'2026-09-14',period:'week',shift:1,scope:'shift',days,first_day:'2026-08-03'}),'Week of Mon 14 – Fri 18 Sep 2026 · shift 1 · compared with the week of Mon 7 – Fri 11 Sep 2026');
assert.equal(sentence({day:'2026-09-02',period:'week',shift:1,scope:'day',days,first_day:'2026-08-03'}),'Week of Mon 31 Aug – Fri 4 Sep 2026 · all three shifts · compared with the week of Mon 24 – Fri 28 Aug 2026');
assert.equal(sentence({day:'2026-09-14',period:'month',shift:2,scope:'shift',days,first_day:'2026-08-03'}),'September 2026 · shift 2 · compared with August 2026');
assert.match(sentence({day:'2026-08-05',period:'month',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/^August 2026 · shift 2 · no earlier month in the store/);
assert.match(sentence({day:'2026-08-03',period:'day',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/first day in the store/);
assert.match(sentence({day:'2026-08-03',period:'week',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/no earlier week in the store/);
assert.match(sentence({day:'2026-09-14',period:'accounting',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/^4-4-5 accounting period containing Monday 14 Sep 2026 · shift 2/);

// ---- Weekend rejection: the reason names the weekday, the accepted day is restored, the note is shown.
assert.match(vm.runInContext(`dayRejection('2026-09-12',${JSON.stringify(days)})`,context),/^Saturday 12 Sep 2026 is a Saturday: the plant is down at weekends.*Monday to Friday, 3 Aug 2026 to 30 Sep 2026/);
assert.match(vm.runInContext(`dayRejection('2026-10-05',${JSON.stringify(days)})`,context),/outside the record store/);
assert.equal(vm.runInContext(`dayRejection('2026-09-14',${JSON.stringify(days)})`,context),null);
vm.runInContext(`config={days:${JSON.stringify(days)},first_day:'2026-08-03',last_day:'2026-09-30',token:'t'};acceptedDay='2026-09-14'`,context);
elements.day.value='2026-09-13';const rejection=elements.day.events.change();
assert.match(rejection,/Sunday 13 Sep 2026 is a Sunday/);assert.equal(elements.day.value,'2026-09-14');assert.equal(elements['day-note'].hidden,false);assert.equal(elements['day-note'].textContent,rejection);
elements.day.value='2026-09-11';assert.equal(elements.day.events.change(),null);assert.equal(elements['day-note'].hidden,true);assert.match(elements['scope-help'].textContent,/^Friday 11 Sep 2026/);
elements.day.value='2026-09-14';elements.day.events.change();

// ---- Segmented buttons mirror the hidden selects; chips are grouped by intent.
vm.runInContext('renderSegments();renderQuestionChips()',context);
const pressed=group=>elements[group+'-seg'].children.filter(b=>b['aria-pressed']==='true').map(b=>b.dataset.value);
assert.deepEqual(elements['period-seg'].children.map(b=>b.dataset.value),['day','week','month','accounting']);
assert.deepEqual(pressed('period'),['day']);assert.deepEqual(pressed('shift'),['2']);
elements['shift-seg'].children.find(b=>b.dataset.value==='all').events.click();assert.equal(elements.scope.value,'day');assert.deepEqual(pressed('shift'),['all']);
elements['shift-seg'].children.find(b=>b.dataset.value==='1').events.click();assert.equal(elements.scope.value,'shift');assert.equal(elements.shift.value,'1');
elements['shift-seg'].children.find(b=>b.dataset.value==='2').events.click();
elements['period-seg'].children.find(b=>b.dataset.value==='month').events.click();assert.equal(elements.period.value,'month');assert.equal(elements.question.disabled,true);
elements['period-seg'].children.find(b=>b.dataset.value==='day').events.click();assert.equal(elements.period.value,'day');
assert.deepEqual(elements.examples.children.map(g=>g.children[0].textContent),['Speed','Downtime','Waste','Compare','Orders']);
const chips=elements.examples.children.flatMap(g=>g.children[1].children);
for(const q of ['How did September go versus August?','Which downtime reason cost the most minutes this week?','Is it bond or warp?','Stops per day this week','Trend of trim over eight weeks','Compare all shifts','Show orders and quantities'])assert.ok(chips.some(b=>b.dataset.question===q),q);
assert.equal(chips.find(b=>b.dataset.question==='Compare all shifts').dataset.scope,'day');

// ---- KPI tiles from a get_period_kpis envelope: change -> class, previous value, more, withheld.
const walk=(el,out=[])=>{for(const c of el.children||[]){out.push(c);walk(c,out);}return out;};
const kpiRow=(metric,label,unit,current,previous,delta,better,change)=>({metric,label,unit,current,previous,delta,better,change});
const kpiRows=[kpiRow('volume_msf','Volume','MSF',2252.2,2366.5,-114.3,'higher','worse'),kpiRow('downtime_pct','Downtime','% of scheduled minutes',2.11,1.48,0.63,'lower','worse'),kpiRow('shear_pct','Shear','% of gross',0.63,0.67,-0.04,'lower','better'),kpiRow('stops','Stops','count',7,7,0,'lower','same'),kpiRow('setups','Setup records','count',60,58,2,'neutral','neutral'),
 ...['speed_run_fpm','speed_all_fpm','minutes_per_stop','trim_pct','dry_end_pct','bale_pct','maintenance_pm_pct','operator_pct','lineal_ft','downtime_minutes','bond_pct'].map(m=>kpiRow(m,m,'% of gross',1,1,0,'lower','same'))];
const envelope=(comparison)=>({tool:'get_period_kpis',window:{label:'2026-09-14 (Mon)',coverage:'complete'},previous_window:{label:'2026-09-11 (Fri)',coverage:'complete'},shift:2,coverage:'complete',comparison,rows:kpiRows,trace:{sql:'SELECT 1',parameters:[[1]]}});
context.result={run_id:'r',session_id:'s',production_day:'2026-09-14',selected_shifts:[],mode:'offline',status:'complete',context:{},usage:{tool_calls:2,model_calls:0},sections:[],trace:[{tool:'get_period_kpis',arguments:{window:{period:'day',anchor:'2026-09-14'},compare:true,shift:2},ok:true,elapsed_ms:3,data:envelope('complete')}],tables:[],charts:[]};
vm.runInContext('showResult(result)',context);
const tiles=walk(elements.kpis).filter(e=>/^tile\b/.test(e.className));
assert.equal(tiles.length,16);assert.equal(tiles.filter(t=>t.className.includes('extra')).length,4);
assert.equal(tiles[0].children[0].textContent,'Volume');   // priority order: volume first
const chipOf=t=>walk(t).find(e=>/^delta\b/.test(e.className));
const byLabel=Object.fromEntries(tiles.map(t=>[t.children[0].textContent,t]));
assert.match(chipOf(byLabel.Volume).className,/\bworse\b/);assert.equal(chipOf(byLabel.Volume).textContent,'▼ −114.3 MSF');
assert.match(chipOf(byLabel.Downtime).className,/\bworse\b/);assert.equal(chipOf(byLabel.Downtime).textContent,'▲ +0.63 pts');
assert.match(chipOf(byLabel.Shear).className,/\bbetter\b/);assert.match(byLabel.Shear.className,/\bbetter\b/);
assert.match(chipOf(byLabel.Stops).className,/\bsame\b/);assert.match(chipOf(byLabel['Setup records']).className,/\bneutral\b/);
assert.equal(walk(byLabel.Volume).find(e=>e.className==='prev').textContent,'prev 2,366.5 MSF');
assert.equal(walk(byLabel.Volume).find(e=>e.className==='v').children.map(c=>c.textContent).join(' '),'2,252.2 MSF');
const more=walk(elements.kpis).find(e=>/\bmore\b/.test(e.className));assert.equal(more.textContent,'Show 4 more');more.events.click();assert.equal(more.textContent,'Show fewer');
context.result.trace[0].data=envelope('withheld: a window is incomplete or unavailable');context.result.trace[0].data.rows=kpiRows.map(r=>({...r,delta:null,change:null}));
vm.runInContext('showResult(result)',context);
const withheld=walk(elements.kpis).filter(e=>/^delta\b/.test(e.className));
assert.equal(withheld.length,16);assert.ok(withheld.every(c=>c.className.includes('withheld')&&c.textContent==='withheld'));
assert.ok(walk(elements.kpis).some(e=>e.className==='warning-note'&&/Deltas withheld/.test(e.textContent)));
assert.ok(walk(elements.trace).some(e=>e.className==='tool'&&e.textContent==='get_period_kpis'));   // the trail row names the tool

// ---- Chart node counts from sample specs: paired bars when y2 is present, a line with partial weeks.
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'bar',title:'paired',coverage:'complete',unit:'per row: see unit',x:'label',y:'current',y2:'previous',rows:[{label:'Volume',unit:'MSF',current:10,previous:8,change:'better'},{label:'Trim',unit:'% of gross',current:3,previous:2.5},{label:'Stops',unit:'count',current:5,previous:null}]})`,context);
let marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='bar cur').length,3);assert.equal(marks.filter(e=>e.class==='bar prev').length,2);
assert.ok(marks.some(e=>e.className==='lg'));
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'line',title:'trend',coverage:'partial',unit:'% of gross',x:'week_start',y:'value',rows:[{week_start:'2026-08-24',value:2.9,partial:false,production_days:5},{week_start:'2026-08-31',value:3.0,partial:false,production_days:5},{week_start:'2026-09-07',value:3.1,partial:false,production_days:5},{week_start:'2026-09-14',value:3.15,partial:true,production_days:3}]})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='line').length,1);assert.equal(marks.filter(e=>/^dot\b/.test(e.class)).length,4);assert.equal(marks.filter(e=>e.class==='dot partial').length,1);
assert.ok(marks.some(e=>e.tag==='text'&&e.textContent==='partial'));
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'bar',title:'pareto',coverage:'complete',unit:'minutes',x:'category',y:'minutes',y2:'previous_minutes',rows:[{category:'Other',stops:11,minutes:140.9,share_pct:44,cumulative_pct:44,previous_minutes:157},{category:'Order change',stops:37,minutes:46.7,share_pct:14.6,cumulative_pct:58.6,previous_minutes:33.3}]})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='bar vital').length,2);assert.ok(marks.some(e=>e.tag==='text'&&e.textContent==='141 · 44% · cum 44%'));
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'bar',title:'waste',coverage:'complete',unit:'% of gross',x:'label',y:'pct_of_gross',y2:'previous_pct_of_gross',rows:[{category:'trim',label:'Side trim',pct_of_gross:3.1,previous_pct_of_gross:3.0},{category:'bond',label:'Dry-end: bond',pct_of_gross:0.4,previous_pct_of_gross:0.4},{category:'warp',label:'Dry-end: warp',pct_of_gross:0.33,previous_pct_of_gross:0.47},{category:'misalignment',label:'Dry-end: misalignment',pct_of_gross:0.3,previous_pct_of_gross:0.3},{category:'moisture',label:'Dry-end: moisture',pct_of_gross:0.1,previous_pct_of_gross:0.1}]})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>/^seg s[1234]$/.test(e.class)).length,8);   // two stacked bars x four segments (bond, warp, misalignment, moisture)
assert.equal(marks.filter(e=>e.class==='bar cur').length,1);           // the non-dry-end categories paired beneath
elements.charts.replaceChildren();
const wetEnds=Array.from({length:12},(_,i)=>({wet_end_id:'WE'+i,actual_fpm:400+i*30,target_fpm:800,speed_to_target_pct:50+i*3,grade:'200-C'}));
vm.runInContext(`showChart({shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:${JSON.stringify(wetEnds)}})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='bar cur').length,10);          // five best and five worst of twelve
const toggle=marks.find(e=>e.tag==='button');assert.equal(toggle.textContent,'Show all 12');toggle.events.click();
assert.equal(walk(elements.charts).filter(e=>e.class==='bar cur').length,12);

// ---- Messages: consecutive reported-note lines fold into one disclosure.
elements.messages.replaceChildren();
vm.runInContext(`message('Investigator',['Shift 2: 600 ft/min.','Shift 2, ST1 - reported note (synthetic, unverified): a','Shift 2, ST2 - reported note (synthetic, unverified): b','Done.'])`,context);
const msg=elements.messages.children[0];
assert.equal(msg.children.length,4);   // label, paragraph, one note group, paragraph
assert.equal(msg.children[2].className,'note-group');assert.equal(msg.children[2].children[0].textContent,'2 reported notes (synthetic, unverified — observations, not causes)');
console.log('UI unit checks passed: scope sentence, weekend rejection, segments, chip groups, KPI tiles, charts, trail row, note folding, heatmap, PDF state.');
