"""Validate narrative structure and substitute metric values from tool evidence."""
import math
import re

KINDS = ('finding', 'hypothesis', 'check', 'limitation')
# A number written in words is still a number the writer did not take from a cited row:
# tens and larger anywhere, or a small count glued to a unit ("three percent", "two stops").
NUMBER_WORDS = re.compile(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|dozen)\b"
                          r"|\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|half|quarter)"
                          r"[- ]?(percent|per cent|points?|minutes?|hours?|seconds?|feet|foot|ft|fpm|msf|sq(uare)? ?f(ee)?t|stops?|setups?|runs?|sheets?|cuts?|orders?|weeks?|days?|shifts?)\b", re.I)
SMALL_NUMBER_WORDS = frozenset('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen half quarter'.split())
# A value glued to a letter, a digit or another value reads as a number no tool returned
# ("Shift 2{{0}}", "{{0}}{{1}}", "{{0}}.{{1}}"): a placeholder stands alone.
GLUED_FACT = re.compile(r'[A-Za-z0-9}]\{\{|\}\}[A-Za-z0-9{]|\}\}[.,]\{\{')
MAX_FACTS = 8  # per paragraph; a four-reason ranking cites a (category, minutes) pair per reason
FACT_SCHEMA = {'type':'object','properties':{
    'result_id':{'type':'string'}, 'row':{'type':'integer'}, 'field':{'type':'string'}},
    'required':['result_id','row','field'],'additionalProperties':False}
NARRATIVE_SCHEMA = {'type':'object','properties':{'paragraphs':{'type':'array','items':{
    'type':'object','properties':{'kind':{'type':'string','enum':list(KINDS)},
    'text':{'type':'string'},'facts':{'type':'array','items':FACT_SCHEMA}},
    'required':['kind','text','facts'],'additionalProperties':False}}},
    'required':['paragraphs'],'additionalProperties':False}


PACKET_DROP=frozenset({'source_ids','usd_basis','msf','machine_minutes','msf_not_made','contribution_usd_per_year','price_terms','share_of_window_pct'})


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
        # The writer may cite the dollars (usd, usd_per_year, paper_usd, machine_time_usd, contribution_usd);
        # the formula text and the intermediate quantities stay on the card, inside the byte caps.
        rows=[{k:v for k,v in row.items() if k not in PACKET_DROP} for row in data.get('rows',[])[:12]]
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
    rendered,omitted=[],[]
    for p in paragraphs:
        try:
            rendered.append(_render_paragraph(p,sources,packet))
        except ValueError as exc:
            # One paragraph's bookkeeping slip (a placeholder with no fact, a field that is not
            # in the preview) omits that paragraph, not the whole interpretation; the reader is
            # told, and nothing from the omitted text is shown.
            omitted.append(str(exc))
    if not rendered:
        raise _rejected(omitted, 'Invalid narrative paragraph')
    return rendered + omission_notes(omitted)


def omission_notes(omitted):
    """One limitation line per distinct reason a paragraph was left out. A comparison withdrawn by the
    ordering check says so in its own words and carries how many paragraphs it took out (`withdrawn`,
    which the agent moves into the answer's usage)."""
    notes = []
    for reason in dict.fromkeys(omitted):
        if reason == COMPARISON_WITHDRAWN:
            notes.append({'kind': 'limitation', 'text': COMPARISON_WITHDRAWN + '.', 'citations': [], 'withdrawn': omitted.count(reason)})
        else:
            notes.append({'kind': 'limitation', 'text': f'One paragraph of the interpretation was omitted because it failed validation ({reason}).', 'citations': []})
    return notes


def _rejected(omitted, default):
    """The error for a narrative with no valid paragraph; it still counts the withdrawn comparisons."""
    error = ValueError(omitted[0] if omitted else default)
    error.withdrawn = omitted.count(COMPARISON_WITHDRAWN)
    return error


