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
A refusal offers at most three questions built from the model's own measures, dimensions and values, each of which the
offline router answers as offered (`_answers_as_offered`). `read` returns a `Reading`; `route` is what the offline router asks: answer with the semantic
model, refuse, or leave the question to the hand router (`agent.parse_request`).
"""
import bisect
from dataclasses import dataclass, field
from datetime import date
import re
import threading

from . import agent, semantic

TOKEN = re.compile(r"[a-z0-9]+(?:[-/.'’][a-z0-9]+)*|%|\$|\|")
MAX_PHRASE = 5
# The words that carry no content of their own, beyond agent.FUNCTION_WORDS: question words, copulas and the verbs a count is
# asked with ("had", "ran", "hit"). A word here is never vocabulary on its own (a multi-word phrase may still hold one: "by shift",
# "paper run"). A verb that says what a quantity is (lost, rejected, spent, took, produced, stopped, running) is a content word: read
# below where the measure agrees with it, else the reading refuses it (the coordinator's review of PR #155 at 74cc46f, item 1: "how
# many sheets were rejected" was read as every sheet cut).
FILLER = agent.FUNCTION_WORDS | frozenset((
    'with', 'had', 'having', 'list', 'lists', 'listed', 'chart', 'charts', 'graph', 'plot', 'about', 'across', 'over', 'during', 'within',
    'did', 'whats', 'was', 'were', 'did', 'do', 'have', 'has', 'into', 'out', 'as', 'then', 'by', 'per', 'each', 'every',
    'happened', 'hit', 'see', 'ran'))
# Words that say minutes were lost: with minutes asked and no measure named, the minutes are downtime ("which shift lost the most minutes")
LOSS = ('lost', 'lose', 'losing', 'down')
# The verbs read only where the measure is what they say: a loss is downtime or a waste line ("which shift lost the most minutes",
# "how much trim did we lose"), a reject is a dry-end reject, a thing produced is what ran or the waste it made ("which shift produced
# the most MSF"). Beside a loss or a reject any other measure ("how many sheets did we lose", "how many feet were rejected") is refused
# over a hand route too. The other verbs a quantity is said with (UNREAD_VERBS, and a thing produced that ran no line) the parser does
# not read: with every other word read, the hand route keeps the question only when it answers what the other words ask ("how much
# downtime did we experience": the review), else the reading refuses the verb ("which shift experienced the most stops": never shift 2's
# Pareto).
LOSS_VERBS = ('lose', 'lost', 'losing')
REJECT_VERBS = ('rejected',)
PRODUCE_VERBS = ('produce', 'produced', 'producing', 'making')
UNREAD_VERBS = ('spend', 'spent', 'spending', 'take', 'took', 'takes', 'taking', 'experience', 'experienced', 'stopped', 'running')
# The hand routes that read every word of a question (#113's allow-list): a verb they consumed is theirs ("how many stops did we lose
# to the stacker", "how long did changeovers take")
ALLOW_LISTED = ('period_quantity', 'period_stops')
LOSS_MEASURES = ('downtime_minutes', 'downtime_pct', 'op_maint_minutes', 'op_maint_pct', 'maintenance_pm_minutes', 'maintenance_pm_pct',
                 'minutes_per_stop', 'downtime_usd', 'trim_sqft', 'trim_pct', 'trim_usd', 'shear_sqft', 'shear_pct', 'shear_usd',
                 'changeover_sqft', 'cutoff_waste_sqft', 'stacker_waste_sqft', 'admin_sqft', 'reject_sqft', 'reject_sheets', 'dry_end_pct',
                 'dry_end_usd', 'bale_sqft', 'bale_msf', 'bale_pct', 'bale_usd', 'unexplained_sqft', 'unexplained_pct', 'unexplained_usd')
REJECT_MEASURES = ('reject_sqft', 'reject_sheets', 'dry_end_pct', 'dry_end_usd')
PRODUCE_UNITS = ('setups', 'order changes', 'paper changes', 'ft', 'sq ft', 'MSF', 'sheets', 'cuts', 'MSF/day', 'ft/setup', 'ft/paper change')
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
# The plural nouns of what the records keep as square feet, never counted (agent.NOT_COUNTED refuses "how many shear chops" and "how
# many stacker boards"). Ranked, compared or said against another value or window they ask a count too ("which shift had the most
# chops", "compare shifts on chops", "chops on shift 1 versus shift 2", "which shift had the fewest stacker boards"), so the reading
# refuses with the records' own reason (the coordinator's review of PR #155 at b955ca4, item 1). A waste line's own name ("cutoff waste
# boards", "knife waste boards", "stacker waste boards") and a unit said ("square feet of chops") are the line's square feet; a split
# ("chops by shift", "boards by grade": the sheets) and the singular ("how much chop", "which shift had the most chop") say no count.
UNCOUNTED = ('chops', 'boards')
# The words of a duration the parser does not read as a unit: a ranking of one ("which grade had the most hours", "which grade took the
# most time") is refused over a hand route that does not lead with it, never the grade totals
TIME_WORDS = ('time', 'hours', 'hour', 'hrs')
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
PERCENT_OF_SHIFT = r"\b(?:what\s+)?(?:percent(?:age)?|%)\s+of\s+(?:the|this|our|my)\s+shift\b(?!\s*[1-3])"
PERCENT_OF_SCHEDULED = r"\b(?:what\s+)?(?:percent(?:age)?|%)\s+of\s+(?:the\s+|our\s+)?(?:scheduled\s+(?:time|minutes)|shift\s+minutes)\b"
# The verb after the measure of a share: the value after it is the one whose share is asked ("what percent of maintenance stops
# were at the stacker": the stacker's share of the maintenance stops; Codex on PR #155), a value before it narrows the whole
COPULA = ('was', 'were', 'is', 'are')
# "top 5", "top 25", "top 200", "bottom three": any whole number, so N up to the model's MAX_LIMIT is read in words as in the address
# (the coordinator's review of PR #186, item 4), and 0 or more than MAX_LIMIT is refused, never clamped (item 5)
_UNITS = ('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine')
_TEENS = ('ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen')
_TENS = ('twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
_BELOW_100 = rf"(?:(?:{'|'.join(_TENS)})(?:[\s-](?:{'|'.join(_UNITS)}))?|{'|'.join(_TEENS)}|{'|'.join(_UNITS)})"
NUMBER_WORDS = rf"(?:(?:a|one|two|three|four|five|six|seven|eight|nine)\s+hundred(?:\s+(?:and\s+)?{_BELOW_100})?|{_BELOW_100}|zero)"


def number_of(words):
    """The whole number `words` say ("25", "twenty-five", "a hundred and ten", "zero")."""
    if words.isdigit():
        return int(words)
    total = 0
    for word in re.split(r"[\s-]+", words):
        if word == 'hundred':
            total = (total or 1) * 100
        elif word in _UNITS:
            total += _UNITS.index(word) + 1
        elif word in _TEENS:
            total += _TEENS.index(word) + 10
        elif word in _TENS:
            total += (_TENS.index(word) + 2) * 10
    return total


TOP_N = r"\b(top|bottom)\s+(\d+|" + NUMBER_WORDS + r")\b"
SHIFTS_ALL = r"\b((across|on|for|over|in)\s+)?all\s+(the\s+|three\s+)*shifts\b|\bshifts\s+(together|combined)\b|\bevery\s+shift\s+together\b"
# ... never "the shift with the most", which ranks the shifts (this round's probe: "the shift with the most board rejects" read as the
# selected shift's grades ranked)
THIS_SHIFT = (r"\b(?:(this|that|my|our)\s+shift\b|(the)\s+shift\b(?!\s+(?:with|that\s+(?:had|has)|which\s+(?:had|has))\s+(?:the\s+)?"
              r"(?:most|fewest|least|highest|lowest|biggest|smallest|largest|more|less|top)\b))")
PER_DAY = r"\bper\s+(production\s+)?day\b|\ba\s+day\b"
LOGGED_VERB = r"\b(logged|recorded|entered|reported|noted|written)\b"   # who logged a stop, said as a verb (agent.KIND_LOGGED)
# the window before, beside the asked one: "versus last week", "than the week before", "compare ... to last week"
PREVIOUS = (r"\b(?:versus|vs\.?|compared\s+(?:to|with)|than|against|to|with)\s+(?:the\s+)?(?:last|previous|prior)\s+(?:production\s+)?(?:week|month|day|period)\b"
            r"|\b(?:versus|vs\.?|compared\s+(?:to|with)|than|against)\s+the\s+(?:week|month|day|period)\s+before\b")
# "broken down by", "split by": a split, read as "by"
SPLIT_BY = r"\b(?:broken|break|split)\s+(?:down\s+|out\s+|up\s+)?(?:by|per)\b"
# A window named in words this parser does not apply: refused, never read as the selected window
UNREAD_WINDOW = (agent.UNREAD_WINDOW + r"|\b(this|last|next)\s+year\b|\bin\s+20\d\d\b|\b20\d\d\b|\bweekends?\b|\btoday\b|\btomorrow\b"
                 r"|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b|\b(year|quarter)\b")
# ... and the spans no hand route reads either, which it answered as the selected day or the Period control's window (the coordinator's
# review of PR #186, item 1: "top operator reasons Q3" was the selected day's shift breakdown, "downtime since June" June's review,
# "downtime month-to-date" all of September, "downtime rolling 4 weeks" this week, "January through September" an MSF compare). Each is
# said whole in the refusal ("since june", "month-to-date"), and refused over any hand route that does not read its own window words.
_MONTH = r"(?:(?:" + '|'.join(agent.MONTH_NAMES) + r"|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b\.?)"
_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|thu(?:rs?)?|fri)"
_DATE = (rf"(?:\d{{1,2}}(?:st|nd|rd|th)?(?:\s+of)?\s+{_MONTH}|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\d{{4}}-\d{{2}}-\d{{2}}"
         rf"|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|the\s+\d{{1,2}}(?:st|nd|rd|th))")
_COUNT = (r"(?:\d+|a\s+few|few|a\s+couple(?:\s+of)?|couple(?:\s+of)?|several|(?:a\s+|one\s+)?hundred"
          r"|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?"
          r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)")
_GRAIN = r"(?:production\s+)?(?:days?|weeks?|months?|quarters?|years?|shifts?|hours?)"
UNREAD_SPAN = (
    # a quarter or a half, and the quarter, fiscal year or half to date ("Q3", "in Q2", "QTD", "FYTD", "H1", "the first half")
    r"\bq[1-4]\b|\bquarters\b|\bquarterly\b|\b(?:q|h|fy|f)td\b|\bh[12]\b|\bhalves\b|\bhalf[\s-]+(?:a\s+)?year\b"
    r"|\b(?:first|second|1st|2nd|last|this|next|back|front)\s+half\b"
    # a span from a day, a month or the window before to now ("since June", "since Monday", "since 1 Jan", "since yesterday", "since
    # last week"; a why-question's "since last week" is the speed variance's base, never read here: WHY_ASK), never the "since" of a
    # cause ("since we changed rolls")
    rf"|\bsince\s+(?:the\s+)?(?:{_DATE}|{_MONTH}|{_WEEKDAY}\b|yesterday\b|(?:start|beginning|first)\s+of\s+(?:the\s+)?(?:week|month|year|quarter|period|{_MONTH})\b"
    r"|(?:last|previous|prior)\s+(?:week|month|(?:accounting\s+)?period)\b|q[1-4]\b|(?:19|20)\d\d\b)"
    # a span of months ("January through September", "between June and August", "from June to August", "June-August")
    rf"|\bbetween\s+{_MONTH}\s+(?:and|&)\s+{_MONTH}|\bfrom\s+{_MONTH}\s+(?:to|through|thru|till|until|-|–)\s+{_MONTH}"
    rf"|\b{_MONTH}\s*(?:\s(?:through|thru|till|until)\s|-|–)\s*{_MONTH}"

    # a window to date said with hyphens or a grain ("month-to-date", "week to date"; the year to date is the model's own)
    r"|\b(?:(?:day|week|month|quarter|period)[\s-]+)?to[\s-]+date\b"
    # a rolling or trailing span ("rolling 4 weeks", "trailing 30 days", "4-week rolling"), the last N said in any number words
    rf"|\b(?:rolling|trailing)\s+(?:{_COUNT}[\s-]+)?{_GRAIN}\b|\b{_COUNT}[\s-]+(?:day|week|month)s?[\s-]+(?:rolling|trailing)\b"
    rf"|\b(?:last|past|previous|prior)\s+{_COUNT}\s+{_GRAIN}\b|\b(?:(?:last|past|previous|prior)\s+)?fortnights?\b"
    # no span at all ("lately", "recently"), and a year said as a rate of no money ("top operator reasons annually")
    r"|\b(?:lately|recently|of\s+late)\b|\b(?:annual(?:ly)?|yearly|per\s+annum)\b")
# ... and "June to September" when no comparison is said: "compare June to September" compares two months, as before (_read)
MONTH_TO = rf"\b{_MONTH}\s+to\s+{_MONTH}"
COMPARE_SAID = r"\b(?:compar\w*|versus|vs)\b"
# Money or a loss said: "a year", "per year" or "annually" beside it is the levers' yearly rate ("what is trim costing us per year",
# "how much a year are we losing to downtime"), never a window; without one it is a window the model does not read (item 1: "top
# operator reasons annually" was the selected day's breakdown)
MONEY_SAID = (r"\$|\b(?:cost|costs|costing|costed|dollars?|money|spend|spending|spent|worth|sav(?:e|es|ed|ing|ings)|los(?:e|es|ing|t|s)"
              r"|price[ds]?|pricing|expensive|expense)\b")
# "Versus previous", "compared to prior", "than the previous one": a comparison whose window is no word the model reads (the review of
# PR #186, item 2: refused naming that word, never "'last week' is not a window"); the previous window said with its grain is PREVIOUS's
BARE_PREVIOUS = (r"\b(?:versus|vs\.?|compared?\s+(?:to|with)|comparing\s+(?:to|with)|than|against|and)\s+(?:the\s+)?(previous|prior)\b"
                 r"(?:\s+(?:one|ones)\b)?"
                 # ... bare: nothing after it names what it is ("the previous 4 weeks", "the prior accounting period", "the previous
                 # crew" are their own words, refused or read as before; the reviewer's check of this round)
                 r"(?=\s*(?:$|[?.,!;|]|(?:on|for|in|at|by|across|during|over|with|and|from)\b))")
# The window words the model does read that a comparison or a second window kept the scope from moving to ("last week vs previous",
# "last week and this week"): never called a window the model does not read (item 2)
RELATIVE_READ = r"(?:last|previous|prior)\s+(?:production\s+)?(?:week|month|(?:accounting\s+)?period)"
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
    versus: list = None    # two values of the split compared ("32-C and 26-C", "shift 1 versus shift 2"): the card leads with both
    verb: str = None       # the verb the parser does not read ("took", "spent"): route refuses naming it unless the hand route covers the rest
    default_top: bool = False   # the rows a ranking lists are the dimension's default top N (issue #185: "top reasons" is the top 5)
    top_said: bool = False      # "top N" said: the card says "top N" whatever N, the default limit's 25 too (review of PR #186, item 3)
    bare_rest: object = None    # a bare "versus previous" refused: the reading of the rest of the question (route, _reads_previous)
    # What a question it offers keeps (_answers_as_offered): the window the question's words name, the values it narrowed by and the
    # one shift it named, as far as the reading got before it refused
    window: dict = None
    filters: dict = field(default_factory=dict)
    shift: int = None
    context: object = field(default=None, repr=False, compare=False)   # the scope it was read in, and the store: the check routes there
    store: object = field(default=None, repr=False, compare=False)
    dropped: list = field(default_factory=list, repr=False, compare=False)   # the questions it could have offered that were not answered as offered
    compared: set = field(default_factory=set, repr=False, compare=False)    # the dimension of a value said after "than": compared, no filter

    @property
    def complete(self):
        return self.query is not None and self.refusal is None and not self.unused


class Refused(Exception):
    """A reading the model's words cannot say: `text` and the questions it could offer, of which _offer keeps at most three."""

    def __init__(self, text, suggestions=()):
        super().__init__(text)
        self.text, self.suggestions = text, [s for s in suggestions if s]


