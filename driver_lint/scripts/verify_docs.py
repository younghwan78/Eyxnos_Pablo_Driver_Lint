#!/usr/bin/env python3
"""Verify generated HTML structure, anchors/local links, and JS syntax."""

import json
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ids = set()
        self.links = []
        self.scripts = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            if values['id'] in self.ids:
                raise ValueError('Duplicate HTML id: ' + values['id'])
            self.ids.add(values['id'])
        if 'href' in values:
            self.links.append(values['href'])
        if tag == 'script':
            if 'src' in values:
                raise ValueError('Offline guide may not load external scripts')
            self.script = []
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script))
            self.script = None
        if not self.stack or self.stack.pop() != tag:
            raise ValueError('HTML tags are not balanced at: ' + tag)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)


def verify(path):
    text = path.read_text(encoding='utf-8')
    if '{{' in text:
        raise ValueError('Unresolved generated HTML placeholder')
    parsed = Document()
    parsed.feed(text)
    parsed.close()
    if parsed.stack:
        raise ValueError('Unclosed tags: ' + repr(parsed.stack))
    for link in parsed.links:
        value = urlsplit(link)
        if value.scheme:
            continue
        if value.path:
            target = (path.parent / unquote(value.path)).resolve()
            if not target.exists():
                raise ValueError('Missing local link: ' + link)
        elif value.fragment and value.fragment not in parsed.ids:
            raise ValueError('Missing anchor: ' + link)
    for script in parsed.scripts:
        with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8') as tmp:
            tmp.write(script)
            tmp.flush()
            process = subprocess.run(['node', '--check', tmp.name], capture_output=True, text=True, timeout=15)
            if process.returncode:
                raise ValueError(process.stderr)
    return parsed


def main():
    guide = verify(ROOT / 'docs/index.html')
    rules = json.loads((ROOT / 'config/rules.json').read_text())
    if not all('rule-' + r['id'] in guide.ids for r in rules):
        raise ValueError('HTML guide is missing a rule card')
    sample = ROOT / 'reports/example-violation.html'
    if sample.is_file():
        verify(sample)
    for directory in ('pablo_is','gdc'):
        verify(ROOT / directory / 'docs/index.html')
    print(json.dumps({'html_structure': 'PASS', 'local_links_and_anchors': 'PASS',
                      'javascript_syntax': 'PASS', 'rule_cards': len(rules),
                      'owner_guides': ['pablo_is/docs/index.html','gdc/docs/index.html'],
                      'browser_rendering': 'not exercised'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
