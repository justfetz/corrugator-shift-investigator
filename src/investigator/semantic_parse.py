"""The semantic model's parser (issue #144, phase 2 of #117): a question becomes a `SemanticQuery`, or a refusal.

Deterministic and offline: no model call, a few milliseconds. A question is read with three vocabularies and nothing else:

- the semantic model's own words (`semantic_model.json`): each measure's name, label and synonyms; each dimension's name,
  label and synonyms; each dimension's values as the store holds them (`semantic.store_values`), with the model's value
  synonyms and labels;
- the window and shift words the router already reads (`agent.WINDOW_WORDS`, `agent.named_shifts`, the month names, the
  comparison with the window before), and the scope `agent.question_scope` moved ("last week", "yesterday");
- one closed table of ranking, grouping and statistic words (RANK, GROUP, COMPARE, STATISTIC below), the units a measure
  is asked in (UNITS), and the function words (`agent.FUNCTION_WORDS` and FILLER).

Every content word is used, or the reading names it and refuses: a word that would change the meaning is never dropped.
A refusal offers at most three questions built from the model's own measures, dimensions and values, each of which this
parser reads completely. `read` returns a `Reading`; `route` is what the offline router asks: answer with the semantic
model, refuse, or leave the question to the hand router (`agent.parse_request`).
"""
from dataclasses import dataclass, field
from datetime import date
import re

from . import agent, semantic

TOKEN = re.compile(r"[a-z0-9]+(?:[-/.'’][a-z0-9]+)*|%|\$|\|")
MAX_PHRASE = 5
# The words that carry no content of their own, beyond agent.FUNCTION_WORDS: question words, copulas and verbs a quantity is
# asked with. A word here is never vocabulary on its own (a multi-word phrase may still hold one: "by shift", "paper run").
FILLER = agent.FUNCTION_WORDS | frozenset((
    'with', 'had', 'having', 'list', 'lists', 'listed', 'chart', 'charts', 'graph', 'plot', 'about', 'across', 'over', 'during', 'within',
    'did', 'whats', 'was', 'were', 'did', 'do', 'have', 'has', 'into', 'out', 'as', 'than', 'then', 'by', 'per', 'each', 'every',
    'happened', 'hit', 'see', 'lose', 'lost', 'losing', 'spend', 'spent', 'take', 'took', 'takes', 'taking', 'experience', 'experienced',
    'stopped', 'ran', 'running', 'produced', 'produce', 'producing', 'making', 'rejected'))
# Words that say minutes were lost: with minutes asked and no measure named, the minutes are downtime ("which shift lost the most minutes")
LOSS = ('lost', 'lose', 'losing', 'down')
# ... of which these are cues before a value or a dimension, and are consumed with it
PLACE_CUES = ('at', 'on', 'in')
REASON_CUES = ('for', 'from', 'to', 'due', 'caused', 'because', 'triggered', 'related')
CONNECTOR = (r"\b(?:(?:caused|triggered|brought\s+on|due|related|attributed|coded|charged)\s+(?:by|to|from)|because\s+of)\b")
# The closed table of ranking, grouping, comparison and statistic words (the issue's "closed set"): the model holds measures,
# dimensions and values, never these.
RANK = {'most': 'most', 'top': 'most', 'highest': 'most', 'biggest': 'most', 'largest': 'most', 'greatest': 'most', 'max': 'most',
        'maximum': 'most', 'peak': 'most', 'busiest': 'most', 'more': 'most', 'rank': 'most', 'ranked': 'most', 'ranking': 'most',
        'fewest': 'least', 'least': 'least', 'lowest': 'least', 'smallest': 'least', 'minimum': 'least', 'min': 'least', 'fewer': 'least',
        'less': 'least', 'quietest': 'least'}
SPEED_RANK = {'slowest': 'least', 'fastest': 'most', 'quickest': 'most'}   # a speed ranked: the model's speed ("speed" is speed_run_fpm)
FREQUENCY = ('common', 'frequent', 'often')    # "most common", "most often": the count of the records
WHICH = ('which', 'whichever')                  # "what" is a function word; "what shift" reads as "which shift" below
GROUP = ('by', 'per', 'each', 'every', 'across', 'between')
COMPARE = ('compare', 'compared', 'comparing', 'comparison', 'versus', 'vs', 'against')
STATISTIC = ('average', 'avg', 'mean', 'median', 'typical', 'typically', 'percentile', 'deviation', 'variance', 'longest',
             'shortest', 'std', 'stdev', 'rate', 'ratio', 'distribution')
EVALUATIVE = ('worst', 'best', 'better', 'worse', 'good', 'bad', 'poor', 'poorest', 'strongest', 'weakest')
SUPERLATIVE = ('worst', 'best', 'poorest', 'strongest', 'weakest')   # a ranking with no measure ("worst shift"): refused over a summary
# The statistics a flagship answer never answers in their place ("median downtime" is not the downtime review): refused over it
STATISTIC_ASKED = ('average', 'avg', 'mean', 'median', 'typical', 'typically', 'percentile', 'deviation', 'variance', 'std', 'stdev')
# The dimensions "per" never splits by: "sheets per order" is a ratio of orders the records do not count (the review of PR #155),
# never each order's sheets
PER_RATIO_DIMS = ('order', 'wet_end', 'customer')
# The units a measure is asked in: the measure of the same quantity in that unit (its ratio in %, its priced dollars, ...)
UNITS = {('percent',): '%', ('percentage',): '%', ('%',): '%', ('pct',): '%', ('dollars',): '$', ('dollar',): '$', ('$',): '$',
         ('cost',): '$', ('costs',): '$', ('costing',): '$', ('usd',): '$', ('money',): '$', ('minutes',): 'min', ('mins',): 'min',
         ('minute',): 'min', ('square', 'feet'): 'sq ft', ('square', 'foot'): 'sq ft', ('sq', 'ft'): 'sq ft', ('sqft',): 'sq ft',
         ('times',): 'count', ('count',): 'count'}
COUNT_UNITS = ('setups', 'order changes', 'paper changes', 'days', 'stops', 'sheets', 'cuts')
# What a value of an entity's dimension asks when no measure is named ("which grade has the most warp"): the reject lines'
# area. Stops are counted or timed, so a stop's reason alone names no measure and is refused with both.
ENTITY_DEFAULT = {'reject': 'reject_sqft'}
# When one word names a value of more than one stop dimension: the kind first (planned maintenance is the Scheduled kind, a power
# outage the External kind, the owner's rulings on PR #111 and #123); a place against a reason needs its cue ("at the stacker",
# "for the stacker") or is refused with both.
DIMENSION_FIRST = ('kind',)
ASK = ((r"\bhow\s+often\b", 'count'), (r"\bhow\s+many\b", None), (r"\bnumber\s+of\b", None), (r"\bcount\s+of\b", None),
       (r"\bhow\s+long\b", 'min'), (r"\bhow\s+much\s+time\b", 'min'), (r"\bhow\s+much\b", None), (r"\btotal\b", None))
SHARE = r"\b(?:what\s+)?(?:percent(?:age)?|%|share|proportion|fraction|portion)\s+of\b|\bhow\s+much\s+of\b"
# The verb after the measure of a share: the value after it is the one whose share is asked ("what percent of maintenance stops
# were at the stacker": the stacker's share of the maintenance stops; Codex on PR #155), a value before it narrows the whole
COPULA = ('was', 'were', 'is', 'are')
TOP_N = r"\b(top|bottom)\s+(\d{1,2}|" + '|'.join(agent.WORD_NUMBERS) + r")\b"
SHIFTS_ALL = r"\b((across|on|for|over|in)\s+)?all\s+(the\s+|three\s+)*shifts\b|\bshifts\s+(together|combined)\b|\bevery\s+shift\s+together\b"
THIS_SHIFT = r"\b(this|that|my|our|the)\s+shift\b"
PER_DAY = r"\bper\s+(production\s+)?day\b|\ba\s+day\b"
# the window before, beside the asked one: "versus last week", "than the week before", "compare ... to last week"
PREVIOUS = (r"\b(?:versus|vs\.?|compared\s+(?:to|with)|than|against|to|with)\s+(?:the\s+)?(?:last|previous|prior)\s+(?:production\s+)?(?:week|month|day|period)\b"
            r"|\b(?:versus|vs\.?|compared\s+(?:to|with)|than|against)\s+the\s+(?:week|month|day|period)\s+before\b")