class Ask(str):
    """A question a refusal offers, and what it asks: the measure it names, its split and ranking, the window before it compares
    with, a share, and the window, filters and shift it keeps. `window` None is the refusal's own window, else {period, anchor} (anchor
    None: the controls' day) that holds the refusal's (a split by day of one day is offered the week), or, `reads`, one reading of
    window words the refusal could not place ("the period" under the week control); `filters` None is the refusal's own filters;
    `shift` None compares no shift. _offer keeps it
    only when the router answers it as that question (the coordinator's review of PR #155 at 2c28fd3, item 1)."""

    def __new__(cls, text, measure, group_by=(), rank=None, compare=None, share=None, filters=None, shift=None, window=None, reads=False):
        self = super().__new__(cls, re.sub(r'\s+', ' ', text).strip())
        self.measure, self.group_by, self.rank, self.compare, self.share = measure, list(group_by), rank, compare, share
        self.filters = None if filters is None else dict(filters)
        self.shift, self.window, self.reads = shift, window, reads
        return self


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
# A dimension's word whose plural is not that dimension said as a set: "boards" are the sheets cut (the sheets' synonym) and the waste
# lines' boards ("cutoff waste boards"), never the board grades, so a bare "boards" is no split by grade (the coordinator's review of
# PR #155 at b955ca4, item 4: "which shift had the most cutoff waste boards" was read by shift and grade)
NO_SET_PLURAL = ('board',)


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
            if not word.endswith('s') and not word.startswith('by') and not re.search(r'[^a-z ]', word.lower()) \
                    and word.lower() not in NO_SET_PLURAL:
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
    for name, d in mdl.dimensions.items():
        for word in (d.get('rank_by') or {}).get('words', []):
            add(word, ('rank', name))   # "pareto": this dimension ranked, by its default measure when none is named (issue #185)
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
    kind: str           # vocab | rank | speed_rank | frequency | which | group | compare | statistic | evaluative | unit | number | and | verb | filler | unknown
    words: str
    start: int
    entries: list = field(default_factory=list)
    value: object = None
    role: str = None    # what the reading made of it
    said: list = field(default_factory=list)   # the measure entries of a phrase read as a value here ("shear", "order changes")


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
                    'verb' if word in LOSS_VERBS + REJECT_VERBS + PRODUCE_VERBS + UNREAD_VERBS else 'than' if word == 'than' else
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
    return {'day': '', 'week': 'this week', 'month': 'this month', 'fiscal': 'this period', semantic.YTD: 'year to date'}[period]


_YTD = {}


def ytd_pattern(mdl=None):
    """The words of the year to date (the model's `windows` block, issue #185: "YTD", "year to date", "this year", "so far this
    year"), a preposition before them consumed with them ("for this year", "over the year to date")."""
    mdl = mdl or semantic.model()
    if id(mdl) not in _YTD:
        words = sorted(mdl.windows[semantic.YTD].get('words', []), key=len, reverse=True)
        alternatives = [r'[\s-]+'.join(re.escape(t) for t in re.split(r'[\s-]+', w.lower().strip())) for w in words]
        _YTD[id(mdl)] = r"\b(?:(?:for|over|in|during|across|through)\s+)?(?:" + '|'.join(alternatives) + r")\b(?:['’]s\b)?"   # "this year's"
    return _YTD[id(mdl)]


# "A year", "per year" said as a rate, never a window (issue #185)
RATE_SAID = r"\b(?:a|an|per|each|every)\s+(?:year|quarter)\b"
# The windows a question offered in place of a window the model does not read names (issue #185), by the words said: the windows the
# model reads that hold the selected day, the nearest first. Another year, one day and a weekday are offered none.
WINDOW_OFFERS = ((r"\b(19|20)\d\d\b|\b(last|next|previous|prior|past)\s+(\d+\s+|[a-z]+\s+)?years?\b", ()),
                 # the spans of the review of PR #186, item 1: since a weekday is this week; since a month or a day, a span of months,
                 # a quarter, a half or a year said as a rate are the year to date or this month; "lately" this week or this month
                 (rf"\bsince\s+(?:the\s+)?{_WEEKDAY}\b", ('week',)),
                 (r"\bsince\b|\bq[1-4]\b|\bquarter|\b(?:q|h|fy|f)td\b|\bh[12]\b|\bhal(?:f|ves)\b|\bannual|\byearly\b|\bper\s+annum\b",
                  (semantic.YTD, 'month')),
                 (rf"\bbetween\s+{_MONTH}|\bfrom\s+{_MONTH}|{_MONTH}\s*(?:\s(?:through|thru|till|until|to)\s|-|–)", (semantic.YTD,)),
                 (r"\blately\b|\brecently\b|\bof\s+late\b|\bfortnights?\b", ('week', 'month')),
                 (r"\bmonths?\b", ('month', semantic.YTD)), (r"\bweekends?\b", ('week',)), (r"\bweeks?\b", ('week', 'month')),
                 (r"\bdays\b", ('month',)), (r"\b(year|ytd|quarter)s?\b|\bto\s+date\b|\bso\s+far\b|\bnow\b", (semantic.YTD, 'month')))


def _unread_offers(reading, text, pattern, context, store, mdl):
    """The questions a refusal of a window the model does not read offers (issue #185, item 4): the same question over the windows it
    reads that hold the selected day ("... this month", "... year to date"), each one only when the question reads completely there
    (the router checks each as offered, _offer). Never inside a check of a question offered."""
    if getattr(_OFFERING, 'text', None):
        return []
    # the words said, with the preposition, the article and the grain said before them ("for the month to date", "over the last 30 days")
    pattern = r"\b(?:(?:for|over|in|during|across|through)\s+)?(?:(?:the|this|that)\s+)?(?:(?:day|week|month|year|period|quarter)\s+)?" + pattern
    said = re.search(pattern, text)
    if said is None or re.match(r"\s+of\s+(?:the\s+|this\s+|that\s+|a\s+)?(?:shift|day|week|month|run|period)\b", text[said.end():]):
        return []   # "the first half of the shift": no window of the model holds it (the reviewer's check of this round)
    periods = next((periods for words, periods in WINDOW_OFFERS if re.search(words, said[0])), ())
    offers = []
    for period in periods:
        words = _window_words(period)
        swapped = re.sub(r'\s+', ' ', re.sub(pattern, f' {words} ', text)).strip(' ?.!,')
        # ... only when the window offered is the one window the question then names ("so far this week" is never "this week this week")
        rest = re.sub(ytd_pattern(mdl), ' ', swapped)
        if len(agent.windows_said(rest)) != (period != semantic.YTD) or re.search(UNREAD_WINDOW, rest) or re.search(agent.ROLLING_WINDOW, rest) \
                or re.search(UNREAD_SPAN, rest):
            continue
        own = read(swapped, context, store, mdl)
        if not own.complete:
            continue
        q = own.query
        offers.append(Ask(swapped, q.measure, q.group_by, own.rank, q.compare, q.share, filters=q.filters, shift=own.shift,
                          window={'period': period, 'anchor': None}, reads=True))
    # ... checked here, so a window refusal with none to offer says only the window's reason (never what the model keeps of the measure)
    return [ask for ask in offers if _answers_as_offered(ask, reading)]


# The windows the semantic model reads, said in every refusal of one it does not (issue #185; the review of PR #186, item 1)
WINDOWS_READ = ('the windows are the production day, the Mon-Fri week, the calendar month, the 4-4-5 period and the year to date')


def _checked_offers(reading, texts, context, store, mdl):
    """The questions of `texts` a refusal offers: each read completely in the scope its own words name ("speed last month"), and
    answered by the router as that reading (_answers_as_offered). Never inside a check of a question offered."""
    if getattr(_OFFERING, 'text', None):
        return []
    offers = []
    for t in dict.fromkeys(re.sub(r'\s+', ' ', t).strip(' ?.!,') for t in texts):
        if not t:
            continue
        try:
            asked = agent.question_scope(t, context, store.production_days, store)
        except ValueError:
            continue
        own = read(t, asked, store, mdl)
        if not own.complete:
            continue
        q = own.query
        offers.append(Ask(t, q.measure, q.group_by, own.rank, q.compare, q.share, filters=q.filters, shift=own.shift,
                          window=dict(q.window), reads=True))
    return [ask for ask in offers if _answers_as_offered(ask, reading)]


def _before_words(grain):
    """How a question asks a window against the one just before it: 'this week compared with the week before'."""
    return 'compared with the day before' if grain == 'day' else f'this {grain} compared with the {grain} before'


def _bare_previous(reading, bare, text, context, store, mdl):
    """Refuse "versus previous", "compared to prior": the word names no window, so it is the one named, never the window said beside
    it (the review of PR #186, item 2: "'last week' is not a window the semantic model reads" was false). The window it would be is
    the one before the question's own window, or the Period control's; the questions offered are the comparison said with its grain
    ("... compared with the week before") and, when that is two windows before the selected one, the question's own window alone,
    each one only when it answers as offered. Refused over any hand route that does not read its own window words (main answered
    "how did last week compare to previous" with this week's MSF against last week's)."""
    original = agent.correct_typos(text.lower())
    said = agent.windows_said(original)
    period = said[0][3] if said else (getattr(context, 'period', 'day') or 'day')
    grain = {'fiscal': 'period'}.get(period, period)
    # ... over any hand route but the KPI summary, which reads "versus the previous one" itself ("how did this week go versus the
    # previous one": this week against the week before); beside a window before the selected one ("last week vs previous") the scope
    # did not move, and every hand route answers another window ("downtime vs previous" was each shift's speed)
    reading.cues.add('bare previous')
    if re.search(r"(?<!the )\b" + RELATIVE_READ + r"\b", original):
        reading.cues.add('unread window')
    # the question without the word: a hand route that answers it, the window before beside, keeps the question (route)
    reading.bare_rest = read(re.sub(BARE_PREVIOUS, ' ', original, count=1), context, store, mdl)
    texts = [re.sub(BARE_PREVIOUS, f' compared with the {grain} before ', original, count=1),
             re.sub(BARE_PREVIOUS, ' ', original, count=1)]
    raise Refused(f"'{bare[1]}' names no window: the semantic model compares a window with the one just before it, asked as "
                  f"'{_before_words(grain)}'", _checked_offers(reading, texts, context, store, mdl))


def _relative_unmoved(reading, words, text, context, store, mdl):
    """Refuse a window the model reads ("last week") that the scope did not move to because a second window or a comparison is said
    beside it ("top maintenance reasons last week and this week", "how did last week compare"): never "'last week' is not a window the
    semantic model reads" (the review of PR #186, item 2). Two windows of one grain, this one and the one before, are offered as the
    one against the one before it, when that answers as offered."""
    original = agent.correct_typos(text.lower())
    period = agent.grain_of(words)
    grain = {'fiscal': 'period'}.get(period, period)
    reading.cues.add('unread window')
    other = next((w for w in agent.windows_said(original) if w[2].strip() != words and words not in w[2]), None)
    if other is None:
        raise Refused(f"'{words}' is compared with no window the question names: the semantic model compares a window with the one just "
                      f"before it, asked as '{_before_words(grain)}'")
    texts = []
    if other[3] == period and re.fullmatch(rf"(?:(?:in|for|during|over)\s+)?(?:this|the\s+current|current)\s+{grain}", other[2].strip()):
        join = r"\s+(?:and|&|or|plus|vs\.?|versus|with|against)\s+(?:(?:in|for|during|over)\s+)?"
        pair = rf"\b(?:{re.escape(words)}{join}{re.escape(other[2].strip())}|{re.escape(other[2].strip())}{join}{re.escape(words)})\b"
        if re.search(pair, original):
            texts.append(re.sub(pair, f' {_before_words(grain)} ', original, count=1))
    raise Refused(f"'{words}' and '{other[2].strip()}' are two windows: ask one window at a time, or one window against the one just "
                  f"before it, asked as '{_before_words(grain)}'", _checked_offers(reading, texts, context, store, mdl))


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
           id(store), getattr(store, 'content_hash', None), id(mdl), strict,
           bool(getattr(_OFFERING, 'text', None)))   # read inside a check, its own questions are not checked: never kept for outside one
    if key in _READINGS:
        return _READINGS[key]
    reading = _read_once(text, context, store, mdl, strict)
    if len(_READINGS) >= READINGS_KEPT:
        _READINGS.clear()
    _READINGS[key] = reading
    return reading