def _render_paragraph(p,sources,packet):
    if True:
        if not isinstance(p,dict) or set(p)!={'kind','text','facts'} or p['kind'] not in KINDS:
            raise ValueError('Invalid narrative paragraph')
        text=p['text']; facts=p['facts']
        if (not isinstance(text,str) or not 1<=len(text)<=700 or
                not isinstance(facts,list) or len(facts)>MAX_FACTS):
            raise ValueError('Narrative size limit')
        if GLUED_FACT.search(text):
            raise ValueError('A fact placeholder must stand alone, not touch a letter, a digit or another placeholder')
        bare=re.sub(r'\{\{\d+\}\}', '', text)
        # "Shift 2" is the selected scope, a known truth, when it names a shift the evidence carries.
        known_shifts={str(r['shift']) for r in packet if r.get('shift') is not None}
        bare=re.sub(r'\b[Ss]hift\s+([123])\b', lambda m: 'shift' if m.group(1) in known_shifts else m.group(0), bare)
        # Numerical statements must use application-substituted evidence fields.
        if any(c.isdigit() or ord(c)<32 for c in bare) or any(c in bare for c in '<>{}') or re.search(r'https?://',bare) or NUMBER_WORDS.search(bare):
            raise ValueError('Use fact placeholders for values; plain prose only (numbers written as words count)')
        tokens=re.findall(r'\{\{(\d+)\}\}',text)
        if any(x!=str(int(x)) for x in tokens):
            raise ValueError('Fact placeholders do not match references')
        placed={int(x) for x in tokens}
        # Every placeholder must name a listed fact. A fact that is listed but not placed stays
        # as a citation: the prose carries no number from it (the digit and number-word checks
        # above), and the live writer regularly names a category in words while placing only
        # its value, or describes a ranking and cites the rows.
        if any(x>=len(facts) for x in placed):
            raise ValueError('Fact placeholders do not match references')
        if p['kind']!='limitation' and not facts:
            raise ValueError('Narrative requires cited evidence')
        citations,cited=[],[]
        template=text
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
            cited.append((source,index,field,val))
        if ordering_contradicted(template,cited):
            raise ValueError(COMPARISON_WITHDRAWN)
        return {'kind':p['kind'],'text':text,'citations':citations}


# ---------------------------------------------------------------- ordering claims (issue #79)
# The checks above prove that every digit came from a cited row; they cannot see a false claim
# about those digits. "Shift 2 ran fastest at 562.75 ft/min, against 608.92 ft/min on Shift 1"
# passed them. When a paragraph orders values it cites, the order its words assert is checked
# against those values:
#   - a superlative (fastest, highest, most, least, best, ...) names one subject, which must be the
#     extreme of the same-quantity values the paragraph cites;
#   - a comparison (more than, below, ahead of, better than, ...) between the two placed values
#     on either side of it;
#   - a direction (rose, fell, up from, a decrease, ...) against the cited current and previous
#     values of the same row, or the sign of a cited delta.
# Values compare only within one quantity: the same tool and field (and the same metric row of a
# KPI table; for a generic `value`, the same result). A claim whose subject these rules cannot pin
# down, a negated claim and a question are left alone: a paragraph is withdrawn only when its own
# figures contradict it.
COMPARISON_WITHDRAWN = 'A comparison was withdrawn because it contradicted its own figures'
_HIGH = frozenset('fastest quickest highest largest biggest greatest longest most maximum'.split())
_LOW = frozenset('slowest lowest smallest shortest least fewest minimum'.split())
_JUDGED = frozenset(('best', 'worst'))
_SPEED_ONLY = frozenset('fastest quickest slowest faster slower'.split())   # said of a speed, not of minutes
_UP = frozenset('more higher greater larger bigger longer faster above exceeds exceeded exceed exceeding ahead outpaced outran'.split())
_DOWN = frozenset('less fewer lower smaller shorter slower below behind trailed trailing'.split())
SUPERLATIVE = re.compile(r'\b(' + '|'.join(sorted(_HIGH | _LOW | _JUDGED)) + r')\b', re.I)
COMPARATIVE = re.compile(r'\b(?:(?P<adj>more|higher|greater|larger|bigger|longer|faster|less|fewer|lower|smaller|shorter|slower|better|worse)'
                         r'(?P<noun>(?:\s+[A-Za-z-]+){0,3}?)\s+than'
                         r'|(?P<prep>above|below|behind|ahead)(?:\s+of)?|(?P<verb>exceed(?:s|ed|ing)?|outpaced|outran|trailed|trailing))\b', re.I)
RISE = re.compile(r'\b(?:rose|risen|rises|rising|rise|increased|increases|increasing|increase|grew|grown|climbed|climbing|jumped'
                  r'|(?<!made )(?<!make )(?<!makes )up(?=\s+(?:from|by|on)\b|\s+\{\{)|(?:went|ticked|edged|moved)\s+up)\b', re.I)
FALL = re.compile(r'\b(?:fell|fallen|falls|falling|dropped|dropping|drops|drop|declined|declines|declining|decline|decreased|decreases'
                  r'|decreasing|decrease|down(?=\s+(?:from|by|on)\b|\s+\{\{)|(?:went|ticked|edged|moved)\s+down)\b', re.I)
# "at most", "the second-largest", "most of", "most likely", "the largest unplanned reason": not a claim about these values.
_NOT_BEFORE = re.compile(r'(?:\bat|\bsecond|\bthird|\bfourth|\bfifth|\bnext)[\s-]+$', re.I)
_NOT_AFTER = re.compile(r'\s*-?\s*(?:\w+ly\b|likely|important|relevant|useful|notable|consistent|reliable|recent|significant|striking|telling'
                        r'|informative|urgent|obvious|clear|actionable|visible|plausible|possible|probable|common|frequent|unplanned'
                        r'|unscheduled|controllable|other|remaining|single|individual|fifth|tenth|quarter|half|decile|quartile|percentile)\b', re.I)
