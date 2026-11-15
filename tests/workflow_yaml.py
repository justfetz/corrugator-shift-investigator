"""A reader for the YAML the workflows under .github/workflows/ are written in (issue #133).

CI installs only `-e . pytest`, so there is no YAML library there, and a guard that skipped without one would
guard nothing. This reads the block style the workflows use: mappings, sequences of mappings or scalars,
literal and folded block scalars (`|`, `|-`, `>`, `>-`), plain, single- and double-quoted scalars, flow
sequences of scalars, and comments; `true`, `false`, `null` and integers are typed as YAML 1.2 types them
(`on` stays a string). Anything else raises ValueError naming the line, so a workflow written in a form this
does not read fails the tests instead of being read wrong. tests/test_repo_hygiene.py checks it against
PyYAML on every workflow whenever PyYAML is installed.
"""
import re

_INT = re.compile(r'[-+]?\d+$')
_KEY = re.compile(r'''([A-Za-z0-9_][A-Za-z0-9_.-]*|'[^']*'|"[^"]*")[ \t]*:(?:[ \t]+|$)''')


def load(text):
    """The document as Python dicts, lists and scalars."""
    lines = text.split('\n')
    value, i = _node(lines, _next(lines, 0), 0)
    if _next(lines, i) < len(lines):
        raise ValueError(f'line {_next(lines, i) + 1}: content after the document')
    return value


def _blank(line):
    stripped = line.strip()
    return not stripped or stripped.startswith('#')


def _next(lines, i):
    while i < len(lines) and _blank(lines[i]):
        i += 1
    return i


def _indent(line):
    return len(line) - len(line.lstrip(' '))


def _node(lines, i, indent):
    """The block node whose first line is lines[i], at `indent` or deeper; returns (value, next line)."""
    i = _next(lines, i)
    if i >= len(lines) or _indent(lines[i]) < indent:
        return None, i
    indent = _indent(lines[i])
    if lines[i][indent:].startswith('- ') or lines[i][indent:].rstrip() == '-':
        return _sequence(lines, i, indent)
    return _mapping(lines, i, indent, {})


def _sequence(lines, i, indent):
    items = []
    while True:
        i = _next(lines, i)
        if i >= len(lines) or _indent(lines[i]) != indent or not lines[i][indent:].startswith('-'):
            if i < len(lines) and _indent(lines[i]) > indent:
                raise ValueError(f'line {i + 1}: unexpected indentation in a sequence')
            return items, i
        rest = lines[i][indent + 1:]
        if rest.strip() == '':
            value, i = _node(lines, i + 1, indent + 1)
            items.append(value)
            continue
        if not rest.startswith(' '):
            raise ValueError(f'line {i + 1}: a sequence item needs a space after the dash')
        inner = indent + 1 + (len(rest) - len(rest.lstrip(' ')))
        if lines[i][inner:].startswith('- ') or lines[i][inner:].rstrip() == '-':
            raise ValueError(f'line {i + 1}: a sequence inside a sequence on one line is not read here')
        if _KEY.match(lines[i][inner:]):
            # "- key: value": a mapping whose first key sits on the dash's line, the rest at its column
            lines = lines[:i] + [' ' * inner + lines[i][inner:]] + lines[i + 1:]
            value, i = _mapping(lines, i, inner, {})
        else:
            value, i = _scalar(lines, i, lines[i][inner:], indent)
        items.append(value)


def _mapping(lines, i, indent, result):
    while True:
        i = _next(lines, i)
        if i >= len(lines) or _indent(lines[i]) < indent:
            return result, i
        if _indent(lines[i]) > indent:
            raise ValueError(f'line {i + 1}: unexpected indentation in a mapping')
        body = lines[i][indent:]
        if body.startswith('- '):
            return result, i   # a sequence at the parent's level ends this mapping
        match = _KEY.match(body)
        if not match:
            raise ValueError(f'line {i + 1}: expected "key: value", got {body!r}')
        key = match.group(1)
        if key[0] in '\'"':
            key = key[1:-1]
        if key in result:
            raise ValueError(f'line {i + 1}: duplicate key {key!r}')
        rest = body[match.end():]
        if _blank(rest):
            nxt = _next(lines, i + 1)
            if nxt < len(lines) and (_indent(lines[nxt]) > indent or
                                     (_indent(lines[nxt]) == indent and lines[nxt][indent:].startswith('- '))):
                value, i = _node(lines, nxt, _indent(lines[nxt]))
            else:
                value, i = None, i + 1
        else:
            value, i = _scalar(lines, i, rest, indent)
        result[key] = value