def _read_once(text, context, store, mdl, strict):
    reading = Reading(question=text, context=context, store=store)
    try:
        try:
            _read(reading, text, context, store, mdl or semantic.model())
        except Refused as exc:
            reading.query = None
            reading.refusal, reading.suggestions = _offer(reading, exc.text, exc.suggestions)
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
    # "how much paper did we waste", "what shear do we waste": "waste" said as the verb of what is wasted is "paper waste", "shear
    # waste" (the coordinator's review of PR #155 at 7b46463, item 2); "how much did we waste" stays every line
    lower = re.sub(r"\b(?:did|do|does)\s+(?:we|you|they|i)\s+waste\b|\b(?:we|you|they|i)\s+wasted\b|\b(?:was|were|got|get|been)\s+wasted\b",
                   ' waste', lower)
    lower = re.sub(r"\b(?:did|do|does)\s+(shift\s*[1-3]|crew\s+[a-c])\s+waste\b", r' waste on \1', lower)   # "how much paper did shift 1 waste"
    used = reading.used
    # a request said around the question ("could you tell me", "I want to know", "can I see", "did we have to", "rack up"): the hand
    # router's own phrases, consumed as it consumes them. "Can", "need", "want", "to" and "up" are not function words (issue #153), so
    # outside these a plan, a capacity or the line running is a word this parser does not read, never dropped
    for pattern in (agent.REQUEST_LEAD, agent.MODAL_YOU, agent.DID_HAVE_TO, agent.UP_PARTICLE):
        lower, _ = _consume(pattern, lower, used, 'request')
    # What the question said that no answer reads (issue #153, read as the hand router reads it): refused once the measure is read, so
    # the questions it offers name it. Who logged a stop is not recorded, its kind is: "the stops the operators logged" is never the
    # Operator kind's stops. Refused over a hand route only when a stop is counted or timed and the words are a verb ("which shift had the
    # most stops logged by operators", "stops the operators did log"), never over "maintenance report this week", "the operator notes"
    # or "downtime by reason, maintenance notes", where they may be a noun.
    said, asked = [], None   # asked: the one window the question's words name when the reading's own is not it (window, words)
    lower, logged = _consume(agent.KIND_LOGGED, lower, used, 'logged')   # the rest is read, so a complete reading is known
    if logged:
        said.append((f"'{logged[0][0].strip()}' could mean who logged the stops, which the records do not keep: a stop's kind (maintenance or "
                     "operator) is kept", lambda m, window: _kinds(mdl, reading, m)))
    # a percent of the shift or of the scheduled time is the measure's percent of the scheduled minutes (downtime %), never a share
    # of the downtime (the review of PR #155: "what percent of the shift was maintenance downtime" is 1.80% of shift 2, the hand
    # router's answer, never maintenance's share of the downtime); "the shift" is still the selected one
    lower, percent_of_shift = re.subn(PERCENT_OF_SHIFT, ' percent this shift ', lower)
    lower = re.sub(PERCENT_OF_SCHEDULED, ' percent ', lower)
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
    if previous and not read_words.get('words'):
        # a window before the selected one compared with the one before it ("last week versus the week before"): two windows before the
        # selected one, as the hand router refuses them; never the selected window's figure
        before = re.search(r"\b(?:the\s+)?(?:last|previous|prior)\s+(?:production\s+)?(?:week|month|period|day)\b", lower)
        if before:
            reading.cues.add('wrong window')
            lower, _ = _consume(re.escape(before[0]), lower, used, 'window')
            # the question offered asks the one window the words name, from the controls ("setups last month": the month before beside
            # it), never this week (the reviewer subagent's check of the fix round on 2c28fd3)
            grain = {'period': 'fiscal'}.get(before[0].split()[-1], before[0].split()[-1])
            day = agent.previous_window_day(grain, anchor, store.production_days)
            if day:
                asked = ({'period': grain, 'anchor': day}, re.sub(r'^the\s+|\s+production(?=\s)', '', before[0].strip()))
            said.append((f"'{before[0].strip()}' and '{previous[0].group(0).strip()}' are two windows before the selected one: ask one window, "
                         "or one window against the one just before it", lambda m, window: [_ask(mdl, reading, m)] if asked else []))
    if re.search(r"\bthan\b", lower):
        # "which grades had more setups than 26-C", "more stops than shift 1", "reasons other than the stacker": a threshold, another
        # value compared or a value excluded, which no query of the model reads; never the value ranked against itself, nor every
        # reason's downtime (the reviewer's checks of c5d840c and 20c833d). Said, so it stands over any other refusal of the words
        reading.cues |= {'said', 'than'}
        tail = lower[re.search(r"\bthan\b", lower).end():]
        # the value or the shift said after "than" is the one compared, never a filter; every other value and shift are kept
        than_shift = bool(set(agent.named_shifts(tail)))
        said.append(("'than' compares with another value, excludes one or names a threshold, which the semantic model does not read: ask "
                     "the split, or two values compared",
                     lambda m, window: [_ask(mdl, reading, m, filters={d: v for d, v in reading.filters.items() if d not in reading.compared},
                                             shift=False if than_shift else None)]))
    # the year to date (issue #185): the model's own window words, read before the words of the windows it does not read ("this year",
    # "ytd", "to date" and "so far" are among those)
    lower, ytd = _consume(ytd_pattern(mdl), lower, used, 'window')
    if ytd:
        reading.cues.add('year to date')   # no hand route reads it: route never lets one answer for it (the reviewer's check of b76f8e4)
    if ytd and read_words.get('words'):
        raise Refused(f"'{read_words['words']}' and '{ytd[0].group(0).strip()}' are two windows: ask one window at a time")
    bare = re.search(BARE_PREVIOUS, lower)
    if bare:
        _bare_previous(reading, bare, text, context, store, mdl)   # "versus previous": the word named, never the window beside it
    # ... and a span whose window words moved the scope ("since yesterday": the scope moved to yesterday and left "since" behind)
    moved_span = next((m for m in re.finditer(UNREAD_SPAN, agent.correct_typos(text.lower()))
                       if read_words.get('words') and read_words['words'] in m[0]), None)
    month_to = None if re.search(COMPARE_SAID, lower) else re.search(MONTH_TO, lower)
    unread = moved_span or re.search(UNREAD_SPAN, lower) or month_to or re.search(UNREAD_WINDOW, lower) or re.search(agent.ROLLING_WINDOW, lower)
    if unread:
        # "a year", "per year" or "annually" said as a rate of money or a loss ("what is trim costing us per year", "dollars a year from
        # side trim": the levers' own yearly figure) is no window: refused by the reading as before, never over a hand route; a window
        # said beside it still is ("what is trim costing us per year this quarter"; the reviewer's checks of b76f8e4 and 5a93655). Said
        # of no money it is a window the model does not read ("top operator reasons annually": the review of PR #186, item 1)
        scan = re.sub(RATE_SAID + r"|\b(?:annual(?:ly)?|yearly|per\s+annum)\b", ' | ', lower) if re.search(MONEY_SAID, lower) else lower
        real = moved_span or re.search(UNREAD_SPAN, scan) or month_to or re.search(UNREAD_WINDOW, scan) or re.search(agent.ROLLING_WINDOW, scan)
        rate, unread = real is None, real or unread
        span = not rate and (re.fullmatch(UNREAD_SPAN, unread[0]) is not None or unread is month_to)   # said whole ("since june", "q3")
        wrong = None if span else re.search(WRONG_WINDOW, lower if rate else scan)
        weekday = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')[date.fromisoformat(context.production_day).weekday()]
        words = unread[0].strip().rstrip('.')
        if words == 'to date':
            words = re.search(r"\b(?:(?:day|week|month|period|quarter)\s+)?to\s+date\b", lower)[0]   # "month to date", said whole
        if wrong and wrong[0] != weekday:
            reading.cues.add('wrong window')
            words = re.sub(r'^the\s+', '', wrong[0].strip())
        if not rate and re.fullmatch(RELATIVE_READ, words):
            # "last week" is a window the model reads: a comparison or a second window kept the scope from moving to it (item 2)
            _relative_unmoved(reading, words, text, context, store, mdl)
        offers = []
        if not rate and not re.fullmatch(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?", words):
            # a window the model does not read is never answered as another one (issue #185, item 4): refused over any hand route
            # that does not read its window words itself (route), with the same question over the windows the model reads. A weekday
            # is the selected day, or a weekday profile ("Mondays"), as before: 'wrong window' when it is another day
            reading.cues.add('unread window')
            said = words if span else unread[0].strip()
            offers = _unread_offers(reading, agent.correct_typos(text.lower()),
                                    r'\b' + r'[\s-]+'.join(re.escape(t) for t in re.split(r'[\s-]+', said)) + r'\b', context, store, mdl)
        raise Refused(f"'{words}' is not a window the semantic model reads: {WINDOWS_READ}", offers)
    named, said_windows = set(), lower
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
    if ytd:
        named.add((semantic.YTD, anchor, None))
    if len(named) > 1:
        raise Refused('The question names more than one window: ask one window at a time, or one window against the one before it')
    said += _two_windows(reading, named, previous, previous_grain, read_words, said_windows, context)
    if named:
        period, anchor, month = next(iter(named))
    elif previous_grain:
        period = previous_grain   # "our speed vs last week": this week against the one before
    reading.window_words = _window_words(period, month)
    reading.window = {'period': period, 'anchor': anchor}
    if previous and read_words.get('words'):
        asked = ({'period': read_words['period'], 'anchor': context.production_day}, read_words['words'])   # the window the words moved to
    if asked:
        reading.window, reading.window_words = asked
    elif read_words.get('words') and read_words.get('period') == period and month is None and not previous_grain:
        # the window the question moved to keeps its own words in a suggestion ("stops by shift last week", never "this week",
        # which in the visitor's scope is another week: the review of PR #155)
        reading.window_words = read_words['words']
    elif not read_words.get('words') and not named and not previous:
        # "yesterday" the scope did not move to, because a comparison word stands elsewhere ("speed against grade target by boards
        # yesterday": agent.RELATIVE_BASE): left unread here, but the questions offered keep it, and the router reads it from the controls
        # (the coordinator's review of PR #155 at 2c28fd3: never the selected day's question)
        rel = next(((m[0], grain) for grain, pattern in agent.RELATIVE_WINDOWS if grain == 'day' for m in [re.search(pattern, lower)] if m), None)
        day = agent.previous_window_day('day', anchor, store.production_days) if rel else None   # under any Period control
        if day:
            reading.window_words, reading.window = rel[0], {'period': 'day', 'anchor': day}
    # ---- the shift: one named (agent.named_shifts), the selected one ("this shift"), or all of them
    compared_to = bool(re.search(r"\bcompar(?:e|ed|ing)\b", lower))
    if compared_to:   # "compare shift 1 to shift 2": the two shifts compared, joined as "and" joins them (the values: below)
        one = r"(?:shift\s*[1-3]|(?:first|second|third|1st|2nd|3rd)\s+shift)\b"
        lower = re.sub(rf"\b({one})\s+to\s+(?=(?:the\s+)?{one})", r"\1 and ", lower)
    lower, unread_shift = _consume(agent.UNREAD_SHIFT, lower, used, 'unread shift')
    if unread_shift:
        raise Refused(f"'{unread_shift[0][0].strip()}' names a shift the semantic model does not read: name it by its number "
                      "('shift 1'), or set the shift control")
    shifts = agent.named_shifts(lower)
    reading.shift = shifts[0] if len(shifts) == 1 else None
    lower, _ = _consume(r"\b(?:(?:on|for|by|in|during|from)\s+)?(?:the\s+)?(?:shift\s*[1-3]|(?:first|second|third|1st|2nd|3rd)\s+shift)\b",
                        lower, used, 'shift')
    lower, this_shift = _consume(r"\b(?:(?:on|for|in|during)\s+)?" + THIS_SHIFT, lower, used, 'shift')
    deictic = False
    if this_shift and context.scope != 'shift' and not shifts and len(this_shift) > percent_of_shift:
        # "this shift", "my shift" with All shifts selected has no referent (issue #153, row 8): never the shift control's shift. Refused
        # over a hand route only when the other words read completely ("setups by grade this shift": never the grades' MSF); "how did
        # this shift go versus the previous one" keeps every shift's KPIs
        deictic = True
        said.append((f"'{this_shift[-1][0].strip()}' names the selected shift, and no shift is selected: choose one in the shift control, "
                     "or name it", lambda m, window: [_ask(mdl, reading, m, shift=context.shift), _ask(mdl, reading, m, 'shift')]))
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
        n = number_of(top[2])
        if not 1 <= n <= semantic.MAX_LIMIT:
            # "top 0", "top 500": no ranking lists them, so the question is refused naming N, never clamped to another N (the review
            # of PR #186, items 4 and 5: "top 0" answered the top 1, and its headline named no reason); refused over any hand route
            reading.cues |= {'rank', 'top refused'}
            offers = [] if n == 0 else _checked_offers(reading, [re.sub(TOP_N, f'{top[1]} {semantic.MAX_LIMIT}', agent.correct_typos(
                text.lower()), count=1)], context, store, mdl)
            raise Refused(f"'{top[0].strip()}' asks for {'no rows' if n == 0 else 'more rows than a ranking lists'}: a ranking lists "
                          f"from 1 to {semantic.MAX_LIMIT} rows", offers)
        limit, rank = n, ('most' if top[1] == 'top' else 'least')
        reading.cues.add('rank')
        reading.top_said = True
        lower, _ = _consume(TOP_N, lower, used, 'rank')
    lower, per_day = _consume(PER_DAY, lower, used, 'per day')
    lower = re.sub(SPLIT_BY, ' by ', lower)
    lower = re.sub(r"\bbreak\s?downs?\b", ' zbreakdownz ', lower)   # a split asked ("downtime breakdown"), never the Maintenance kind's synonym
    # "cost the most minutes": the verb of the measure said after it; "which reason cost us the most" names no other, so it asks the money
    # (the review of PR #186, item 6: priced downtime, never the minutes)
    lower = re.sub(r"\b(cost|costs|costing)(\s+(?:us|me))?\s+(?=(the\s+)?(most|least|more|less)\s+"
                   r"(?!(?:in|at|on|for|from|by|of|during|over|across|with|to|among|between)\b)[a-z$%])", ' ', lower)
    lower = re.sub(r"\b(cost|costs|costing)\s+(?:us|me)\s+(?=(the\s+)?(most|least|more|less)\b)", r'\1 ', lower)
    lower, connectors = _consume(CONNECTOR, lower, used, 'reason cue')
    # ---- the words, longest vocabulary phrase first
    vocab = vocabulary(store, mdl)
    items = _items(lower, vocab)
    if compared_to:
        # "compare 32-C to 26-C", "compare the stacker to the splicer": a "to" between two values of one dimension, with a comparison
        # said, joins them as "and" does. "To" is not a function word (issue #153: "to go", "have to run"); anywhere else it is unread
        for i, item in enumerate(items):
            after = next((it for it in items[i + 1:] if not (it.kind == 'filler' and it.words in ('the', 'a', 'an', 'our'))), None)
            before = _prev(items, i)
            if item.words == 'to' and item.kind == 'unknown' and before is not None and after is not None and \
                    {e[1] for e in _entries(before, 'value')} & {e[1] for e in _entries(after, 'value')}:
                item.kind = 'and'
    for i, item in enumerate(items):
        # "how much did we lose to warp", "minutes lost to the stacker": "to" after a loss verb is the cue of the value after it, and so
        # is a "to" after the loss ranked or measured ("which shift lost the most to warp", "lost the most minutes to the stacker"; the
        # review of b955ca4, item 3)
        after = next((it for it in items[i + 1:] if not (it.kind == 'filler' and it.words in ('the', 'a', 'an', 'our'))), None)
        j = i - 1
        while j >= 0 and (items[j].kind in ('rank', 'filler', 'unit') or
                          items[j].kind == 'vocab' and len(_entries(items[j], 'measure')) == len(items[j].entries)):
            j -= 1
        if item.words == 'to' and item.kind == 'unknown' and j >= 0 and items[j].words in LOSS_VERBS and after is not None:
            # the parser's own reading, which no hand route has: refused over one when the reading is refused ("which shift lost the
            # most to changeovers", "to speed", "to slow running": never the side trim, the grade totals or one shift's speed)
            reading.cues.add('lost to')
            if after.kind == 'vocab':
                item.kind, item.role = 'filler', 'reason cue'   # ... before a value or a measure ("which shift lost the most to trim")
    a = {'rank': rank, 'limit': limit, 'top_said': bool(top), 'per_day': bool(per_day), 'reason_cue': bool(connectors), 'period': period, 'anchor': anchor,
         'compare': compare, 'shifts': shifts, 'this_shift': bool(this_shift), 'all_shifts': bool(all_shifts), 'ask_unit': ask_unit,
         'ask_often': ask_often, 'loss': bool(set(tokens_of(text)) & set(LOSS))}
    try:
        _assign(reading, items, mdl, store, a, context)
    except (Refused, semantic.SemanticError) as exc:
        # what the words asked that a refusal fired before reading: a verb, a count of chops or boards, a line's waste (route sees it)
        _verb_cues(reading, items, mdl, a)
        late = _uncounted(reading, items, a, exc) if not said else None
        _line_waste(reading, items)
        if late is not None:
            raise late from exc
        if not said:
            raise
        reading.query = None
    if deictic and reading.query is not None:
        reading.cues.add('said')
    if logged and reading.measure in mdl.measures and mdl.measure_entities(reading.measure)[0] == 'stop' \
            and (re.search(LOGGED_VERB, logged[0][0]) or re.search(r"\b(did|do|does)\b.*\b(log|record|enter|report|note)\b", text.lower())):
        reading.cues.add('said')
    if said:
        text, asks = said[0]
        measure = reading.query.measure if reading.query else reading.measure if reading.measure in mdl.measures else 'stops'
        reading.query = None
        raise Refused(text, asks(measure, reading.window_words))


def _verb_cues(reading, items, mdl, a):
    """The cues of a verb that an earlier refusal fired before _assign read it (the coordinator's review of PR #155 at b955ca4, item 3:
    "which grade took the most minutes" was refused as naming no measure before 'took' was read, and route let the grade totals
    answer). What _assign would have made of it once the measure was known:

    - a loss or a reject said of what ran ("which shift lost the most feet to warp"), or of no measure at all ("which grade lost the most
      time"; money lost is the money answer's own), is 'loss': refused over every hand route but #113's allow-listed ones; a speed lost
      keeps its own refusal and the hand route's speed answer;
    - a verb the parser does not read ("took", "spent", and "produced" of what ran no line) is 'verb' when the other words name what is
      asked, a measure, a unit or the time ("which grade took the most minutes", "which grade spent the most time down"): route keeps
      the hand route only when it answers them without the verb ("which reason took the most minutes": the Pareto), else refuses
      naming the verb. With nothing named ("which grade were we running the most", "how fast are we running this grade") or only money
      ("what did slow running cost us": the levers) the hand route stands, as before."""
    measure = reading.measure if reading.measure in mdl.measures else None
    units = [u for u in a.get('units') or [] if u != '$']
    leftover = list(a.get('leftover') or [])
    money = '$' in (a.get('units') or [])
    named = any(_entries(it, 'measure') for it in items)
    if any(w in TIME_WORDS for w in leftover):
        reading.cues.add('unit')   # "which grade had the most hours", "the most time down": a duration asked, which a ranking ranks
    if any(w not in TIME_WORDS for w in leftover):
        return   # another word the parser does not read ("how fast are we running between changeovers"): the hand route decides
    asked = measure is not None or units or leftover or any(it.kind == 'statistic' for it in items)
    for item in items:
        if item.kind != 'verb' or item.role == 'verb':
            continue
        unit = mdl.measures[measure]['unit'] if measure else None
        if item.words in LOSS_VERBS + REJECT_VERBS:
            read_of = REJECT_MEASURES if item.words in REJECT_VERBS else LOSS_MEASURES
            if measure is not None and measure not in read_of and unit != 'ft/min' or measure is None and not named and not money:
                reading.cues.add('loss')
        elif item.words in UNREAD_VERBS or item.words in PRODUCE_VERBS and (measure is None or unit not in PRODUCE_UNITS):
            # "which grade took the most", "which grade stopped the most": no hand route answers what took, spent or stopped; "running"
            # and "produced" with nothing named are what ran ("which grade were we running the most": the grade totals)
            if asked or item.words in UNREAD_VERBS and item.words != 'running':
                reading.cues.add('verb')
                reading.verb = reading.verb or item.words


def _uncounted(reading, items, a, exc):
    """The records' refusal of a ranking, a comparison or a 'vs' of the chops or the boards (UNCOUNTED) when another refusal fired
    first ("which shift had the most chops at the shear", "which shift had the most reject boards", "which boards had the most stops":
    the reviewer's check of 0d23e4a), with that refusal's questions, each read completely; else None."""
    if 'uncounted' in reading.cues:
        return None
    earlier, reading.dropped = reading.dropped, []   # the boards' refusal offers questions of its own
    board = _boards_dimension(reading, items, a)
    if board is not None:
        reading.cues.add('uncounted')
        return board
    reading.dropped = earlier + reading.dropped
    noun = _uncounted_noun(items, a, reading.question)
    if noun is None:
        return None
    reading.cues.add('uncounted')
    return Refused(next(text for pattern, text in agent.NOT_COUNTED if re.search(pattern, noun)), getattr(exc, 'suggestions', []))


SPLIT_WORDS = ('by', 'each', 'every', 'across', 'between')


def _boards_dimension(reading, items, a):
    """The refusal of "boards" said as the dimension of another measure ("which boards had the most downtime", "downtime by boards",
    "which boards were slowest"): the records count no boards, and "boards" is no dimension either, so the grade is offered (the
    coordinator's review of PR #155 at 7b46463, item 4); else None, and a count of the boards keeps the records' reason."""
    mdl = semantic.model()
    for k, it in enumerate(items):
        if it.kind != 'vocab' or it.words != 'boards':
            continue
        prev = _prev(items, k)
        after = next((o for o in items[k + 1:] if not (o.kind == 'filler' and o.words in ARTICLES + ('with', 'that', 'had', 'has'))), None)
        split = prev is not None and (prev.kind in ('which', 'compare') or prev.words == 'what' or prev.kind == 'group' and prev.words in SPLIT_WORDS)
        ranked = after is not None and (after.kind in ('rank', 'speed_rank') or after.kind == 'group' and after.words == 'by' and a.get('rank'))
        if not (split or ranked):
            continue   # "boards with the most downtime", "top 3 boards by downtime": the boards ranked, as "which boards" ranks them
        # a measure the reading read first ("which boards had the most order change stops": the stops), else one said as a value
        # ("which boards had the most shear": the shear); never a dimension's word ("which day had the most boards" counts boards by day),
        # nor a value said as one ("compare boards at the shear on shift 1 versus shift 2" compares a count of boards)
        # ... or a phrase of the model that is a measure and a dimension said as a set ("production days by boards": the days run)
        said = [o.said if (not _entries(o, 'dimension') or len(o.words.split()) > 1 and o.words.endswith('s'))
                and getattr(_prev(items, j), 'words', None) not in PLACE_CUES + REASON_CUES else [] for j, o in enumerate(items)]
        other = next((e[1] for entries in ([_entries(o, 'measure') for o in items], said) for o, found in zip(items, entries)
                      if o is not it and o.kind == 'vocab' for e in found if e[1] != 'sheets'), None)
        unit = next((u for u in a.get('units') or [] if u not in ('count', 'min')), None)
        if other is not None and unit is not None:
            other = _family(mdl, other, unit) or other   # "shear % by boards": the shear's percent, as _assign reads the unit said
        unkept = None
        if other is not None and a.get('per_day') and mdl.measures[other]['unit'] != 'ft/min':
            # "volume per production day by boards": the MSF per day, never the MSF; a measure with no ratio to the days ("stops per
            # production day") is offered in no other words (the reviewer subagent's re-check of e51adf7)
            other, unkept = _ratio(mdl, other, 'production_days') or other, None if _ratio(mdl, other, 'production_days') else other
        speed = next((SPEED_RANK[o.words] for o in items if o.kind == 'speed_rank'), None)
        if other is None and speed:
            other = 'speed_run_fpm'
        timed = other is None and ('min' in (a.get('units') or []) or a.get('ask_unit') == 'min')   # "which boards took the most minutes"
        if other is None and not timed:
            continue
        rank = (a.get('rank') or speed or next((RANK[o.words] for o in items if o.kind == 'rank'), None)
                or ('most' if prev is not None and (prev.kind == 'which' or prev.words == 'what') else None))
        if unkept:
            reading.dropped.append(_ask(mdl, reading, unkept, 'grade'))   # none offered: the refusal says what the model keeps of it
            return Refused("'boards' is not a dimension the semantic model reads: boards are ranked or split by their grade", [])
        if not reading.filters and other is not None:
            # a refusal fired before the values were read ("which boards had the most operator stops": two measures): the questions
            # offered keep the values said beside the measure, never every stop's ranking (the coordinator's review of 2c28fd3, item 1)
            reading.filters = _values_beside(mdl, items, other, it)
        if timed:
            asks = _suggest(mdl, reading, None, 'grade', rank or 'most', measures=_minutes_of(mdl, None))
        else:
            no_grade = semantic._usable(mdl, other, 'grade', as_filter=False)
            if no_grade is not None:
                # "which boards had the most bale": the measure carries no grade, so none is offered; the model's reason, and the
                # first dimension it is split by (the coordinator's review of PR #155 at 97b81f4, item 2: never "bale by grade",
                # which the grade totals answer)
                m = mdl.measures[other]
                why = no_grade[0].split(';')[0].replace(f' ({other})', '').replace(m['label'], _measure_word(mdl, other), 1)
                dim = next((d for d in no_grade[1] if mdl.dimensions[d]['type'] != 'time'), None)
                return Refused(f"'boards' is not a dimension the semantic model reads, and no grade stands in for it: {why}",
                               _suggest(mdl, reading, other, dim, rank) if dim else [_ask(mdl, reading, other)])
            asks = _suggest(mdl, reading, other, 'grade', rank)
            if rank:
                asks = [q for q in asks if q.rank][:1] + [_ask(mdl, reading, other, 'grade')]
        return Refused("'boards' is not a dimension the semantic model reads: boards are ranked or split by their grade", asks)
    return None


def _values_beside(mdl, items, measure, boards):
    """{dimension: value} of the values said beside `measure` when a refusal fired before _assign read them: each word that names one
    value of a dimension the measure can be filtered by, its dimension chosen as _assign chooses it (the cue before it, the kind first,
    an exact name), skipping a word that is the measure's own and one after a split word ("by splicer"). A word that names values of
    two dimensions ("glue": a stop place and a stop reason) is kept as UNPLACED, which no question offered can keep: none is offered."""
    found = {}
    for j, o in enumerate(items):
        if o is boards or o.kind != 'vocab' or any(e[1] == measure for e in _entries(o, 'measure') + o.said):
            continue
        candidates = [e for e in _entries(o, 'value') if semantic._usable(mdl, measure, e[1], as_filter=True) is None]
        prev = _prev(items, j)
        if not candidates or _entries(o, 'dimension') or prev is not None and prev.kind == 'group':
            continue
        dims = list(dict.fromkeys(e[1] for e in candidates))
        cue = prev.words if prev is not None else None
        if len(dims) > 1:
            narrowed = (['place'] if cue in PLACE_CUES and 'place' in dims else ['reason'] if cue in REASON_CUES and 'reason' in dims else
                        [d for d in DIMENSION_FIRST if d in dims][:1] or list(dict.fromkeys(e[1] for e in candidates if e[3])))
            dims = narrowed if len(narrowed) == 1 else dims
        if len(dims) == 1 and dims[0] not in found:
            found[dims[0]] = next(e[2] for e in candidates if e[1] == dims[0])
        elif len(dims) > 1:
            found[UNPLACED] = o.words
    return found


UNPLACED = '?'   # a value said that the reading could not place: the filters of no question offered match it


def _uncounted_noun(items, a, question, compared=False):
    """The plural noun of UNCOUNTED the question counts, ranks, compares or splits by ("how many boards by grade", "which shift had
    the most chops", "chops on shift 1 versus shift 2", "downtime by boards"), else None: never a waste line's own name ("knife waste
    boards"), nor with a unit said ("square feet of chops")."""
    if a.get('units'):
        return None
    for k, it in enumerate(items):
        noun = next((w for w in it.words.split() if w in UNCOUNTED), None) if it.kind == 'vocab' and 'waste' not in it.words.split() else None
        if noun is None or _ratio_said(items, k) or a.get('per_day'):
            continue   # "how many boards per setup", "... per day": a ratio the records do not keep, the hand router's own reason
        if (compared or a['rank'] or a['compare'] or len(a['shifts']) > 1 or any(i.kind in ('rank', 'which', 'compare') for i in items)
                or re.search(COUNT_ASK, question, re.I) or k and items[k - 1].kind == 'group'):
            return noun
    return None


def _ratio_said(items, k):
    """True when the noun at `k` is followed by "per" and a measure or a dimension "per" divides by ("boards per setup", "boards per
    order"): a ratio. "Per" before any other dimension is a split ("boards per grade"), as "by grade" is (the reviewer's check of this
    round)."""
    if k + 1 >= len(items) or items[k + 1].words != 'per':
        return False
    rest = [it for it in items[k + 2:] if not (it.kind == 'filler' and it.words in ARTICLES)]
    nxt = rest[0] if rest else None
    if nxt is not None and (nxt.kind in ('unit', 'number') or nxt.words in TIME_WORDS):
        return True   # "boards per hour", "per minute", "per 1,000 feet"
    return nxt is not None and nxt.kind == 'vocab' and (bool(_entries(nxt, 'measure')) or any(e[1] in PER_RATIO_DIMS for e in _entries(nxt, 'dimension')))


def _line_waste(reading, items):
    """'line waste' when "waste" is said after a value or a measure of the model that names no line ("paper waste", "splice waste"):
    only a bare "waste" is every line, so the waste breakdown never answers it in the reading's place (the reviewer's check of 0d23e4a,
    item 2). A word the model does not hold before it ("this week's waste", "our main waste", "excess waste") leaves a bare "waste",
    every line, as on main (its re-check of 87f0146). "Waste" said as the verb of a value ("how much paper did we waste") is read beside
    it, as "paper waste" (`_read`; the coordinator's review of 7b46463, item 2)."""
    for i, item in enumerate(items):
        if item.kind == 'vocab' and item.words in WHOLE_WASTE and i:
            before = items[i - 1]
            if before.kind == 'vocab' and (_entries(before, 'value') or _entries(before, 'measure')):
                reading.cues.add('line waste')
        elif item.kind == 'vocab' and i and any(e[1] in WASTE_FAMILY for e in _entries(item, 'measure')):
            # "knife rejects": a value before a waste line that the line is not recorded by (a stop reason before the dry-end rejects),
            # never every line led by side trim (the reviewer's check of this round); "moisture rejects" is the family's own
            before, mdl = items[i - 1], semantic.model()
            line = next(e[1] for e in _entries(item, 'measure') if e[1] in WASTE_FAMILY)
            values = _entries(before, 'value')
            if before.kind == 'vocab' and values and not _entries(before, 'measure') and \
                    all(semantic._usable(mdl, line, e[1], as_filter=True) is not None for e in values):
                reading.cues.add('line waste')


def _two_windows(reading, named, previous, previous_grain, read_words, said, context):
    """The windows said that are not one window, or one window and the one just before it (issue #153, read as the hand router reads
    them), as [(text, asks)]: a window of another length compared ("this week versus last month"), a month named beside the selected
    day's month before it ("August versus last month" in September), a comparison with a window before one the question already moved
    to ("last week versus the week before"), and "the period" or "this day" under a ranged Period control (the 4-4-5 period or the
    control's window; a production day its address cannot name). Each answers another window than the one asked: refused over a hand
    route too (the cue 'wrong window')."""
    selected = getattr(context, 'period', 'day') or 'day'
    words = previous[0].group(0).strip() if previous else None
    found = []
    mdl = semantic.model()

    def at(m, words, period):   # one reading of window words the reading cannot place: the 4-4-5 period, or the control's window
        return _ask(mdl, reading, m, words=words, window={'period': period, 'anchor': None}, reads=True)

    if previous and read_words.get('words'):
        found.append((f"'{read_words['words']}' and '{words}' are two windows before the selected one: ask one window, or one window "
                      "against the one just before it", lambda m, window: [_ask(mdl, reading, m)]))
    elif previous and named:
        grain, start, name = next(iter(named))
        before = {'fiscal': 'period'}.get(previous_grain, previous_grain)
        asked = _window_words(grain, name) or 'the day'
        if grain != previous_grain:
            found.append((f"'{asked}' and '{words}' are windows of different lengths: ask one window, or one window against the one "
                          "just before it", lambda m, window: [_ask(mdl, reading, m)]))   # the window named, never another
        elif name and start[:7] != context.production_day[:7] and re.search(r"\b(last|previous|prior)\s+month\b", words):
            found.append((f"'last month' is the month before the selected day's, so '{asked} {words}' names two months that are not one "
                          "month and the one before it: ask one month, or this month against the one before it",
                          lambda m, window: [_ask(mdl, reading, m)]))
    elif selected != 'day' and len(named) == 1 and not previous and not read_words.get('words') \
            and not re.search(r"\b(fiscal|accounting|4-4-5|445|p(1[0-3]|[1-9]))\b", said):
        grain = next(iter(named))[0]
        the_period, this_day = re.search(agent.THE_PERIOD_SAID, said), re.search(agent.THIS_DAY_SAID, said)
        if grain == 'fiscal' and the_period and selected in ('week', 'month'):
            found.append((f"'{the_period[0]}' could be the 4-4-5 accounting period or the {agent.PERIOD_WORDS[selected]} the Period "
                          "control selects", lambda m, window: [at(m, 'this period', 'fiscal'), at(m, f'this {selected}', selected)]))
        elif grain == 'day' and this_day:
            found.append((f"'{this_day[0]}' names the selected production day, and the Period control is on the "
                          f"{agent.PERIOD_WORDS[selected]}: set it to Day",
                          lambda m, window: [at(m, f"this {'period' if selected == 'fiscal' else selected}", selected)]))
    if found:
        reading.cues.add('wrong window')
    return found

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
    # the shear is said as "shear", never as "shear chops": a ranking or a comparison of the chops is a count the records do not hold
    # (UNCOUNTED), so a suggestion naming them would be refused in its turn; the priced shear is its synonym "shear cost" for the same
    # reason (this round's probe of the review of PR #155 at 97b81f4: "which grade had the most shear chops, priced" offered itself)
    # The production days are "days run" ("production days" reads as the volume split by day), the speed against grade target
    # "attainment" ("against … last week" leaves the window unmoved), and the running one "running attainment" (the coordinator's review
    # of PR #155 at 2c28fd3, item 2)
    words = {'downtime_minutes': 'minutes of downtime', 'stops': 'stops', 'setups': 'setups', 'msf': 'MSF', 'reject_sqft': 'dry-end rejects',
             'speed_run_fpm': 'speed', 'shear_sqft': 'shear', 'shear_usd': 'shear cost', 'running_minutes': 'run minutes',
             'elapsed_minutes': 'setup minutes', 'production_days': 'days run', 'speed_to_target_pct': 'attainment',
             'run_speed_to_target_pct': 'running attainment'}
    if name in words:
        return words[name]
    # a word that reads back as this measure: a percent or the bale in MSF shares its label with the square feet ("bale"), so its own
    # synonym says it ("bale %", "trim percent"; the reviewer's check of this round: "which shift had the most bale" answered sq ft)
    return next((w for w in [_strip_parens(m['label']).lower(), *m.get('synonyms', [])] if mdl.measure_words.get(w) == name),
                _strip_parens(m['label']).lower())


def _dim_word(mdl, dim, whole=False):
    """How a refusal or a suggestion says a dimension. `whole` names a stop's reason, place and kind as the stop's ("is not recorded by
    stop reason"): the dry-end rejects are recorded by reject reason, so a bare "reason" there is false (the coordinator's review of
    PR #155 at 7b46463, item 3)."""
    if whole and dim in ('reason', 'place', 'kind'):
        return f'stop {dim}'
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
        # ... across a value of another dimension that narrows the noun phrase ("which maintenance reason", "which 26-C shift": issue
        # #185, item 1)
        if it.role in ('measure', 'unit', 'value') or it.kind in ('frequency', 'unit') or (it.kind == 'filler' and it.words in ARTICLES):
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


def _ranked_measure(items, item):
    """True when a phrase that names a measure and a dimension said as a set ("production days": the days run, and the production days
    split) is ranked right after its ranking word beside another dimension asked as the split ("which grade had the most production
    days"): the thing counted, never a second split read with the volume its first word names (the reviewer subagent's check of the fix
    round on 2c28fd3: it offered the MSF of each grade and day)."""
    k, mdl = items.index(item), semantic.model()
    prev = _prev(items, k)
    measure = next((e[1] for e in _entries(item, 'measure')), None)
    splits = [e[1] for o in items if o is not item and o.kind == 'vocab' and not _entries(o, 'measure') for e in _entries(o, 'dimension')[:1]]
    # ... and only where the measure is recorded by that split ("which reason had the most paper runs" keeps its own refusal)
    return (prev is not None and prev.kind == 'rank' and len(item.words.split()) > 1 and measure is not None
            and (any(o.words in WHO for o in items) or bool(splits) and all(semantic._usable(mdl, measure, d, as_filter=False) is None for d in splits)))


def _joined(items, i):
    """(the 'and', the dimension before it) when the dimension at `i` is joined by "and" to the dimension before it ("by shift and
    grade", "which shift and grade"), else None: the two are one split, as "by shift by grade" is."""
    j = i - 1
    while j >= 0 and items[j].kind == 'filler' and items[j].words in ARTICLES:
        j -= 1
    if j < 1 or items[j].kind != 'and' or items[j].words != 'and':
        return None   # "shift or grade" asks one or the other, never both (the reviewer's check of this round)
    k = j - 1
    while k >= 0 and items[k].kind == 'filler' and items[k].words in ARTICLES:
        k -= 1
    return (j, k) if k >= 0 and items[k].role == 'dimension' else None


def _modifier(items, i):
    """True when the dimension at `i` is said bare before the measure it qualifies ("the most board stops", "the most wet end
    rejects", "top 3 shifts by board stops"): singular, no value after it, no split word of its own. Beside a ranking or a split of
    another dimension it is no second split (the coordinator's review of PR #155 at 7b46463, item 1)."""
    item = items[i]
    nxt = items[i + 1] if i + 1 < len(items) else None
    return (item.words not in INHERENT and not item.words.endswith('s') and nxt is not None
            and (nxt.role == 'measure' or nxt.kind == 'unit'))


_OFFERING = threading.local()   # per thread: the server parses under two locks at once (the reviewer's re-check of 709035c)


# ------------------------------------------------------------------ the questions a refusal offers
#
# A refusal offers at most three questions, and offers one only when the offline router, asked it in the refusal's own controls,
# answers it as the question it offers (the coordinator's review of PR #155 at 2c28fd3, item 1): the semantic query of the measure it
# names over the refusal's window, with its split, ranking and filters; or a hand route whose answer opens with that measure
# (_hand_leads). Any other question is dropped: "order changes yesterday" is the orders card's 95 order fragments, never the 116 order
# changes; "setup minutes yesterday" the day's 49 setups; "maintenance + pm downtime this week" the review's maintenance and operator
# downtime. When none is kept, the refusal says what the model keeps of the measure. The check is offline: the router and the parser,
# a few milliseconds a question, each verdict kept (_ANSWERED).

def _ask(mdl, reading, measure, dim=None, rank=None, words=None, window=None, compare=None, filters=None, shift=None, word=None, reads=False):
    """One question offered, in the model's own words: `measure` (said `word`, else _measure_word), split by `dim` (a dimension or a
    list of two) or ranked on it (`rank` most or least), over `words` (the refusal's window words when None; `window` the spec when
    they name a window that holds the refusal's, or, `reads`, one reading of window words it could not place), keeping the refusal's
    filters and the one shift it named unless `filters` or `shift` says otherwise (`shift` False: none)."""
    dims = [dim] if isinstance(dim, str) else list(dim or [])
    filters = dict(reading.filters if filters is None else filters)
    shift = None if shift is False or 'shift' in dims else (reading.shift if shift is None else shift)
    word = word or _measure_word(mdl, measure)
    said = f'{word} {_narrowing(mdl, filters, shift)}'
    words = reading.window_words if words is None else words
    unit = mdl.measures[measure]['unit']
    if rank and dims:
        d = _dim_word(mdl, dims[0])
        if measure == 'speed_run_fpm':   # "fastest" ranks the speed without stops; another speed is named ("speed with stops")
            text = f"which {d} {'was the slowest' if rank == 'least' else 'was the fastest'} {_narrowing(mdl, filters, shift)} {words}"
        elif unit == 'ft/min':
            text = f"which {d} had the {'lowest' if rank == 'least' else 'highest'} {said} {words}"
        else:
            verb = 'had the most' if rank == 'most' else 'had the fewest' if unit in COUNT_UNITS else 'had the least'
            text = f'which {d} {verb} {said} {words}'
    elif dims:
        text = f"{said} by {' and '.join(_dim_word(mdl, d) for d in dims)} {words}"
    else:
        text = f'{said} {words}'
    return Ask(text, measure, dims, rank if dims else None, compare, filters=filters, shift=shift, window=window, reads=reads)


def _narrowing(mdl, filters, shift):
    """The words that keep a refusal's filters and the shift it named in a question it offers: "for order change", "at the stacker",
    "for maintenance", "for 32-C", "on shift 1" (each read back by the parser; _offer drops a question that does not)."""
    parts = []
    for dim, value in (filters or {}).items():
        if dim == UNPLACED:
            continue
        if dim == 'place':
            parts.append(f'at the {str(value).lower()}')
        elif isinstance(value, (int, float)):
            parts.append(f'for {_dim_word(mdl, dim)} {value:g}')
        else:
            parts.append(f'for {_label_word(mdl, dim, value)}')
    if shift:
        parts.append(f'on shift {shift}')
    return ' '.join(parts)


def _kinds(mdl, reading, measure):
    """The questions offered for who logged a stop: the measure asked of each kind and split by kind, keeping the refusal's other
    filters and the shift it named; none for a measure no stop kind narrows."""
    others = {d: v for d, v in reading.filters.items() if d != 'kind'}
    if semantic._usable(mdl, measure, 'kind', as_filter=True) is not None:
        # a measure no stop kind narrows ("dry-end rejects logged by operators"): none offered, never the stops in its place
        reading.dropped.append(_ask(mdl, reading, measure, filters=others))
        return []
    return [_ask(mdl, reading, measure, filters={**others, 'kind': kind}) for kind in ('Operator', 'Maintenance')] + \
        [_ask(mdl, reading, measure, 'kind', filters=others)]


def _held(mdl, reading, measure, dim, period):
    """The split by `dim` over the `period` window that holds the refusal's ("which day had the most side trim yesterday": side trim by
    day over yesterday's week, said from the controls), or None when no words name that window."""
    around = _around(reading, period)
    return _ask(mdl, reading, measure, dim, words=around[0], window=around[1]) if around else None


def _around(reading, period):
    """(words, window) of the `period` window that holds the refusal's own ("a split by day needs a longer window": the week of the day
    asked), said from the controls as "this week" or "last week" ("this month", "last month"), or None when no such words name it."""
    from .record_store import parse_window
    controls = _controls(reading.context, reading.store) if reading.context is not None and reading.store else None
    if controls is None or reading.window is None:
        return None
    days = reading.store.production_days
    try:
        held = parse_window({'period': period, 'anchor': reading.window['anchor']})[2:]
        if parse_window({'period': period, 'anchor': controls.production_day})[2:] == held:
            return f'this {period}', {'period': period, 'anchor': reading.window['anchor']}
        before = agent.previous_window_day(period, controls.production_day, days)
        if before and parse_window({'period': period, 'anchor': before})[2:] == held:
            return f'last {period}', {'period': period, 'anchor': reading.window['anchor']}
    except ValueError:
        return None
    return None


def _offer(reading, text, asks):
    """(the refusal's text, the questions it offers): at most three of `asks`, each one the router answers as offered. When it had
    questions and none is, the text says what the semantic model keeps of the measure they named."""
    kept = _checked(reading, asks)
    if not kept:
        mdl = semantic.model()
        named = next((ask.measure for ask in list(asks) + reading.dropped if isinstance(ask, Ask) and ask.measure in mdl.measures), None)
        if named:
            text = f"{text.rstrip('.')}. {_kept_for(mdl, named)}"
    return text, kept


def _checked(reading, asks):
    """The first three of `asks` the router answers as offered, in their order; the others it checked are `reading.dropped`."""
    kept = []
    for ask in dict.fromkeys(asks):
        if len(kept) == 3:
            break
        if _answers_as_offered(ask, reading):
            kept.append(ask)
        else:
            reading.dropped.append(ask)
    return kept


def _kept_for(mdl, measure):
    """What the semantic model keeps of a measure, said when no question a refusal could offer is answered as offered."""
    dims = [_dim_word(mdl, d, whole=True) for d, o in mdl.dimensions.items() if o['type'] != 'time' and semantic._usable(mdl, measure, d, as_filter=False) is None]
    kept = f"by {', '.join(dims[:-1])} and {dims[-1]}" if len(dims) > 1 else f'by {dims[0]}' if dims else 'only for a whole window'
    return f"The semantic model keeps {_measure_word(mdl, measure)} {kept}"


_ANSWERED = {}
ANSWERED_KEPT = 4096


def _answers_as_offered(ask, reading):
    """True when the offline router, asked `ask` in the controls the refusal was asked in (_controls), answers it as the question it
    offers: the semantic query of its measure, split, ranking, window before, share, filters and shift over its window, or a hand route
    whose answer opens with that measure (_hand_leads). A plain question (no Ask) is checked against its own complete reading. One level
    deep: a question offered inside another's check is not checked (that reading is never kept, read() keys on it)."""
    if getattr(_OFFERING, 'text', None):
        return True
    context, store = reading.context, reading.store
    if context is None or not store:
        return False
    controls = _controls(context, store)
    if controls is None:
        return False
    key = (str(ask), getattr(ask, 'measure', None), tuple(getattr(ask, 'group_by', ())), getattr(ask, 'rank', None),
           getattr(ask, 'compare', None), getattr(ask, 'share', None), repr(getattr(ask, 'filters', None)), getattr(ask, 'shift', None),
           repr(getattr(ask, 'window', None)), repr(reading.window), repr(reading.filters), controls.production_day, controls.shift,
           controls.scope, controls.period, repr(sorted((controls.rates or {}).items())), id(store), getattr(store, 'content_hash', None))
    if key not in _ANSWERED:
        if len(_ANSWERED) >= ANSWERED_KEPT:
            _ANSWERED.clear()
        _OFFERING.text = str(ask)
        try:
            _ANSWERED[key] = _as_offered(ask, reading, controls, store)
        except (ValueError, semantic.SemanticError):   # refused: never offered
            _ANSWERED[key] = False
        finally:
            _OFFERING.text = None
    return _ANSWERED[key]


def _as_offered(ask, reading, controls, store):
    request, asked = agent.route(str(ask), controls, store.production_days, store)
    if not isinstance(ask, Ask):
        own = read(str(ask), asked, store)
        if not own.complete:
            return False
        q = own.query
        ask = Ask(str(ask), q.measure, q.group_by, own.rank, q.compare, q.share, filters=q.filters, window=reading.window)
    wanted = reading.window or {'period': getattr(controls, 'period', 'day'), 'anchor': None}
    wanted = {'period': wanted['period'], 'anchor': wanted['anchor'] or controls.production_day}
    window = wanted
    if ask.window:
        window = {'period': ask.window['period'], 'anchor': ask.window['anchor'] or controls.production_day}
        if not ask.reads and not _holds(window, wanted):
            return False   # a window of its own is offered only when it holds the one asked
    filters = reading.filters if ask.filters is None else ask.filters
    plan = request.get('plan') or []
    if request.get('intent') == 'semantic':
        args, lead = plan[0]['arguments'], request.get('lead') or {}
        return (args['measure'] == ask.measure and list(args['group_by']) == ask.group_by and lead.get('rank') == ask.rank
                and args.get('compare') == ask.compare and args.get('share') == ask.share and not lead.get('shares')
                and args['filters'] == filters and _same_window(args['window'], window) and (not ask.shift or args.get('shift') == ask.shift))
    # a hand route: the question's own reading is the one offered, over the window the route reads, and the route opens with it
    own = read(str(ask), asked, store)
    if not own.complete:
        return False
    q = own.query
    hand = _hand_window(request, asked)
    return (q.measure == ask.measure and q.group_by == ask.group_by and own.rank == ask.rank and q.compare == ask.compare
            and q.share == ask.share and not own.shares and q.filters == filters and _same_window(q.window, window)
            and (not ask.shift or q.shift == ask.shift) and hand is not None
            and _same_window({'period': hand[0], 'anchor': hand[1]}, window) and _hand_leads(request, own, asked, store))


def _holds(outer, inner):
    """True when the window `outer` holds every production day of `inner`."""
    try:
        (start, end), (first, last) = semantic.parse_window(outer)[2:], semantic.parse_window(inner)[2:]
    except (ValueError, TypeError, KeyError):
        return False
    return start <= first and last <= end


def _same_window(one, other):
    """True when two windows are the same production days (a week anchored on its Monday or its Friday is one week)."""
    try:
        return semantic.parse_window(one)[2:] == semantic.parse_window(other)[2:]
    except (ValueError, TypeError, KeyError):
        return False


def _controls(context, store):
    """The controls a refused question was asked in, as far as the scope it was read in tells them (question_scope keeps no copy): a
    question offered is routed from them, as a visitor who asks it next is. Moved by its words ("yesterday", "last week"), the controls'
    day is the first production day whose same words name that window again; moved to every shift by the owner's quantity default,
    the scope is the selected shift. None when no such day is in the records."""
    read_words = getattr(context, 'read', None) or {}
    day, period = context.production_day, getattr(context, 'period', 'day') or 'day'
    if read_words.get('words'):
        from .record_store import parse_window
        days, grain = store.production_days, read_words['period']
        key = (grain, day, id(store), getattr(store, 'content_hash', None))
        if key not in _CONTROL_DAYS:
            # the first production day after the window the words moved to, when the same words name that window from it
            end = day if grain == 'day' else str(parse_window({'period': grain, 'anchor': day})[3])
            later = days[bisect.bisect_right(days, end):][:1]
            _CONTROL_DAYS[key] = next((d for d in later if agent.previous_window_day(grain, d, days) == day), None)
        day = _CONTROL_DAYS[key]
        if day is None:
            return None
        period = 'day'
    scope = 'shift' if read_words.get('all_shifts') else context.scope
    return agent.Context(day, context.shift, scope=scope, period=period, rates=dict(getattr(context, 'rates', None) or {}))


_CONTROL_DAYS = {}


# The hand routes whose answer opens with a measure the semantic model names, and which: any other hand route opens with another
# figure (the orders card with the order fragments, the day's matrix with its setups, the day's speed with the speed with stops, the
# KPI summary with every tile), so a question offered that the router sends there is dropped. #113's quantity routes open with their
# metric; the Pareto with its first row; the grade totals with the window's MSF; the grades' speed card with the speed without stops,
# by grade led by the grade slowest against its own target (issue #75) over a complete window; the over/under-run card with its percent; the shift's rejects
# with the dry-end percent of yield; the owner's downtime review with maintenance and operator downtime as a percent of the scheduled
# minutes; the waste breakdown with its largest line's percent of gross.
QUANTITY_MEASURES = {'maintenance_minutes': ('downtime_minutes', {'kind': 'Maintenance'}), 'operator_minutes': ('downtime_minutes', {'kind': 'Operator'}),
                     'maintenance_stops': ('stops', {'kind': 'Maintenance'}), 'operator_stops': ('stops', {'kind': 'Operator'})}
# ... and a waste line's square feet or percent of gross (metric waste_sqft or waste_pct, waste=<line>); a reject family's square feet
QUANTITY_WASTE = {('waste_sqft', 'trim'): 'trim_sqft', ('waste_pct', 'trim'): 'trim_pct', ('waste_sqft', 'shear'): 'shear_sqft',
                  ('waste_pct', 'shear'): 'shear_pct', ('waste_sqft', 'dry_end'): 'reject_sqft', ('waste_sqft', 'bale'): 'bale_sqft',
                  ('waste_pct', 'bale'): 'bale_pct', ('waste_sqft', 'unexplained'): 'unexplained_sqft', ('waste_pct', 'unexplained'): 'unexplained_pct'}


def _hand_leads(request, own, asked, store):
    """True when the hand route `request` opens with what the complete reading `own` of a question offered asks (the table above)."""
    q, intent, rank = own.query, request.get('intent'), own.rank
    plan = request.get('plan') or []
    tools = [step['tool'] for step in plan]
    args = plan[0]['arguments'] if plan else {}
    whole = not q.group_by and not rank and not q.filters
    if intent == 'period_quantity':
        lead = request.get('lead') or {}
        if lead.get('metric') not in agent.QUANTITY_TOOLS or 'grade' in lead:
            return False
        if 'waste' in lead:
            family = lead['waste'].upper() if lead['metric'] == 'waste_sqft' and lead['waste'] in ('bond', 'warp', 'misalignment', 'moisture') else None
            measure, filters = ('reject_sqft', {'reject_family': family}) if family else (QUANTITY_WASTE.get((lead['metric'], lead['waste'])), {})
        else:
            measure, filters = QUANTITY_MEASURES.get(lead['metric'], (lead['metric'], {}))
        filters = {**filters, **{k: lead[k] for k in ('reason', 'place') if k in lead}}
        return not q.group_by and not rank and q.measure == measure and q.filters == filters
    if tools == ['get_downtime_pareto']:
        unit = 'stops' if args.get('measure') == 'count' else 'downtime_minutes'
        return q.measure == unit and q.group_by == [args.get('group_by')] and not q.filters and rank in (None, 'most') and q.limit == semantic.DEFAULT_LIMIT
    if intent == 'period_grade' and tools == ['get_grade_totals']:
        return q.measure == 'msf' and q.group_by in ([], ['grade']) and not q.filters and not rank
    if intent == 'period_speed':
        # by grade only over a complete window: an incomplete one opens with the grades observed, unranked (shapes.PeriodAnswer.whole)
        return q.measure == 'speed_run_fpm' and not q.filters and (whole or q.group_by == ['grade'] and rank in (None, 'least')
                                                                    and semantic.window_of(store, q.window)['coverage'] == 'complete')
    if intent == 'period_overrun':
        return q.measure == 'overrun_pct' and whole
    if intent == 'quality':
        return q.measure == 'dry_end_pct' and whole
    if intent == 'period_review':
        return q.measure == 'op_maint_pct' and whole
    if intent in ('period_waste', 'period_quality') or tools == ['get_waste_breakdown']:
        line = WASTE_LINE.get(q.measure)
        return (whole and semantic.model().measures[q.measure]['unit'].startswith('%') and line is not None and line not in NEVER_LEADS
                and _waste_leads(own, store, asked))
    return False


def _assign(reading, items, mdl, store, a, context):
    """Every item given a role, or the reading refused; then the query built and validated. `a` holds what the first pass read:
    the window, the shift, the asks, the share, "top N", "per day" and a reason's connector."""
    rank, limit, units, leftover = a['rank'], a['limit'], [], []
    a['units'], a['leftover'] = units, leftover   # what was read when a refusal fires, for _verb_cues
    statistic, evaluative, which, speed_rank, frequency, who = [], [], False, None, False, False
    group_by, filters, per_ratio, verbs, than, modifiers, compared = [], {}, None, [], [], [], []
    ranked_by = []   # the dimensions a ranking word of the model names ("pareto": the stop reasons, issue #185)
    for i, item in enumerate(items):
        kind = item.kind
        if kind == 'vocab' and item.entries and all(e[0] == 'rank' for e in item.entries):
            rank = rank or 'most'
            item.role = 'rank'
            reading.cues.add('rank')
            ranked_by.append(item.entries[0][1])
        elif kind == 'verb':
            verbs.append(item)   # read once the measure is known (LOSS_VERBS, REJECT_VERBS, PRODUCE_VERBS), else refused
        elif kind == 'than':
            than.append(item)    # "more setups than 26-C": refused in _read, over any other refusal ("than last week" is PREVIOUS's)
            after = next((o for o in items[i + 1:] if not (o.kind == 'filler' and o.words in ARTICLES)), None)
            if after is not None:
                compared.append(after)   # the value compared, which the questions offered do not keep
        elif kind == 'unit':
            units.append(item.value)
            item.role = 'unit'
            reading.cues.add('unit')   # a unit asked names what a ranking ranks, even with no measure ("which grade had the most minutes")
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
    if not pure and mixed and not (len(mixed) == 1 and _cued(items, items.index(mixed[0])) and _entries(mixed[0], 'dimension')
                                   and not _ranked_measure(items, mixed[0])):
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
        # kept for _boards_dimension: "which boards had the most shear" names the shear, though beside "boards" it reads as a place
        # (the coordinator's review of PR #155 at 97b81f4, item 4)
        it.said = _entries(it, 'measure')
        it.entries = [e for e in it.entries if e[0] != 'measure']
    for it in pure:
        it.role = 'measure'
    named = list(dict.fromkeys(_entries(it, 'measure')[0][1] for it in pure))
    if len(named) > 1:
        words = [it.words for it in pure]
        raise Refused(f"'{words[0]}' and '{words[1]}' are two measures: ask one at a time",
                      [_ask(mdl, reading, n) for n in named[:3]])
    # ---- dimensions (a split when asked as one) and values
    values = []
    for i, item in enumerate(items):
        if item.kind != 'vocab' or item.role == 'measure':
            continue
        dims, vals = _entries(item, 'dimension'), _entries(item, 'value')
        cue = _cued(items, i) or _joined(items, i) is not None
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
        joined = _joined(items, i)
        joined = joined if joined is not None and items[joined[1]].value in group_by else None
        if _cued(items, i) or item.words in INHERENT or (item.words.endswith('s') and ('compare' in reading.cues or rank or which)) \
                or joined is not None or a['top_said']:   # "top 1 reason": the split N rows of which are asked (review of PR #186, item 5)
            if item.words == 'daily' and dim == 'day':
                a['per_day'] = True   # "daily setups": per day, read below as the measure's ratio to the days or each day's stops
                item.role = 'per'
                continue
            if joined is not None:
                items[joined[0]].role = 'group'   # "by shift and grade": the "and" of one split by both
            if _modifier(items, i):
                modifiers.append((item, items[i + 1]))
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
    # ---- a ranking word of the model that names its dimension ("maintenance pareto": the stop reasons ranked, issue #185), when the
    # question splits by nothing else
    for dim in ranked_by:
        if not group_by:
            group_by.append(dim)
            reading.cues.add('group')
    # ... and the dimension's default ranking (the model's `rank_by`: stop reasons rank by downtime minutes, the owner's ruling on
    # #172): "worst" of a stop's reasons is the most, never a direction the model does not hold
    rank_by = (mdl.dimensions[group_by[0]].get('rank_by') if len(group_by) == 1 else None) or {}
    if rank_by and evaluative and set(evaluative) <= {'worst', 'poorest'}:
        rank, evaluative = rank or 'most', []
        reading.cues.discard('evaluative')
    # ---- the measure when none is named: what the split's own words say ("downtime reason", "stop reason"), the default of a value's
    # records (a reject family's area), a count of stops asked, a speed ranked, minutes lost
    measure = named[0] if named else None
    if measure is None:
        for item in items:
            if item.role == 'dimension' and item.value in group_by and not item.said:
                # ... never from a phrase that is itself a measure ("production days": never the volume its first word names)
                inside = [mdl.measure_words.get(w) for w in item.words.split()]
                found = next((m for m in inside if m and mdl.measures[m]['agg'] in semantic.SIMPLE), None)
                if found and rank and rank_by and found != rank_by['measure']:
                    # ... but a ranking of a dimension the model ranks by a default measure ranks by it: "top stop reasons" rank by
                    # downtime minutes like any other reasons, and only "by stops" or "most stops" (a measure named) counts them (the
                    # review of PR #186, item 6, the owner's 15 Nov calls)
                    break
                if found:
                    measure = found
                    break
    if measure is None:
        for _, it in values:
            entities = {mdl.dimensions[e[1]]['entity'] for e in it.value}
            # a value's own name beats a word that is only another value's synonym: "moisture" is the reject family Moisture and a
            # synonym of the stop reason Steam / moisture, so "how much moisture" is the family's rejects (the review of b955ca4, item 2);
            # a count asked ("how often moisture", "how many times glue dam", "how many glue dam") is the stops', as before, and a
            # loss ("how much did we lose to the glue dam") could be either's: refused, as before
            exact = {mdl.dimensions[e[1]]['entity'] for e in it.value if e[3]}
            if len(entities) > 1 and len(exact) == 1 and not (frequency or a['ask_often'] or 'count' in units
                                                                or re.search(COUNT_ASK, reading.question, re.I)
                                                                or any(v.words in LOSS_VERBS for v in verbs)):
                entities = exact
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
    if measure is None and ('min' in units or a['ask_unit'] == 'min'):
        # minutes lost ("which shift lost the most minutes"), or the minutes of a stop's own split or value ("which reason had the most
        # minutes", "which place took the most minutes": the minutes stopped); the minutes of a grade or a shift are not said
        stopped = [mdl.dimensions[d]['entity'] for d in group_by] + [mdl.dimensions[e[1]]['entity'] for _, it in values for e in it.value]
        if a['loss'] or stopped and all(e == 'stop' for e in stopped):
            measure = 'downtime_minutes'
    # ... never of one ("what does a missed splice cost": a cost per splice, which no measure keeps, never the window's total)
    per_unit = any(i > 0 and items[i - 1].words in ('a', 'an', 'each', 'one', 'per', 'every') for i, _ in values)
    if measure is None and units and units[-1] == '$' and values and not group_by and per_unit:
        # "what does a missed splice cost": the cost of one stop, which no measure keeps; never the window's total, nor the shift's
        # dollars over a hand route (the cue 'said'; the reviewer's check of 5a93655)
        reading.cues.add('said')
        one = next(it for i, it in values if i > 0 and items[i - 1].words in ('a', 'an', 'each', 'one', 'per', 'every'))
        entities = {mdl.dimensions[e[1]]['entity'] for e in one.value}
        priced = [n for n, o in mdl.measures.items() if o['agg'] == 'priced' and entities == {mdl.measure_entities(n)[0]}]
        raise Refused(f"'{one.words}' asks what one costs, and the semantic model prices a window's records, never one of them",
                      [_ask(mdl, reading, priced[0], filters={one.value[0][1]: one.value[0][2]},
                            word=next(iter(mdl.measures[priced[0]].get('synonyms') or []), None))] if len(priced) == 1 else [])
    if measure is None and units and units[-1] == '$' and values and not group_by and not per_unit:
        # money asked of a value of one kind of record, with no measure named ("how much money have missed splices cost me"): the one
        # priced measure of those records (a stop's is its downtime priced as machine time; issue #185), never a choice between several;
        # "where is the money" names no value and stays the money's own answer
        entities = {mdl.dimensions[e[1]]['entity'] for _, it in values for e in it.value}
        entities.discard('*')
        priced = [n for n, o in mdl.measures.items() if o['agg'] == 'priced' and entities == {mdl.measure_entities(n)[0]}]
        if len(priced) == 1:
            measure = priced[0]
    if measure is None and rank_by and rank:
        # "top maintenance reasons", "biggest maintenance problems", "maintenance pareto": the dimension ranked by its default measure
        # (issue #185); a unit said is that measure's in the unit ("which reasons cost the most": the downtime's dollars)
        measure = rank_by['measure']
        reading.cues.add('default measure')
    if rank_by and rank and not a['top_said'] and (ranked_by or any(
            it.role == 'dimension' and it.value == group_by[0] and it.words.endswith('s') for it in items)):
        # ... and the reasons ranked as a set ("top reasons", "the pareto") with no number asked list the model's default top N
        limit = rank_by['top']
        reading.default_top = True
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
                          "semantic model", [_ask(mdl, reading, measure)])
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
                          [_held(mdl, reading, measure, 'day', 'week') if a['period'] == 'day' else _ask(mdl, reading, measure, 'day'),
                           _ask(mdl, reading, measure)])
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
            dim = next((e[1] for e in _entries(item, 'dimension') if semantic._usable(mdl, measure, e[1], as_filter=False) is None), None)
            beside = [d for d in group_by if d != dim]   # every other split one the measure is recorded by ("crew rejects by reason": none)
            if dim is not None and _modifier(items, i) and beside and \
                    all(semantic._usable(mdl, measure, d, as_filter=False) is None for d in beside):
                # "crew rejects by shift": no value of the word narrows the measure, but the word is also a dimension the measure is
                # recorded by, said bare before it beside a split of another: _modifier's reason below, never "not recorded by stop
                # kind" (the coordinator's review of PR #155 at 97b81f4, item 5)
                item.role, item.value = 'dimension', dim
                modifiers.append((item, items[i + 1]))
                continue
            dims = sorted({e[1] for e in item.value})
            raise Refused(f"{_strip_parens(mdl.measures[measure]['label'])} is not recorded by {' or '.join(_dim_word(mdl, d, whole=True) for d in dims)}, so "
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
            m = measure or 'stops'
            word = _measure_word(mdl, m)
            # every other value the question said, read so far or after it, and the shift it named, stay in each reading offered
            so_far = {**_values_beside(mdl, items, m, item),
                      **{it.value[0]: it.value[1] for _, it in values if it.role == 'value' and isinstance(it.value, tuple)}}
            narrow = _narrowing(mdl, so_far, reading.shift)
            options = []
            for d in dims:
                value = next(e[2] for e in candidates if e[1] == d)
                if 'share' in reading.cues:
                    options.append(Ask(f"what percent of {word} {narrow} was {_value_phrase(mdl, d, value)} {reading.window_words}", m, [d],
                                       share=value, filters=so_far, shift=reading.shift))
                else:
                    options.append(Ask(f"{word} {narrow} {'at' if d == 'place' else 'for' if d == 'reason' else 'of'} "
                                       f"{'the ' if d == 'place' else ''}{value} {reading.window_words}", m, filters={**so_far, d: value},
                                       shift=reading.shift))
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
        if any(item is o for o in compared):
            reading.compared.add(dim)
    by_dim = {}
    for _, it in values:
        if it.role == 'value' and isinstance(it.value, tuple):
            by_dim.setdefault(it.value[0], []).append(it.value[1])
    joined = False
    reading.filters = {d: vs[0] for d, vs in by_dim.items() if len(set(vs)) == 1}   # what a question offered keeps, should a refusal fire
    for dim, vals in by_dim.items():
        vals = list(dict.fromkeys(vals))
        if len(vals) > 1:
            if 'compare' in reading.cues or any(it.kind == 'and' for it in items):
                # two values of one dimension compared ("32-C and 26-C"): the split by it, the card led by the two (the review of PR
                # #155: never by the total of every value), every other value after them
                if dim not in group_by:
                    group_by.append(dim)
                reading.cues.add('compare')
                if len(vals) == 2:
                    reading.versus = vals
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
                m = measure or 'stops'
                word = _measure_word(mdl, m)
                first, second = list(filters)[:2]
                raise Refused('Two values are named and the share of one of them is not said: ask the share of one within the other',
                              [Ask(f"what percent of {_label_word(mdl, one, filters[one])} {word} were {_value_phrase(mdl, other, filters[other])} "
                                   f"{reading.window_words}", m, [other], share=filters[other], filters={one: filters[one]})
                               for one, other in ((first, second), (second, first))])
            share_value = filters.pop(dim)
            group_by.append(dim)
            reading.filters = dict(filters)
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
            if len(a['shifts']) == 2 and reading.versus is None:
                reading.versus = list(a['shifts'])   # "scrap on shift 1 versus shift 2": the two shifts lead
            joined = True
        else:
            raise Refused('Two shifts named: compare them ("compare shift 1 and shift 2 stops"), or ask one shift at a time')
    for item in items:
        if item.kind == 'and' and not joined and item.role is None:
            leftover.append(item.words)
        elif item.kind == 'compare' and not (group_by or a['compare']):
            leftover.append(item.words)
        elif item.kind == 'group' and item.role is None and not group_by:
            leftover.append(item.words)
    for i, item in enumerate(items):   # "top 3 shifts by stops": the split listed by its measure, a word the reading used
        nxt = items[i + 1] if i + 1 < len(items) else None
        if item.kind == 'group' and item.role is None and group_by and nxt is not None and nxt.role == 'measure':
            item.role = 'group'
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
        asks = [_ask(mdl, reading, r, word=_strip_parens(mdl.measures[r]['label']).lower()) for r in ratios[:2]]
        raise Refused(f"'{statistic[0]}' asks for a statistic the semantic model does not define: it keeps sums, counts and ratios of sums",
                      list(dict.fromkeys(asks + _suggest(mdl, reading, base, group_by[0] if group_by else None, None)))[:3])
    loss = [it for it in verbs if it.words in LOSS_VERBS + REJECT_VERBS]
    if measure is None and loss and not leftover and '$' not in units:
        # "which shift rejected the most", "which shift lost the most": a loss with no measure is never the waste breakdown's side trim.
        # Money lost ("how much money did we lose", "where are we losing money") is the money answer's own: the levers, the shift's
        # recorded losses (the reviewer's check of 20c833d)
        reading.cues.add('loss')
        rejected = loss[0].words in REJECT_VERBS
        raise Refused(f"'{loss[0].words}' asks what was {'rejected' if rejected else 'lost'} and names no measure: the "
                      f"{'rejects are kept as square feet of dry-end rejects and as reject sheets' if rejected else 'losses the semantic model keeps are minutes of downtime and the waste lines (side trim, shear, dry-end rejects)'}",
                      [next(iter(_suggest(mdl, reading, m, group_by[0], rank)), None) if group_by else _ask(mdl, reading, m)
                       for m in (('reject_sqft', 'reject_sheets') if rejected else ('downtime_minutes', 'reject_sqft', 'trim_sqft'))])
    # a duration asked ("took the most minutes", "the most time"): the suggestions offer the model's minutes, never stops or setups
    timed = 'min' in units or a['ask_unit'] == 'min' or any(w in TIME_WORDS for w in leftover)
    if measure is None:
        raise Refused('The question names no measure the semantic model holds',
                      _suggest(mdl, reading, None, group_by[0] if group_by else None, rank or speed_rank,
                               measures=_minutes_of(mdl, None) if timed else None))
    if leftover:
        reading.cues.add('unknown')
        reading.unused = list(dict.fromkeys('breakdown' if w == 'zbreakdownz' else w for w in leftover))   # the word as said
        timed = timed and set(reading.unused) <= set(TIME_WORDS) and mdl.measures[measure]['unit'] != 'min'
        raise Refused(f"'{reading.unused[0]}' is not a word the semantic model reads",
                      _suggest(mdl, reading, None if timed else measure, group_by[0] if group_by else None, rank or speed_rank,
                               measures=_minutes_of(mdl, measure) if timed else None))
    others = [d for d in group_by if d not in {it.value for it, _ in modifiers}]
    if modifiers and others:
        # "which shift had the most board stops", "which crew had the most board waste", "the most cutoff knife rejects": a dimension
        # said bare before the measure, beside a ranking or a split of another, names no value and asks no split of its own (the
        # coordinator's review of PR #155 at 7b46463, item 1: never "Shift 1 · 275-BC")
        reading.cues.add('modifier')
        (said, noun), dim, other = (modifiers[0][0].words, modifiers[0][1].words), modifiers[0][0].value, others[0]
        word = _measure_word(mdl, measure)
        ranking = bool(rank or speed_rank or which)
        asks = _suggest(mdl, reading, measure, other, rank or speed_rank or ('most' if which else None))[:1]
        head = f"'{said}' is said before '{noun}' with no value of its own, beside the {_dim_word(mdl, other)}s {'ranked' if ranking else 'split'}"
        if semantic._usable(mdl, measure, dim, as_filter=False) is not None:
            # "which shift had the most order stops": no split by both to offer (the reviewer's check of this round)
            raise Refused(f"{head}, and {_strip_parens(mdl.measures[measure]['label']).lower()} is not recorded by "
                          f"{_dim_word(mdl, dim, whole=True)}: ask {'the ranking' if ranking else 'that split'}", asks)
        both = _ask(mdl, reading, measure, [other, dim])
        if _answers_as_offered(both, reading):
            raise Refused(f"{head}, so it is no split by {_dim_word(mdl, dim)}: ask {'the ranking' if ranking else 'that split'}, "
                          "or split by both", asks + [both])
        raise Refused(f"{head}, so it is no split by {_dim_word(mdl, dim)}: ask {'the ranking' if ranking else 'that split'}", asks)
    board = _boards_dimension(reading, items, a)
    if board is not None:
        reading.cues.add('uncounted')
        raise board
    uncounted = _uncounted_noun(items, a, reading.question, 'compare' in reading.cues)
    if uncounted:
        # "which shift had the most chops", "compare shifts on chops", "stacker boards on shift 1 versus shift 2", "which grade had the
        # most boards", "how many boards by grade": a count of what the records do not count (UNCOUNTED), refused with their reason over
        # any hand route, as "how many boards" is
        reading.cues.add('uncounted')
        why = next(text for pattern, text in agent.NOT_COUNTED if re.search(pattern, uncounted))
        raise Refused(why, _suggest(mdl, reading, measure, group_by[0] if group_by else None, rank or ('most' if which else None)))
    if a['loss'] and mdl.measures[measure]['unit'] == 'ft/min':
        raise Refused('Speed lost is a gap against a target or a baseline, which the semantic model does not hold: it keeps the speed itself',
                      _suggest(mdl, reading, measure, group_by[0] if group_by else 'shift', 'least'))
    for item in loss:
        rejected = item.words in REJECT_VERBS
        if measure in (REJECT_MEASURES if rejected else LOSS_MEASURES):
            item.role = 'verb'
            continue
        # "how many sheets were rejected", "which shift lost the most MSF": a loss or a reject said of what ran is never what ran (the
        # coordinator's review of PR #155 at 74cc46f, item 1). Refused over a hand route that answers it as something else ('loss').
        reading.cues.add('loss')
        dim, word = (group_by[0] if group_by else None), _measure_word(mdl, measure)
        verb = 'is' if word == 'MSF' else 'count' if re.search(r'(s|feet)$', word) else 'counts'
        if rejected:
            text = (f"'{item.words}' names the dry-end rejects, and {word} {verb} what ran, rejected or not: the semantic model keeps "
                    "the rejects as square feet of dry-end rejects and as reject sheets")
            alternatives = ('reject_sqft', 'reject_sheets')
        else:
            text = (f"'{item.words}' asks what was lost, and {word} {verb} what ran: the losses the semantic model keeps are minutes of "
                    "downtime and the waste lines (side trim, shear, dry-end rejects)")
            alternatives = ('downtime_minutes', 'reject_sqft', 'trim_sqft')
        asks = [next(iter(_suggest(mdl, reading, m, dim, rank)), None) if dim else _ask(mdl, reading, m)
                for m in alternatives]
        raise Refused(text, [q for q in dict.fromkeys(asks) if q])
    for item in verbs:
        if item.words in PRODUCE_VERBS and mdl.measures[measure]['unit'] in PRODUCE_UNITS:
            item.role = 'verb'   # "which shift produced the most MSF", "how much scrap did we produce": what ran or the waste it made
        elif item.words not in LOSS_VERBS + REJECT_VERBS:
            # every other word read: route keeps the hand answer only when it answers the question without the verb, else refuses
            reading.cues.add('verb')
            reading.unused, reading.verb = [item.words], item.words
            raise Refused(_verb_said(item.words),
                          _suggest(mdl, reading, measure, group_by[0] if group_by else None, rank or speed_rank))
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
                      _suggest(mdl, reading, measure, 'shift', rank or speed_rank) +
                      [Ask(f'{_measure_word(mdl, measure)} {_narrowing(mdl, reading.filters, None)} on {said} {reading.window_words}', measure,
                           filters=reading.filters, shift=a['shifts'][0])])
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
    grains = ('day', 'week', 'month', 'fiscal', semantic.YTD)
    for dim in group_by:
        grain = mdl.dimensions[dim].get('grain')
        if grain and grains.index(grain) >= grains.index(a['period']):
            raise Refused(f"A split by {_dim_word(mdl, dim)} needs a window longer than one {_dim_word(mdl, dim)}",
                          [_held(mdl, reading, measure, dim, 'week' if grain == 'day' else 'month')])
    order = None
    if rank:
        order = 'value' if rank == 'most' else 'value_asc'
        reading.rank = rank
    query = semantic.SemanticQuery(measure=measure, group_by=group_by, filters=filters, window={'period': a['period'], 'anchor': a['anchor']},
                                   compare=a['compare'], order=order, limit=limit, shift=shift, share=share_value)
    reading.query = semantic.validate(query, store, mdl)
    reading.share = reading.query.share
    if reading.versus is not None and (len(reading.query.group_by) != 1 or reading.shares or reading.share is not None or reading.rank):
        reading.versus = None   # the two lead a split by them alone; a ranking or a share leads with its own row
    if reading.versus is not None and reading.query.group_by != ['shift']:
        reading.versus = [semantic.resolve_value(reading.query.group_by[0], v, store, mdl) for v in reading.versus]
    reading.used += [(it.words, it.role) for it in items if it.role]