# "the largest reason after Planned maintenance": the set is narrowed by words the check cannot follow
_QUALIFIED = re.compile(r'\b(?:after|besides|excluding|except|apart\s+from|other\s+than|outside)\b', re.I)
_NEGATION = re.compile(r"\b(?:not|no|never|nor|neither|without|hardly)\b|n't\b", re.I)
_CLAUSE_BREAK = re.compile(r'[,:;()–—]|\s-\s|\b(?:and|but|while|whereas|although|though|against|versus|vs|compared|than|ahead|behind|so|which)\b', re.I)
# the subject named right after a superlative: "the fastest was Shift 1", "highest speed came on Shift 3"
_SUBJECT_GAP = re.compile(r'(?:\s+[A-Za-z/-]+){0,3}\s*(?::|\s(?:was|is|were|are|came|went|belonged|occurred|on|in|for|from|to|with|by))'
                          r'\s+(?:(?:on|in|to|from|with|by|at)\s+)?(?:the\s+)?', re.I)
_PLACE = re.compile(r'\{\{(\d+)\}\}')
_MANY = object()   # a name that fits several cited values decides nothing
# a quantity named right after a superlative ("the most stops") and the field names it can mean
_NOUN_FIELDS = {'speed': ('fpm', 'speed'), 'pace': ('fpm', 'speed'), 'downtime': ('down', 'minutes'), 'minutes': ('minutes', 'down'),
                'time': ('down', 'minutes', 'elapsed'),
                'stops': ('stop',), 'stoppages': ('stop',), 'waste': ('sqft', 'pct', 'reject', 'waste', 'trim', 'shear', 'dry_end'),
                'rejects': ('reject', 'sqft', 'dry_end', 'pct'), 'trim': ('trim', 'sqft', 'pct'), 'setups': ('setup',),
                'volume': ('msf', 'volume', 'lineal', 'sqft'), 'output': ('msf', 'volume', 'lineal', 'sqft')}
_LOWER_IS_BETTER = ('down', 'stop', 'trim', 'shear', 'dry_end', 'warp', 'bond', 'misalign', 'moisture', 'other_pct', 'changeover', 'admin', 'reject', 'waste', 'bale',
                    'unexplained', 'maintenance', 'operator', 'overrun', 'over_', 'under')
_HIGHER_IS_BETTER = ('fpm', 'speed', 'msf', 'lineal', 'good', 'volume', 'throughput', 'of_target', 'inside_band')
_LABEL_SKIP = frozenset(('unit', 'better', 'change', 'notes', 'start', 'end', 'production_day', 'week_start', 'week_end', 'record_type'))
_WEEKDAYS = {'mon': 'monday', 'tue': 'tuesday', 'wed': 'wednesday', 'thu': 'thursday', 'fri': 'friday'}


def _quantity(entry, row, field):
    """Values with the same key measure the same thing in the same units."""
    key = (entry.get('tool'), field, row.get('metric'))
    return key + ((entry['result_id'],) if field == 'value' else ())   # a trend's or a ranking's metric is the result's own


def _better(tool, row, field):
    """'higher' or 'lower' when the field's better direction is plain, else None (no best/worse claim is checked)."""
    if field in ('current', 'previous', 'delta') and row.get('better') in ('higher', 'lower'):
        return row['better']
    if any(k in field for k in ('gap', 'delta', 'share', 'cumulative', 'rank', 'target_fpm')):
        return None
    if tool in ('get_waste_breakdown', 'get_quality_breakdown') and ('sqft' in field or 'pct' in field):
        return 'lower'
    if any(k in field for k in _LOWER_IS_BETTER):
        return 'lower'
    return 'higher' if any(k in field for k in _HIGHER_IS_BETTER) else None


def _labels(entry, row):
    """What the prose may call a cited row: its shift and its words (a reason, a place, a weekday)."""
    out = set()
    shift = row['shift'] if type(row.get('shift')) is int else entry.get('shift')
    if shift in (1, 2, 3):
        out.add(f'shift {shift}')
    for key, value in row.items():
        # a band ("on target", "flag") or a lever's control mark (issue #101) grades a value; it does not name the row
        if key in _LABEL_SKIP or key.endswith(('band', 'band_text', 'control', 'control_note')) or not isinstance(value, str):
            continue
        value = ' '.join(value.split()).lower()
        if 2 <= len(value) <= 60 and re.search('[a-z]', value) and not re.search(r'\d', value):   # prose cannot hold a digit
            out.add(value)
            if value in _WEEKDAYS:
                out.add(_WEEKDAYS[value])
    return out


def _speed(field):
    """A speed in ft/min; a percent of target is not what "fastest" is said of."""
    return ('fpm' in field or 'speed' in field) and 'gap' not in field and 'pct' not in field