def _scalar(lines, i, text, indent):
    """A scalar that starts with `text` on line i (a key's or a sequence item's value); returns (value, next line)."""
    head = text.strip()
    block = re.match(r'([|>])([+-]?)(?:\s+#.*)?$', head)
    if block:
        return _block_scalar(lines, i + 1, indent, block.group(1), block.group(2))
    if head.startswith("'"):
        end = re.match(r"'((?:[^']|'')*)'(\s+#.*)?$", head)
        if not end:
            raise ValueError(f'line {i + 1}: a single-quoted scalar must end on its line')
        return end.group(1).replace("''", "'"), i + 1
    if head.startswith('"'):
        end = re.match(r'"((?:[^"\\]|\\.)*)"(\s+#.*)?$', head)
        if not end:
            raise ValueError(f'line {i + 1}: a double-quoted scalar must end on its line')
        return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), end.group(1)), i + 1
    if head.startswith('['):
        inner = re.match(r'\[(.*)\](\s+#.*)?$', head)
        if not inner or any(ch in inner.group(1) for ch in '[]{}'):
            raise ValueError(f'line {i + 1}: only a one-line flow sequence of scalars is read')
        return [_plain(part.strip()) for part in inner.group(1).split(',') if part.strip()], i + 1
    if head.startswith(('{', '&', '*', '!', '%', '@', '`')):
        raise ValueError(f'line {i + 1}: {head[0]!r} is not read here')
    value = re.split(r'\s+#', head, maxsplit=1)[0].rstrip()
    nxt = _next(lines, i + 1)
    if nxt < len(lines) and _indent(lines[nxt]) > indent and not _KEY.match(lines[nxt].strip()) \
            and not lines[nxt].strip().startswith('- '):
        raise ValueError(f'line {nxt + 1}: a plain scalar continued on the next line is not read here')
    return _plain(value), i + 1


def _plain(value):
    if value in ('true', 'True', 'TRUE'):
        return True
    if value in ('false', 'False', 'FALSE'):
        return False
    if value in ('', '~', 'null', 'Null', 'NULL'):
        return None
    if _INT.match(value):
        return int(value)
    if value.startswith(("'", '"')):
        return value[1:-1].replace("''", "'") if value[0] == "'" else value[1:-1]
    if ': ' in value:
        raise ValueError(f'a plain scalar holds ": ": {value!r}')
    return value


def _block_scalar(lines, i, parent_indent, style, chomp):
    """The lines of a `|` or `>` block scalar, from line i on; returns (text, next line)."""
    j = i
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines) or _indent(lines[j]) <= parent_indent:
        return '', i
    indent = _indent(lines[j])
    body = []
    while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) >= indent):
        body.append(lines[i][indent:] if lines[i].strip() else '')
        i += 1
    while i > j and not lines[i - 1].strip():   # trailing blank lines belong to the chomping, not the text
        i -= 1
        body.pop()
    if style == '|':
        text = '\n'.join(body)
    else:
        # Folding: a line break between two lines of text becomes a space, unless either line is more indented
        # (it then stays); each blank line between them is a newline of its own.
        leading = 0
        while not body[leading]:
            leading += 1
        text, prev, blanks = '\n' * leading + body[leading], body[leading], 0
        for line in body[leading + 1:]:
            if not line:
                blanks += 1
                continue
            kept = prev.startswith(' ') or line.startswith(' ')
            text += '\n' * (blanks + kept) if blanks or kept else ' '
            text += line
            prev, blanks = line, 0
    if chomp == '-':
        return text, i
    return text + '\n', i