def _value_phrase(mdl, dim, value):
    """A value as the end of a share question says it: `at the Stacker`, `for Missed splice`, `Maintenance`."""
    return f'at the {value}' if dim == 'place' else f'for {value}' if dim == 'reason' else str(value)


def _label_word(mdl, dim, value):
    """A value as a word before the measure: `maintenance stops`, `32-C setups`."""
    return str(value).lower() if dim in ('kind', 'place', 'reason', 'reject_family', 'reject_reason') else str(value)


def _minutes_of(mdl, measure):
    """The measures in minutes a duration asked can mean ("which grade took the most minutes", "which stop reason took the most
    time"): a stop's minutes are the downtime; anything else's the running minutes, then the downtime (the coordinator's review of
    PR #155 at 7b46463, item 5)."""
    if measure and mdl.measure_entities(measure)[0] == 'stop':
        return ['downtime_minutes']
    if measure == 'setups':
        return ['elapsed_minutes', 'running_minutes']   # "which grade took the most setup time": the setup clock
    return ['running_minutes', 'downtime_minutes']


def _suggest(mdl, reading, measure, dim, rank, dims=None, measures=None):
    """At most three questions in the model's own words, each one the router answers as offered (_answers_as_offered): the measure
    split by the dimension, the dimension ranked on it, the measure alone, keeping the refusal's window, filters and named shift.
    `measures` offers several measures in the place of one."""
    out = []
    measures = measures or ([measure] if measure else ['stops', 'downtime_minutes', 'setups'])
    dims = dims or ([dim] if dim else (['reason', 'shift'] if measure and mdl.measure_entities(measure)[0] == 'stop' else ['shift', 'grade']))
    for m in measures:
        for d in dims:
            if semantic._usable(mdl, m, d, as_filter=False) is None:
                out.append(_ask(mdl, reading, m, d, rank))
        if measure:
            out.append(_ask(mdl, reading, m))
    return _checked(reading, out)


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


