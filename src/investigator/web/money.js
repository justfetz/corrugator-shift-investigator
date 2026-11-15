'use strict';
// ---- The client formula (issue #31b). A dollar on the card is a tool's quantity times a rate. Every priced row a
// tool returns carries `price_terms`, the rate-free call that priced it (kind, quantity, the window's gross area
// and scheduled minutes; src/investigator/cost_model.py). `price` below is that call, line for line: the same
// products in the same order, the same rounding (Python's round half to even) and the same text, so a figure the
// card re-prices at a visitor's rates is the figure the server returns for the same rates (the permalink), digit
// for digit. A rate edit is a multiplication in this browser: no request, no model. No rate is written here: the
// rates, the post's assumptions and the constants they derive come from /api/config (cost_model.client_config).
// tests/test_ui.cjs holds this file to the server's own answers, and the golden case
// value-dollars-shift-0914-shift-2-client holds get_shift_dollars to this file's output.
const Money=(()=>{
 const PAPER=new Set(['trim','waste','paper']), TIME=new Set(['downtime','speed']);
 const MONEY_TOOLS=new Set(['get_shift_dollars','get_opportunities','get_day_dollars']);
 // A priced field and the words that follow it in the prose (issue #164): `at` before " at standard", `above` before " above
 // standard", `ptd` before " above standard in" (the period to date a card adds to), `year` before a yearly word.
 // The whole dollars a card prints (`*_shown`, PR #168) come first: a ledger's prose prints them, never the cents' own rounding.
 const WINDOW_FIELDS=[['usd_shown'],['at_standard_usd_shown','at'],['above_standard_usd_shown','above'],
  ['usd'],['at_standard_usd','at'],['above_standard_usd','above'],['all_recorded_usd'],['paper_usd'],['machine_time_usd'],['contribution_usd']],
  YEAR_FIELDS=['usd_per_year','contribution_usd_per_year'];
 const TOTAL_WINDOW=[['total_usd_shown'],['total_contribution_usd_shown'],['controllable_usd_shown'],['dry_end_usd_shown'],['dry_end_at_standard_usd_shown','at'],['dry_end_above_standard_usd_shown','above'],
  ['at_standard_usd_shown','at'],['above_standard_usd_shown','above'],['no_band_usd_shown'],['all_in_usd_shown'],['ptd_usd_shown','ptd'],
  ['total_usd'],['total_paper_usd'],['total_machine_time_usd'],['total_contribution_usd'],['controllable_usd'],['controllable_contribution_usd'],
  ['dry_end_usd'],['dry_end_at_standard_usd','at'],['dry_end_above_standard_usd','above'],['at_standard_usd','at'],['above_standard_usd','above'],['no_band_usd'],
  ['all_in_usd'],['ptd_usd','ptd']],
  TOTAL_YEAR=['total_usd_per_year','controllable_usd_per_year','dry_end_usd_per_year','ptd_usd_per_year'];
 const LABEL_KEYS=['gap','lever','category','label','reason'];
 // Python's f'{x:.{d}f}': correctly rounded on the binary value, a tie to even (toFixed breaks a tie upward).
 // A double is a tie at d places only when x * 2^(d+1) is an odd integer.
 function fixed(x,d){
  if(!Number.isFinite(x))return String(x);
  const neg=x<0||Object.is(x,-0), a=Math.abs(x), j=a*2**(d+1);
  let text;
  if(Number.isInteger(j)&&j%2===1&&j<2**50){ const n=(j*5**d-1)/2; text=((n%2===0?n:n+1)/10**d).toFixed(d); }
  else text=a.toFixed(d);
  return (neg?'-':'')+text;
 }
 function group(text){ const m=/^(-?)(\d+)(.*)$/.exec(text); return m?m[1]+m[2].replace(/\B(?=(\d{3})+$)/g,',')+m[3]:text; }
 const fmt=(x,d)=>group(fixed(x,d));                                   // f'{x:,.{d}f}'
 const round=(x,d)=>x===null||x===undefined?null:Number(fixed(x,d));   // round(x, d)
 const gText=(v,comma=false)=>{ const t=String(Number(Number(v).toPrecision(6))); return comma?group(t):t; };   // f'{v:g}' for the sizes used here
 function rateText(v,comma=false){ let t=comma?fmt(v,2):fixed(v,2); if(t.includes('.'))t=t.replace(/0+$/,'').replace(/\.$/,''); return t; }
 const money=v=>v===null||v===undefined?'unavailable':'$'+fmt(v,0);   // shapes.money: the answer's prose
 const moneyRaw=v=>'$'+fmt(v,2);                                     // cost_model._money: inside a formula, in cents (PR #168)
 // A dollar figure for a tile or a bar label: whole dollars from $100, $1.2k from $10,000, $5.7M from $1M.
 function short(v){
  if(v===null||v===undefined)return 'unavailable';
  const a=Math.abs(v);
  if(a>=1e6)return '$'+fixed(v/1e6,a>=1e7?1:2).replace(/\.?0+$/,'')+'M';
  if(a>=1e4)return '$'+fixed(v/1e3,a>=1e5?0:1).replace(/\.0$/,'')+'k';
  return a>=100?'$'+fmt(v,0):'$'+fmt(v,2);
 }

 // ---- rates: the server's (the post's defaults, then INVESTIGATOR_RATES), then the visitor's own ('yours')
 function base(cfg){ const values={}, sources={}; for(const r of cfg.rates){ values[r.id]=r.value; sources[r.id]=r.source; } return {values,sources}; }
 function effective(cfg,overrides={}){ const r=base(cfg); for(const [id,v] of Object.entries(overrides||{})){ if(id in r.values){ r.values[id]=v; r.sources[id]='yours'; } } return r; }
 // `paper:55,hour:1200,margin:40,trim:3` -> {rate id: value}, refused exactly as cost_model.parse_rates refuses it.
 function parse(text,cfg){
  if(text===null||text===undefined||text==='')return {};
  if(typeof text!=='string'||text.length>200)throw new Error('rates is a short list such as paper:55,hour:1200,margin:40');
  const byShort=Object.fromEntries(cfg.rates.map(r=>[r.short,r])), out={};
  for(const item of text.split(',')){
   const at=item.indexOf(':'); const key=at<0?item:item.slice(0,at), value=at<0?'':item.slice(at+1), spec=at<0?null:byShort[key];
   if(!spec)throw new Error('rates takes '+cfg.rates.map(r=>r.short+':<number>').join(', ')+', for example paper:55,margin:40');
   if(spec.id in out)throw new Error(`Repeated rate '${key}'`);
   const why=check(spec,value); if(why)throw new Error(why);
   out[spec.id]=Number(value);
  }
  return out;
 }
 // Why a typed rate is refused (the server's own rule: a number above 0, at most two decimals, within the limit), or null.
 function check(spec,value){
  if(!/^\d{1,6}(\.\d{1,2})?$/.test(String(value)))return `${spec.label} must be a number with at most two decimals, for example ${spec.default??40}`;
  const n=Number(value); if(!(n>0&&n<=spec.max))return `${spec.label} must be above 0 and at most ${gText(spec.max)} ${spec.unit}`;
  return null;
 }
 function format(overrides,cfg){ return cfg.rates.filter(r=>overrides&&r.id in overrides).map(r=>`${r.short}:${rateText(overrides[r.id])}`).join(','); }   // cost_model.format_rates
 function rateRows(rates,cfg){ return cfg.rates.map(r=>({rate_id:r.id,label:r.label,unit:r.unit,value:rates.values[r.id],status:rates.values[r.id]!==null&&rates.values[r.id]!==undefined?'set':'not set',source:rates.sources[r.id]})); }
 function provenance(rates,cfg){   // cost_model.provenance
  const v=rates.values, s=rates.sources, unset=cfg.margin_not_set;
  const margin=v.margin_usd_msf!==null&&v.margin_usd_msf!==undefined?`margin $${rateText(v.margin_usd_msf)}/MSF (${s.margin_usd_msf})`:`contribution ${unset} (assumption: ${s.margin_usd_msf})`;
  return `Rates: paper $${rateText(v.paper_usd_msf)}/MSF (${s.paper_usd_msf}), machine time $${rateText(v.machine_usd_hour,true)}/hr (${s.machine_usd_hour}), ${margin}, trim target ${rateText(v.trim_target_pct)}% (${s.trim_target_pct}).`;
 }

 // ---- pricing: cost_model.price, line for line. A price is the window's own dollars: nothing is annualized here (issue #164)
 const NOT_PRICED={msf:null,paper_usd:null,machine_minutes:null,machine_time_usd:null,usd:null,usd_per_year:null,msf_not_made:null,contribution_usd:null,contribution_usd_per_year:null,contribution_status:'not priced',usd_basis:'not priced: the quantity is unavailable or a negative remainder',share_of_window_pct:null,price_terms:null};
 const add=values=>{ let total=0; for(const v of values)total+=v; return total; };   // cost_model._add: left to right
 function price(kind,quantity,rates,gross,minutes,cfg){
  const v=rates.values, C=cfg.constants, paper=v.paper_usd_msf, hour=v.machine_usd_hour, margin=v.margin_usd_msf;
  if(quantity===null||quantity===undefined||quantity<0)return {...NOT_PRICED};
  const parts=[]; let msf, paperUsd, machineMinutes, notMade;
  if(PAPER.has(kind)){
   msf=quantity/1000; paperUsd=msf*paper; machineMinutes=kind==='waste'?msf/C.msf_per_minute:0; notMade=kind==='waste'?msf:0;
   parts.push(`${fmt(quantity,1)} sq ft = ${fmt(msf,3)} MSF x $${rateText(paper)}/MSF paper = ${moneyRaw(paperUsd)}`);
  }else if(TIME.has(kind)){ msf=null; paperUsd=null; machineMinutes=quantity; notMade=quantity*C.msf_per_minute; }
  else throw new Error(`Unknown cost kind ${kind}`);
  const machineUsd=machineMinutes/60*hour;
  if(kind!=='trim'&&kind!=='paper'){ const lead=TIME.has(kind)?'':`${fmt(msf,3)} MSF / ${gText(C.msf_per_minute)} MSF per min = `; parts.push(`${lead}${fmt(machineMinutes,2)} min x $${rateText(hour,true)}/hr machine time = ${moneyRaw(machineUsd)}`); }
  // a waste row's dollars are its two parts to the cent, added, and its formula ends with that sum (cost_model.price, PR #168)
  const usd=kind==='waste'?round(paperUsd,2)+round(machineUsd,2):(paperUsd||0)+machineUsd;
  if(kind==='waste')parts.push(`${moneyRaw(paperUsd)} + ${moneyRaw(machineUsd)} = ${moneyRaw(round(usd,2))}`);
  let contribution=null, status=cfg.margin_not_set;
  if(margin===null||margin===undefined)parts.push(`contribution: ${cfg.margin_not_set}`);
  else{ contribution=notMade*margin; status='set'; parts.push(`contribution ${fmt(notMade,3)} MSF not made x $${rateText(margin)}/MSF = ${moneyRaw(contribution)} (not added)`); }
  const base=PAPER.has(kind)?gross:minutes;
  return {msf:msf===null?null:round(msf,3),paper_usd:round(paperUsd,2),machine_minutes:round(machineMinutes,2),machine_time_usd:round(machineUsd,2),usd:round(usd,2),
   usd_per_year:null,msf_not_made:round(notMade,3),contribution_usd:round(contribution,2),contribution_usd_per_year:null,contribution_status:status,usd_basis:parts.join('; '),
   share_of_window_pct:base?round(quantity/base*100,4):null,price_terms:{kind,quantity,gross_sqft:gross||null,minutes:minutes||null}};
 }
 // cost_model.annual: dollars a year at a period's rate, period-to-date dollars per production day x the post's days a year.
 // With fewer production days than min_rate_days there is no yearly figure (the coordinator's default on PR #168).
 const annual=(usd,days,cfg)=>usd===null||usd===undefined||!days||days<cfg.constants.min_rate_days?null:round(usd/days*cfg.constants.days_per_year,0);
 // A figure of a card's whole dollars by its path (`of`, `shown_of`): ['levers','trim'], ['lines','bond'].
 const wholeOf=(w,[g,k])=>w[g][k];
 // cost_model.trim_cells_over: the trim line above a target from the trim cells, each cell's gap to 0.1 sq ft, added left to right.
 const trimCellsOver=(cells,target)=>round(add(cells.map(([trim,gross])=>round(Math.max(0,trim-target/100*gross),1))),1);
 const pctText=(n,d)=>d?fixed(round(n/d*100,2),2):'unavailable';   // cost_model._pct_text: two decimals
 // cost_model.at_standard: all recorded less above standard, each priced on its own quantity.
 function atStandard(kind,total,over,rates,gross,minutes,cfg){
  const whole=price(kind,total,rates,gross,minutes,cfg), above=price(kind,over,rates,gross,minutes,cfg);
  const diff=(k,d=2)=>whole[k]===null||whole[k]===undefined?null:round(whole[k]-(above[k]||0),d);
  const atQty=round(total-over,TIME.has(kind)?2:1), base=PAPER.has(kind)?gross:minutes;
  return {msf:diff('msf',3),paper_usd:diff('paper_usd'),machine_minutes:diff('machine_minutes'),machine_time_usd:diff('machine_time_usd'),usd:diff('usd'),usd_per_year:null,
   msf_not_made:diff('msf_not_made',3),contribution_usd:diff('contribution_usd'),contribution_usd_per_year:null,contribution_status:whole.contribution_status,
   all_recorded_usd:whole.usd,above_standard_usd:above.usd,usd_basis:`${moneyRaw(whole.usd)} all recorded less ${moneyRaw(above.usd)} above standard = ${moneyRaw(diff('usd'))} at standard; `+
    (whole.contribution_usd===null?`contribution: ${cfg.margin_not_set}`:`contribution ${moneyRaw(diff('contribution_usd'))} (not added)`),
   share_of_window_pct:base?round(atQty/base*100,4):null,price_terms:{kind,part:'at',quantity:atQty,total,over,gross_sqft:gross||null,minutes:minutes||null}};
 }
 // A trim quantity at `rates`: from its trim cells when it carries them (the target is a rate), else its own.
 const trimQty=(t,rates)=>t&&t.trim_cells?trimCellsOver(t.trim_cells,rates.values.trim_target_pct):t?t.quantity:null;
 // cost_model._yearly: a ledger row's dollars a year at this period's rate, from its line of the period to date's whole dollars
 // (`of`, its cells' added up: the review of 82c0994 on PR #168, item 2); the contribution from its period to date's quantity.
 function yearly(yt,rates,cfg,ptdWhole){
  const qty=trimQty(yt,rates), p=price(yt.kind,qty,rates,null,null,cfg);
  const usd=yt.of&&ptdWhole?wholeOf(ptdWhole,yt.of):p.usd;
  return {usd_per_year:annual(usd,yt.days,cfg),contribution_usd_per_year:p.contribution_usd===null?null:annual(p.contribution_usd,yt.days,cfg),year_terms:{...yt,quantity:qty}};
 }
 // The trim lever's words name the target, a rate (cost_model.lever_label, at_label, lever_records).
 const trimLabel=target=>`Side trim over the ${gText(target)}% target`, trimAtLabel=target=>`Side trim at standard (up to ${gText(target)}% of gross)`;
 function trimRecords(cells,target){
  const trim=round(add(cells.map(c=>c[0])),1), gross=round(add(cells.map(c=>c[1])),1), over=cells.filter(([t,g])=>round(Math.max(0,t-target/100*g),1)>0).length;
  return `${over} of ${cells.length} shift${cells.length===1?'':'s'} over the ${gText(target)}% target, side trim ${pctText(trim,gross)}% of gross`;
 }
 // One priced row at `rates`, from its own terms: a ledger row's quantity above its band (the trim line from its cells, at the
 // target in force), its cost at standard (`part` at), its yearly figure from its period to date, an ownership line's split.
 function repriceRow(tool,row,rates,cfg,ptdWhole){
  const t=row&&row.price_terms; if(!t)return row;
  const target=rates.values.trim_target_pct; let out;
  if(t.part==='at'){
   const over=t.trim_cells?trimCellsOver(t.trim_cells,target):t.over, p=atStandard(t.kind,t.total,over,rates,t.gross_sqft,t.minutes,cfg);
   if(t.trim_cells)p.price_terms.trim_cells=t.trim_cells;
   out={...row,...p,quantity:p.price_terms.quantity,...(t.trim_cells?{lever:trimAtLabel(target)}:{})};
  }else if(t.trim_cells){
   const qty=trimCellsOver(t.trim_cells,target), p=price(t.kind,qty,rates,t.gross_sqft,t.minutes,cfg);
   p.price_terms.trim_cells=t.trim_cells;
   out={...row,...p,quantity:qty,lever:trimLabel(target),band_target:target,records:trimRecords(t.trim_cells,target),
    shifts_over:t.trim_cells.filter(([tr,g])=>round(Math.max(0,tr-target/100*g),1)>0).length};
  }else out={...row,...price(t.kind,t.quantity,rates,t.gross_sqft,t.minutes,cfg)};
  if(row.year_terms)Object.assign(out,yearly(row.year_terms,rates,cfg,ptdWhole));
  if(row.above_terms){   // the ownership lens: a banded line split at its band, above standard the lever's own figure
   const qty=trimQty(row.above_terms,rates), above=price(row.above_terms.kind,qty,rates,t.gross_sqft,t.minutes,cfg).usd;
   Object.assign(out,{above_standard_usd:above,at_standard_usd:round(out.usd-above,2),above_terms:{...row.above_terms,quantity:qty}});
   if(row.above_terms.trim_cells){   // the shifts over the target beside the split (PR #168, item 4; cost_model.over_records)
    const over=row.above_terms.trim_cells.filter(([tr,g])=>round(Math.max(0,tr-target/100*g),1)>0).length;
    Object.assign(out,{band_target:target,shifts_over:over,over_records:`${over} of ${out.shifts} shift${out.shifts===1?'':'s'} over the ${gText(target)}% target`});
   }
  }
  return out;
 }
 function rank(rows,key){   // cost_model._rank: by the whole dollars printed, then the key, largest first, then by name; a stable sort as Python's
  const name=r=>'gap' in r?r.gap:r.lever, shown=r=>r.usd_shown!==null&&r.usd_shown!==undefined?r.usd_shown:(r[key]||0);
  rows.sort((a,b)=>(shown(b)-shown(a))||((b[key]||0)-(a[key]||0))||(name(a)<name(b)?-1:name(a)>name(b)?1:0));
  rows.forEach((r,i)=>{r.rank=i+1;});
  return rows;
 }
 // ---- whole dollars that add up (cost_model.whole_dollars, the owner's top rule on PR #168): every set of parts a card
 // prints adds to the figure it prints, across views and nested windows. The root is the cell, one shift of one production
 // day (the review of 82c0994, item 2): its levers print their nearest dollar together and share it by the largest
 // remainder, its rows at standard theirs, each row apart and each plain waste line its own; a card is its cells added up.
 const LEVERS=['speed','trim','shear','dry_end','operator','maintenance'], BANDED=['trim','shear','dry_end','operator','maintenance'];
 const LEVER_KIND={speed:'speed',trim:'trim',shear:'waste',dry_end:'waste',operator:'downtime',maintenance:'downtime'};
 const APART_KEYS=['scheduled','external','changeover','boards'], BOARDS=['cutoff_waste','stacker_waste'];
 const APART_KIND={scheduled:'downtime',external:'downtime',changeover:'waste',boards:'waste',cutoff_waste:'waste',stacker_waste:'waste'};
 const DRY_END_KINDS=['bond','warp','misalignment','moisture','other'], AREA_LEVERS=['trim','shear','dry_end'];
 const LINE_TERMS=[...DRY_END_KINDS,'admin','bale','unexplained'], LINE_KIND={bond:'waste',warp:'waste',misalignment:'waste',moisture:'waste',other:'waste',admin:'waste',bale:'paper',unexplained:'paper'};
 const OWNER_LINES=['trim','shear','changeover','cutoff_waste','stacker_waste','admin',...DRY_END_KINDS,'unexplained'];   // cost_model.OWNER_LINES
 const toCents=usd=>usd===null||usd===undefined?0:Math.round(usd*100);   // cost_model.to_cents: two places, so never a tie
 function nearestDollar(c){ const d=Math.floor(c/100), r=c-d*100; return d+(r>50||(r===50&&((d%2)+2)%2===1)?1:0); }   // a tie to the even dollar
 function largestRemainder(parts,target){   // cost_model.largest_remainder, line for line
  const mod=c=>((c%100)+100)%100, idx=parts.map((_,i)=>i), out=parts.map(c=>Math.floor(c/100));
  let gap=target-out.reduce((a,b)=>a+b,0);
  const fraction=[...idx].sort((a,b)=>(mod(parts[b])-mod(parts[a]))||(a-b)).filter(i=>mod(parts[i]));
  const up=Math.min(Math.max(gap,0),fraction.length); for(let k=0;k<up;k++)out[fraction[k]]+=1; gap-=up;
  const largest=[...idx].sort((a,b)=>(parts[b]-parts[a])||(a-b)), positive=largest.filter(i=>parts[i]>0), rising=positive.length?positive:largest.slice(0,1);
  let j=0; while(gap>0&&rising.length){ out[rising[j%rising.length]]+=1; gap--; j++; }
  j=0; while(gap<0&&largest.some(i=>out[i]>0)){ const i=largest[j%largest.length]; if(out[i]>0){ out[i]--; gap++; } j++; }
  return out;
 }
 const sum=xs=>xs.reduce((a,b)=>a+b,0), zip=(keys,vals)=>Object.fromEntries(keys.map((k,i)=>[k,vals[i]]));
 // cost_model._priced: (cents, paper, machine time, contribution) of one quantity, price's arithmetic step for step without its text
 function priced4(kind,q,rates,cfg){
  if(q===null||q===undefined||q<0)return [0,0,0,0];
  const v=rates.values, C=cfg.constants; let paper, minutes, notMade;
  if(PAPER.has(kind)){ const msf=q/1000; paper=msf*v.paper_usd_msf; minutes=kind==='waste'?msf/C.msf_per_minute:0; notMade=kind==='waste'?msf:0; }
  else if(TIME.has(kind)){ paper=null; minutes=q; notMade=q*C.msf_per_minute; }
  else throw new Error(`Unknown cost kind ${kind}`);
  const machine=minutes/60*v.machine_usd_hour, usd=kind==='waste'?round(paper,2)+round(machine,2):(paper||0)+machine;
  const margin=v.margin_usd_msf, contribution=margin===null||margin===undefined?null:notMade*margin, c=x=>x===null?0:toCents(round(x,2));
  return [c(usd),c(paper),c(machine),c(contribution)];
 }
 function termsCents(t,rates,cfg){   // cost_model.terms_cents: the trim lever from its cell at the target in force
  const over={...t.levers,trim:trimCellsOver(t.trim_cells,rates.values.trim_target_pct)};
  const above=Object.fromEntries(LEVERS.map(k=>[k,priced4(LEVER_KIND[k],over[k],rates,cfg)])), recorded=Object.fromEntries(BANDED.map(k=>[k,priced4(LEVER_KIND[k],t.all[k],rates,cfg)]));
  const plain=Object.fromEntries(LINE_TERMS.map(k=>[k,priced4(LINE_KIND[k],t.lines[k],rates,cfg)])), boards=Object.fromEntries(BOARDS.map(k=>[k,priced4(APART_KIND[k],t.apart[k],rates,cfg)]));
  const changeover=priced4('waste',t.apart.changeover,rates,cfg);
  const owner=[recorded.trim,recorded.shear,changeover,boards.cutoff_waste,boards.stacker_waste,plain.admin,...DRY_END_KINDS.map(k=>plain[k]),plain.unexplained];
  const part=(rows,i)=>sum(rows.map(r=>r[i])), ab=LEVERS.map(k=>above[k]);
  return {levers:ab.map(r=>r[0]),at:BANDED.map(k=>recorded[k][0]-above[k][0]),apart:APART_KEYS.map(k=>priced4(APART_KIND[k],t.apart[k],rates,cfg)[0]),
   boards:BOARDS.map(k=>boards[k][0]),lines:Object.fromEntries(LINE_TERMS.map(k=>[k,plain[k][0]])),split:[part(ab,1),part(ab,2)],contribution:part(ab,3),
   owner_split:[part(owner,1),part(owner,2)],owner_contribution:part(owner,3)};
 }
 function cellWhole(t,rates,cfg){   // cost_model.cell_whole
  const p=termsCents(t,rates,cfg), total=nearestDollar(sum(p.levers)), atTotal=nearestDollar(sum(p.at));
  const e={shift:t.shift,total,levers:zip(LEVERS,largestRemainder(p.levers,total)),at_total:atTotal,at:zip(BANDED,largestRemainder(p.at,atTotal)),
   apart:zip(APART_KEYS,p.apart.map(nearestDollar))};
  e.boards=zip(BOARDS,largestRemainder(p.boards,e.apart.boards));
  const lines={trim:e.levers.trim+e.at.trim,shear:e.levers.shear+e.at.shear,dry_end:e.levers.dry_end+e.at.dry_end};
  largestRemainder(DRY_END_KINDS.map(k=>p.lines[k]),lines.dry_end).forEach((v,i)=>{ lines[DRY_END_KINDS[i]]=v; });
  Object.assign(lines,{changeover:e.apart.changeover,...e.boards});
  for(const k of ['admin','bale','unexplained'])lines[k]=nearestDollar(p.lines[k]);
  e.lines=lines;
  [e.paper,e.machine]=largestRemainder(p.split,total); e.contribution=nearestDollar(p.contribution);
  e.owner=sum(OWNER_LINES.map(k=>lines[k])); [e.owner_paper,e.owner_machine]=largestRemainder(p.owner_split,e.owner); e.owner_contribution=nearestDollar(p.owner_contribution);
  return e;
 }
 const WHOLE_SCALARS=['total','at_total','paper','machine','contribution','owner','owner_paper','owner_machine','owner_contribution'], WHOLE_GROUPS=['levers','at','apart','boards','lines'];
 const GROUP_KEYS={levers:LEVERS,at:BANDED,apart:APART_KEYS,boards:BOARDS,lines:[...AREA_LEVERS,...DRY_END_KINDS,'changeover',...BOARDS,'admin','bale','unexplained']};
 function added(cells,shift=null){   // cost_model._added
  const out={shift}; for(const k of WHOLE_SCALARS)out[k]=sum(cells.map(e=>e[k]));
  for(const g of WHOLE_GROUPS){ const keys=cells.length?Object.keys(cells[0][g]):GROUP_KEYS[g]; out[g]=Object.fromEntries(keys.map(k=>[k,sum(cells.map(e=>e[g][k]))])); }
  out.all_in=out.total+out.at_total+sum(Object.values(out.apart));
  return out;
 }
 function cellTerms(terms){   // cost_model.cell_terms: display_terms' rows of numbers as each cell's terms
  return terms.cells.map(row=>{ const v=Object.fromEntries(terms.fields.map((f,i)=>[f,row[i]]));
   return {shift:v.shift,levers:Object.fromEntries(LEVERS.filter(k=>k!=='trim').map(k=>[k,v[k]])),all:Object.fromEntries(BANDED.map(k=>[k,v['all_'+k]])),
    apart:Object.fromEntries([...APART_KEYS,...BOARDS].map(k=>[k,v['apart_'+k]])),lines:Object.fromEntries(LINE_TERMS.map(k=>[k,v[k]])),trim_cells:[[v.trim,v.gross]]}; });
 }
 // a cell's whole dollars at one set of rates, kept for the page (cost_model._CELL_WHOLE): a day card and its shifts read one period to date
 const CELL_WHOLE=new Map();
 function wholeDollars(terms,rates,cfg){   // cost_model.whole_dollars: the card's cells added up, and each shift's
  const v=rates.values, key=[v.paper_usd_msf,v.machine_usd_hour,v.margin_usd_msf,v.trim_target_pct].join('|');
  if(CELL_WHOLE.size>50000)CELL_WHOLE.clear();
  const ts=cellTerms(terms), cells=terms.cells.map((row,i)=>{ const k=key+'#'+row.join(','); if(!CELL_WHOLE.has(k))CELL_WHOLE.set(k,cellWhole(ts[i],rates,cfg)); return CELL_WHOLE.get(k); }), by=new Map();
  for(const e of cells){ if(!by.has(e.shift))by.set(e.shift,[]); by.get(e.shift).push(e); }
  const out=added(cells); out.shifts=[...by.keys()].sort((a,b)=>a-b).map(sh=>added(by.get(sh),sh));
  return out;
 }
 const splitShown=(parts,target)=>largestRemainder(parts.map(toCents),target);   // cost_model.split_shown
 const rowShown=(r,w)=>!r.apart?w.levers[r.key]:r.apart==='at-standard'?w.at[r.key]:w.apart[r.key];   // cost_model.row_shown
 function attachShown(out,rows,w){   // cost_model.attach_shown
  for(const r of rows)r.usd_shown=rowShown(r,w);
  out.total_usd_shown=w.total; out.at_standard_usd_shown=w.at_total; out.all_in_usd_shown=w.all_in;
  out.total_paper_usd_shown=w.paper; out.total_machine_time_usd_shown=w.machine;
  out.total_contribution_usd_shown=out.total_contribution_usd===null||out.total_contribution_usd===undefined?null:w.contribution;
  for(const b of out.buckets||[])b.usd_shown=rows.filter(r=>!r.apart&&r.bucket===b.bucket).reduce((a,r)=>a+r.usd_shown,0);
 }
 function ownershipShown(rows,w){   // cost_model.ownership_shown: each line the figure every view of it prints (wholeLines)
  for(const r of rows){ const k=r.key;
   if(k==='trim'||k==='shear'){ r.above_standard_usd_shown=w.levers[k]; r.at_standard_usd_shown=w.at[k]; }
   r.usd_shown=w.lines[k];
  }
  return [w.levers.dry_end,w.at.dry_end];
 }
 function ownershipTotalsShown(out,rows,dry,w){   // cost_model.ownership_totals_shown
  const split=rows.filter(r=>r.above_standard_usd_shown!==null&&r.above_standard_usd_shown!==undefined), group=out.dry_end_rank!==null&&out.dry_end_rank!==undefined;
  out.total_usd_shown=sum(rows.map(r=>r.usd_shown));
  out.above_standard_usd_shown=sum(split.map(r=>r.above_standard_usd_shown))+(group?dry[0]:0);
  out.at_standard_usd_shown=sum(split.map(r=>r.at_standard_usd_shown))+(group?dry[1]:0);
  out.no_band_usd_shown=out.total_usd_shown-out.above_standard_usd_shown-out.at_standard_usd_shown;
  if(group){ out.dry_end_usd_shown=sum(rows.filter(r=>r.group==='dry_end').map(r=>r.usd_shown)); [out.dry_end_above_standard_usd_shown,out.dry_end_at_standard_usd_shown]=dry; }
  if(w.owner===out.total_usd_shown){ out.total_paper_usd_shown=w.owner_paper; out.total_machine_time_usd_shown=w.owner_machine; }
  else [out.total_paper_usd_shown,out.total_machine_time_usd_shown]=splitShown([out.total_paper_usd||0,out.total_machine_time_usd||0],out.total_usd_shown);
  out.total_contribution_usd_shown=out.total_contribution_usd===null||out.total_contribution_usd===undefined?null:w.owner_contribution;
  for(const b of out.buckets||[])b.usd_shown=sum(rows.filter(r=>bucketOf(r)===b.bucket).map(r=>r.usd_shown));
 }
 // cost_model.tile_shown: a tile prints the ledger's whole dollars it is (shown_of), else its own nearest dollar
 const tileShown=(t,w)=>t.shown_of&&w&&t.usd!==null&&t.usd!==undefined?t.shown_of.reduce((a,[g,k])=>a+w[g][k],0):t.usd===null||t.usd===undefined?null:nearestDollar(toCents(t.usd));
 // cost_model.trim_by_shift at `rates`: each shift's side trim from its own cells (above the target in force, or all recorded)
 function trimByShift(details,w,rates,cfg){
  const target=rates.values.trim_target_pct;
  return details.map(r=>{ const t=r.price_terms, all=t.part==='all', qty=all?t.quantity:trimCellsOver(t.trim_cells,target), p=price('trim',qty,rates,t.gross_sqft,t.minutes,cfg);
   const e=w.shifts.find(x=>x.shift===r.shift)||{levers:{trim:0},at:{trim:0}};
   return {...r,...(all?{}:{lever:trimLabel(target)}),quantity:qty,usd:p.usd,usd_shown:e.levers.trim+(all?e.at.trim:0),band_target:target,
    shifts_over:t.trim_cells.filter(([tr,g])=>round(Math.max(0,tr-target/100*g),1)>0).length,price_terms:{...p.price_terms,trim_cells:t.trim_cells,part:t.part}}; });
 }
 // cost_model.rank_ledger (issue #164): the levers ranked by dollars (the supervisor lens: the controllable ones only); the rest
 // after them with no rank, by group (APART: at standard, schedule-driven, external, no band) and dollars.
 const APART={'at-standard':0,'schedule-driven':1,external:2,'no-band':3};
 const ledgerGroup=r=>r.apart?APART[r.apart]:r.control==='schedule-driven'?APART['schedule-driven']:APART.external;
 const byUsd=(a,b)=>((b.usd||0)-(a.usd||0))||(a.lever<b.lever?-1:a.lever>b.lever?1:0);
 const shownOf=r=>r.usd_shown!==null&&r.usd_shown!==undefined?r.usd_shown:(r.usd||0);
 const byShown=(a,b)=>(shownOf(b)-shownOf(a))||byUsd(a,b);   // the whole dollars printed first (PR #168), then the cents, then the name
 function rankLedger(rows,lens){
  const ranked=rows.filter(r=>!r.apart&&(lens!=='supervisor'||r.control==='controllable')), rest=rows.filter(r=>!ranked.includes(r));
  rank(ranked,'usd'); rest.sort((a,b)=>(ledgerGroup(a)-ledgerGroup(b))||byShown(a,b)); rest.forEach(r=>{r.rank=null;});
  rows.splice(0,rows.length,...ranked,...rest); return rows;
 }
 // Ownership (cost_model.owner_order): side trim first always; then the dry-end group (the tag scrap, PR #111) by the sum of its
 // rows and each other line by dollars, the whole dollars printed first; the group holds the rank, its rows beneath it.
 function rankOwnership(rows){
  const [first,...rest]=rows, groups=new Map(); first.rank=1;
  for(const r of rest){ const k=r.group||r.lever; if(!groups.has(k))groups.set(k,[]); groups.get(k).push(r); }
  const size=new Map(); for(const [k,m] of groups){ m.sort(byShown); size.set(k,[m.reduce((a,r)=>a+shownOf(r),0),add(m.map(r=>r.usd||0))]); }
  const keys=[...groups.keys()].sort((a,b)=>(size.get(b)[0]-size.get(a)[0])||(size.get(b)[1]-size.get(a)[1])||(a<b?-1:a>b?1:0)), out=[first];
  keys.forEach((k,i)=>{ for(const r of groups.get(k)){ r.rank=r.group?null:i+2; out.push(r); } });
  rows.splice(0,rows.length,...out); return rows;
 }
 // The ownership lens's dry-end subtotal (cost_model.dry_end_subtotal): its rows' dollars added left to right, split at its band.
 function dryEnd(rows,data,rates,cfg){
  const keys=[]; for(const r of rows.slice(1)){ const k=r.group||r.lever; if(!keys.includes(k))keys.push(k); }
  const mine=rows.filter(r=>r.group==='dry_end'), years=mine.map(r=>r.usd_per_year), usd=round(add(mine.map(r=>r.usd||0)),2);
  const t=data.dry_end_above_terms, above=price(t.kind,t.quantity,rates,data.gross_sqft,data.scheduled_minutes,cfg).usd;
  return {dry_end_rank:keys.indexOf('dry_end')+2,dry_end_kinds:mine.length,dry_end_usd:usd,
   dry_end_usd_per_year:years.some(v=>v===null||v===undefined)?null:round(add(years.map(v=>v||0)),0),
   dry_end_above_standard_usd:above,dry_end_at_standard_usd:round(usd-above,2)};
 }
 // The controllable levers (cost_model.opportunities): in their ranked order, added left to right.
 function controllable(rows){
  const mine=rows.filter(r=>r.control==='controllable'&&!r.apart), years=mine.map(r=>r.usd_per_year);
  return {controllable_levers:mine.length,controllable_usd:round(add(mine.map(r=>r.usd||0)),2),
   controllable_usd_per_year:years.some(v=>v===null||v===undefined)?null:round(add(years.map(v=>v||0)),0)};
 }
 function totals(rows,cfg){   // cost_model._totals
  const total=key=>{ const vs=rows.map(r=>r[key]).filter(v=>v!==null&&v!==undefined); return vs.length?round(add(vs),2):0; };
  const out={total_usd:total('usd'),total_paper_usd:total('paper_usd'),total_machine_time_usd:total('machine_time_usd')};
  const years=rows.map(r=>r.usd_per_year);
  out.total_usd_per_year=rows.length&&!years.some(v=>v===null||v===undefined)?round(add(years),0):null;
  const set=rows.every(r=>r.contribution_status!==cfg.margin_not_set);
  out.total_contribution_usd=set?total('contribution_usd'):null; out.contribution_status=set?'set':cfg.margin_not_set;
  return out;
 }
 // cost_model.ledger_totals: the levers above standard, the cost at standard and everything recorded, left to right in row order.
 function ledgerTotals(rows,cfg){
  const levers=rows.filter(r=>!r.apart), at=rows.filter(r=>r.apart==='at-standard'&&r.usd!==null&&r.usd!==undefined).map(r=>r.usd);
  const usd=rows.map(r=>r.usd).filter(v=>v!==null&&v!==undefined);
  return {...totals(levers,cfg),...summary(levers),levers:levers.length,apart_gaps:rows.length-levers.length,
   at_standard_usd:at.length?round(add(at),2):0,all_in_usd:usd.length?round(add(usd),2):0};
 }
 // cost_model.ptd_ledger: the period to date's dollars above standard, lever by lever, its whole dollars (its cells' added up)
 // and each lever's yearly figure at this period's rate, read from its whole dollars (the review of 82c0994, item 2).
 function ptd(terms,rates,cfg,w){
  const levers=terms.levers.map(l=>{ const p=price(l.kind,trimQty(l,rates),rates,null,null,cfg), shown=w?w.levers[l.key]:null;
   return {key:l.key,usd:p.usd,usd_shown:shown,usd_per_year:annual(w?shown:p.usd,terms.days,cfg)}; });
  const years=levers.map(l=>l.usd_per_year);
  return {ptd_usd:round(add(levers.map(l=>l.usd)),2),ptd_usd_per_year:years.some(v=>v===null)?null:round(add(years),0),levers};
 }
 // cost_model.bucket_of / summary: the chips' bucket totals and the arithmetic's sums, from the rows in their ranked order.
 function bucketOf(row){
  if(row.bucket)return row.bucket;
  const kind=(row.price_terms&&row.price_terms.kind)||row.kind, name=String(row.gap||row.lever||'').toLowerCase();
  if(row.kind==='setup')return 'Setups';
  if(kind==='downtime'||kind==='speed'||kind==='trim')return {downtime:'Stops',speed:'Speed',trim:'Trim'}[kind];
  return name.startsWith('shear')?'Shear':name.startsWith('dry-end')?'Dry-end':'Other waste';
 }
 function summary(rows){
  const buckets=new Map();
  for(const r of rows)if(r.usd!==null&&r.usd!==undefined){ const k=bucketOf(r), b=buckets.get(k)||{usd:[],year:[]}; b.usd.push(r.usd); b.year.push(r.usd_per_year); buckets.set(k,b); }
  const out=[...buckets.entries()].map(([bucket,b])=>({bucket,usd:round(add(b.usd),2),usd_per_year:b.year.some(v=>v===null||v===undefined)?null:round(add(b.year),0)}));
  out.sort((a,b)=>(b.usd-a.usd)||(a.bucket<b.bucket?-1:a.bucket>b.bucket?1:0));
  const priced=rows.filter(r=>r.price_terms), minutes=kind=>round(add(priced.filter(r=>r.price_terms.kind===kind).map(r=>r.machine_minutes)),2);
  const area=add(priced.filter(r=>PAPER.has(r.price_terms.kind)).map(r=>r.price_terms.quantity));
  return {buckets:out,area_sqft:round(area,1),area_msf:round(area/1000,3),
   stop_minutes:minutes('downtime'),speed_minutes:minutes('speed'),waste_run_minutes:minutes('waste'),
   machine_minutes:round(add(priced.map(r=>r.machine_minutes)),2),msf_not_made:round(add(priced.map(r=>r.msf_not_made)),3)};
 }
 // cost_model.tile: a KPI tile's dollars, the sum of its parts' pricing calls; the window's own, never a year.
 function tile(t,rates,cfg){
  const priced=(t.parts||[]).map(p=>p?price(p.kind,p.quantity,rates,p.gross_sqft,p.minutes,cfg):{...NOT_PRICED});
  const usd=priced.map(r=>r.usd).filter(v=>v!==null&&v!==undefined);
  return {...t,usd:usd.length?round(add(usd),2):null,usd_per_year:null,
   machine_minutes:round(add(priced.map(r=>r.machine_minutes||0)),2),usd_basis:priced.map(r=>r.usd_basis).join(' + '),parts:priced.map(r=>r.price_terms)};
 }
 // A tool result at `rates`: every priced row re-priced from its terms; the two money tools re-ranked with their
 // totals redone; the rates rows and the provenance line rewritten. `byIndex` (not enumerable) keeps the re-priced rows
 // in the result's own order, so a reference to "row 3" still finds its row after the ranking moved it.
 // get_speed_variance (cost_model.speed_variance_dollars): a bucket's feet over the reference speed are its machine
 // minutes; its formula starts with that division, which is rate-free and kept. The grade mix is schedule-driven and
 // never priced (issue #101); the envelope's controllable dollars are the priced buckets in the tool's order, added
 // left to right, and the gap as a whole is not priced.
 const SPEEDVAR_LEAD=/^.*? reference speed = /;
 function speedVariance(data,byIndex,out,rates,cfg){
  byIndex.forEach((r,i)=>{ const was=data.rows[i]; if(!was.price_terms)return; const lead=SPEEDVAR_LEAD.exec(was.usd_basis||''); if(lead)r.usd_basis=lead[0]+r.usd_basis; });
  if(!('controllable_usd' in data))return;   // a chart of it has the rows only
  const priced=byIndex.filter(r=>r.control==='controllable'&&r.usd!==null&&r.usd!==undefined);   // never the mix, planned or external stops
  const total=(key,places=2)=>{ const vs=priced.map(r=>r[key]).filter(v=>v!==null&&v!==undefined); return vs.length?round(add(vs),places):null; };
  Object.assign(out,{controllable_machine_minutes:total('machine_minutes'),controllable_usd:total('usd'),
   controllable_usd_per_year:priced.length&&priced.every(r=>r.usd_per_year!==null&&r.usd_per_year!==undefined)?total('usd_per_year',0):null,
   controllable_contribution_usd:total('contribution_usd'),
   contribution_status:rates.values.margin_usd_msf===null||rates.values.margin_usd_msf===undefined?cfg.margin_not_set:'set'});
 }
 function reprice(tool,data,rates,cfg){
  if(!priced(data))return data;
  // the period to date's whole dollars first: a window's yearly figures are read from them (cost_model._yearly)
  // (a chart of a window's levers carries them as `ptd_display_terms`, agent.chart_for)
  const ptdTerms=data.ptd_terms&&data.ptd_terms.whole?data.ptd_terms.whole:data.ptd_display_terms||null, pw=ptdTerms?wholeDollars(ptdTerms,rates,cfg):null;
  const rows=Array.isArray(data.rows)?data.rows:[], byIndex=rows.map(r=>repriceRow(tool,r,rates,cfg,pw)), out={...data,rows:[...byIndex]};
  if(tool==='get_speed_variance')speedVariance(data,byIndex,out,rates,cfg);   // a chart of it has the rows only
  // the whole dollars a card prints (cost_model.whole_dollars, PR #168), from its display terms at these rates
  const w=data.display_terms?wholeDollars(data.display_terms,rates,cfg):null;
  if(MONEY_TOOLS.has(tool)){   // the one money ledger (issue #164): ranked, its totals redone, its period to date re-priced
   if(tool==='get_opportunities'&&data.lens==='ownership'){
    const dry=w?ownershipShown(out.rows,w):null;
    rankOwnership(out.rows); Object.assign(out,totals(out.rows,cfg)); if('buckets' in data)Object.assign(out,summary(out.rows));
    if('dry_end_usd' in data)Object.assign(out,dryEnd(out.rows,data,rates,cfg));
    if('above_standard_usd' in data){   // cost_model.opportunities: the lens's split, the lines' parts added left to right
     const above=out.rows.filter(r=>r.above_standard_usd!==null&&r.above_standard_usd!==undefined).map(r=>r.above_standard_usd).concat('dry_end_usd' in data?[out.dry_end_above_standard_usd]:[]);
     const at=out.rows.filter(r=>r.at_standard_usd!==null&&r.at_standard_usd!==undefined).map(r=>r.at_standard_usd);
     out.above_standard_usd=round(add(above),2); out.at_standard_usd=round(add(at)+('dry_end_usd' in data?out.dry_end_at_standard_usd:0),2);
     out.no_band_usd=round(out.total_usd-out.above_standard_usd-out.at_standard_usd,2);
    }
    if(w)ownershipTotalsShown(out,out.rows,dry,w);
   }else{
    if(w)for(const r of out.rows)r.usd_shown=rowShown(r,w);
    rankLedger(out.rows,data.lens);
    if('levers' in data)Object.assign(out,ledgerTotals(out.rows,cfg));
    else Object.assign(out,totals(out.rows.filter(r=>!r.apart),cfg));   // a chart of it has the rows only
    if(w&&'levers' in data)attachShown(out,out.rows,w);
    if(tool==='get_opportunities'&&'controllable_usd' in data){ Object.assign(out,controllable(out.rows)); if(w)out.controllable_usd_shown=out.rows.filter(r=>r.control==='controllable'&&!r.apart).reduce((a,r)=>a+r.usd_shown,0); }
   }
   if(w&&Array.isArray(data.details))out.details=trimByShift(data.details,w,rates,cfg);
   if(w&&tool==='get_day_dollars'&&Array.isArray(data.shifts))   // each shift's share of the day's whole dollars (cost_model.day_dollars)
    out.shifts=data.shifts.map(x=>{ const e=w.shifts.find(y=>y.shift===x.shift); return e&&'usd_shown' in x?{...x,usd_shown:e.total}:x; });
   if(data.ptd_terms){
    const p=ptd(data.ptd_terms,rates,cfg,pw);
    out.ptd_usd=p.ptd_usd; out.ptd_usd_per_year=p.ptd_usd_per_year;
    if(pw)out.ptd_usd_shown=pw.total;
    out.ptd_terms={...data.ptd_terms,levers:data.ptd_terms.levers.map(l=>({...l,quantity:trimQty(l,rates)}))};
    if(Array.isArray(data.ptd_levers))out.ptd_levers=data.ptd_levers.map(l=>{ const q=p.levers.find(x=>x.key===l.key); return {...l,usd:q.usd,usd_per_year:q.usd_per_year,...('usd_shown' in l?{usd_shown:q.usd_shown}:{}),...(l.key==='trim'?{lever:trimLabel(rates.values.trim_target_pct)}:{})}; });
   }
  }
  if(w&&tool==='get_waste_breakdown')for(const r of out.rows)r.usd_shown=r.usd===null||r.usd===undefined?null:w.lines[r.category];   // cost_model.waste_shown
  if(w&&!MONEY_TOOLS.has(tool))for(const r of out.rows)if(r.shown_of&&r.usd!==null&&r.usd!==undefined)r.usd_shown=r.shown_of.reduce((a,[g,k])=>a+w[g][k],0);   // cost_model.daily_shown
  if(Array.isArray(data.tile_money))out.tile_money=data.tile_money.map(t=>{ const x=tile(t,rates,cfg); return {...x,usd_shown:tileShown(x,w)}; });
  if('provenance' in data){ out.rates=rateRows(rates,cfg); out.provenance=provenance(rates,cfg); }
  Object.defineProperty(out,'byIndex',{value:byIndex,enumerable:false});
  return out;
 }
 const priced=data=>!!(data&&(Array.isArray(data.tile_money)&&data.tile_money.length||data.ptd_terms||Array.isArray(data.rows)&&data.rows.some(r=>r&&r.price_terms)));
 // A chart drawn from a priced result (render_chart): its rows at `rates`, ranked as its tool ranks them.
 const repriceChart=(chart,tool,rates,cfg)=>priced(chart)?reprice(tool,chart,rates,cfg):chart;
 // A priced row's share of its window: area rows of the gross, time rows of the scheduled minutes.
 const share=row=>row&&row.share_of_window_pct!==undefined?row.share_of_window_pct:null;   // the row's own share of its window (cost_model.price)

 // ---- the answer's prose at the visitor's rates (shapes.py wrote it at the request's rates). Each dollar figure is a
 // field of the row or result its line cites, written by shapes.money; it becomes the same field re-priced. The rates
 // line, a formula line and the contribution line are written again whole. A ranked money list is put in its new order.
 const TOKEN=/\$\d{1,3}(?:,\d{3})*(?!\d|,\d|\.\d|\/(?:MSF|hr))/g, REF=/\(query (\d+)(?: · (row|detail|weekday) (\d+))?\)$/, YEARLY=/^(?:\/yr|\/year| a year| per year)/;
 function contributionLine(data,cfg){   // shapes.contribution
  if(data.contribution_status===cfg.margin_not_set)return `Contribution ${cfg.margin_not_set}: without an owner margin no lost contribution is priced, so the dollars are paper and machine time.`;
  if('controllable_contribution_usd' in data)   // shapes.period_speedvar: the controllable buckets' contribution, the mix never priced
   return data.controllable_contribution_usd===null||data.controllable_contribution_usd===undefined?null:
    `Contribution ${money(data.controllable_contribution_usd)} for the controllable buckets at the set margin, not added to the machine time.`;
  if(data.total_contribution_usd!==null&&data.total_contribution_usd!==undefined)return `Contribution ${money(data.total_contribution_usd_shown!==null&&data.total_contribution_usd_shown!==undefined?data.total_contribution_usd_shown:data.total_contribution_usd)} at the set margin, not added to the losses above.`;
  return null;
 }
 const CONTRIBUTION=/^Contribution (?:.+ at the set margin, not added to the losses above\.|.+ for the controllable buckets at the set margin, not added to the machine time\.|.+: without an owner margin no lost contribution is priced, so the dollars are paper and machine time\.)$/;
 // steps: index = query number - 1; each {tool, before, after} for a priced step, null otherwise.
 function repriceSections(sections,steps,rates,cfg){
  const live=steps.filter(Boolean); if(!live.length)return sections;
  const head=REF.exec(sections[0]||''), cited=head&&steps[Number(head[1])-1];
  const money1=cited&&'contribution_status' in cited.after?cited:live.find(s=>'contribution_status' in s.after);   // the result whose contribution the answer states
  const out=[];
  for(const line of sections){
   if(line.startsWith('Rates: ')&&line.endsWith(' '+cfg.note)){ out.push(`${provenance(rates,cfg)} ${cfg.note}`); continue; }
   if(line.startsWith('How a dollar is priced, for ')){ out.push(basisLine(line,live)); continue; }
   if(CONTRIBUTION.test(line)){ const again=money1?contributionLine(money1.after,cfg):null; if(again)out.push(again); continue; }
   out.push(repriceLine(line,steps,live));
  }
  return reorder(out,steps);
 }
 // shapes.formula_line: a row's formula in cents, and the figure the row prints when that is not the cents' nearest dollar
 const printsNote=row=>row&&row.usd_shown!==null&&row.usd_shown!==undefined&&row.usd!==null&&row.usd!==undefined&&row.usd_shown!==nearestDollar(toCents(row.usd))?
  `It prints ${money(row.usd_shown)} (whole dollars that add up).`:'';
 function formulaLine(label,row){ const note=printsNote(row); return `How a dollar is priced, for ${label}: ${row.usd_basis}.`+(note?' '+note:''); }
 function basisLine(line,live){
  // a day card's formula line is its day's (shapes.ledger_notes of get_day_dollars): when one shift holds a lever the day's row
  // and that shift's read the same line, and at other rates they part, so the day's step is read first
  const order=[...live.filter(s=>s.tool==='get_day_dollars'),...live.filter(s=>s.tool!=='get_day_dollars')];
  for(const s of order)for(const [i,row] of s.before.rows.entries()){
   if(!row||!row.usd_basis)continue;
   // A money tool's formula line is for its first row, the most dollars: at other rates that may be another row.
   for(const key of LABEL_KEYS)if(key in row&&line===formulaLine(row[key],row)){ const now=MONEY_TOOLS.has(s.tool)&&i===0?s.after.rows[0]:s.after.byIndex[i]; return formulaLine(now[key],now); }
  }
  return line;
 }
 function candidates(line,steps,live){
  const m=REF.exec(line), out=[];
  const rowFields=(s,i)=>{ for(const [f,role] of WINDOW_FIELDS)out.push({old:s.before.rows[i][f],now:s.after.byIndex[i][f],role:role||'window'});
   for(const f of YEAR_FIELDS)out.push({old:s.before.rows[i][f],now:s.after.byIndex[i][f],role:'year'}); };
  const totalFields=s=>{ for(const [f,role] of TOTAL_WINDOW)if(f in s.before)out.push({old:s.before[f],now:s.after[f],role:role||'window',controllable:f.startsWith('controllable_'),subtotal:f.startsWith('dry_end_')});
   for(const f of TOTAL_YEAR)if(f in s.before)out.push({old:s.before[f],now:s.after[f],role:'year',controllable:f.startsWith('controllable_'),subtotal:f.startsWith('dry_end_')}); };
  const step=m?steps[Number(m[1])-1]:null;
  if(m&&!step)return out;   // the line cites an unpriced query: its dollars, if any, are not this layer's
  const detail=step&&m[2]==='detail'&&Array.isArray(step.before.details)&&step.before.details[Number(m[3])];   // a shift's side trim (PR #168)
  if(detail){ for(const [f,role] of WINDOW_FIELDS)out.push({old:detail[f],now:(step.after.details||[])[Number(m[3])]?.[f],role:role||'window'}); totalFields(step); }
  else if(step&&m[2]==='row'&&step.before.rows[Number(m[3])]){ rowFields(step,Number(m[3])); totalFields(step); }
  else if(step){ totalFields(step); step.before.rows.forEach((r,i)=>{ if(r&&r.price_terms)rowFields(step,i); }); }
  else for(const s of live){ totalFields(s); s.before.rows.forEach((r,i)=>{ if(r&&r.price_terms)rowFields(s,i); }); }
  return out;
 }
 // The trim lever's name and records name the target, a rate: a line citing that row says the new target.
 // A split's shifts over its band (`over_records`, PR #168, item 4) and a shift's side trim line name the target too.
 function renamed(line,steps){
  const m=REF.exec(line), s=m&&(m[2]==='row'||m[2]==='detail')?steps[Number(m[1])-1]:null; if(!s||!MONEY_TOOLS.has(s.tool))return line;
  const detail=m[2]==='detail', was=detail?(s.before.details||[])[Number(m[3])]:s.before.rows[Number(m[3])], now=detail?(s.after.details||[])[Number(m[3])]:s.after.byIndex[Number(m[3])];
  if(!was||!now)return line;
  let out=line; for(const key of ['lever','records','over_records'])if(!detail&&was[key]&&now[key]&&was[key]!==now[key])out=out.replace(was[key],now[key]);
  if(detail&&was.band_target!==now.band_target)out=out.replace(`over the ${gText(was.band_target)}% target ·`,`over the ${gText(now.band_target)}% target ·`);
  return out;
 }
 // The controllable totals (issue #101) are stated only on the lines that name them, "… in controllable levers" (the
 // supervisor lens) and "The controllable buckets are …" (the speed variance): such a line takes them first, and no
 // other line takes them, so a window whose controllable total reads as its all-lever total (no side trim over the target)
 // re-prices each line to its own figure (review of #111).
 // The ownership lens's dry-end subtotal (PR #111) is the same: only the line naming the tag scrap takes it, first.
 // A figure's following words pick among fields of the same old value (issue #164): " at standard", " above standard in"
 // (the period to date), " above standard", a yearly word; with none, a window figure.
 const CONTROLLABLE=/\bcontrollable (?:losses|buckets|levers)\b/, TAG_SCRAP=/\(tag scrap\)/;
 const ROLE_OF=after=>YEARLY.test(after)?'year':/^ at standard\b/.test(after)?'at':/^ above standard in\b/.test(after)?'ptd':/^ above standard\b/.test(after)?'above':'window';
 function repriceLine(line,steps,live){
  if(!line.includes('$'))return line;
  const names=CONTROLLABLE.test(line), tag=TAG_SCRAP.test(line);
  const all=candidates(line,steps,live).filter(c=>(names||!c.controllable)&&(tag||!c.subtotal));
  const first=c=>names?c.controllable:tag?c.subtotal:false;
  const pool=[...all.filter(first),...all.filter(c=>!first(c))], used=new Set();
  return renamed(line,steps).replace(TOKEN,(token,at,whole)=>{
   const role=ROLE_OF(whole.slice(at+token.length)), yearly=role==='year';
   // the same role first; then, for a window figure, any window field ("above standard" also follows a ledger's own total)
   const order=[...pool.filter(c=>c.role===role),...pool.filter(c=>c.role!==role&&(c.role==='year')===yearly),...pool.filter(c=>(c.role==='year')!==yearly)];
   const hit=order.find(c=>!used.has(c)&&c.old!==null&&c.old!==undefined&&money(c.old)===token);
   if(!hit)return token;
   used.add(hit); return money(hit.now);
  });
 }
 // "- 3. Side trim: $4,383 · … (query 1 · row 2)": a money tool's ranked list, put in its re-priced order and renumbered.
 // Every line that cites a money tool's row takes the row's place in the re-ranked rows, which is what the server cites.
 // The numbered lines of one result fill its numbered slots in rank order, wherever the answer put those slots (the
 // supervisor lens lists five beside the headline and the other controllable levers under a label, issue #101), and
 // its unnumbered lines (the levers a lens shows unranked) fill theirs in row order.
 // The ownership lens with its dry-end subtotal (PR #111): the subtotal line goes with its group, so the block of lever
 // lines is written again in the re-ranked order, the subtotal before the group's first row, each line renumbered.
 function grouped(out,s,g,RANKED){
  const at=out.findIndex(l=>l.startsWith('- ')&&TAG_SCRAP.test(l)&&RANKED.test(l)), lines=new Map();
  for(const x of [...g.ranked,...g.plain])lines.set(s.after.byIndex[x.i],x.line);
  const slots=[...g.ranked,...g.plain].map(x=>x.at).concat(at<0?[]:[at]).sort((a,b)=>a-b), seq=[];
  s.after.rows.forEach((r,pos)=>{
   if(r.group==='dry_end'&&at>=0&&!seq.some(x=>x.sub))seq.push({sub:true,line:out[at].replace(RANKED,(m,who)=>`- ${who}${s.after.dry_end_rank}. `)});
   const line=lines.get(r); if(line===undefined)return;
   seq.push({line:(RANKED.test(line)?line.replace(RANKED,(m,who)=>`- ${who}${r.rank}. `):line).replace(/ · row \d+\)$/,` · row ${pos})`)});
  });
  slots.forEach((slot,j)=>{ out[slot]=seq[j].line; });
 }
 function reorder(lines,steps){
  const RANKED=/^- ((?:Shift \d · )?)(\d+)\. /;
  const cite=line=>{ const m=REF.exec(line); if(!line.startsWith('- ')||!m||m[2]!=='row')return null; const s=steps[Number(m[1])-1]; return s&&MONEY_TOOLS.has(s.tool)?{s,i:Number(m[3])}:null; };
  const out=[...lines], groups=new Map();
  out.forEach((line,at)=>{ const k=cite(line); if(!k)return; const g=groups.get(k.s)||{ranked:[],plain:[]}; (RANKED.test(line)?g.ranked:g.plain).push({at,line,i:k.i}); groups.set(k.s,g); });
  for(const [s,g] of groups){
   if(s.tool==='get_opportunities'&&s.after.lens==='ownership'&&'dry_end_rank' in s.after){ grouped(out,s,g,RANKED); continue; }
   const pos=x=>s.after.rows.indexOf(s.after.byIndex[x.i]);
   const fill=(slots,write)=>{ const sorted=[...slots].sort((x,y)=>pos(x)-pos(y)); slots.forEach((slot,j)=>{ out[slot.at]=write(sorted[j]); }); };
   fill(g.ranked,x=>x.line.replace(RANKED,(m,who)=>`- ${who}${s.after.byIndex[x.i].rank}. `).replace(/ · row \d+\)$/,` · row ${pos(x)})`));
   fill(g.plain,x=>x.line.replace(/ · row \d+\)$/,` · row ${pos(x)})`));
  }
  return out;
 }
 return {fixed,fmt,round,group,gText,rateText,money,moneyRaw,short,base,effective,parse,check,format,rateRows,provenance,price,repriceRow,reprice,repriceChart,priced,share,rank,rankLedger,rankOwnership,controllable,totals,ledgerTotals,summary,bucketOf,trimCellsOver,annual,atStandard,tile,
  repriceSections,contributionLine,PAPER,TIME,MONEY_TOOLS,
  nearestDollar,largestRemainder,wholeDollars,splitShown,toCents,formulaLine,printsNote};
})();
if(typeof module!=='undefined'&&module.exports)module.exports=Money;