# "broken down by", "split by": a split, read as "by"
SPLIT_BY = r"\b(?:broken|break|split)\s+(?:down\s+|out\s+|up\s+)?(?:by|per)\b"
# A window named in words this parser does not apply: refused, never read as the selected window
UNREAD_WINDOW = (agent.UNREAD_WINDOW + r"|\b(this|last|next)\s+year\b|\bin\s+20\d\d\b|\b20\d\d\b|\bweekends?\b|\btoday\b|\btomorrow\b"
                 r"|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b|\b(year|quarter)\b")
# ... of which these are never the selected day, so a hand route that answers the selected day for them answers another window (round 5
# of the coordinator's notes on #144): refused over it. A weekday is one when it is not the selected day's ("on Monday" on a Monday is
# that day; "Mondays" asks a weekday profile, not a window).
WRONG_WINDOW = (r"\b(this|last|next)\s+year\b|\bin\s+20\d\d\b|\bweekends?\b|\b(the\s+)?day\s+before\b|\b(the\s+)?previous\s+(production\s+)?day\b"
                r"|\b(last|previous|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
                r"|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|" + agent.ROLLING_WINDOW)


@dataclass
class Reading:
    """What the parser made of a question. `query` is the validated SemanticQuery when every content word is used;
    `refusal` the short "did you mean" text when the question asks something the model's words cannot say; `cues` the
    structures it asked for (group, rank, share, per, compare, statistic, filter), which decide whether a hand route that
    answers something else is a misroute."""
    question: str
    query: object = None
    rank: str = None
    share: object = None
    used: list = field(default_factory=list)
    unused: list = field(default_factory=list)
    cues: set = field(default_factory=set)
    refusal: str = None
    suggestions: list = field(default_factory=list)
    measure: str = None
    window_words: str = ''
    shares: bool = False   # a share of each row of a split ("percent of stops by kind"): the card leads with the largest share

    @property
    def complete(self):
        return self.query is not None and self.refusal is None and not self.unused


class Refused(Exception):
    """A reading the model's words cannot say: `text` and at most three suggestions."""

    def __init__(self, text, suggestions=()):
        super().__init__(text)
        self.text, self.suggestions = text, list(suggestions)[:3]


# ------------------------------------------------------------------ the vocabulary

def normalize(text):
    """Lower case, a hyphen between letters read as a space ("dry-end", "set-ups", "double-backer"; "26-C" and "4-4-5" kept)."""
    return re.sub(r"(?<=[a-z])-(?=[a-z])", ' ', str(text).lower())


def tokens_of(text):
    out = []
    for token in TOKEN.findall(normalize(text)):
        if re.search(r"['’]s$", token) and token not in FILLER:
            token = token[:-2]
        out.append(token)
    return out


def _strip_parens(label):
    return re.sub(r'\s*\([^)]*\)', '', label).strip()


_VOCAB = {}


def vocabulary(store, mdl=None):
    """{phrase tokens: [entries]}: every measure, dimension and value word of the model, and the store's values. An entry is
    ('measure', name), ('dimension', name) or ('value', dimension, the store's value, exact). A function word on its own is
    never vocabulary."""
    mdl = mdl or semantic.model()
    key = (id(store), getattr(store, 'content_hash', None), id(mdl))
    if key in _VOCAB:
        return _VOCAB[key]
    phrases = {}

    def add(words, entry):
        toks = tuple(tokens_of(words))
        if not toks or (len(toks) == 1 and toks[0] in FILLER) or all(t in FILLER for t in toks):
            return
        bucket = phrases.setdefault(toks, [])
        if entry not in bucket:
            bucket.append(entry)

    for name, m in mdl.measures.items():
        for word in [name.replace('_', ' '), _strip_parens(m['label']), *m.get('synonyms', [])]:
            add(word, ('measure', name))
    for name, d in mdl.dimensions.items():
        words = [name.replace('_', ' '), _strip_parens(d['label']), *d.get('synonyms', [])]
        for word in words:
            add(word, ('dimension', name))
            if not word.endswith('s') and not word.startswith('by') and not re.search(r'[^a-z ]', word.lower()):
                add(word + 's', ('dimension', name))   # "grades", "stop reasons", "crews": the dimension, said as a set
        if d['type'] != 'categorical' or name == 'shift':
            continue
        values = semantic.store_values(name, store, mdl) or []
        held = {str(v).lower(): v for v in values}
        for value in values:
            if isinstance(value, str):
                add(value, ('value', name, value, True))
        for canonical, label in (d.get('value_labels') or {}).items():
            if canonical.lower() in held:
                add(label, ('value', name, held[canonical.lower()], True))
        for canonical, syns in (d.get('value_synonyms') or {}).items():
            if canonical.lower() not in held:
                continue   # a v1 code (PM) the store does not hold: its words read as the value the store does hold
            for word in syns:
                add(word, ('value', name, held[canonical.lower()], False))
    # a phrase that names several measures (the label "Downtime" of minutes and of the percent) reads as the model's own word for
    # it (Model.measure_words: names and synonyms first, in the file's order), and likewise a dimension
    for words, kind in ((mdl.measure_words, 'measure'), (mdl.dimension_words, 'dimension')):
        canonical = {}
        for word, name in words.items():
            canonical.setdefault(tuple(tokens_of(word)), name)
        for toks, bucket in phrases.items():
            named = [e for e in bucket if e[0] == kind]
            if len(named) > 1:
                keep = canonical.get(toks, named[0][1])
                phrases[toks] = [e for e in bucket if e[0] != kind or e[1] == keep]
    _VOCAB[key] = phrases
    return phrases


def numeric_values(dim, store, mdl=None):
    """A dimension's numeric values (web width, lane): read only right after the dimension's own word ("lane 2")."""
    values = semantic.store_values(dim, store, mdl) or []
    return [v for v in values if isinstance(v, (int, float))]


# ------------------------------------------------------------------ the reading

@dataclass
class Item:
    kind: str           # vocab | rank | speed_rank | frequency | which | group | compare | statistic | evaluative | unit | number | and | filler | unknown
    words: str
    start: int
    entries: list = field(default_factory=list)
    value: object = None
    role: str = None    # what the reading made of it


def _items(text, vocab):
    """The question's tokens as items, longest vocabulary phrase first; `|` separates spans already read."""
    tokens = tokens_of(text)
    out, i = [], 0
    while i < len(tokens):
        if tokens[i] == '|':
            i += 1
            continue
        found = None
        for n in range(min(MAX_PHRASE, len(tokens) - i), 0, -1):
            span = tuple(tokens[i:i + n])
            if '|' in span:
                continue
            if span in UNITS and n > 1:
                found = Item('unit', ' '.join(span), i, value=UNITS[span])
                break
            if span in vocab:
                found = Item('vocab', ' '.join(span), i, entries=list(vocab[span]))
                break
        if found is None:
            word = tokens[i]
            n = 1
            kind = ('unit' if (word,) in UNITS else 'rank' if word in RANK else 'speed_rank' if word in SPEED_RANK else
                    'frequency' if word in FREQUENCY else 'which' if word in WHICH else 'group' if word in GROUP else
                    'compare' if word in COMPARE else 'statistic' if word in STATISTIC else 'evaluative' if word in EVALUATIVE else
                    'and' if word in ('and', 'or', 'plus', 'also') else 'number' if re.fullmatch(r"\d[\d,.]*", word) else
                    'filler' if word in FILLER else 'unknown')
            found = Item(kind, word, i, value=UNITS.get((word,)))
        else:
            n = len(found.words.split())
        out.append(found)
        i += n
    return out


def _window_words(period, month=None):
    if month:
        return 'in ' + month
    return {'day': '', 'week': 'this week', 'month': 'this month', 'fiscal': 'this period'}[period]