def _verb_said(verb):
    """What the parser says of a verb it does not read here: "produced" or "making" is read only of what ran or the waste it made
    ("which grade produced the most minutes"), every other one not at all ("took", "spent")."""
    if verb in PRODUCE_VERBS:
        return f"'{verb}' is read only of what ran or the waste it made"
    return f"'{verb}' is not a word the semantic model reads"


def verb_refusal_text(reading):
    """The refusal of a verb the parser does not read, naming it (docs/ASKING.md): the reading's own refusal when that is the verb's,
    else the verb and then what else the reading could not say ("'took' is not a word the semantic model reads, and the question
    names no measure the semantic model holds"), with the reading's questions, each read completely."""
    said = _verb_said(reading.verb)
    if reading.refusal.startswith(said):
        return refusal_text(reading)
    other = reading.refusal if reading.refusal.startswith("'") else reading.refusal[:1].lower() + reading.refusal[1:]
    return refusal_text(Reading(question=reading.question, refusal=f'{said}, and {other}', suggestions=reading.suggestions))


def request_of(reading):
    """The offline request for a complete reading: one semantic_query step; `lead` carries the ranking or the shares the card
    opens with (urls.py writes them as rank= and share=each; one value's share is the query's own `share`, share=<value>)."""
    query = reading.query
    lead = {}
    if reading.rank:
        lead['rank'] = reading.rank
    if reading.shares:
        lead['shares'] = True
    if reading.versus:
        lead['versus'] = list(reading.versus)
    rank_by = (semantic.model().dimensions[query.group_by[0]].get('rank_by') or {}) if len(query.group_by) == 1 else {}
    if 'default measure' in reading.cues and rank_by.get('measure') == query.measure:
        # the measure is the split's default ranking, no word of the question (issue #185): the address leaves out m=; a unit said
        # makes another measure ("which reasons cost the most": the downtime's dollars), which the address names
        lead['default'] = True
    if reading.top_said and query.limit == semantic.DEFAULT_LIMIT:
        # "top 25" said: the limit is the default's, so the card and the address carry that N was asked (top=25), never "the first
        # 25" (the review of PR #186, item 3); any other N is the query's own limit
        lead['top'] = True
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
# The hand routes that read a window the semantic model does not read in their own words, so a refusal of those words never stands over
# them: #113's quantity routes (every word consumed: "how many stops this week and last week") and the trend ("over the last eight weeks")
WINDOW_READERS = (*ALLOW_LISTED, 'period_trend')
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


