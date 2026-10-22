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
console.log('UI unit checks passed: scope, period controls, metrics, notes table, heatmap, speed chart, PDF state.');
