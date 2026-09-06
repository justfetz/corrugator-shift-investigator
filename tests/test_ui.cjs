// Unit-level UI checks with a minimal DOM stub; no browser session or visual QA.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync('src/investigator/web/index.html','utf8');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.dataset={};this.disabled=false;this.checked=false;this.textContent='';this.selectedOptions=[{textContent:'Week ending on date'}];}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(key,value){this[key]=value;}
 addEventListener(){}
 querySelector(){return null;}
 click(){}
}
const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
Object.assign(elements.day,{value:'2026-09-14'});elements.period.value='day';elements.shift.value='2';elements.scope.value='shift';elements.mode.value='offline';
const document={getElementById:id=>{assert.ok(elements[id],`Unknown control ${id}`);return elements[id];},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>[]};
const context={document,fetch:async()=>({ok:true,json:async()=>({days:['2026-09-01','2026-09-30'],token:'test'})}),window:{addEventListener(){}},console,setTimeout,URL,Blob,AbortController};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),context);
vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/Shift 2 only/);
elements.period.value='week';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,true);assert.equal(elements.mode.disabled,true);
elements.period.value='day';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,false);
const row={observed_shift_fpm:600,speed_to_target_pct:75,valid_setups:60,paper_changes:30,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,throughput_in:94,lineal_per_setup:4800};
context.result={run_id:'test',session_id:'session',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{shift:2},usage:{tool_calls:1,model_calls:0},trace:[{tool:'get_shift_kpis',ok:true,elapsed_ms:1,data:{shift:2,coverage:'complete',rows:[row]}}],tables:[{title:'get_shift_notes',shift:2,coverage:'complete',rows:[{notes:'<script>untrusted</script>'}]}],charts:[{shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:[{wet_end_id:'W2-01',actual_fpm:600,target_fpm:800,speed_to_target_pct:75,grade:'200-C'}]}]};
vm.runInContext('showResult(result)',context);assert.equal(elements.metrics.children.length,11);assert.equal(elements.pdf.disabled,false);assert.equal(elements.tables.children.length,1);assert.equal(elements.charts.children.length,1);
vm.runInContext('reset()',context);assert.equal(elements.pdf.disabled,true);assert.equal(elements.tables.children.length,0);
console.log('UI unit checks passed: scope, period controls, metrics, notes table, speed chart, PDF state.');
