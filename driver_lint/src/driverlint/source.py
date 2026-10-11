"""Lexical C views. Offsets and newlines are retained; no C AST is claimed."""

from __future__ import annotations

import bisect
import difflib
import re
from dataclasses import dataclass

from .model import Change, LintError


LEXEMES = re.compile(r'/\*.*?\*/|//(?:\\\n|[^\n])*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*(?:"(?P<quoted>[^"\n]+)"|<(?P<angle>[^>\n]+)>)', re.MULTILINE)
FUNCTION = re.compile(
    r'^[ \t]*(?!(?:if|for|while|switch|else|return|do)\b)'
    r'(?:[A-Za-z_][\w]*[ \t]+|\*[ \t]*)+'
    r'(?P<name>[A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t\r\n]*(?:__\w+[ \t]*)?\{',
    re.MULTILINE,
)


def blank(value: str) -> str:
    return ''.join('\n' if ch == '\n' else ' ' for ch in value)


def decode_source(data: bytes) -> tuple[str, str]:
    # The release contains one sensor setfile with CP949 Korean comments.
    # Preserve its bytes; lexical checks operate on decoded text only.
    for encoding in ('utf-8', 'cp949'):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise LintError('Source is neither UTF-8 nor the existing CP949 legacy encoding')


@dataclass(frozen=True)
class Function:
    name: str
    start: int
    body: int
    end: int
    line: int
    end_line: int


class Source:
    def __init__(self, data: bytes):
        self.text, self.encoding = decode_source(data)
        self.lines = self.text.splitlines()
        self.starts = [0] + [m.end() for m in re.finditer('\n', self.text)]
        self.code = LEXEMES.sub(lambda m: blank(m.group()), self.text)
        self.with_strings = LEXEMES.sub(
            lambda m: blank(m.group()) if m.group().startswith(('/',)) else m.group(), self.text
        )

    def line_at(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)

    def evidence(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""

    def includes(self):
        for match in INCLUDE.finditer(self.with_strings):
            yield self.line_at(match.start()), match.group('quoted') or match.group('angle'), match.start()

    def positive_test_guard(self, target_line: int) -> bool:
        """Recognize only simple positive KUnit guards used in this tree."""
        stack = []
        for raw in self.with_strings.splitlines()[:target_line - 1]:
            directive = re.match(r'^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$', raw)
            if not directive:
                continue
            name, expression = directive.groups()
            expression = expression.strip()
            symbol = r'CONFIG_(?:PABLO_)?KUNIT(?:_TEST)?'
            positive = (name == 'ifdef' and re.fullmatch(symbol, expression)) or (
                name in ('if', 'elif') and re.fullmatch(r'(?:IS_ENABLED|defined)\(\s*' + symbol + r'\s*\)', expression)
            )
            if name in ('if', 'ifdef', 'ifndef'):
                stack.append(bool(positive))
            elif name in ('elif', 'else') and stack:
                stack[-1] = bool(positive)
            elif name == 'endif' and stack:
                stack.pop()
        return any(stack)

    def functions(self) -> list[Function]:
        # Build brace pairs once. This also tolerates unbalanced #if alternatives,
        # which must ultimately be validated by compiling each configuration.
        pairs = {}
        stack = []
        for match in re.finditer(r'[{}]', self.code):
            if match.group() == '{':
                stack.append(match.start())
            elif stack:
                pairs[stack.pop()] = match.start() + 1
        found = []
        for match in FUNCTION.finditer(self.code):
            body = match.end() - 1
            if body in pairs:
                end = pairs[body]
                found.append(Function(match.group('name'), match.start(), body, end,
                                      self.line_at(match.start()), self.line_at(end - 1)))
        return found


def changed_lines(change: Change) -> Change:
    if change.audit or change.after is None:
        return change
    old = decode_source(change.before or b'')[0].splitlines(keepends=True)
    new = decode_source(change.after)[0].splitlines(keepends=True)
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new).get_opcodes():
        if tag != 'equal':
            change.added_lines.update(range(c + 1, d + 1))
            change.deleted_lines.extend(line.rstrip('\r\n') for line in old[a:b])
    return change


def patch_for(change: Change) -> str:
    """Generate a patch from the selected bytes, including staged-only views."""
    old = decode_source(change.before or b'')[0].splitlines(keepends=True)
    new = decode_source(change.after or b'')[0].splitlines(keepends=True)
    source = f'a/{change.path}' if change.before is not None else '/dev/null'
    target = f'b/{change.path}' if change.after is not None else '/dev/null'
    lines = list(difflib.unified_diff(old, new, fromfile=source, tofile=target, n=3))
    if not lines:
        return ''
    output = []
    for line in lines:
        output.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    return f'diff --git a/{change.path} b/{change.path}\n' + ''.join(output)