# The waste breakdown's line of each waste measure (record_store.get_waste_breakdown's categories): the card leads with the largest
# line but the dry end, the bale and its unexplained remainder (shapes.PeriodAnswer.largest_waste), its other lines are findings.
WASTE_LINE = {'trim_sqft': 'trim', 'trim_pct': 'trim', 'trim_usd': 'trim', 'shear_sqft': 'shear', 'shear_pct': 'shear', 'shear_usd': 'shear',
              'changeover_sqft': 'changeover', 'cutoff_waste_sqft': 'cutoff_waste', 'stacker_waste_sqft': 'stacker_waste', 'admin_sqft': 'admin',
              'reject_sqft': 'dry_end', 'reject_sheets': 'dry_end', 'dry_end_pct': 'dry_end', 'dry_end_usd': 'dry_end', 'bale_sqft': 'bale',
              'bale_msf': 'bale', 'bale_pct': 'bale', 'bale_usd': 'bale', 'unexplained_sqft': 'unexplained', 'unexplained_pct': 'unexplained',
              'unexplained_usd': 'unexplained'}
NEVER_LEADS = ('dry_end', 'bale', 'unexplained')
# a bare "waste" names every waste line, which the breakdown answers (_whole_waste): the model's synonym narrows it to the dry-end
# rejects; "<line> waste" names that line
WHOLE_WASTE = ('waste', 'wastes')
# a reject family or reason the question says to show ("show warp this month", PR #123's rulings), never one named bare ("bond this week")
SHOWN = r"\b(show|see|display|list|chart|graph|plot|is\s+it)\b"


