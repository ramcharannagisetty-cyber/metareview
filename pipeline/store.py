"""Review workspace: one folder per review under reviews/, JSON files, append-only log."""
import json
import os
import re
import time
from pathlib import Path

from . import PipelineError

STAGES = ['protocol', 'search', 'screen_ta', 'fulltext', 'screen_ft', 'extract', 'rob', 'analyze', 'report']


def home():
    return Path(os.environ.get('MR_HOME') or Path.cwd() / 'reviews')


def slugify(text):
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug[:60].rstrip('-') or 'review'


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


class Review:
    def __init__(self, slug):
        self.slug = slug
        self.root = home() / slug

    @classmethod
    def active(cls):
        marker = home() / '.active'
        if not marker.is_file():
            raise PipelineError('No active review. Run: init "<question>"')
        return cls(marker.read_text().strip())

    @classmethod
    def create(cls, question):
        slug = slugify(question)
        review = cls(slug)
        if review.root.exists():
            raise PipelineError(f'Review folder already exists: {review.root}. Use a different question or delete it.')
        for sub in ('search', 'screening', 'fulltext/uploads', 'extraction', 'rob', 'analysis', 'report', 'work'):
            (review.root / sub).mkdir(parents=True)
        review.write('review.json', {'question': question, 'slug': slug, 'created': now(), 'settings': {}})
        home().mkdir(parents=True, exist_ok=True)
        (home() / '.active').write_text(slug)
        review.log('init', {'question': question})
        return review

    def path(self, relative):
        return self.root / relative

    def exists(self, relative):
        return self.path(relative).is_file()

    def read(self, relative, default=None):
        p = self.path(relative)
        if not p.is_file():
            if default is not None:
                return default
            raise PipelineError(f'Missing file: {p}')
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except ValueError as exc:
            raise PipelineError(f'Invalid JSON in {p}: {exc}')

    def write(self, relative, data):
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + '.tmp')
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
        os.replace(tmp, p)

    def lines(self, relative):
        p = self.path(relative)
        if not p.is_file():
            return []
        out = []
        for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                raise PipelineError(f'{p} line {i} is not valid JSON')
        return out

    def append(self, relative, row):
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')

    def text(self, relative):
        return self.path(relative).read_text(encoding='utf-8', errors='replace')

    def log(self, action, detail=None):
        self.append('log.jsonl', {'ts': now(), 'action': action, 'detail': detail or {}})

    def records(self):
        return {r['pmid']: r for r in self.lines('search/records.jsonl')}