def _partner(field):
    if field in ('current', 'previous'):
        return 'previous' if field == 'current' else 'current'
    return field[len('previous_'):] if field.startswith('previous_') else 'previous_' + field


def ordering_contradicted(text, cited):
    """True when an ordering the paragraph asserts contradicts the values it cites.

    `text` is the paragraph with {{i}} placeholders; cited[i] is (packet entry, row, field, value)
    of fact i, placed or not."""
    facts, first = [], {}
    for i, (entry, row, field, value) in enumerate(cited):
        record = entry['rows'][row]
        number = (round(float(value), 2) if isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value) and field != 'shift' else None)
        ident = (entry['result_id'], row, field)
        labels = _labels(entry, record)
        same = sum(1 for r in entry['rows'] if r.get('metric') == record.get('metric')
                   and isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool))
        facts.append({'canon': first.setdefault(ident, i), 'row': (entry['result_id'], row), 'result': entry['result_id'],
                      'field': field, 'value': value, 'number': number,
                      'key': _quantity(entry, record, field) if number is not None else None, 'labels': labels,
                      'better': _better(entry.get('tool'), record, field),
                      # a row of a table that ranks rows (a reason, a day, a week): its peers are the rows of its own
                      # result, not the same reason in another shift's table; a one-row result (a shift's KPIs) is
                      # itself the entity, and its peers are the other one-row results
                      'ranked': same > 1 or (record.get('metric') is None and any(not l.startswith('shift ') for l in labels))})
    unique = [i for i, f in enumerate(facts) if f['canon'] == i]
    places = [(m.start(), m.end(), facts[int(m.group(1))]['canon']) for m in _PLACE.finditer(text) if int(m.group(1)) < len(facts)]
    groups = {}
    for i in unique:
        if facts[i]['key'] is not None:
            groups.setdefault(facts[i]['key'], []).append(i)
    mentions = _mentions(text, facts, places)
    for s0, s1 in ((m.start(), m.end()) for m in re.finditer(r'[^.!?;]+[.!?;]?', text)):
        if text[s0:s1].rstrip().endswith('?'):
            continue
        here = [x for x in places if s0 <= x[0] < s1]
        for m in SUPERLATIVE.finditer(text, s0, s1):
            if _superlative_contradicted(text, m, s0, s1, facts, groups, here, mentions):
                return True
        for m in COMPARATIVE.finditer(text, s0, s1):
            if _comparison_contradicted(text, m, s0, s1, facts, groups, here, mentions):
                return True
        for pattern, rise in ((RISE, True), (FALL, False)):
            for m in pattern.finditer(text, s0, s1):
                if _direction_contradicted(text, m, s0, rise, facts, unique, here):
                    return True
    return False


def _mentions(text, facts, places):
    """Where the prose names a cited row: "Shift 2", a reason or place in words, or a placeholder of a
    row's words ({{0}} = "Order change"). Each is (start, end, ('label', name) or ('row', row id))."""
    found = [(m.start(), m.end(), ('label', f'shift {m.group(1)}')) for m in re.finditer(r'\bshift\s+([123])\b', text, re.I)]
    for label in {label for f in facts for label in f['labels'] if not label.startswith('shift ')}:
        found += [(m.start(), m.end(), ('label', label)) for m in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(label) + r'(?![A-Za-z0-9])', text, re.I)]
    for start, end, i in places:
        if facts[i]['field'] == 'shift' and facts[i]['value'] in (1, 2, 3):
            found.append((start, end, ('label', f'shift {facts[i]["value"]}')))
        elif isinstance(facts[i]['value'], str):
            found.append((start, end, ('row', facts[i]['row'])))
    out = []
    for mention in sorted(found, key=lambda m: (m[0], m[0] - m[1])):   # the longest name at a position wins
        if not out or mention[0] >= out[-1][1]:
            out.append(mention)
    return out


def _denotes(mention, group, facts):
    kind, key = mention[2]
    return [i for i in group if (facts[i]['row'] == key if kind == 'row' else key in facts[i]['labels'])]


def _negated(text, start, s0):
    """A negation in the 30 characters before the word, within its own clause: "did not meet target
    but ran fastest" keeps "fastest" checked, because the "not" belongs to the clause before "but"."""
    window = text[max(s0, start - 30):start]
    cut = 0
    for m in _CLAUSE_BREAK.finditer(window):
        cut = m.end()
    return bool(_NEGATION.search(window[cut:]))


def _clause(text, start, end, s0, s1):
    c0 = s0
    for m in _CLAUSE_BREAK.finditer(text, s0, start):
        c0 = m.end()
    m = _CLAUSE_BREAK.search(text, end, s1)
    return c0, (m.start() if m else s1)


