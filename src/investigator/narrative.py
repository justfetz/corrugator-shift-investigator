"""Validate narrative structure and substitute metric values from tool evidence."""
import math
import re

KINDS = ('finding', 'hypothesis', 'check', 'limitation')
MAX_FACTS = 6  # per paragraph; the live Haiku writer cites five in a dense hypothesis
FACT_SCHEMA = {'type':'object','properties':{
    'result_id':{'type':'string'}, 'row':{'type':'integer'}, 'field':{'type':'string'}},
    'required':['result_id','row','field'],'additionalProperties':False}
NARRATIVE_SCHEMA = {'type':'object','properties':{'paragraphs':{'type':'array','items':{
    'type':'object','properties':{'kind':{'type':'string','enum':list(KINDS)},
    'text':{'type':'string'},'facts':{'type':'array','items':FACT_SCHEMA}},
    'required':['kind','text','facts'],'additionalProperties':False}}},
    'required':['paragraphs'],'additionalProperties':False}


def evidence_packet(evidence):
    """Bound the writer's preview explicitly; row positions stay stable.

    `coverage` here is the coverage of the rows the writer may cite. A daily tool's coverage
    is its own (excluded records make it partial). A period tool's envelope coverage also
    folds in its comparison window and the weeks it could not find, which would bar every
    finding on an August-versus-July table or an eight-week trend that starts before the
    data; those rows are exact, so the packet reports the current window's coverage (for a
    trend: whether any returned week is short) and discloses what is missing beside it."""
    packet=[]
    for result in evidence:
        data=result['data']
        rows=[{k:v for k,v in row.items() if k!='source_ids'} for row in data.get('rows',[])[:12]]
        entry={'result_id':result['result_id'],'tool':result['tool'],
            'shift':data['shift'],'coverage':data['coverage'],'rows':rows,
            'total_rows':len(data.get('rows',[])), 'preview_rows':len(rows)}
        if isinstance(data.get('window'),dict):
            if 'missing_weeks' in data:
                entry['coverage']='complete' if rows and not any(r.get('partial') for r in rows) else data['coverage']
                entry['missing_weeks']=list(data['missing_weeks'])
            else:
                entry['coverage']=data['window'].get('coverage',data['coverage'])
            previous=data.get('previous_window')
            if isinstance(previous,dict):
                entry['previous_window_coverage']=previous.get('coverage')
            if data.get('day_basis_note'):
                entry['day_basis_note']=data['day_basis_note']
        packet.append(entry)
    return packet


def render_narrative(value, packet):
    """References/values are checked; prose interpretation still needs review."""
    if not isinstance(value,dict) or set(value)!={'paragraphs'}:
        raise ValueError('Invalid narrative object')
    paragraphs=value['paragraphs']
    if not isinstance(paragraphs,list) or not 1<=len(paragraphs)<=6:
        raise ValueError('Narrative needs one to six paragraphs')
    sources={r['result_id']:r for r in packet}
    rendered=[]
    for p in paragraphs:
        if not isinstance(p,dict) or set(p)!={'kind','text','facts'} or p['kind'] not in KINDS:
            raise ValueError('Invalid narrative paragraph')
        text=p['text']; facts=p['facts']
        if (not isinstance(text,str) or not 1<=len(text)<=700 or
                not isinstance(facts,list) or len(facts)>MAX_FACTS):
            raise ValueError('Narrative size limit')
        bare=re.sub(r'\{\{\d+\}\}', '', text)
        # Numerical statements must use application-substituted evidence fields.
        if any(c.isdigit() or ord(c)<32 for c in bare) or any(c in bare for c in '<>{}') or re.search(r'https?://',bare):
            raise ValueError('Use fact placeholders for values; plain prose only')
        tokens=re.findall(r'\{\{(\d+)\}\}',text)
        if any(x!=str(int(x)) for x in tokens):
            raise ValueError('Fact placeholders do not match references')
        placed={int(x) for x in tokens}
        # Placeholders must name a prefix of the fact list; a placeholder with no fact, or a
        # gap, is an error. When some facts are placed, the ones listed after the last placed
        # fact are dropped: the prose claims nothing from them. When NONE are placed the prose
        # carries no numbers at all (the digit check above), so every listed fact stays as a
        # citation: it is the pointer to the evidence the paragraph talks about, and the live
        # writer regularly describes a ranking or a category in words and cites the rows.
        if placed!=set(range(len(placed))) or len(placed)>len(facts):
            raise ValueError('Fact placeholders do not match references')
        if placed:
            facts=facts[:len(placed)]
        if p['kind']!='limitation' and not facts:
            raise ValueError('Narrative requires cited evidence')
        citations=[]
        for i,fact in enumerate(facts):
            if not isinstance(fact,dict) or set(fact)!={'result_id','row','field'}:
                raise ValueError('Invalid fact reference')
            rid,index,field=fact['result_id'],fact['row'],fact['field']
            if not isinstance(rid,str) or rid not in sources or type(index) is not int or not isinstance(field,str):
                raise ValueError('Unknown narrative source')
            source=sources[rid]
            if not 0<=index<len(source['rows']):
                raise ValueError(f'Narrative cites row {index} of {rid}; only the {len(source["rows"])} preview rows may be cited')
            if field in source['rows'][index]:
                val=source['rows'][index][field]
            elif field=='shift' and source.get('shift') is not None:
                val=source['shift']   # the shift is result metadata, not a row field; the writer asks for it by name
            else:
                raise ValueError('Unknown narrative field')
            if source['coverage']!='complete' and p['kind']!='limitation':
                raise ValueError('Incomplete evidence is not a performance finding')
            if isinstance(val,(bool,list,dict)) or isinstance(val,float) and not math.isfinite(val):
                raise ValueError('Unavailable narrative value')
            # A null field (an order's requested sheets, a withheld delta) renders as the word, never
            # as a number: the paragraph survives and says plainly that the value is not recorded.
            formatted='unavailable' if val is None else f'{val:,.2f}' if isinstance(val,float) else str(val)
            if len(formatted)>200:
                raise ValueError('Narrative field too long')
            text=text.replace('{{'+str(i)+'}}',formatted)
            scope = f"Shift {source['shift']}" if source['shift'] is not None else 'All shifts'
            citations.append(f"{scope}, {field}, {rid}, row {index}")
        rendered.append({'kind':p['kind'],'text':text,'citations':citations})
    return rendered