def _consume(pattern, text, used, role, flags=0):
    """Every match of `pattern` in `text` replaced by a separator, recorded in `used` with its role; returns (text, matches)."""
    found = list(re.finditer(pattern, text, flags))
    for m in found:
        used.append((m.group(0).strip(), role))
    return (re.sub(pattern, ' | ', text, flags=flags) if found else text), found


_READINGS = {}
READINGS_KEPT = 256   # one question is read up to five times while question_scope probes its scope: read once per scope


def read(text, context, store=None, mdl=None, strict=False):
    """The reading of `text` in `context` (agent.Context, after agent.question_scope moved it): a Reading, which callers treat
    as read-only (it is kept for the same question in the same scope). It never raises for a question; a defect of the parser
    itself leaves the reading empty (the hand router answers), unless `strict` (tests)."""
    store = store if store is not None else _default_store()
    key = (text, context.production_day, context.shift, context.scope, getattr(context, 'period', 'day'),
           ((getattr(context, 'read', None) or {}).get('words'), (getattr(context, 'read', None) or {}).get('period')),   # all it reads of `read`
           id(store), getattr(store, 'content_hash', None), id(mdl), strict)
    if key in _READINGS:
        return _READINGS[key]
    reading = _read_once(text, context, store, mdl, strict)
    if len(_READINGS) >= READINGS_KEPT:
        _READINGS.clear()
    _READINGS[key] = reading
    return reading


def _read_once(text, context, store, mdl, strict):
    reading = Reading(question=text)
    try:
        _read(reading, text, context, store, mdl or semantic.model())
    except Refused as exc:
        reading.refusal, reading.suggestions, reading.query = exc.text, exc.suggestions, None
    except semantic.SemanticError as exc:
        reading.refusal, reading.query = str(exc), None
    except Exception:   # noqa: BLE001 - the parser must never take an answer away from the hand router
        if strict:
            raise
        return Reading(question=text)
    return reading


def _default_store():
    from .record_store import default_store
    return default_store()


def _read(reading, text, context, store, mdl):
    lower = agent.correct_typos(text.lower())
    read_words = getattr(context, 'read', None) or {}
    if read_words.get('words'):
        lower = lower.replace(read_words['words'], agent.READ_AS[read_words['period']])
    lower = re.sub(r"(\d),(\d{3})\b", r"\1\2", lower)   # "1,000 feet"
    used = reading.used
    # ---- the window: the scope's, or the one the question names
    period, anchor, month = getattr(context, 'period', 'day') or 'day', context.production_day, None
    lower, previous = _consume(PREVIOUS, lower, used, 'compare')
    previous_grain = None
    if previous:
        word = re.search(r"\b(week|month|day|period)\b", previous[0].group(0))[1]
        previous_grain = {'period': 'fiscal'}.get(word, word)
    if previous and re.search(r"\bcompar\w*\b", lower):
        lower, _ = _consume(r"\bcompar\w*\b", lower, used, 'compare')
    compare = 'previous' if previous else None
    unread = re.search(UNREAD_WINDOW, lower) or re.search(agent.ROLLING_WINDOW, lower)
    if unread:
        wrong = re.search(WRONG_WINDOW, lower)
        weekday = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')[date.fromisoformat(context.production_day).weekday()]
        said = unread[0].strip()
        if wrong and wrong[0] != weekday:
            reading.cues.add('wrong window')
            said = re.sub(r'^the\s+', '', wrong[0].strip())
        raise Refused(f"'{said}' is not a window the semantic model reads: the windows are the production day, the "
                      "Mon-Fri week, the calendar month and the 4-4-5 period")
    named = set()
    for m in re.finditer(r"\b(" + '|'.join(agent.MONTH_NAMES) + r")\b", lower):
        number = agent.MONTH_NAMES[m[1]]
        year = int(anchor[:4]) - (1 if number > int(anchor[5:7]) else 0)
        named.add(('month', f'{year:04d}-{number:02d}-01', m[1].capitalize()))
    for pattern in agent.WINDOW_WORDS:
        for m in re.finditer(pattern, lower):
            words = m.group(0)
            if re.search(r"\b(fiscal|accounting|4-4-5|445|period|p(1[0-3]|[1-9]))\b", words):
                named.add(('fiscal', anchor, None))
            elif 'month' in words and not re.search(r"\b(" + '|'.join(agent.MONTH_NAMES) + r")\b", words):
                named.add(('month', anchor, None))
            elif 'week' in words:
                named.add(('week', anchor, None))
            elif re.search(r"\bday\b", words):
                named.add(('day', anchor, None))
    for pattern in agent.WINDOW_WORDS:
        lower, _ = _consume(pattern, lower, used, 'window')
    if read_words.get('words'):
        # the words the scope moved on ("last week", "yesterday") are the ones the question used, not their reading in that scope
        moved = agent.READ_AS[read_words['period']]
        hit = next((k for k, (words, role) in enumerate(used) if role == 'window' and words == moved), None) if moved else None
        if hit is not None:
            used[hit] = (read_words['words'], 'window')
        elif not moved:
            used.append((read_words['words'], 'window'))
    if len(named) > 1:
        raise Refused('The question names more than one window: ask one window at a time, or one window against the one before it')
    if named:
        period, anchor, month = next(iter(named))
    elif previous_grain:
        period = previous_grain   # "our speed vs last week": this week against the one before
    reading.window_words = _window_words(period, month)
    if read_words.get('words') and read_words.get('period') == period and month is None and not previous_grain:
        # the window the question moved to keeps its own words in a suggestion ("stops by shift last week", never "this week",
        # which in the visitor's scope is another week: the review of PR #155)
        reading.window_words = read_words['words']
    # ---- the shift: one named (agent.named_shifts), the selected one ("this shift"), or all of them
    lower, unread_shift = _consume(agent.UNREAD_SHIFT, lower, used, 'unread shift')
    if unread_shift:
        raise Refused(f"'{unread_shift[0][0].strip()}' names a shift the semantic model does not read: name it by its number "
                      "('shift 1'), or set the shift control")
    shifts = agent.named_shifts(lower)
    lower, _ = _consume(r"\b(?:(?:on|for|by|in|during|from)\s+)?(?:the\s+)?(?:shift\s*[1-3]|(?:first|second|third|1st|2nd|3rd)\s+shift)\b",
                        lower, used, 'shift')
    lower, this_shift = _consume(r"\b(?:(?:on|for|in|during)\s+)?" + THIS_SHIFT, lower, used, 'shift')
    lower, all_shifts = _consume(SHIFTS_ALL, lower, used, 'all shifts')
    # ---- the share, the asks and "top N" (the share first: "how much of the downtime" is a share, not an ask)
    lower, share = _consume(SHARE, lower, used, 'share')
    if share:
        reading.cues.add('share')
    ask_unit, ask_often = None, bool(re.search(r"\bhow\s+often\b", lower))
    for pattern, unit in ASK:
        lower, hits = _consume(pattern, lower, used, 'ask')
        if hits and ask_unit is None and unit is not None:
            ask_unit = unit
    limit, rank = semantic.DEFAULT_LIMIT, None
    top = re.search(TOP_N, lower)
    if top:
        n = int(top[2]) if top[2].isdigit() else agent.WORD_NUMBERS[top[2]]
        limit, rank = max(1, min(n, semantic.MAX_LIMIT)), ('most' if top[1] == 'top' else 'least')
        reading.cues.add('rank')
        lower, _ = _consume(TOP_N, lower, used, 'rank')
    lower, per_day = _consume(PER_DAY, lower, used, 'per day')
    lower = re.sub(SPLIT_BY, ' by ', lower)
    lower = re.sub(r"\bbreak\s?downs?\b", ' zbreakdownz ', lower)   # a split asked ("downtime breakdown"), never the Maintenance kind's synonym
    lower = re.sub(r"\b(cost|costs|costing)(\s+us)?\s+(?=(the\s+)?(most|least|more|less)\b)", ' ', lower)   # "cost the most minutes": the verb
    lower, connectors = _consume(CONNECTOR, lower, used, 'reason cue')
    # ---- the words, longest vocabulary phrase first
    vocab = vocabulary(store, mdl)
    items = _items(lower, vocab)
    a = {'rank': rank, 'limit': limit, 'per_day': bool(per_day), 'reason_cue': bool(connectors), 'period': period, 'anchor': anchor,
         'compare': compare, 'shifts': shifts, 'this_shift': bool(this_shift), 'all_shifts': bool(all_shifts), 'ask_unit': ask_unit,
         'ask_often': ask_often, 'loss': bool(set(tokens_of(text)) & set(LOSS))}
    _assign(reading, items, mdl, store, a, context)