def _asked(reading):
    """True when the question asks a quantity ("how much", "how many", "total"), not to see or compare."""
    return any(role == 'ask' for _, role in reading.used)


def _whole_waste(reading):
    """True when "waste" is the question's whole measure: a bare "waste" (split by a reject family or reason or not) means every line.
    "<line> waste" ("dry end waste", "scrap waste") or a family's waste ("warp waste") names that line (the coordinator's review of PR
    #155 at b955ca4, item 2)."""
    words = [w for w, role in reading.used if role == 'measure']
    return bool(words) and all(w in WHOLE_WASTE for w in words) and not reading.query.filters


def _waste_leads(reading, store, context):
    """True when the waste breakdown (or the shift's rejects) leads with what the complete reading asked (the coordinator's review of
    PR #155 at 74cc46f, item 3): "waste" itself; a reject family or reason shown or compared ("show warp", "is it bond or warp", "what
    did cracks cost": the breakdown's family lines and sub-reasons, the rulings of PR #123); or the line the card opens with. "How much
    shear", "how much warp", "how much bale" or "dry-end rejects" asks a line the breakdown does not lead with: never side trim."""
    q = reading.query
    if _whole_waste(reading):
        return True
    rejects = set(q.group_by) | set(q.filters)
    shown = (re.search(SHOWN, reading.question, re.I) or q.group_by or 'compare' in reading.cues
             or semantic.model().measures[q.measure]['agg'] == 'priced')
    if rejects & set(REJECT_DIMS) and not _asked(reading) and shown:
        return True
    line = q.filters['reject_family'].lower() if set(q.filters) == {'reject_family'} and not q.group_by else WASTE_LINE.get(q.measure)
    if line is None or q.filters.get('reject_reason') is not None:
        return False
    if store is None or context is None:
        return False
    if line in NEVER_LEADS:
        return False
    from .shapes import PeriodAnswer
    try:
        envelope = store.get_waste_breakdown(q.window, q.shift)
    except Exception:   # noqa: BLE001 - no breakdown to lead: the semantic answer
        return False
    i, row = PeriodAnswer.largest_waste({'data': envelope.get('data') or envelope})
    return row is not None and row['category'] == line