def _superlative_contradicted(text, m, s0, s1, facts, groups, here, mentions):
    word = m.group(1).lower()
    c0, c1 = _clause(text, m.start(), m.end(), s0, s1)
    if (_NOT_BEFORE.search(text[max(s0, m.start() - 8):m.start()]) or _NOT_AFTER.match(text, m.end()) or _negated(text, m.start(), s0)
            or word in ('most', 'least') and re.match(r'\s+of\b', text[m.end():]) or _QUALIFIED.search(text, m.end(), c1)):
        return False
    noun = re.match(r'\s+(?:the\s+)?([A-Za-z-]+)', text[m.end():s1])
    noun = [noun.group(1)] if noun else []

    def fitting(i):
        return facts[i]['key'] is not None and _fits(word, facts[i]) and _named(noun, facts[i]['key'])
    # The value placed in the word's own clause says which quantity it is about ("fastest at {{0}} ft/min");
    # only a clause without one looks to the rest of the sentence, and then only to a single quantity.
    after = [i for st, _, i in here if m.end() <= st < c1 and fitting(i)]
    before = [i for st, en, i in here if c0 <= st and en <= m.start() and fitting(i)]
    allowed = {key for key, group in groups.items() if len(group) >= 2 and fitting(group[0])}
    sentence = list(dict.fromkeys(facts[i]['key'] for _, _, i in here if facts[i]['key'] in allowed))
    if after or before:
        key = facts[(after or before[::-1])[0]]['key']
    elif len(sentence) == 1:
        key = sentence[0]
    elif not sentence and len(allowed) == 1 and (_named(noun, None) is False or word in _SPEED_ONLY):
        key = next(iter(allowed))
    else:
        return False        # no value of one quantity is tied to the word: nothing to check
    group = groups.get(key, [])
    subject = _subject(text, m, s0, s1, c0, c1, group, facts, here, mentions) if len(group) >= 2 else None
    if subject is None:
        return False
    own_table = facts[subject]['ranked']
    peers = [i for i in group if (facts[i]['result'] == facts[subject]['result'] if own_table else not facts[i]['ranked'])]
    if len(peers) < 2:
        return False        # nothing it is ranked against was cited
    high = word in _HIGH or (word in _JUDGED and (word == 'best') == (facts[group[0]]['better'] == 'higher'))
    values, own = [facts[i]['number'] for i in peers], facts[subject]['number']
    return max(values) > own if high else min(values) < own


def _named(words, key):
    """Whether quantity words next to an ordering ("the most stops", "less time to stops than") allow the
    quantity `key`. With key None: False when the words name a quantity at all (a known noun), else None."""
    known = [_NOUN_FIELDS[w.lower()] for w in words if w.lower() in _NOUN_FIELDS]
    if key is None:
        return False if known else None
    return not known or key[1] == 'value' or any(n in key[1] for fragments in known for n in fragments)


def _fits(word, fact):
    if word in _SPEED_ONLY:
        return _speed(fact['field'])
    if word in _JUDGED or word in ('better', 'worse'):
        return fact['better'] is not None
    return True


def _subject(text, m, s0, s1, c0, c1, group, facts, here, mentions):
    """The cited value a superlative is said of, or None when the plain rules cannot tell. A name that
    fits several of the values (the selected shift, said of every row) is passed over; a name that fits
    none of them is a subject outside this comparison, and the claim is left alone."""
    def decide(mention):
        hit = _denotes(mention, group, facts)
        return hit[0] if len(hit) == 1 else None if not hit else _MANY
    # 1. named right after the word: "the fastest was Shift 1", "the highest speed came on Shift 3"
    stop = re.search(r'[,;()]', text[m.end():s1])
    limit = m.end() + stop.start() if stop else s1
    for mention in mentions:
        if m.end() <= mention[0] < limit and _SUBJECT_GAP.fullmatch(text, m.end(), mention[0]):
            found = decide(mention)
            if found is not _MANY:
                return found
            break
    # 2. named before it in its clause ("Shift 2 ran fastest"): the nearest name that picks one value
    for mention in reversed([x for x in mentions if c0 <= x[0] and x[1] <= m.start()]):
        found = decide(mention)
        if found is not _MANY:
            return found
    # 3. the value placed in its clause ("fastest at {{0}}", "{{0}}, the highest")
    for st, _, i in [x for x in here if m.end() <= x[0] < c1] + list(reversed([x for x in here if c0 <= x[0] and x[1] <= m.start()])):
        if i in group:
            return i
    # 4. named earlier in the sentence ("Shift 1 posted {{0}}, the fastest of the three")
    for mention in reversed([x for x in mentions if s0 <= x[0] and x[1] <= c0]):
        found = decide(mention)
        if found is not _MANY:
            return found
    # 5. the nearest value placed before it in the sentence
    for st, _, i in reversed([x for x in here if x[1] <= m.start()]):
        if i in group:
            return i
    return None