def _entries(item, kind):
    return [e for e in item.entries if e[0] == kind]


def _prev(items, i):
    """The item before i, skipping articles."""
    j = i - 1
    while j >= 0 and items[j].kind == 'filler' and items[j].words in ('the', 'a', 'an', 'our', 'its', 'their'):
        j -= 1
    return items[j] if j >= 0 else None


def _family(mdl, base, unit):
    """The measure over `base`'s quantity in `unit` (its ratio in %, its priced dollars, its count, its minutes), or None."""
    m = mdl.measures[base]
    if unit is None:
        return base
    if unit == '$':
        if m['agg'] == 'priced':
            return base
        quantity = m['quantity'] if m['agg'] == 'priced' else base
        return next((n for n, o in mdl.measures.items() if o['agg'] == 'priced' and o['quantity'] == quantity), None)
    if unit == '%':
        if m['unit'].startswith('%'):
            return base
        if m['agg'] == 'ratio':
            return None
        return next((n for n, o in mdl.measures.items() if o['agg'] == 'ratio' and o['numerator'] == base and o['unit'].startswith('%')), None)
    entity = mdl.measure_entities(base)[0]
    if unit == 'count':
        if m['unit'] in COUNT_UNITS:
            return base
        return next((n for n, o in mdl.measures.items() if o.get('entity') == entity and o['agg'] == 'count' and not o.get('filter')
                     and not o.get('above_band')), None)
    if unit in ('min', 'sq ft'):
        if m['unit'] == unit:
            return base
        if unit == 'min' and entity != 'stop':
            return None   # a stop's minutes are its length; a setup's clock is not how long a setup took
        same = [n for n, o in mdl.measures.items() if o.get('entity') == entity and o['unit'] == unit and o['agg'] == 'sum'
                and not o.get('filter')]
        return same[0] if same else None
    return None


def _ratio(mdl, numerator, denominator):
    return next((n for n, o in mdl.measures.items() if o['agg'] == 'ratio' and o['numerator'] == numerator and o['denominator'] == denominator), None)


def _measure_word(mdl, name):
    """How a suggestion says a measure: its first synonym when it has one, else its label."""
    m = mdl.measures[name]
    words = {'downtime_minutes': 'minutes of downtime', 'stops': 'stops', 'setups': 'setups', 'msf': 'MSF', 'reject_sqft': 'dry-end rejects',
             'speed_run_fpm': 'speed'}
    return words.get(name) or _strip_parens(m['label']).lower()


def _dim_word(mdl, dim):
    return {'grade': 'grade', 'reject_family': 'reject family', 'reject_reason': 'reject reason', 'web_width': 'web width',
            'wet_end': 'paper run'}.get(dim, dim)


ARTICLES = ('the', 'a', 'an', 'our', 'its', 'their', 'my')
INHERENT = ('daily', 'weekly', 'monthly', 'by day', 'by week', 'by month', 'by shift', 'shifts')
WHO = ('who', 'whom', 'whose')


def _cued(items, i):
    """True when the dimension at `i` is asked as a split: a ranking, grouping or comparison word before it, across the measure,
    unit and article words of its noun phrase ("which shift", "by grade", "highest downtime day", "most common stop reason")."""
    if items[i].words == 'where':
        return True   # "where did we stop most often": which place
    j = i - 1
    while j >= 0:
        it = items[j]
        if it.kind in ('group', 'which', 'rank', 'speed_rank', 'compare') or it.words == 'what' or it.words in SUPERLATIVE:
            return True   # ... or a superlative with no measure ("best crew": the crew, ranked on a measure not named)
        if it.role in ('measure', 'unit') or it.kind in ('frequency', 'unit') or (it.kind == 'filler' and it.words in ARTICLES):
            j -= 1
            continue
        return False
    # ... or after it: "shift with the most waste", "the day that had the fewest stops"
    for it in items[i + 1:i + 7]:
        if it.kind in ('rank', 'speed_rank'):
            return True
        if not (it.kind == 'filler' or it.words in ('with', 'that', 'had', 'has')):
            break
    return False