def covers(request, reading, context=None, store=None):
    """True when the hand router's request answers what the complete reading read: the same window, an answer that leads with the
    reading's measure and carries the window before when the reading compares with it, and every split, ranking, share and filter the
    reading found is one the request applies."""
    q = reading.query
    intent = request.get('intent')
    plan = request.get('plan') or []
    tools = [s['tool'] for s in plan]
    args = plan[0]['arguments'] if plan else {}
    if context is not None:
        hand = _hand_window(request, context)
        if hand is not None and hand != (q.window['period'], q.window['anchor']) and intent not in ('period_quantity', 'period_stops', 'period_trend'):
            return False
        if q.window['period'] == semantic.YTD and (hand is None or hand[0] != semantic.YTD):
            return False   # no hand route reads the year to date (issue #185): never the selected day's answer for it
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
                and rank in (None, 'most') and q.limit == semantic.DEFAULT_LIMIT and not reading.default_top and not reading.top_said)
    if intent == 'period_review' or (tools and set(tools) == {'get_downtime_review'}):
        # the owner's downtime answer (issue #101): downtime by reason, ranked, maintenance and operator first; never a count, a split
        # by anything else or another filter
        # bare stops ("stops", "stops yesterday") are the owner's downtime default too (the review of PR #155); "most common" is a
        # count, which the review (ranked by minutes) does not rank
        # A kind narrows only the whole: the review's ranking holds every kind's reasons together, so "top operator downtime reasons"
        # (a split or a ranking by reason of one kind) is never its answer (issue #185: Knife / slitter jam, a maintenance reason, led it)
        return ((q.measure in DOWNTIME_FAMILY and rank in (None, 'most') or q.measure == 'stops' and rank is None)
                and group_by in ([], ['reason']) and set(filters) <= {'kind'} and not (filters and group_by)
                and filters.get('kind') in (None, 'Maintenance', 'Operator') and share is None
                # ... and never the reasons ranked as a set ("top downtime reasons", "worst downtime reasons", "top 25 reasons"): the
                # semantic ranking answers each alike, top N by downtime minutes (the review of PR #186, item 7, retiring #185's carve-out)
                and q.limit == semantic.DEFAULT_LIMIT and not reading.default_top and not reading.top_said
                # ... nor a ranking by the measure no word named ("top stop reason": the reasons' default, downtime minutes, item 6)
                and 'default measure' not in reading.cues)
    if intent == 'quality':
        # the shift's rejects open with its dry-end rejects, the families and reasons after them: the dry-end line, "waste", or a family
        # or reason shown; never another waste line ("how much trim"), and no production day before beside it
        return (q.measure in WASTE_FAMILY and set(group_by) <= set(REJECT_DIMS) and set(filters) <= set(REJECT_DIMS) and share is None
                and rank in (None, 'most') and not q.compare
                and (WASTE_LINE.get(q.measure) == 'dry_end' and not filters or _waste_leads(reading, None, None)))
    if intent in ('period_waste', 'period_quality') or (tools and set(tools) == {'get_waste_breakdown'}):
        # the waste breakdown: every waste line with the window before, each dry-end family and reason with its dollars, led by the
        # largest line; a reading of a line it does not lead with is answered by the semantic model (_waste_leads)
        return (q.measure in WASTE_FAMILY and set(group_by) <= set(REJECT_DIMS) and set(filters) <= set(REJECT_DIMS)
                and share is None and rank is None and _waste_leads(reading, store, context))
    if intent == 'period_grade' and tools == ['get_grade_totals']:
        # the grade totals open with the window's MSF and carry no window before (agent.compares): never the lineal feet or the
        # area of each grade, nor "MSF this week versus last week" (the review of 74cc46f, item 3)
        return q.measure == 'msf' and group_by in ([], ['grade']) and not filters and not rank and share is None and not q.compare
    if intent in ('period_speed', 'speed'):
        # the grades' card leads with each grade's speed without stops against its target and carries no window before: never the
        # elapsed speed by grade, nor "speed by grade this week versus last week". Its headline is the grade slowest against its own
        # target (issue #75, the owner's example), so it leads a grade ranked slowest, never the fastest ("which grade was the fastest":
        # the coordinator's review of PR #155 at 97b81f4, item 1). "The lowest speed" in ft/min is the follow-up issue's
        return (q.measure in SPEED_FAMILY and group_by in ([], ['grade']) and not filters and share is None
                and (not rank or group_by == ['grade'] and rank == 'least')
                and (group_by != ['grade'] or q.measure in ('speed_run_fpm', 'run_speed_to_target_pct') and not q.compare))
    if intent == 'period_compare':
        return False   # the KPI summary answers "how did we do", never a measure the reading named
    if intent in ('period_money', 'dollars'):
        # the levers open with every lever's dollars a year, the shift's dollars with every recorded loss: one priced measure is led
        # only by the ownership lens's side trim ("what's trim costing us"); never "downtime dollars this week" (the reviewer's item 12)
        whole = _whole_waste(reading)   # "what did waste cost": every line
        return ((whole or args.get('lens') == 'ownership' and WASTE_LINE.get(q.measure) == 'trim') and not group_by and not filters
                and not rank and share is None and not q.compare)
    if intent == 'compare':   # the day's shift comparison opens with each shift's speed
        return q.measure in SPEED_FAMILY and set(group_by) <= {'shift'} and not filters and share is None
    if intent == 'downtime':   # the day's downtime breakdown, per shift, by reason, place or kind
        # ... of every row: never a top N asked or defaulted ("top 5 maintenance reasons yesterday", "top maintenance reasons"; the
        # reviewer's check of b76f8e4), nor a ranking by the default measure no word named
        return (q.measure in DOWNTIME_FAMILY + ('stops',) and group_by in ([], [request.get('group_by')]) and rank in (None, 'most')
                and q.limit == semantic.DEFAULT_LIMIT and not reading.default_top and not reading.top_said and 'default measure' not in reading.cues
                and set(filters) <= {'kind'} and (filters.get('kind') is None or str(filters['kind']).lower() == str(request.get('kind') or '').lower())
                and share is None)
    return not (group_by or rank or share is not None or filters)


# The hand router's refusals of a structure or a word its one quantity does not carry, which the semantic model's reading can answer
# in its place: the generic help, a ranking, comparison, share, grouping or statistic, a stop reason, place or kind the router does not
# apply, a word it does not read, a count for one shift or one grade at a time, a ratio kept for all stops or the line, each day's
# stops or downtime kept whole, the Pareto grouped by day. Any other refusal gives a domain reason ("rejects are recorded as square
# feet by reason, not as sheets", "no tool returns the longest stop"): a complete reading never answers in its place (the coordinator's
# review of PR #155 at 74cc46f, item 1).
ROUTER_LIMITS = (r"^Offline mode (?:supports|answers one quantity at a time)", r"which one quantity does not answer",
                 r"is not one stop reason, place or kind the router applies", r"is a word one quantity does not read",
                 r"^A count is answered for one (?:shift|grade)", r"\bkept for all stops together\b", r"\bis kept for the line, not for one\b",
                 r"^Each production day's downtime is kept in minutes", r"^One quantity is the whole window's",
                 r"^The Pareto groups a window's stops by reason, place or kind, not by day")
# ... and the domain reasons that refuse a reading of a word the semantic model reads otherwise, overruled only when the reading reads
# it that way: a customer or an order split or filtered by, never counted ("which customer had the most scrap"); "per" a dimension read
# as a split, never a ratio ("stops per stop reason"); a noun the router does not count read inside a longer phrase of the model ("roll
# changes", "good board", "stacker boards", "chop cost") or as a measure that is no count, no count asked ("how much chop"), never
# ranked or compared (the reading refuses that itself, UNCOUNTED); a measure the model keeps in square feet ("gross square feet").
# The last two rows are the re-pointed refusals count-reject-sheets-refused and count-stops-of-a-grade-refused, which wait on the
# owner's "approve anyway" on PR #155 (taking a row out restores main's refusal).
COUNT_ASK = r"\b(?:how\s+many|number\s+of|count\s+of)\b"


def reads_otherwise(reading, nouns):
    """True when the reading read one of `nouns` inside a phrase of two or more words of the model's vocabulary ("roll changes", "good
    board"), or as a measure kept in no count unit ("how much chop"); a count asked ("how many stacker boards", "how many shear chops")
    is read otherwise only as a count ("how many roll changes"), never answered in square feet (the reviewer's check of 20c833d)."""
    words = [set(tokens_of(w)) for w, role in reading.used if role in ('measure', 'dimension', 'value', 'qualifier')]
    counted = semantic.model().measures[reading.query.measure]['unit'] in COUNT_UNITS
    if re.search(COUNT_ASK, reading.question, re.I) and not counted:
        return False
    return any(len(w) > 1 and w & nouns for w in words) or (any(w & nouns for w in words) and not counted)


OVERRULED = ((r"^The records do not count customers", lambda r: 'customer' in r.query.group_by or 'customer' in r.query.filters),
             (r"^The records do not count distinct orders", lambda r: 'order' in r.query.group_by or 'order' in r.query.filters),
             (r"\bis not a ratio the records keep\b",
              lambda r: bool(r.query.group_by) and semantic.model().measures[r.query.measure]['agg'] != 'ratio'),
             (r"^The records do not count boards", lambda r: reads_otherwise(r, {'board', 'boards'})),
             (r"^The records do not count rolls", lambda r: reads_otherwise(r, {'roll', 'rolls'})),
             (r"^The records keep shear chops as square feet", lambda r: reads_otherwise(r, {'chop', 'chops'})),
             (r"^Square feet are kept for the waste lines", lambda r: semantic.model().measures[r.query.measure]['unit'] == 'sq ft'),
             (r"^Rejects are recorded as square feet by reason, not as sheets", lambda r: r.query.measure == 'reject_sheets'),
             (r"\bnot by grade\b", lambda r: 'grade' in r.query.filters or 'grade' in r.query.group_by),
             # the hand router reads no year to date ("'ytd' is not a window the router reads"): the reading read it (issue #185)
             (r"is not a window the router reads", lambda r: r.query.window['period'] == semantic.YTD))


def _reads_previous(request, reading, context, store):
    """True when the hand route answers a question said with a bare "versus previous" as asked: the KPI summary of a window against
    the one before ("how did this week go versus the previous one"), or a period route that covers the rest of the question, whose
    card carries the window before beside it ("downtime this week vs previous": this week's review). Never the day's routes, which
    hold no window before ("downtime vs previous" was each shift's speed), nor a ranking or a measure the route does not lead with."""
    intent, rest = request.get('intent') or '', reading.bare_rest
    if rest is None:
        return False
    if intent == 'period_compare':
        return not (rest.measure or rest.cues & set(STRONG))   # the KPI tiles answer no measure, ranking or share
    # ... the period routes whose card prints the window before beside the figure (the review's "previous" share, the waste line's
    # "previous 3.34%", the speed's "vs 640.0 ft/min", the Pareto's "previous 28.02 min"); the grade totals print none, so "msf this
    # week vs previous" is refused naming the word, never answered without the comparison
    return intent in PREVIOUS_READERS and rest.complete and covers(request, rest, context, store)


PREVIOUS_READERS = ('period_review', 'period_waste', 'period_speed', 'period_pareto')


def router_limit(refused):
    """True when the hand router's refusal names a structure or a word its one quantity does not carry (ROUTER_LIMITS)."""
    return any(re.search(p, str(refused)) for p in ROUTER_LIMITS)


def stands(refused, reading):
    """True when the hand router's refusal gives a domain reason a complete reading never answers in place of (ROUTER_LIMITS,
    OVERRULED)."""
    return not router_limit(refused) and not any(re.search(p, str(refused)) and overrules(reading) for p, overrules in OVERRULED)


def route(text, context, store, request, refused):
    """What the offline router does with a question the hand router answered (`request`) or refused (`refused`): a semantic
    request when the reading is complete, the hand route does not answer it and no domain reason refused it, a ValueError (the
    reading's refusal) when the question asks a structure the hand route drops, else None (the hand route, or its refusal, stands)."""
    if store is False or not isinstance(text, str) or not text.strip() or re.search(agent.WHY_ASK, agent.correct_typos(text.lower())):
        return None   # store False: a single-day fixture, which no semantic query can read
    if isinstance(refused, agent.Standing):
        # the hand router refused something said that no answer reads (two windows, "during the day", a hedge, a plan or "so far", the
        # line running, "this shift" under All, who logged a stop; issue #153): a complete reading of the other words never answers it
        return None
    store = store if store is not None else _default_store()
    reading = read(text, context, store)
    if 'top refused' in reading.cues and reading.refusal:
        # "top 0", "top 500": refused naming N over whatever the hand route answered or refused (the review of PR #186, items 4 and 5)
        raise ValueError(refusal_text(reading))
    if 'uncounted' in reading.cues:
        # "which shift had the most chops", "compare shifts on chops", "stacker boards on shift 1 versus shift 2": a count of what the
        # records keep as square feet, refused with their reason whatever the hand route answered or refused (the review of b955ca4,
        # item 1: never the chops' square feet ranked, the overview or the speed comparison); the hand router's own refusal with the
        # same reason keeps its wording ("how many shear chops")
        if refused is not None and str(refused).startswith(reading.refusal):
            return None
        raise ValueError(refusal_text(reading))
    if reading.complete:
        if refused is not None and stands(refused, reading):
            return None   # a domain reason refused it: the refusal stands
        if refused is not None or request is None or not covers(request, reading, context, store):
            return request_of(reading)
        return None
    if reading.refusal is None:
        return None
    unread_verb = 'verb' in reading.cues and not reading.cues & {'wrong window', 'said', 'loss'}
    if refused is not None and 'year to date' in reading.cues and (router_limit(refused) or str(refused).startswith('Offline mode supports')
                                                                   or re.search(r"is not a window the router reads", str(refused))):
        # the year to date read and the rest refused: the reading's reason, never the router's one quantity of another window
        raise ValueError(refusal_text(reading))
    if refused is not None:
        # the generic help ("Offline mode supports ...") answers nothing a ranking, a share or a statistic asked: the reading's
        # refusal names what it could not say and three questions that answer ("best crew"; the review of PR #155)
        if str(refused).startswith('Offline mode supports') and reading.cues & (set(STRONG) - {'statistic'} | {'statistic asked', 'evaluative',
                                                                                                             'unread window'}):
            raise ValueError(refusal_text(reading))   # ... and a window the model does not read ("top reasons last year"; issue #185)
        if 'modifier' in reading.cues and not str(refused).startswith(reading.refusal):
            # "which shift had the most board stops", "... board scrap": the dimension said bare beside the ranking, with the two
            # questions that answer, over the router's "one quantity" and its count of boards alike
            raise ValueError(refusal_text(reading))
        if (reading.cues & {'loss', 'than', 'lost to'} or unread_verb) and router_limit(refused):
            # "which shift lost the most sheets", "which grade took the most setups": the verb it could not read, never "one quantity
            # does not answer a ranking"
            raise ValueError(verb_refusal_text(reading) if unread_verb else refusal_text(reading))
        return None
    if request is None:
        return None
    intent = request.get('intent')
    if 'year to date' in reading.cues:
        # "how much money did we lose this year", "waste breakdown this year", "summary this year": the year to date read, the rest
        # refused, and no hand route reads the year to date, so any answer it gives is another window's (issue #185, item 4)
        raise ValueError(refusal_text(reading))
    if 'bare previous' in reading.cues and intent not in WINDOW_READERS and not _reads_previous(request, reading, context, store):
        # "downtime vs previous", "trim versus prior": the word names no window, and the hand route answered another question (each
        # shift's speed); the refusal names the word and how a comparison is asked (the review of PR #186, item 2)
        raise ValueError(refusal_text(reading))
    if 'unread window' in reading.cues and intent not in WINDOW_READERS:
        # "top operator downtime reasons month to date", "stops this quarter": a window the model does not read, which the hand route
        # answers as another one (the selected day, the Period control's window; issue #185, item 4): refused with the parser's reason
        # and the same question over the windows it reads
        raise ValueError(refusal_text(reading))
    if unread_verb:
        # every word read but a verb the parser does not read: the hand route keeps the question only when it answers what the other
        # words ask ("how much MSF did we produce": the grade totals), else the verb is refused, never answered as something else
        # ("which shift produced the most MSF": never shift 2's grade totals; the review of PR #155 at 74cc46f, item 1)
        rest = read(re.sub(rf"\b{re.escape(reading.verb)}\b", ' ', text, count=1, flags=re.I), context, store)
        if intent in ALLOW_LISTED or rest.complete and covers(request, rest, context, store):
            return None
        raise ValueError(verb_refusal_text(reading))
    if reading.cues & {'loss', 'lost to'} and intent not in ALLOW_LISTED:
        # "how much MSF was rejected", "which grade lost the most MSF", "which shift lost the most setups yesterday": the waste
        # breakdown, the grade totals or the shift's KPIs answer what ran, never what was lost; "which place lost the most to warp",
        # "which day lost the most minutes to shear": a loss to a value or a line the model cannot say, never the waste breakdown's
        # side trim (the review of b955ca4, item 3)
        raise ValueError(refusal_text(reading))
    if 'modifier' in reading.cues and intent not in ALLOW_LISTED:
        # "which shift had the most board rejects": a dimension said bare before the measure beside a ranking of another, never the
        # waste breakdown's side trim or the two split together (the coordinator's review of PR #155 at 7b46463, item 1)
        raise ValueError(refusal_text(reading))
    if intent == 'period_rollstock' and reading.measure in WASTE_FAMILY:
        # "how much rollstock waste", "how much paper was wasted": the paper consumed is no waste (the reviewer's check of this round)
        raise ValueError(refusal_text(reading))
    if 'line waste' in reading.cues and intent not in ALLOW_LISTED:
        # "paper waste", "splice waste", "baler waste", "how much paper did we waste": a line said with "waste" that the reading cannot
        # place, never every line led by side trim nor the paper consumed (only a bare "waste" is the breakdown's; the review of
        # b955ca4, item 2, and of 7b46463, item 2)
        raise ValueError(refusal_text(reading))
    if reading.cues & {'wrong window', 'said'} and intent not in NEVER_PREEMPT:
        # "stops this year", "downtime over the weekend", "stops on Tuesday" (on a Monday): the hand route answers the selected day;
        # "this week versus last month", "the stops the operators logged": it answers another window or another question
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
    timed = set(reading.unused) <= set(TIME_WORDS)   # "which grade had the most time down": a duration, not an unknown word
    if strong and ('unknown' not in reading.cues or timed) and (reading.measure or reading.cues & {'single', 'per_quantity', 'unit'}) \
            and reading.measure not in _family_of(intent):
        # a ranking, a share or a statistic of a measure or a unit the hand route does not lead with ("which grade had the most minutes",
        # "which shift made the most minutes": never the grade totals' MSF)
        raise ValueError(refusal_text(reading))
    return None
