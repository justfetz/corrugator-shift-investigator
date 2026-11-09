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
        raise ValueError(omitted[0] if omitted else 'Invalid narrative paragraph')
    for reason in omitted:
        rendered.append({'kind':'limitation','text':f'One paragraph of the interpretation was omitted because it failed validation ({reason}).','citations':[]})
    return rendered


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
        return {'kind':p['kind'],'text':text,'citations':citations}


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
            raise ValueError(self.omitted[0] if self.omitted else 'The model wrote no interpretation')
        out = list(self.rendered)
        for reason in dict.fromkeys(self.omitted):
            out.append({'kind': 'limitation', 'text': f'One paragraph of the interpretation was omitted because it failed validation ({reason}).', 'citations': []})
        return out