def _assign(reading, items, mdl, store, a, context):
    """Every item given a role, or the reading refused; then the query built and validated. `a` holds what the first pass read:
    the window, the shift, the asks, the share, "top N", "per day" and a reason's connector."""
    rank, limit, units, leftover = a['rank'], a['limit'], [], []
    statistic, evaluative, which, speed_rank, frequency, who = [], [], False, None, False, False
    group_by, filters, per_ratio = [], {}, None
    for i, item in enumerate(items):
        kind = item.kind
        if kind == 'unit':
            units.append(item.value)
            item.role = 'unit'
        elif kind == 'rank':
            rank = rank or RANK[item.words]
            item.role = 'rank'
            reading.cues.add('rank')
        elif kind == 'speed_rank':
            speed_rank = SPEED_RANK[item.words]
            item.role = 'rank'
            reading.cues.add('rank')
        elif kind == 'frequency':
            frequency, item.role = True, 'unit'
        elif kind == 'which':
            which, item.role = True, 'rank'
        elif kind == 'statistic':
            statistic.append(item.words)
            reading.cues.add('statistic')
            if item.words in STATISTIC_ASKED:
                reading.cues.add('statistic asked')
        elif kind == 'evaluative':
            evaluative.append(item.words)
            reading.cues.add('rank')
            if item.words in SUPERLATIVE:
                reading.cues.add('evaluative')
        elif kind == 'compare':
            item.role = 'compare'
            reading.cues.add('compare')
        elif kind == 'unknown':
            if item.words in WHO:
                who = True
                reading.cues |= {'rank', 'who'}
            else:
                leftover.append(item.words)
    # ---- the measure: a phrase that is only a measure; a phrase that is also a value or a dimension is the measure only when no
    # other measure is named ("how much shear" against "stops at the shear", "how many changeovers" against "stops for changeovers")
    denominators = set()   # the measure after "per" is the ratio's denominator, never a second measure ("downtime per 1,000 feet")
    for i, item in enumerate(items):
        if item.kind == 'group' and item.words == 'per':
            rest = [j for j in range(i + 1, len(items)) if not (items[j].kind == 'filler' and items[j].words in ARTICLES)]
            if rest and items[rest[0]].kind == 'number':
                rest = rest[1:]
            if rest and items[rest[0]].kind == 'vocab' and _entries(items[rest[0]], 'measure') and not _entries(items[rest[0]], 'dimension'):
                denominators.add(rest[0])
    measure_items = [it for k, it in enumerate(items) if it.kind == 'vocab' and _entries(it, 'measure') and k not in denominators]
    pure = [it for it in measure_items if len(_entries(it, 'measure')) == len(it.entries)]
    mixed = [it for it in measure_items if it not in pure]
    if not pure and mixed and not (len(mixed) == 1 and _cued(items, items.index(mixed[0])) and _entries(mixed[0], 'dimension')):
        pure, mixed = [mixed[0]], mixed[1:]
        pure[0].entries = _entries(pure[0], 'measure')
    for it in list(mixed):   # "trim and shear": a measure joined to a measure is a second measure, never a value
        k = items.index(it)
        if any(0 <= j < len(items) and items[j].kind == 'and' and 0 <= 2 * j - k < len(items) and items[2 * j - k] in pure
               for j in (k - 1, k + 1)):
            mixed.remove(it)
            it.entries = _entries(it, 'measure')
            pure.append(it)
    for it in mixed:
        it.entries = [e for e in it.entries if e[0] != 'measure']
    for it in pure:
        it.role = 'measure'
    named = list(dict.fromkeys(_entries(it, 'measure')[0][1] for it in pure))
    if len(named) > 1:
        words = [it.words for it in pure]
        raise Refused(f"'{words[0]}' and '{words[1]}' are two measures: ask one at a time",
                      [f'{_measure_word(mdl, n)} {reading.window_words}'.strip() for n in named[:3]])
    # ---- dimensions (a split when asked as one) and values
    values = []
    for i, item in enumerate(items):
        if item.kind != 'vocab' or item.role == 'measure':
            continue
        dims, vals = _entries(item, 'dimension'), _entries(item, 'value')
        cue = _cued(items, i)
        plural = bool(dims) and item.words.endswith('s') and ' ' not in item.words
        if dims and (cue or item.words in INHERENT or (plural and ('compare' in reading.cues or rank or which)) or not vals):
            item.role, item.value = 'dimension', dims[0][1]
        elif vals:
            item.role, item.value = 'value', vals
            values.append((i, item))
    for i, item in enumerate(items):   # a numeric value right after its dimension's word ("lane 2", "web width 100")
        if item.kind != 'number':
            continue
        prev = items[i - 1] if i else None
        if prev is None or prev.role != 'dimension':
            leftover.append(item.words)
            continue
        hit = next((v for v in numeric_values(prev.value, store, mdl) if float(item.words) == float(v)), None)
        if hit is None:
            leftover.append(f'{prev.words} {item.words}')
            continue
        prev.role, item.role, item.value = 'qualifier', 'value', [('value', prev.value, hit, True)]
        values.append((i, item))
    for i, item in enumerate(items):
        if item.role != 'dimension':
            continue
        dim = item.value
        if dim == 'shift' and a['shifts'] and not (which or rank or 'compare' in reading.cues or _cued(items, i) or item.words in INHERENT):
            item.role = 'shift'   # "shift" said beside its number; "by shift" beside one is a split, refused below
            continue
        nxt = items[i + 1] if i + 1 < len(items) else None
        if nxt is not None and nxt.role == 'value' and any(e[1] == dim for e in nxt.value):
            item.role, nxt.value = 'qualifier', [e for e in nxt.value if e[1] == dim]   # "stop kind Maintenance", "reason stacker"
            continue
        if _cued(items, i) or item.words in INHERENT or (item.words.endswith('s') and ('compare' in reading.cues or rank or which)):
            if item.words == 'daily' and dim == 'day':
                a['per_day'] = True   # "daily setups": per day, read below as the measure's ratio to the days or each day's stops
                item.role = 'per'
                continue
            if dim not in group_by:
                group_by.append(dim)
            for j in range(i - 1, -1, -1):
                if items[j].kind == 'group' and items[j].role is None:
                    items[j].role = 'group'
                    if items[j].words == 'per' and dim in PER_RATIO_DIMS:
                        per_ratio = item.words
                    break
                if items[j].kind not in ('filler', 'frequency', 'unit') and items[j].role not in ('measure', 'unit', 'rank'):
                    break
            reading.cues.add('group')
        else:
            leftover.append(item.words)
    # ---- the measure when none is named: what the split's own words say ("downtime reason", "stop reason"), the default of a value's
    # records (a reject family's area), a count of stops asked, a speed ranked, minutes lost
    measure = named[0] if named else None
    if measure is None:
        for item in items:
            if item.role == 'dimension' and item.value in group_by:
                inside = [mdl.measure_words.get(w) for w in item.words.split()]
                found = next((m for m in inside if m and mdl.measures[m]['agg'] in semantic.SIMPLE), None)
                if found:
                    measure = found
                    break
    if measure is None:
        for _, it in values:
            entities = {mdl.dimensions[e[1]]['entity'] for e in it.value}
            if len(entities) == 1 and next(iter(entities)) in ENTITY_DEFAULT:
                measure = ENTITY_DEFAULT[next(iter(entities))]   # "warp" is a reject family only; "glue" names stops too, and is refused
                break
    counted = frequency or a['ask_often'] or 'count' in units
    if measure is None and counted:
        entities = [mdl.dimensions[d]['entity'] for d in group_by] + [mdl.dimensions[e[1]]['entity'] for _, it in values for e in it.value]
        entity = next((e for e in entities if e != '*'), None)
        measure = next((n for n, o in mdl.measures.items() if o.get('entity') == entity and o['agg'] == 'count' and not o.get('filter')
                        and not o.get('above_band')), None) if entity else None
    if measure is None and speed_rank:
        measure = 'speed_run_fpm'
    if measure is None and ('min' in units or a['ask_unit'] == 'min') and a['loss']:
        measure = 'downtime_minutes'
    # ---- the unit asked: the measure of the same quantity in it
    unit = units[-1] if units else None
    if counted:
        unit = 'count'   # "most common", "how often", "how many times": the count of the records
    if unit is None and a['ask_unit'] == 'min' and measure and mdl.measures[measure]['unit'] != 'ft/min' \
            and not mdl.measures[measure]['unit'].startswith('%'):
        unit = 'min'     # "how long": minutes
    stat_word = next((w for it in pure for w in it.words.split() if w in STATISTIC), None)
    if measure is not None and unit is not None and stat_word and _family(mdl, measure, unit) != measure:
        # "average stop count": the model's average stop is a length; its count, cost or rate is a statistic it does not define
        raise Refused(f"'{stat_word}' asks for a statistic the semantic model does not define: it keeps sums, counts and ratios of sums",
                      _suggest(mdl, reading, measure, None, None))
    if measure is not None and unit is not None:
        refined = _family(mdl, measure, unit)
        if refined is None:
            raise Refused(f"{_strip_parens(mdl.measures[measure]['label'])} is not kept in {'dollars' if unit == '$' else unit} by the "
                          "semantic model", [f'{_measure_word(mdl, measure)} {reading.window_words}'.strip()])
        measure = refined
    reading.measure = measure
    if per_ratio:
        reading.cues.add('per')
        raise Refused(f"{(_measure_word(mdl, measure) if measure else 'that').capitalize()} per {per_ratio} is not a ratio the semantic model "
                      "defines: it counts no orders, paper runs or customers to divide by", _suggest(mdl, reading, measure or 'sheets', None, None))
    # ---- per: a dimension after it splits; a measure after it is a ratio the model defines, or refused
    for i, item in enumerate(items):
        if item.kind != 'group' or item.words != 'per' or item.role == 'group':
            continue
        rest = [it for it in items[i + 1:] if not (it.kind == 'filler' and it.words in ARTICLES)]
        if rest and rest[0].kind == 'number':
            rest[0].role = 'per'
            if rest[0].words in leftover:
                leftover.remove(rest[0].words)
            rest = rest[1:]
        nxt = rest[0] if rest else None
        reading.cues.add('per')
        if units or measure:
            reading.cues.add('per_quantity')   # "dollars per stop", "downtime per 1,000 feet": a numerator was said
        denominator = None
        if nxt is not None and nxt.kind == 'vocab':
            denominator = next((e[1] for e in nxt.entries if e[0] == 'measure'), None) or \
                          next((m for m in [mdl.measure_words.get(nxt.words)] if m), None)
        ratio = _ratio(mdl, measure, denominator) if measure and denominator else None
        if ratio is None:
            what = nxt.words if nxt is not None else 'that'
            raise Refused(f"{(_measure_word(mdl, measure) if measure else 'that').capitalize()} per {what} is not a ratio the semantic model "
                          "defines", _suggest(mdl, reading, measure or 'stops', None, None))
        measure = ratio
        item.role = nxt.role = 'per'
        if nxt.words in leftover:
            leftover.remove(nxt.words)
    if a['per_day'] and not (measure and mdl.measures[measure]['unit'] == 'ft/min'):   # "average speed per day": the speed
        reading.cues.add('per')
        days = _ratio(mdl, measure, 'production_days') if measure else None
        if days:
            measure = days
        elif measure and mdl.measure_entities(measure)[0] != 'stop' and mdl.measures[measure]['agg'] in semantic.SIMPLE:
            # setups or feet per day is no ratio the model keeps (the review of PR #155: never each day's rows said as a rate);
            # the stops and the downtime of each production day are the stops profile's own reading
            raise Refused(f"{_measure_word(mdl, measure).capitalize()} per day is not a ratio the semantic model defines",
                          [f'{_measure_word(mdl, measure)} by day {reading.window_words or "this week"}'.strip(),
                           f'{_measure_word(mdl, measure)} {reading.window_words}'.strip()])
        elif 'day' not in group_by:
            group_by.append('day')
    reading.measure = measure
    # ---- values: the dimension each belongs to
    for i, item in values:
        prev = _prev(items, i)
        candidates = [e for e in item.value if measure is None or semantic._usable(mdl, measure, e[1], as_filter=True) is None]
        if not candidates:
            if measure is None:
                continue
            dims = sorted({e[1] for e in item.value})
            raise Refused(f"{_strip_parens(mdl.measures[measure]['label'])} is not recorded by {', '.join(_dim_word(mdl, d) for d in dims)}, so "
                          f"'{item.words}' cannot narrow it", _suggest(mdl, reading, measure, None, None))
        dims = list(dict.fromkeys(e[1] for e in candidates))
        cue = prev.words if prev is not None else None
        if len(dims) > 1:
            if cue in PLACE_CUES and 'place' in dims:
                dims = ['place']
            elif (cue in REASON_CUES or a['reason_cue']) and 'reason' in dims:
                dims = ['reason']
            elif any(d in dims for d in DIMENSION_FIRST):
                dims = [next(d for d in DIMENSION_FIRST if d in dims)]
            else:
                exact = list(dict.fromkeys(e[1] for e in candidates if e[3]))
                dims = exact if len(exact) == 1 else dims
        if len(dims) > 1:
            word = _measure_word(mdl, measure or 'stops')
            options = [(f"what percent of {word} was {_value_phrase(mdl, d, next(e[2] for e in candidates if e[1] == d))} "
                        if 'share' in reading.cues else
                        f"{word} {'at' if d == 'place' else 'for' if d == 'reason' else 'of'} "
                        f"{'the ' if d == 'place' else ''}{next(e[2] for e in candidates if e[1] == d)} ") + reading.window_words
                       for d in dims]
            options = [o.strip() for o in options]
            raise Refused(f"'{item.words}' is a {' and a '.join(_dim_word(mdl, d) for d in dims)}: say which", options)
        dim = dims[0]
        value = next(e[2] for e in candidates if e[1] == dim)
        if prev is not None and prev.kind == 'group' and not a['reason_cue'] and not (prev.words == 'by' and dim == 'kind'):
            # "stops by splicer", "by knife": a split of some values, not one value; refused rather than read as a filter
            raise Refused(f"'{prev.words} {item.words}' splits by part of a dimension: split by the whole {_dim_word(mdl, dim)}",
                          _suggest(mdl, reading, measure or 'stops', dim, None))
        if prev is not None and prev.kind == 'group':
            prev.role = 'reason cue'
        item.role, item.value = 'value', (dim, value)
    by_dim = {}
    for _, it in values:
        if it.role == 'value' and isinstance(it.value, tuple):
            by_dim.setdefault(it.value[0], []).append(it.value[1])
    joined = False
    for dim, vals in by_dim.items():
        vals = list(dict.fromkeys(vals))
        if len(vals) > 1:
            if 'compare' in reading.cues or any(it.kind == 'and' for it in items):
                # two values of one dimension compared ("32-C and 26-C"): the split by it, every value listed
                if dim not in group_by:
                    group_by.append(dim)
                reading.cues.add('compare')
                joined = True
                continue
            raise Refused(f"{' and '.join(str(v) for v in vals)} are two values of one {_dim_word(mdl, dim)}: ask one, or split by it",
                          _suggest(mdl, reading, measure or 'stops', dim, None))
        filters[dim] = vals[0]
    share_value = None
    if 'share' in reading.cues:
        if filters and not group_by:
            # the value after the verb is the share ("what percent of maintenance stops were at the stacker"), a value beside the
            # measure narrows the whole; with two values and no verb between them, which share is asked is not said
            at = {it.value[0]: i for i, it in values if it.role == 'value' and isinstance(it.value, tuple)}
            measure_at = next((k for k, it in enumerate(items) if it.role == 'measure'), None)
            verb = next((k for k, it in enumerate(items) if it.words in COPULA and (measure_at is None or k > measure_at)), None)
            after = sorted((d for d in filters if verb is not None and at.get(d, -1) > verb), key=lambda d: at[d])
            if len(filters) == 1:
                dim = next(iter(filters))
            elif after:
                dim = after[0]   # the first value after the verb; one after it narrows ("were warp on 32-C": warp's share of 32-C's)
            else:
                word = _measure_word(mdl, measure or 'stops')
                first, second = list(filters)[:2]
                raise Refused('Two values are named and the share of one of them is not said: ask the share of one within the other',
                              [f"what percent of {_label_word(mdl, first, filters[first])} {word} were {_value_phrase(mdl, second, filters[second])} "
                               f"{reading.window_words}".strip(),
                               f"what percent of {_label_word(mdl, second, filters[second])} {word} were {_value_phrase(mdl, first, filters[first])} "
                               f"{reading.window_words}".strip()])
            share_value = filters.pop(dim)
            group_by.append(dim)
        elif group_by:
            if len(group_by) > 1:
                raise Refused('A share of each row is read for a split by one dimension: ask one split at a time',
                              _suggest(mdl, reading, measure or 'stops', group_by[0], None))
            reading.shares = True   # "percent of stops by kind": each row's share of the total
        else:
            raise Refused('A share is of a whole split by a dimension: name the value whose share you want, or what to split by',
                          _suggest(mdl, reading, measure or 'stops', None, None))
    if filters:
        reading.cues.add('filter')
    if len(a['shifts']) > 1:
        if 'compare' in reading.cues or any(it.kind == 'and' for it in items):
            if 'shift' not in group_by:
                group_by.append('shift')
            joined = True
        else:
            raise Refused('Two shifts named: compare them ("compare shift 1 and shift 2 stops"), or ask one shift at a time')
    for item in items:
        if item.kind == 'and' and not joined:
            leftover.append(item.words)
        elif item.kind == 'compare' and not (group_by or a['compare']):
            leftover.append(item.words)
        elif item.kind == 'group' and item.role is None and not group_by:
            leftover.append(item.words)
    # ---- refusals, the most specific first
    if who:
        reading.cues.add('who')
        raise Refused("'who' could be a shift or a crew: say which", _suggest(mdl, reading, measure or 'stops', None, rank or 'most',
                                                                                 dims=['shift', 'crew']))
    if evaluative:
        said = group_by[0] if group_by else next((it.value for it in items if it.role == 'dimension' and isinstance(it.value, str)), 'shift')
        raise Refused(f"'{evaluative[0]}' does not say which way is better: name the measure and the direction",
                      _suggest(mdl, reading, measure, said, 'most'))
    if statistic:
        base = measure or 'stops'
        ratios = [n for n, o in mdl.measures.items() if o['agg'] == 'ratio' and base in (o['numerator'], o['denominator'])
                  and not o['unit'].startswith('%')]
        asks = [f'{_strip_parens(mdl.measures[r]["label"]).lower()} {reading.window_words}'.strip() for r in ratios[:2]]
        raise Refused(f"'{statistic[0]}' asks for a statistic the semantic model does not define: it keeps sums, counts and ratios of sums",
                      list(dict.fromkeys(asks + _suggest(mdl, reading, base, group_by[0] if group_by else None, None)))[:3])
    if measure is None:
        raise Refused('The question names no measure the semantic model holds',
                      _suggest(mdl, reading, None, group_by[0] if group_by else None, rank or speed_rank))
    if leftover:
        reading.cues.add('unknown')
        reading.unused = list(dict.fromkeys(leftover))
        raise Refused(f"'{reading.unused[0]}' is not a word the semantic model reads",
                      _suggest(mdl, reading, measure, group_by[0] if group_by else None, rank or speed_rank))
    if a['loss'] and mdl.measures[measure]['unit'] == 'ft/min':
        raise Refused('Speed lost is a gap against a target or a baseline, which the semantic model does not hold: it keeps the speed itself',
                      _suggest(mdl, reading, measure, group_by[0] if group_by else 'shift', 'least'))
    if speed_rank:
        if mdl.measures[measure]['unit'] != 'ft/min':
            raise Refused(f"'{'slowest' if speed_rank == 'least' else 'fastest'}' ranks a speed, and the question names "
                          f"{_measure_word(mdl, measure)}", _suggest(mdl, reading, 'speed_run_fpm', group_by[0] if group_by else 'shift', speed_rank))
        rank = rank or speed_rank
    if (rank or which) and not group_by:
        if mdl.measures[measure]['unit'] in COUNT_UNITS:
            reading.cues.add('single')   # "the biggest stop", "the longest setup": one record, which no measure is
        raise Refused('A ranking needs what it ranks: a shift, a day, a grade, a reason, a place or a crew',
                      _suggest(mdl, reading, measure, None, rank or 'most'))
    if 'share' in reading.cues and mdl.measures[measure]['agg'] not in semantic.ADDITIVE:
        raise Refused(f"A share of the total is read for a count or a sum, and {_measure_word(mdl, measure)} is a ratio",
                      _suggest(mdl, reading, measure, group_by[0] if group_by else None, None))
    if 'shift' in group_by and len(a['shifts']) == 1 and not joined:
        # "which shift had the most stops on shift 2": the named shift is never dropped for the split (the review of PR #155)
        said = next((w for w, role in reading.used if role == 'shift'), f'shift {a["shifts"][0]}')
        said = re.sub(r'^(?:on|for|by|in|during|from)\s+(?:the\s+)?', '', said)
        reading.cues.add('shift split')
        raise Refused(f"'{said}' names one shift beside a split by shift: ask every shift, or that shift alone",
                      _suggest(mdl, reading, measure, 'shift', rank or speed_rank) + [f'{_measure_word(mdl, measure)} on {said} {reading.window_words}'.strip()])
    # ---- the shift: a split by shift reads every shift; else the one named, the selected one, or every shift
    if 'shift' in group_by or a['all_shifts']:
        shift = None
    elif a['shifts']:
        shift = a['shifts'][0]
    elif a['this_shift'] or context.scope == 'shift':
        shift = context.shift
    else:
        shift = None
    if context.scope == 'shift' and shift is None:
        raise Refused('Selected shift only: choose All in the shift control to split or rank by shift')
    grains = ('day', 'week', 'month', 'fiscal')
    for dim in group_by:
        grain = mdl.dimensions[dim].get('grain')
        if grain and grains.index(grain) >= grains.index(a['period']):
            raise Refused(f"A split by {_dim_word(mdl, dim)} needs a window longer than one {_dim_word(mdl, dim)}",
                          [f'{_measure_word(mdl, measure)} by {_dim_word(mdl, dim)} this {"week" if grain == "day" else "month"}'])
    order = None
    if rank:
        order = 'value' if rank == 'most' else 'value_asc'
        reading.rank = rank
    query = semantic.SemanticQuery(measure=measure, group_by=group_by, filters=filters, window={'period': a['period'], 'anchor': a['anchor']},
                                   compare=a['compare'], order=order, limit=limit, shift=shift, share=share_value)
    reading.query = semantic.validate(query, store, mdl)
    reading.share = reading.query.share
    reading.used += [(it.words, it.role) for it in items if it.role]


