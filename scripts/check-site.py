#!/usr/bin/env python3
"""Fail publication on broken local links, assets, or inaccessible form labels."""
import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'
errors = []

class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.references = []
        self.labels = set()
        self.inputs = []
        self.h1 = 0
    def handle_starttag(self, tag, values):
        attrs = dict(values)
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                errors.append(f'Duplicate HTML id: {attrs["id"]}')
            self.ids.add(attrs['id'])
        if tag == 'h1': self.h1 += 1
        if tag == 'img' and 'alt' not in attrs: errors.append('Image lacks alt attribute')
        if tag == 'label': self.labels.add(attrs.get('for'))
        if tag == 'select': self.inputs.append(attrs.get('id'))
        for key in ('href', 'src'):
            if key in attrs: self.links.append(attrs[key])
        for key in ('aria-describedby', 'aria-labelledby'):
            self.references.extend(attrs.get(key, '').split())
        if attrs.get('property') == 'og:image': self.links.append(attrs['content'])

page = Page()
page.feed((SITE/'index.html').read_text())
if page.h1 != 1: errors.append('Expected exactly one primary heading')
for ref in page.references:
    if ref not in page.ids: errors.append(f'Missing ARIA target: {ref}')
for control in page.inputs:
    if control not in page.labels: errors.append(f'Unlabeled selector: {control}')
for link in page.links:
    url = urlsplit(link)
    path = unquote(url.path)
    if url.netloc == 'napiermd.github.io':
        path = path.removeprefix('/model-harbor/')
    elif url.scheme or url.netloc:
        continue
    if not path and url.fragment:
        if url.fragment not in page.ids: errors.append(f'Missing anchor: {link}')
    elif not (SITE/(path or '.')).exists(): errors.append(f'Missing site asset: {link}')
for name in ('README.md','NOTICE.md','CONTRIBUTING.md','SECURITY.md','FORK.md','ROADMAP.md'):
    path = ROOT/name
    for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        url = urlsplit(link)
        if not url.scheme and url.path and not (path.parent/unquote(url.path)).exists():
            errors.append(f'{name}: missing {link}')
for css_url in re.findall(r'url\([\'\"]?([^\'\")]+)', (SITE/'styles.css').read_text()):
    if not (SITE/css_url).exists(): errors.append(f'Missing font: {css_url}')
for svg in list((ROOT/'branding').glob('*.svg')) + list((SITE/'assets').glob('*.svg')): ET.parse(svg)
ET.parse(SITE/'sitemap.xml')
with (ROOT/'examples/baseten-provider.toml').open('rb') as stream: tomllib.load(stream)
catalog = json.loads((ROOT/'examples/baseten-models.json').read_text())
slugs = [m['slug'] for m in catalog['models']]
if len(slugs) != len(set(slugs)): errors.append('Duplicate example model ID')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    sys.exit(1)
print(f'Site checks passed: {len(page.links)} links/assets, {len(page.inputs)} labeled task selectors, example TOML/JSON, SVGs, and sitemap.')