def _comparison_contradicted(text, m, s0, s1, facts, groups, here, mentions):
    """"A <comparison> B": between the placed values on either side, B close after the word; failing
    that, between the values of the two names on either side ("Shift 2 ran faster than Shift 1, ...")."""
    word = (m.group('adj') or m.group('prep') or m.group('verb')).lower()
    noun = (m.group('noun') or '').split()
    if _negated(text, m.start(), s0) or (noun and _NOT_AFTER.match(' ' + noun[0])):
        return False        # "no more than", "more likely than"

    def fitting(i):
        return facts[i]['key'] is not None and _fits(word, facts[i]) and _named(noun, facts[i]['key'])
    pair = _placed_pair(text, m, facts, groups, here, mentions, fitting) or _named_pair(text, m, s0, s1, facts, groups, here, mentions, fitting)
    if pair is None:
        return False
    a, b = pair
    if word in ('better', 'worse'):
        up = (word == 'better') == (facts[b]['better'] == 'higher')
    else:
        up = word in _UP or word.startswith('exceed')
    va, vb = facts[a]['number'], facts[b]['number']
    return va <= vb if up else va >= vb      # strict words: equal values contradict "more than" and "below" alike


def _placed_pair(text, m, facts, groups, here, mentions, fitting):
    after = [x for x in here if x[0] >= m.end()]
    if not after:
        return None
    gap = text[m.end():after[0][0]]
    if (re.search(r'[,;:()]', gap) or len(re.findall(r"[A-Za-z']+", gap)) > 4
            or re.search(r'\b(?:was|were|is|are|had|has|ran|recorded|logged|lost)\b', gap, re.I)):
        return None         # the next value is not the object of this comparison ("below target, while Shift 1 ran ...")
    b = after[0][2]
    if not fitting(b):
        return None
    group = groups[facts[b]['key']]
    before = [x for x in here if x[1] <= m.start() and x[2] in group and x[2] != b]
    if not before:
        return None
    a_end, a = before[-1][1], before[-1][2]
    between = [_denotes(x, group, facts) for x in mentions if a_end <= x[0] and x[1] <= m.start()]
    if any(len(hit) == 1 and hit != [a] for hit in between):
        return None         # another subject is named between the first value and the comparison
    return a, b


def _named_pair(text, m, s0, s1, facts, groups, here, mentions, fitting):
    """The subject named last before the word in its clause and the object named right after it, each
    picking exactly one cited value of one quantity (preferring a quantity placed in the sentence)."""
    c0, _ = _clause(text, m.start(), m.end(), s0, s1)
    subjects = [x for x in mentions if c0 <= x[0] and x[1] <= m.start()]
    target = next((x for x in mentions if m.end() <= x[0] < s1), None)
    if not subjects or target is None or not re.fullmatch(r'\s*(?:(?:on|for|in|at|by)\s+)?(?:the\s+)?', text[m.end():target[0]]):
        return None
    found = []
    for key, group in groups.items():
        if len(group) < 2 or not fitting(group[0]):
            continue
        a, b = _denotes(subjects[-1], group, facts), _denotes(target, group, facts)
        if len(a) == 1 and len(b) == 1 and a != b:
            found.append((key, a[0], b[0]))
    if len(found) > 1:
        placed = {facts[i]['key'] for _, _, i in here}
        found = [f for f in found if f[0] in placed]
    return found[0][1:] if len(found) == 1 else None


def _direction_contradicted(text, m, s0, rise, facts, unique, here):
    """"rose", "fell by", "up from": the first value placed after the word (else the last before it) must be
    a delta of the right sign, or one side of a cited current / previous pair that moved that way."""
    if _negated(text, m.start(), s0):
        return False
    after = [x for x in here if x[0] >= m.end()]
    before = [x for x in here if x[1] <= m.start()]
    if after:
        gap, words = text[m.end():after[0][0]], 4          # "rose by {{0}}", "fell sharply from {{2}} to {{3}}"
    elif before:
        gap, words = text[before[-1][1]:m.start()], 6      # "moved by {{2}} points of gross, which is a decrease"
    else:
        return False
    if (re.search(r'[;:()]' if not after else r'[,;:()]', gap) or len(re.findall(r"[A-Za-z']+", gap)) > words
            or re.search(r'\b(?:and|but|while|whereas)\b', gap, re.I)):
        return False        # that value is not what moved
    fact = facts[(after[0] if after else before[-1])[2]]
    if fact['number'] is None:
        return False
    field = fact['field']
    if field == 'delta' or field.startswith('delta_') or field.endswith('_delta'):
        return fact['number'] <= 0 if rise else fact['number'] >= 0      # a zero delta neither rose nor fell
    if m.group(0).lower() in ('up', 'down') and text[m.end():].lstrip().startswith('{{'):
        return False        # "up {{x}}" is a change only when x is a delta; "was down {{x}} minutes" is stopped time
    partner = next((j for j in unique if facts[j]['row'] == fact['row'] and facts[j]['field'] == _partner(field)
                    and facts[j]['number'] is not None), None)
    if partner is None:
        return False
    previous = field == 'previous' or field.startswith('previous_')
    current, earlier = (facts[partner], fact) if previous else (fact, facts[partner])
    return current['number'] <= earlier['number'] if rise else current['number'] >= earlier['number']