def _value_phrase(mdl, dim, value):
    """A value as the end of a share question says it: `at the Stacker`, `for Missed splice`, `Maintenance`."""
    return f'at the {value}' if dim == 'place' else f'for {value}' if dim == 'reason' else str(value)


def _label_word(mdl, dim, value):
    """A value as a word before the measure: `maintenance stops`, `32-C setups`."""
    return str(value).lower() if dim in ('kind', 'place', 'reason', 'reject_family', 'reject_reason') else str(value)


def _suggest(mdl, reading, measure, dim, rank, dims=None):
    """At most three questions in the model's own words, each read completely by this parser: the measure split by the
    dimension, the dimension ranked on it, the measure alone."""
    window = reading.window_words
    out = []
    measures = [measure] if measure else ['stops', 'downtime_minutes', 'setups']
    dims = dims or ([dim] if dim else (['reason', 'shift'] if measure and mdl.measure_entities(measure)[0] == 'stop' else ['shift', 'grade']))
    for m in measures:
        word = _measure_word(mdl, m)
        for d in dims:
            if semantic._usable(mdl, m, d, as_filter=False) is not None:
                continue
            if rank:
                verb = 'had the most' if rank == 'most' else 'had the fewest' if mdl.measures[m]['unit'] in COUNT_UNITS else \
                       'had the least'
                if mdl.measures[m]['unit'] == 'ft/min':
                    verb = 'was the slowest' if rank == 'least' else 'was the fastest'
                    out.append(f'which {_dim_word(mdl, d)} {verb} {window}'.strip())
                else:
                    out.append(f'which {_dim_word(mdl, d)} {verb} {word} {window}'.strip())
            else:
                out.append(f'{word} by {_dim_word(mdl, d)} {window}'.strip())
        if measure:
            out.append(f'{word} {window}'.strip())
    return list(dict.fromkeys(out))[:3]


