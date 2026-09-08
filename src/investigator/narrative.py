"""Validate narrative structure and substitute metric values from tool evidence."""
import math
import re

KINDS = ('finding', 'hypothesis', 'check', 'limitation')
FACT_SCHEMA = {'type':'object','properties':{
    'result_id':{'type':'string'}, 'row':{'type':'integer'}, 'field':{'type':'string'}},
    'required':['result_id','row','field'],'additionalProperties':False}
NARRATIVE_SCHEMA = {'type':'object','properties':{'paragraphs':{'type':'array','items':{
    'type':'object','properties':{'kind':{'type':'string','enum':list(KINDS)},
    'text':{'type':'string'},'facts':{'type':'array','items':FACT_SCHEMA}},
    'required':['kind','text','facts'],'additionalProperties':False}}},
    'required':['paragraphs'],'additionalProperties':False}


def evidence_packet(evidence):
    """Bound the writer's preview explicitly; row positions stay stable."""
    packet=[]
    for result in evidence:
        data=result['data']
        rows=[{k:v for k,v in row.items() if k!='source_ids'} for row in data.get('rows',[])[:12]]
        packet.append({'result_id':result['result_id'],'tool':result['tool'],
            'shift':data['shift'],'coverage':data['coverage'],'rows':rows,
            'total_rows':len(data.get('rows',[])), 'preview_rows':len(rows)})
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
                not isinstance(facts,list) or len(facts)>4):
            raise ValueError('Narrative size limit')
        bare=re.sub(r'\{\{\d+\}\}', '', text)
        # Numerical statements must use application-substituted evidence fields.
        if any(c.isdigit() or ord(c)<32 for c in bare) or any(c in bare for c in '<>{}') or re.search(r'https?://',bare):
            raise ValueError('Use fact placeholders for values; plain prose only')
        if p['kind']!='limitation' and not facts:
            raise ValueError('Narrative requires cited evidence')
        tokens=re.findall(r'\{\{(\d+)\}\}',text)
        if any(x!=str(int(x)) for x in tokens) or {int(x) for x in tokens} != set(range(len(facts))):
            raise ValueError('Fact placeholders do not match references')
        citations=[]
        for i,fact in enumerate(facts):
            if not isinstance(fact,dict) or set(fact)!={'result_id','row','field'}:
                raise ValueError('Invalid fact reference')
            rid,index,field=fact['result_id'],fact['row'],fact['field']
            if not isinstance(rid,str) or rid not in sources or type(index) is not int or not isinstance(field,str):
                raise ValueError('Unknown narrative source')
            source=sources[rid]
            if not 0<=index<len(source['rows']) or field not in source['rows'][index]:
                raise ValueError('Unknown narrative field')
            if source['coverage']!='complete' and p['kind']!='limitation':
                raise ValueError('Incomplete evidence is not a performance finding')
            val=source['rows'][index][field]
            if val is None or isinstance(val,(bool,list,dict)) or isinstance(val,float) and not math.isfinite(val):
                raise ValueError('Unavailable narrative value')
            formatted=f'{val:,.2f}' if isinstance(val,float) else str(val)
            if len(formatted)>200:
                raise ValueError('Narrative field too long')
            text=text.replace('{{'+str(i)+'}}',formatted)
            citations.append(f"Shift {source['shift']}, {field}, {rid}, row {index}")
        rendered.append({'kind':p['kind'],'text':text,'citations':citations})
    return rendered
