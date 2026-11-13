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
 const WINDOW_FIELDS=['usd','paper_usd','machine_time_usd','contribution_usd'], YEAR_FIELDS=['usd_per_year','contribution_usd_per_year'];
 const TOTAL_WINDOW=['total_usd','total_paper_usd','total_machine_time_usd','total_contribution_usd','controllable_usd','controllable_contribution_usd','dry_end_usd','all_in_usd'],
  TOTAL_YEAR=['total_usd_per_year','controllable_usd_per_year','dry_end_usd_per_year','all_in_usd_per_year'];
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
 const moneyRaw=v=>'$'+(Math.abs(v)>=100?fmt(v,0):fmt(v,2));         // cost_model._money: inside a formula
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

 // ---- pricing: cost_model.price, line for line
 const NOT_PRICED={msf:null,paper_usd:null,machine_minutes:null,machine_time_usd:null,usd:null,usd_per_year:null,msf_not_made:null,contribution_usd:null,contribution_usd_per_year:null,contribution_status:'not priced',usd_basis:'not priced: the quantity is unavailable or a negative remainder',share_of_window_pct:null,price_terms:null};
 const add=values=>{ let total=0; for(const v of values)total+=v; return total; };   // cost_model._add: left to right
 function price(kind,quantity,rates,gross,minutes,cfg){
  const v=rates.values, C=cfg.constants, paper=v.paper_usd_msf, hour=v.machine_usd_hour, margin=v.margin_usd_msf;
  const byVolume=gross?C.annual_msf_per_machine/(gross/1000):null, byTime=minutes?C.annual_minutes/minutes:null;
  if(quantity===null||quantity===undefined||quantity<0)return {...NOT_PRICED};
  const parts=[]; let msf, paperUsd, paperYear, machineMinutes, notMade;
  if(PAPER.has(kind)){
   msf=quantity/1000; paperUsd=msf*paper; machineMinutes=kind==='waste'?msf/C.msf_per_minute:0; notMade=kind==='waste'?msf:0;
   parts.push(`${fmt(quantity,1)} sq ft = ${fmt(msf,3)} MSF x $${rateText(paper)}/MSF paper = ${moneyRaw(paperUsd)}`);
   paperYear=byVolume?paperUsd*byVolume:null;
  }else if(TIME.has(kind)){ msf=null; paperUsd=null; paperYear=0; machineMinutes=quantity; notMade=quantity*C.msf_per_minute; }
  else throw new Error(`Unknown cost kind ${kind}`);
  const machineUsd=machineMinutes/60*hour;
  if(kind!=='trim'&&kind!=='paper'){ const lead=TIME.has(kind)?'':`${fmt(msf,3)} MSF / ${gText(C.msf_per_minute)} MSF per min = `; parts.push(`${lead}${fmt(machineMinutes,2)} min x $${rateText(hour,true)}/hr machine time = ${moneyRaw(machineUsd)}`); }
  const usd=(paperUsd||0)+machineUsd;
  const factor=kind==='waste'?byVolume:byTime;
  const machineYear=machineUsd===0?0:factor?machineUsd*factor:null;
  const usdYear=paperYear!==null&&machineYear!==null?paperYear+machineYear:null;
  let contribution=null, contributionYear=null, status=cfg.margin_not_set;
  if(margin===null||margin===undefined)parts.push(`contribution: ${cfg.margin_not_set}`);
  else{
   contribution=notMade*margin; status='set';
   const f=PAPER.has(kind)?byVolume:byTime; contributionYear=f?contribution*f:null;
   parts.push(`contribution ${fmt(notMade,3)} MSF not made x $${rateText(margin)}/MSF = ${moneyRaw(contribution)} (not added)`);
  }
  const volumeText=byVolume?`x ${fmt(byVolume,2)} (${group(String(C.annual_msf_per_machine))} MSF a year / ${fmt(gross/1000,3)} MSF gross)`:null;
  const timeText=byTime?`x ${fmt(byTime,2)} (${fmt(C.annual_minutes,0)} scheduled min a year / ${gText(minutes,true)} min)`:null;
  if(usdYear===null)parts.push('per year: not computed (no window volume or minutes)');
  else parts.push(`per year: ${(PAPER.has(kind)?volumeText:timeText)??'None'}`);
  return {msf:msf===null?null:round(msf,3),paper_usd:round(paperUsd,2),machine_minutes:round(machineMinutes,2),machine_time_usd:round(machineUsd,2),usd:round(usd,2),
   usd_per_year:usdYear===null?null:round(usdYear,0),msf_not_made:round(notMade,3),contribution_usd:round(contribution,2),
   contribution_usd_per_year:contributionYear===null?null:round(contributionYear,0),contribution_status:status,usd_basis:parts.join('; '),
   share_of_window_pct:(PAPER.has(kind)?gross:minutes)?round(quantity/(PAPER.has(kind)?gross:minutes)*100,4):null,
   price_terms:{kind,quantity,gross_sqft:gross||null,minutes:minutes||null}};
 }
 // One priced row at `rates`, from its own terms. The get_opportunities trim lever is re-derived first: its quantity is
 // the trim points over the target, which is itself a rate (cost_model.opportunities).
 function repriceRow(tool,row,rates,cfg){
  const t=row&&row.price_terms; if(!t)return row;
  if(tool==='get_opportunities'&&t.kind==='trim'&&t.trim_pct!==undefined){
   const target=rates.values.trim_target_pct, over=Math.max(0,(t.trim_pct||0)-target), sqft=t.gross_sqft?over/100*t.gross_sqft:0;
   const priced=price('trim',sqft,rates,t.gross_sqft,t.minutes,cfg);
   return {...row,lever:`Side trim over the ${gText(target)}% target`,quantity:round(over,4),records:String(row.records||'').replace(/, at or under target$/,'')+(over?'':', at or under target'),
    ...priced,price_terms:{...priced.price_terms,trim_pct:t.trim_pct}};
  }
  return {...row,...price(t.kind,t.quantity,rates,t.gross_sqft,t.minutes,cfg)};
 }
 function rank(rows,key){   // cost_model._rank: by the key, largest first, then by name; a stable sort as Python's
  const name=r=>'gap' in r?r.gap:r.lever;
  rows.sort((a,b)=>((b[key]||0)-(a[key]||0))||(name(a)<name(b)?-1:name(a)>name(b)?1:0));
  rows.forEach((r,i)=>{r.rank=i+1;});
  return rows;
 }
 // cost_model.rank_gaps (the owner, PR #111): a money card's recorded losses ranked by dollars; the gaps apart (planned
 // maintenance, schedule-driven; a power outage, external) after them with no rank, by group (APART) and dollars.
 function rankGaps(rows){
  const name=r=>r.gap, byUsd=(a,b)=>((b.usd||0)-(a.usd||0))||(name(a)<name(b)?-1:name(a)>name(b)?1:0);
  const ranked=rank(rows.filter(r=>!r.apart),'usd'), rest=rows.filter(r=>r.apart).sort((a,b)=>(APART[a.apart]-APART[b.apart])||byUsd(a,b));
  rest.forEach(r=>{r.rank=null;}); rows.splice(0,rows.length,...ranked,...rest); return rows;
 }
 // cost_model.gap_totals: the all-in total, every gap added left to right in row order, the gaps apart included.
 function allIn(rows){
  const usd=rows.map(r=>r.usd).filter(v=>v!==null&&v!==undefined), years=rows.map(r=>r.usd_per_year);
  return {apart_gaps:rows.filter(r=>r.apart).length,all_in_usd:usd.length?round(add(usd),2):0,
   all_in_usd_per_year:rows.length&&!years.some(v=>v===null||v===undefined)?round(add(years),0):null};
 }
 // cost_model.opportunities (issue #101): the levers in the order and with the ranks the lens gives them. ownership: side
 // trim, all of it, first always (every owner asks what trim costs), then the rest by dollars a year; supervisor: the
 // controllable levers ranked, then the others with no rank, by control (schedule-driven, then external; cost_model.APART)
 // and within each by dollars a year; all: every lever.
 const APART={'schedule-driven':0,external:1};
 // Ownership (cost_model.owner_order): after trim, the dry-end group (the tag scrap, PR #111) by the sum of its rows and each
 // other lever by dollars a year; the group holds the rank, its rows beneath it by dollars a year and with no rank.
 function rankLevers(rows,lens){
  const byYear=(a,b)=>((b.usd_per_year||0)-(a.usd_per_year||0))||(a.lever<b.lever?-1:a.lever>b.lever?1:0);
  const apart=r=>r.control in APART?APART[r.control]:Object.keys(APART).length;
  if(lens==='ownership'){
   const [first,...rest]=rows, groups=new Map(); first.rank=1;
   for(const r of rest){ const k=r.group||r.lever; if(!groups.has(k))groups.set(k,[]); groups.get(k).push(r); }
   const size=new Map(); for(const [k,m] of groups){ m.sort(byYear); size.set(k,add(m.map(r=>r.usd_per_year||0))); }
   const keys=[...groups.keys()].sort((a,b)=>(size.get(b)-size.get(a))||(a<b?-1:a>b?1:0)), out=[first];
   keys.forEach((k,i)=>{ for(const r of groups.get(k)){ r.rank=r.group?null:i+2; out.push(r); } });
   rows.splice(0,rows.length,...out); return rows;
  }
  if(lens==='supervisor'){
   const mine=rows.filter(r=>r.control==='controllable').sort(byYear), rest=rows.filter(r=>r.control!=='controllable').sort((a,b)=>(apart(a)-apart(b))||byYear(a,b));
   mine.forEach((r,i)=>{r.rank=i+1;}); rest.forEach(r=>{r.rank=null;}); rows.splice(0,rows.length,...mine,...rest); return rows;
  }
  return rank(rows,'usd_per_year');
 }
 // The ownership lens's dry-end subtotal (cost_model.dry_end_subtotal): its rows' dollars added left to right, its place among the groups.
 function dryEnd(rows){
  const keys=[]; for(const r of rows.slice(1)){ const k=r.group||r.lever; if(!keys.includes(k))keys.push(k); }
  const mine=rows.filter(r=>r.group==='dry_end'), years=mine.map(r=>r.usd_per_year);
  return {dry_end_rank:keys.indexOf('dry_end')+2,dry_end_kinds:mine.length,dry_end_usd:round(add(mine.map(r=>r.usd||0)),2),
   dry_end_usd_per_year:years.some(v=>v===null||v===undefined)?null:round(add(years.map(v=>v||0)),0)};
 }
 // The controllable losses (cost_model.opportunities): the controllable levers in their ranked order, added left to right.
 function controllable(rows){
  const mine=rows.filter(r=>r.control==='controllable'), years=mine.map(r=>r.usd_per_year);
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
 // cost_model.bucket_of / summary: the chips' bucket totals and the arithmetic's sums, from the rows in their ranked order.
 function bucketOf(row){
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
 // cost_model.trim_over_target: the trim points over the target and their paper dollars a year, from the side trim's share.
 function trimOver(t,rates,cfg){
  const target=rates.values.trim_target_pct, over=Math.max(0,(t.trim_pct||0)-target);
  const p=price('trim',t.gross_sqft?over/100*t.gross_sqft:0,rates,t.gross_sqft,t.minutes,cfg);
  return {trim_pct_of_gross:t.trim_pct===null||t.trim_pct===undefined?null:round(t.trim_pct,4),trim_target_pct:target,trim_points_over:round(over,4),
   trim_over_usd:p.usd,trim_over_usd_per_year:p.usd_per_year,trim_over_basis:p.usd_basis,trim_over_terms:{...p.price_terms,trim_pct:t.trim_pct||0}};
 }
 // cost_model.tile: a KPI tile's dollars, the sum of its parts' pricing calls.
 function tile(t,rates,cfg){
  const priced=(t.parts||[]).map(p=>p?price(p.kind,p.quantity,rates,p.gross_sqft,p.minutes,cfg):{...NOT_PRICED});
  const usd=priced.map(r=>r.usd).filter(v=>v!==null&&v!==undefined), years=priced.map(r=>r.usd_per_year);
  return {...t,usd:usd.length?round(add(usd),2):null,usd_per_year:!priced.length||years.some(v=>v===null||v===undefined)?null:round(add(years),0),
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
  const rows=Array.isArray(data.rows)?data.rows:[], byIndex=rows.map(r=>repriceRow(tool,r,rates,cfg)), out={...data,rows:[...byIndex]};
  if(tool==='get_speed_variance')speedVariance(data,byIndex,out,rates,cfg);   // a chart of it has the rows only
  if(MONEY_TOOLS.has(tool)){
   if(tool==='get_opportunities')rankLevers(out.rows,data.lens); else rankGaps(out.rows);
   // the recorded losses' totals, chips and arithmetic leave the gaps apart out (the owner, PR #111); a lever is never apart
   const counted=out.rows.filter(r=>!r.apart);
   Object.assign(out,totals(counted,cfg)); if('buckets' in data)Object.assign(out,summary(counted));
   if('all_in_usd' in data)Object.assign(out,allIn(out.rows));
   if(tool==='get_opportunities'&&'controllable_usd' in data)Object.assign(out,controllable(out.rows));
   if(tool==='get_opportunities'&&'dry_end_usd' in data)Object.assign(out,dryEnd(out.rows));
  }
  if(data.trim_over_terms)Object.assign(out,trimOver(data.trim_over_terms,rates,cfg));
  if(Array.isArray(data.tile_money))out.tile_money=data.tile_money.map(t=>tile(t,rates,cfg));
  if('provenance' in data){ out.rates=rateRows(rates,cfg); out.provenance=provenance(rates,cfg); }
  Object.defineProperty(out,'byIndex',{value:byIndex,enumerable:false});
  return out;
 }
 const priced=data=>!!(data&&(Array.isArray(data.tile_money)&&data.tile_money.length||data.trim_over_terms||Array.isArray(data.rows)&&data.rows.some(r=>r&&r.price_terms)));
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
  if(data.total_contribution_usd!==null&&data.total_contribution_usd!==undefined)return `Contribution ${money(data.total_contribution_usd)} at the set margin, not added to the losses above.`;
  return null;
 }
 const CONTRIBUTION=/^Contribution (?:.+ at the set margin, not added to the losses above\.|.+ for the controllable buckets at the set margin, not added to the machine time\.|.+: without an owner margin no lost contribution is priced, so the dollars are paper and machine time\.)$/;
 // steps: index = query number - 1; each {tool, before, after} for a priced step, null otherwise.
 function repriceSections(sections,steps,rates,cfg){
  const live=steps.filter(Boolean); if(!live.length)return sections;
  const head=REF.exec(sections[0]||''), cited=head&&steps[Number(head[1])-1];
  const money1=cited&&'contribution_status' in cited.after?cited:live.find(s=>'contribution_status' in s.after);   // the result whose contribution the answer states
  const swapped=refocus(sections,steps,rates,cfg); if(swapped)return swapped;
  const out=[];
  for(const line of sections){
   if(line.startsWith('Rates: ')&&line.endsWith(' '+cfg.note)){ out.push(`${provenance(rates,cfg)} ${cfg.note}`); continue; }
   if(line.startsWith('How a dollar is priced, for ')){ out.push(basisLine(line,live)); continue; }
   if(CONTRIBUTION.test(line)){ const again=money1?contributionLine(money1.after,cfg):null; if(again)out.push(again); continue; }
   out.push(repriceLine(line,steps,live));
  }
  return reorder(out,steps);
 }
 function basisLine(line,live){
  for(const s of live)for(const [i,row] of s.before.rows.entries()){
   if(!row||!row.usd_basis)continue;
   // A money tool's formula line is for its first row, the most dollars: at other rates that may be another row.
   for(const key of LABEL_KEYS)if(key in row&&line===`How a dollar is priced, for ${row[key]}: ${row.usd_basis}.`){ const now=MONEY_TOOLS.has(s.tool)&&i===0?s.after.rows[0]:s.after.byIndex[i]; return `How a dollar is priced, for ${now[key]}: ${now.usd_basis}.`; }
  }
  return line;
 }
 function candidates(line,steps,live){
  const m=REF.exec(line), out=[];
  const rowFields=(s,i)=>{ for(const f of [...WINDOW_FIELDS,...YEAR_FIELDS])out.push({old:s.before.rows[i][f],now:s.after.byIndex[i][f],year:YEAR_FIELDS.includes(f)}); };
  const totalFields=s=>{ for(const f of [...TOTAL_WINDOW,...TOTAL_YEAR])if(f in s.before)out.push({old:s.before[f],now:s.after[f],year:TOTAL_YEAR.includes(f),controllable:f.startsWith('controllable_'),subtotal:f.startsWith('dry_end_'),allin:f.startsWith('all_in_')}); };
  const step=m?steps[Number(m[1])-1]:null;
  if(m&&!step)return out;   // the line cites an unpriced query: its dollars, if any, are not this layer's
  if(step&&m[2]==='row'&&step.before.rows[Number(m[3])]){ rowFields(step,Number(m[3])); totalFields(step); }
  else if(step){ totalFields(step); step.before.rows.forEach((r,i)=>{ if(r&&r.price_terms)rowFields(step,i); }); }
  else for(const s of live){ totalFields(s); s.before.rows.forEach((r,i)=>{ if(r&&r.price_terms)rowFields(s,i); }); }
  return out;
 }
 // The trim lever's name and records name the target, a rate: a line citing that row says the new target.
 function renamed(line,steps){
  const m=REF.exec(line), s=m&&m[2]==='row'?steps[Number(m[1])-1]:null; if(!s||s.tool!=='get_opportunities')return line;
  const was=s.before.rows[Number(m[3])], now=s.after.byIndex[Number(m[3])]; if(!was||!now)return line;
  let out=line; for(const key of ['lever','records'])if(was[key]&&now[key]&&was[key]!==now[key])out=out.replace(was[key],now[key]);
  return out;
 }
 // The controllable totals (issue #101) are stated only on the lines that name them, "… in controllable losses" (the
 // supervisor lens) and "The controllable buckets are …" (the speed variance): such a line takes them first, and no
 // other line takes them, so a window whose controllable total reads as its all-lever total (no planned maintenance,
 // side trim at or under the target) re-prices each line to its own figure (review of #111).
 // The ownership lens's dry-end subtotal (PR #111) is the same: only the line naming the tag scrap takes it, first.
 // A money card's all-in total (PR #111) is the figure followed by "all in", and no other figure takes it.
 const CONTROLLABLE=/\bcontrollable (?:losses|buckets)\b/, TAG_SCRAP=/\(tag scrap\)/, ALL_IN=/^ all in\b/;
 function repriceLine(line,steps,live){
  if(!line.includes('$'))return line;
  const names=CONTROLLABLE.test(line), tag=TAG_SCRAP.test(line);
  const all=candidates(line,steps,live).filter(c=>(names||!c.controllable)&&(tag||!c.subtotal));
  const first=c=>names?c.controllable:tag?c.subtotal:false;
  const pool=[...all.filter(first),...all.filter(c=>!first(c))], used=new Set();
  return renamed(line,steps).replace(TOKEN,(token,at,whole)=>{
   const after=whole.slice(at+token.length), yearly=YEARLY.test(after), allin=ALL_IN.test(after);
   const order=[...pool.filter(c=>c.year===yearly),...pool.filter(c=>c.year!==yearly)].filter(c=>!!c.allin===allin);
   const hit=order.find(c=>!used.has(c)&&c.old!==null&&c.old!==undefined&&money(c.old)===token);
   if(!hit)return token;
   used.add(hit); return money(hit.now);
  });
 }
 // All three shifts asked where the money went (shapes.DailyAnswer, intent dollars): the answer is about the shift that
 // lost the most dollars, "on shift 3, the highest of the three shifts". At other rates that can be another shift; the
 // answer is then written again about that one, in the shape's own order: the headline, a line per other shift, the
 // focus shift's ranked gaps, what else the answer said, its notes, and where to look.
 const OTHER=/^- Shift (\d): \*\*\$[\d,]+\*\* of recorded losses · /, FOCUSED=/^- Shift (\d) · \d+\. /, ON=/ on shift (\d)(, the \w+ of the )/;
 // shapes.all_in and the apart blocks of shapes.shape_dollars (the owner, PR #111)
 const ALL_IN_CLAUSE=/ · \$[\d,]+ all in, with the schedule-driven and external downtime/, APART_LABELS={'schedule-driven':'Schedule-driven, shown apart',external:'External, not controllable'};
 const allInText=d=>d.apart_gaps?` · ${money(d.all_in_usd)} all in, with the schedule-driven and external downtime`:'';
 const APART_LINE=/^- Shift \d · (?!\d+\. ).+ · (?:schedule-driven|external), .+ \(query \d+ · row \d+\)$/;
 function refocus(sections,steps,rates,cfg){
  const dollars=steps.map((s,i)=>s&&s.tool==='get_shift_dollars'?{s,q:i+1,shift:s.before.shift}:null).filter(Boolean);
  const head=sections[0]||'', on=ON.exec(head), ref=REF.exec(head);
  if(dollars.length<2||!on||!ref)return null;
  const ranked=dollars.filter(d=>d.s.after.coverage==='complete').map(d=>[d.s.after.total_usd,d.shift,d]).sort((a,b)=>(a[0]-b[0])||(a[1]-b[1]));
  if(!ranked.length)return null;
  const focus=ranked[ranked.length-1][2], was=Number(on[1]);   // max((total, shift)): a tie goes to the later shift, as in Python
  if(focus.shift===was)return null;
  const d=focus.s.after;
  // The headline is the old one about the new shift: its two figures, its shift and its query.
  const top=head.replace(/^\*\*\$[\d,]+\*\*/,`**${money(d.total_usd)}**`).replace(/\$[\d,]+(?= per year)/,money(d.total_usd_per_year))
   .replace(ALL_IN_CLAUSE,'').replace(/ \(query \d+\)$/,`${allInText(d)} (query ${focus.q})`).replace(ON,` on shift ${focus.shift}$2`);
  const others=dollars.filter(x=>x!==focus&&x.s.after.coverage==='complete').sort((a,b)=>a.shift-b.shift)
   .map(x=>`- Shift ${x.shift}: **${money(x.s.after.total_usd)}** of recorded losses · ${money(x.s.after.total_usd_per_year)} per year${allInText(x.s.after)} (query ${x.q})`);
  const yearly=r=>r.usd_per_year!==null&&r.usd_per_year!==undefined?` · ${money(r.usd_per_year)}/yr`:'';
  const gaps=d.rows.map((r,i)=>r.apart?null:`- Shift ${focus.shift} · ${r.rank}. ${r.gap}: **${money(r.usd)}**${yearly(r)} (query ${focus.q} · row ${i})`).filter(Boolean);
  for(const [apart,label] of Object.entries(APART_LABELS)){   // the gaps apart, each group under its label (shapes.shape_dollars)
   const mine=d.rows.map((r,i)=>[r,i]).filter(([r])=>r.apart===apart);
   if(mine.length)gaps.push(label,...mine.map(([r,i])=>`- Shift ${focus.shift} · ${r.gap}: **${money(r.usd)}**${yearly(r)} · ${apart}, ${r.apart_note} (query ${focus.q} · row ${i})`));
  }
  const look=sections.indexOf('Where to look'), body=sections.slice(1,look<0?sections.length:look), looks=look<0?[]:sections.slice(look);
  const rest=body.filter(l=>!OTHER.test(l)&&!FOCUSED.test(l)&&!APART_LINE.test(l)&&!Object.values(APART_LABELS).includes(l)&&!CONTRIBUTION.test(l)&&!l.startsWith('How a dollar is priced, for ')&&!(l.startsWith('Rates: ')&&l.endsWith(' '+cfg.note)));
  const notes=[contributionLine(d,cfg),d.rows.length?`How a dollar is priced, for ${d.rows[0].gap}: ${d.rows[0].usd_basis}.`:null,`${provenance(rates,cfg)} ${cfg.note}`].filter(Boolean);
  return [top,...others,...gaps,...rest,...notes,...looks.map(l=>l.replace(new RegExp(` for shift ${was}”$`),` for shift ${focus.shift}”`))];
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
 return {fixed,fmt,round,group,gText,rateText,money,moneyRaw,short,base,effective,parse,check,format,rateRows,provenance,price,repriceRow,reprice,repriceChart,priced,share,rank,rankLevers,controllable,totals,summary,bucketOf,trimOver,tile,
  repriceSections,contributionLine,PAPER,TIME,MONEY_TOOLS};
})();
if(typeof module!=='undefined'&&module.exports)module.exports=Money;