# ------------------------------------------------------------------ the router's question

def refusal_text(reading):
    """The refusal a visitor reads: what the model could not say, and at most three questions that answer."""
    text = reading.refusal.rstrip('.')
    if reading.suggestions:
        quoted = [f"'{s}'" for s in reading.suggestions[:3]]
        text += '. Did you mean ' + (', '.join(quoted[:-1]) + ' or ' + quoted[-1] if len(quoted) > 1 else quoted[0]) + '?'
    else:
        text += '.'
    return text


def request_of(reading):
    """The offline request for a complete reading: one semantic_query step; `lead` carries the ranking or the shares the card
    opens with (urls.py writes them as rank= and share=each; one value's share is the query's own `share`, share=<value>)."""
    query = reading.query
    lead = {}
    if reading.rank:
        lead['rank'] = reading.rank
    if reading.shares:
        lead['shares'] = True
    request = {'intent': 'semantic', 'shifts': [query.shift] if query.shift else [], 'kind': None, 'group_by': 'reason',
               'want_chart': bool(query.group_by), 'ranking_metrics': [], 'plan': [{'tool': semantic.TOOL, 'arguments': query.to_arguments()}]}
    if lead:
        request['lead'] = lead
    return request


# The hand routes a reading never takes a question from unless it read completely: #113's allow-listed quantity routes (every word
# already consumed) and the investigations and shapes no split, ranking or share describes (the overview, the summary, the trend, the
# speed variance and opportunity, rollstock, the over/under-run, the day's matrices, notes and rankings).
NEVER_PREEMPT = ('period_quantity', 'period_stops', 'period_trend', 'period_speedvar', 'period_speed_opportunity', 'period_rollstock',
                 'period_overrun', 'period_overview', 'period_summary', 'overview', 'summary', 'matrix', 'orders', 'notes', 'ranking')
# The cues a hand route that answers something else drops: a ranking, a share, a ratio the model does not define, a statistic, one
# record asked ("the biggest stop"), "who".
STRONG = ('rank', 'share', 'per', 'statistic', 'who', 'single')
# The flagship shapes: a refusal never takes a question from them (the owner's review, the Pareto, the waste breakdown and the shift's
# rejects, the money levers and the shift's dollars, the day's downtime breakdown); a complete reading does, when it read a split, a
# ranking, a share, a filter or a measure they do not answer.
FLAGSHIP = ('period_review', 'period_pareto', 'period_waste', 'period_quality', 'quality', 'period_money', 'dollars', 'downtime')
DOWNTIME_FAMILY = ('downtime_minutes', 'downtime_pct', 'op_maint_minutes', 'op_maint_pct', 'maintenance_pm_minutes', 'maintenance_pm_pct',
                   'minutes_per_stop', 'downtime_usd')
