// The client formula (src/investigator/web/money.js) run from the command line, for the Python side of its tests.
//   node tests/money_cli.cjs parity <cases.json>   each case {url, rates, before, after}: the answer at the server's rates
//                                                  re-priced here must equal the server's own answer at `rates` (every
//                                                  priced row, total and provenance; every line of prose). Prints a JSON
//                                                  summary; exits 1 on a mismatch. tests/test_money_ui.py writes the cases.
//   node tests/money_cli.cjs reprice <in.json>     {cfg, tool, data, rates}: prints the re-priced result (evals/make_client_money.py).
'use strict';
const fs=require('node:fs');
const Money=require('../src/investigator/web/money.js');
const FIELDS=['msf','paper_usd','machine_minutes','machine_time_usd','usd','usd_per_year','msf_not_made','contribution_usd','contribution_usd_per_year','contribution_status','usd_basis','share_of_window_pct','rank','lever','gap','quantity','records','control','group'];
const TOTALS=['total_usd','total_paper_usd','total_machine_time_usd','total_usd_per_year','total_contribution_usd','contribution_status','provenance','gap_machine_minutes','gap_usd','gap_usd_per_year','gap_contribution_usd',
 'buckets','area_sqft','area_msf','stop_minutes','speed_minutes','waste_run_minutes','machine_minutes','msf_not_made',   // the summary the card states (#31b review)
 'trim_pct_of_gross','trim_target_pct','trim_points_over','trim_over_usd','trim_over_usd_per_year','trim_over_basis','trim_over_terms','tile_money','shifts',
 'controllable_levers','controllable_usd','controllable_usd_per_year','controllable_machine_minutes','controllable_contribution_usd','lens',   // the lenses and the speed variance (issue #101)
 'dry_end_rank','dry_end_kinds','dry_end_usd','dry_end_usd_per_year'];   // the ownership lens's tag-scrap subtotal (PR #111)
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function parity(file){
 const {cfg,cases}=JSON.parse(fs.readFileSync(file,'utf8')), bad=[]; let fields=0, lines=0;
 for(const c of cases){
  const rates=Money.effective(cfg,Money.parse(c.rates,cfg)), tools=Object.fromEntries(c.before.trace.map(e=>[e.result_id,e.tool]));
  const toolOf=e=>e.tool==='render_chart'?tools[e.data.source_result_id]:e.tool;
  const steps=c.before.trace.map(e=>e.ok&&Money.priced(e.data)?{tool:toolOf(e),before:e.data,after:Money.reprice(toolOf(e),e.data,rates,cfg)}:null);
  c.after.trace.forEach((e,i)=>{ const s=steps[i]; if(!s)return;
   for(const k of TOTALS)if(k in e.data){ fields++; if(!same(s.after[k],e.data[k]))bad.push({url:c.url,rates:c.rates,step:i,field:k,client:s.after[k],server:e.data[k]}); }
   e.data.rows.forEach((r,j)=>{ for(const k of FIELDS)if(k in r){ fields++; if(!same(s.after.rows[j][k],r[k]))bad.push({url:c.url,rates:c.rates,step:i,row:j,field:k,client:s.after.rows[j][k],server:r[k]}); } });
  });
  const prose=Money.repriceSections(c.before.sections,steps.map((s,i)=>c.before.trace[i].tool==='render_chart'?null:s),rates,cfg);
  for(let i=0;i<Math.max(prose.length,c.after.sections.length);i++){ lines++; if(prose[i]!==c.after.sections[i]){ bad.push({url:c.url,rates:c.rates,line:i,client:prose[i],server:c.after.sections[i]}); break; } }
 }
 process.stdout.write(JSON.stringify({cases:cases.length,fields,lines,mismatches:bad.slice(0,20),count:bad.length})+'\n');
 process.exit(bad.length?1:0);
}
function reprice(file){
 const {cfg,tool,data,rates}=JSON.parse(fs.readFileSync(file,'utf8'));
 process.stdout.write(JSON.stringify(Money.reprice(tool,data,Money.effective(cfg,Money.parse(rates,cfg)),cfg))+'\n');
}
const [cmd,file]=process.argv.slice(2);
if(cmd==='parity')parity(file); else if(cmd==='reprice')reprice(file);
else{ process.stderr.write('usage: node tests/money_cli.cjs parity|reprice <file.json>\n'); process.exit(2); }