# ---------------------------------------------------------------- streamed narrative (issue #23)
# The analyst writes the interpretation as plain lines, one paragraph per line:
#
#     finding: Shift 2 ran at {{r1.0.observed_shift_fpm}} ft/min ...
#
# A placeholder names an evidence result by its short alias (r1, r2, ...), a zero-based
# preview row and a field. Application code substitutes the value as the text streams, so
# the reader never sees a number the model typed: prose with a digit outside a placeholder
# stops that paragraph at once and withdraws it. When a line ends it goes through the same
# `_render_paragraph` rules as the structured narrative; `finish()` returns only validated
# paragraphs, and that list (not the preview) is what the answer keeps.
STREAM_REF = re.compile(r'\{\{(r\d+)\.(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\}\}')
STREAM_KIND = re.compile(r'\s*(finding|hypothesis|check|limitation)\s*:\s*', re.I)
MAX_PARAGRAPHS = 6
MAX_LINE = 1200   # raw characters of one line, placeholders included; rendered text is capped at 700 by the paragraph rules


def stream_aliases(packet):
    """(alias -> result_id, packet as the model sees it): short ids keep the placeholders short."""
    aliases = {f'r{i + 1}': entry['result_id'] for i, entry in enumerate(packet)}
    return aliases, [{**entry, 'result_id': alias} for alias, entry in zip(aliases, packet)]