VOLUME_FAMILY = ('msf', 'gross_sqft', 'lineal_ft')
SPEED_FAMILY = ('speed_run_fpm', 'speed_all_fpm', 'speed_elapsed_fpm', 'speed_to_target_pct', 'run_speed_to_target_pct')


def _family_of(intent):
    """The measures a hand route's answer leads with (a refusal of a reading in the same family never replaces it)."""
    return {'period_speed': SPEED_FAMILY, 'speed': SPEED_FAMILY, 'period_grade': VOLUME_FAMILY, 'volume': VOLUME_FAMILY}.get(intent, ())


def _hand_window(request, context):
    """The window the hand route reads: its first step's, else the selected day (the day path)."""
    plan = request.get('plan') or []
    if plan:
        args = plan[0]['arguments']
        window = args.get('window') or args.get('current_window')
        if window:
            return window['period'], window['anchor']
        return None
    return getattr(context, 'period', 'day'), context.production_day


REJECT_DIMS = ('reject_family', 'reject_reason')
WASTE_FAMILY = ('reject_sqft', 'reject_sheets', 'dry_end_pct', 'dry_end_usd', 'trim_sqft', 'trim_pct', 'trim_usd', 'shear_sqft', 'shear_pct',
                'shear_usd', 'changeover_sqft', 'cutoff_waste_sqft', 'stacker_waste_sqft', 'admin_sqft', 'bale_sqft', 'bale_msf', 'bale_pct',
                'bale_usd', 'unexplained_sqft', 'unexplained_pct', 'unexplained_usd', 'yield_sqft')


def covers(request, reading, context=None):
    """True when the hand router's request answers what the complete reading read: the same window, the same measure family, and
    every split, ranking, share and filter the reading found is one the request applies."""
    q = reading.query
    intent = request.get('intent')
    plan = request.get('plan') or []
    tools = [s['tool'] for s in plan]
    args = plan[0]['arguments'] if plan else {}
    if context is not None:
        hand = _hand_window(request, context)
        if hand is not None and hand != (q.window['period'], q.window['anchor']) and intent not in ('period_quantity', 'period_stops', 'period_trend'):
            return False
        if intent in ('period_quantity', 'period_stops') and hand is not None and hand[0] == q.window['period'] == 'month' \
                and hand[1][:7] != q.window['anchor'][:7]:
            return False   # "stops per day in August" read as September: the month the question named is not the one answered
    group_by = list(q.group_by)
    per_shift = len({s['arguments'].get('shift') for s in plan}) > 1 or (not plan and intent == 'downtime' and len(request.get('shifts') or []) > 1)
    if 'shift' in group_by and per_shift:
        group_by.remove('shift')   # the hand route runs once per shift named ("downtime by reason for first shift and third shift")
    rank, filters = reading.rank, q.filters
    share = q.share if q.share is not None else ('each' if reading.shares else None)
    if intent in ('period_quantity', 'period_stops'):
        return True    # #113's allow-list: every word of the question is the route's own
    if tools and set(tools) == {'get_downtime_pareto'}:
        unit = 'stops' if args.get('measure') == 'count' else 'downtime_minutes'
        return (q.measure == unit and group_by == [args.get('group_by')] and not filters and share is None
                and rank in (None, 'most') and q.limit == semantic.DEFAULT_LIMIT)
    if intent == 'period_review' or (tools and set(tools) == {'get_downtime_review'}):
        # the owner's downtime answer (issue #101): downtime by reason, ranked, maintenance and operator first; never a count, a split
        # by anything else or another filter
        # bare stops ("stops", "stops yesterday") are the owner's downtime default too (the review of PR #155); "most common" is a
        # count, which the review (ranked by minutes) does not rank
        return ((q.measure in DOWNTIME_FAMILY and rank in (None, 'most') or q.measure == 'stops' and rank is None)
                and group_by in ([], ['reason']) and set(filters) <= {'kind'}
                and filters.get('kind') in (None, 'Maintenance', 'Operator') and share is None and q.limit == semantic.DEFAULT_LIMIT)
    if intent in ('period_waste', 'period_quality', 'quality') or (tools and set(tools) == {'get_waste_breakdown'}):
        # the waste breakdown and the shift's rejects: every waste line, each dry-end family and reason with its dollars
        return (q.measure in WASTE_FAMILY and set(group_by) <= set(REJECT_DIMS) and set(filters) <= set(REJECT_DIMS)
                and share is None and rank in (None, 'most'))
    if intent == 'period_grade' and tools == ['get_grade_totals']:
        return q.measure in VOLUME_FAMILY and group_by in ([], ['grade']) and not filters and not rank and share is None
    if intent in ('period_speed', 'speed'):
        return (q.measure in SPEED_FAMILY and group_by in ([], ['grade']) and not filters and share is None
                and (not rank or group_by == ['grade']))
    if intent == 'period_compare':
        return False   # the KPI summary answers "how did we do", never a measure the reading named
    if intent == 'compare':   # the day's shift comparison opens with each shift's speed
        return q.measure in SPEED_FAMILY and set(group_by) <= {'shift'} and not filters and share is None
    if intent == 'downtime':   # the day's downtime breakdown, per shift, by reason, place or kind
        return (q.measure in DOWNTIME_FAMILY + ('stops',) and group_by in ([], [request.get('group_by')]) and rank in (None, 'most')
                and set(filters) <= {'kind'} and filters.get('kind') in (None, request.get('kind')) and share is None)
    return not (group_by or rank or share is not None or filters)


def route(text, context, store, request, refused):
    """What the offline router does with a question the hand router answered (`request`) or refused (`refused`): a semantic
    request when the reading is complete and the hand route does not answer it, a ValueError (the reading's refusal) when the
    question asks a structure the hand route drops, else None (the hand route, or its refusal, stands)."""
    if store is False or not isinstance(text, str) or not text.strip() or re.search(agent.WHY_ASK, agent.correct_typos(text.lower())):
        return None   # store False: a single-day fixture, which no semantic query can read
    reading = read(text, context, store)
    if reading.complete:
        if refused is not None or request is None or not covers(request, reading, context):
            return request_of(reading)
        return None
    if reading.refusal is None:
        return None
    if refused is not None:
        # the generic help ("Offline mode supports ...") answers nothing a ranking, a share or a statistic asked: the reading's
        # refusal names what it could not say and three questions that answer ("best crew"; the review of PR #155)
        if str(refused).startswith('Offline mode supports') and reading.cues & (set(STRONG) - {'statistic'} | {'statistic asked', 'evaluative'}):
            raise ValueError(refusal_text(reading))
        return None
    if request is None:
        return None
    intent = request.get('intent')
    if 'wrong window' in reading.cues and intent not in NEVER_PREEMPT:
        # "stops this year", "downtime over the weekend", "stops on Tuesday" (on a Monday): the hand route answers the selected day
        raise ValueError(refusal_text(reading))
    if reading.cues & {'statistic asked', 'share', 'shift split'} and intent not in NEVER_PREEMPT and reading.measure not in _family_of(intent):
        # a median, an average or a share the model does not read is refused, never answered with the review or the Pareto
        # ("median downtime this week", "what portion of downtime was the stacker"; the coordinator on #144)
        raise ValueError(refusal_text(reading))
    if 'evaluative' in reading.cues and intent in ('overview', 'summary', 'period_overview', 'period_summary', 'period_compare'):
        raise ValueError(refusal_text(reading))   # "worst shift": no measure and no direction, never the selected shift's overview
    if reading.cues & {'who'} and intent not in NEVER_PREEMPT:
        raise ValueError(refusal_text(reading))   # "who had the most stops": never a reason Pareto
    if intent in NEVER_PREEMPT or intent in FLAGSHIP:
        return None    # a flagship answer stands unless a complete reading shows it answers something else
    strong = reading.cues & set(STRONG)
    if intent == 'period_compare' and (reading.measure or strong):
        raise ValueError(refusal_text(reading))   # the KPI summary never answers a measure, a ranking, a share or a statistic
    if strong and 'unknown' not in reading.cues and (reading.measure or reading.cues & {'single', 'per_quantity'}) \
            and reading.measure not in _family_of(intent):
        raise ValueError(refusal_text(reading))
    return None