class NarrativeStream:
    """Incremental parser and validator for the line format above.

    feed(text) takes model text as it arrives and calls `emit` with
    {'type': 'delta', 'paragraph': n, 'kind': kind, 'text': substituted text} while a line is
    written, then {'type': 'paragraph', 'paragraph': n, kind, text, citations} when it passed
    validation or {'type': 'retract', 'paragraph': n, 'reason': why} when it did not. finish() validates what is left and
    returns the rendered paragraphs in `render_narrative`'s shape."""

    def __init__(self, packet, emit=None):
        self.packet = packet
        self.sources = {r['result_id']: r for r in packet}
        self.aliases = {f'r{i + 1}': entry['result_id'] for i, entry in enumerate(packet)}
        self.known_shifts = {str(r['shift']) for r in packet if r.get('shift') is not None}
        self.emit = emit or (lambda event: None)
        self.rendered, self.omitted = [], []
        self.index = 0          # paragraph number of the line being read
        self._line = ''         # raw text of the current line
        self._kind = None
        self._shown = 0         # characters of _line already checked and emitted
        self._dead = False      # the current line failed a check; the rest of it is ignored
        self._started = False   # a delta for the current line has been emitted
        self._prose = ''        # the plain prose of the current line shown so far, placeholders as a space

    def feed(self, text):
        for part in re.split(r'(\n)', text):
            if part == '\n':
                self._end_line()
            elif part:
                self._line += part
                self._advance()

    def _retract(self, reason):
        if not self._dead:
            self._dead = True
            self.omitted.append(reason)
            if self._started:
                self.emit({'type': 'retract', 'paragraph': self.index, 'reason': reason})

    def _value(self, alias, row, field):
        """The formatted evidence value for a preview placeholder, or None when it does not resolve."""
        source = self.sources.get(self.aliases.get(alias))
        if source is None or not 0 <= row < len(source['rows']):
            return None
        if field in source['rows'][row]:
            val = source['rows'][row][field]
        elif field == 'shift' and source.get('shift') is not None:
            val = source['shift']
        else:
            return None
        if isinstance(val, (bool, list, dict)) or isinstance(val, float) and not math.isfinite(val):
            return None
        return 'unavailable' if val is None else f'{val:,.2f}' if isinstance(val, float) else str(val)

    def _advance(self):
        """Emit the part of the current line that is safe to show: whole words and whole placeholders."""
        if self._dead or len(self.rendered) + 1 > MAX_PARAGRAPHS:
            return
        if len(self._line) > MAX_LINE:
            return self._retract('Narrative size limit')
        if self._kind is None:
            found = STREAM_KIND.match(self._line)
            if not found:
                if len(self._line) > 24 and self._line.strip():
                    self._retract('Invalid narrative paragraph')
                return
            if found.end() == len(self._line):
                return      # the text has not started; wait so the prefix match cannot grow
            self._kind, self._shown = found.group(1).lower(), found.end()
        out = []
        while self._shown < len(self._line):
            rest = self._line[self._shown:]
            brace = rest.find('{')
            if brace == 0:
                before = self._line[self._shown - 1] if self._shown else ' '
                if before.isalnum() or before == '}' or (before in '.,' and self._line[max(0, self._shown - 3):self._shown - 1] == '}}'):
                    return self._flush(out, 'A fact placeholder must stand alone, not touch a letter, a digit or another placeholder')
                found = STREAM_REF.match(rest)
                if not found:
                    if '}}' in rest or len(rest) > 120:
                        return self._flush(out, 'Fact placeholders do not match references')
                    break   # an unfinished placeholder: wait for the rest
                value = self._value(found.group(1), int(found.group(2)), found.group(3))
                if value is None:
                    return self._flush(out, 'Unknown narrative source')
                after = rest[found.end():found.end() + 1]
                if not after:
                    break   # what follows decides whether the value stands alone: wait for it
                if after in '.,':
                    beyond = rest[found.end() + 1:found.end() + 2]
                    if not beyond:
                        break   # "{{a}}." may be the end of a sentence or the start of "{{a}}.{{b}}"
                    after = '{' if beyond == '{' else after
                if after.isalnum() or after == '{':
                    return self._flush(out, 'A fact placeholder must stand alone, not touch a letter, a digit or another placeholder')
                out.append(value)
                self._prose += ' '
                self._shown += found.end()
                continue
            plain = rest if brace < 0 else rest[:brace]
            if brace < 0:
                # Hold back the last, possibly unfinished word so "Shift 2" is judged whole.
                cut = max(plain.rfind(' '), plain.rfind('\t'))
                if cut < 0:
                    break
                tail = plain[:cut].rstrip().rsplit(' ', 1)[-1].lower()
                if tail == 'shift':   # the shift number may be next: keep "Shift" back with it
                    cut = plain[:cut].rstrip().rfind(' ')
                    if cut < 0:
                        break
                plain = plain[:cut + 1]
                # A small number word counts only with the unit after it ("two stops"): keep it back with that word.
                words = plain.rstrip().rsplit(' ', 1)
                if re.sub(r'[^a-z]', '', words[-1].lower()) in SMALL_NUMBER_WORDS:
                    plain = plain[:len(words[0]) + 1] if len(words) > 1 else ''
            if not plain:
                break
            bare = re.sub(r'\b[Ss]hift\s+([123])\b', lambda m: 'shift' if m.group(1) in self.known_shifts else m.group(0), plain)
            prose = self._prose + plain
            if (any(c.isdigit() or ord(c) < 32 for c in bare) or any(c in plain for c in '<>}') or re.search(r'https?://', prose)
                    or NUMBER_WORDS.search(prose)):
                return self._flush(out, 'Use fact placeholders for values; plain prose only (numbers written as words count)')
            out.append(plain)
            self._prose = prose
            self._shown += len(plain)
        self._flush(out)

    def _flush(self, out, failure=None):
        text = ''.join(out)
        if text and not failure:
            self._started = True
            self.emit({'type': 'delta', 'paragraph': self.index, 'kind': self._kind, 'text': text})
        if failure:
            self._retract(failure)

    def _end_line(self, truncated=False):
        line, dead, started = self._line, self._dead, self._started
        self._line, self._kind, self._shown, self._dead, self._started, self._prose = '', None, 0, False, False, ''
        if not line.strip():
            return
        if len(self.rendered) >= MAX_PARAGRAPHS:
            return                      # extra paragraphs are dropped silently, like an over-long list
        index, self.index = self.index, self.index + 1
        if dead:
            return                      # already withdrawn and recorded
        reason = None
        found = STREAM_KIND.match(line)
        if truncated:
            reason = 'the model stopped before finishing it'
        elif not found:
            reason = 'Invalid narrative paragraph'
        else:
            facts, order = [], {}
            def place(match):
                key = (match.group(1), int(match.group(2)), match.group(3))
                if key not in order:
                    order[key] = len(facts)
                    facts.append({'result_id': self.aliases.get(key[0], key[0]), 'row': key[1], 'field': key[2]})
                return '{{' + str(order[key]) + '}}'
            text = STREAM_REF.sub(place, line[found.end():].strip())
            try:
                paragraph = _render_paragraph({'kind': found.group(1).lower(), 'text': text, 'facts': facts}, self.sources, self.packet)
                self.rendered.append(paragraph)
                self.emit({'type': 'paragraph', 'paragraph': index, **paragraph})   # the validated text replaces the preview
                return
            except ValueError as exc:
                reason = str(exc)
        self.omitted.append(reason)
        if started:
            self.emit({'type': 'retract', 'paragraph': index, 'reason': reason})

    def finish(self, truncated=False):
        """Validated paragraphs, with one limitation line per omitted paragraph; ValueError when none survived."""
        self._end_line(truncated=truncated and bool(self._line.strip()))
        if not self.rendered:
            raise _rejected(self.omitted, 'The model wrote no interpretation')
        return list(self.rendered) + omission_notes(self.omitted)
