"""Model access for API mode: one `complete()` call, provider chosen by config/env, usage logged per call.

Providers: `anthropic` (official `anthropic` SDK), `openai` (official `openai` SDK), `fake` (tests).
Keys are read from ANTHROPIC_API_KEY / OPENAI_API_KEY or ~/.config/metareview/config.json; never printed.
"""
import json
import os
import re
from pathlib import Path

from . import PipelineError

DEFAULT_MODEL = {'anthropic': 'claude-opus-5-5', 'openai': 'gpt-5'}
_fake = [None]


def config_path():
    return Path(os.environ.get('MR_CONFIG') or Path.home() / '.config' / 'metareview' / 'config.json')


def load_config():
    cfg = {}
    path = config_path()
    if path.is_file():
        try:
            cfg = json.loads(path.read_text())
        except ValueError:
            raise PipelineError(f'{path} is not valid JSON')
    for key in ('provider', 'model', 'effort', 'anthropic_api_key', 'openai_api_key'):
        env = {'provider': 'MR_PROVIDER', 'model': 'MR_MODEL', 'effort': 'MR_EFFORT',
               'anthropic_api_key': 'ANTHROPIC_API_KEY', 'openai_api_key': 'OPENAI_API_KEY'}[key]
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if not cfg.get('provider'):
        cfg['provider'] = 'anthropic' if cfg.get('anthropic_api_key') else 'openai' if cfg.get('openai_api_key') else 'anthropic'
    if not cfg.get('model'):
        cfg['model'] = DEFAULT_MODEL.get(cfg['provider'], '')
    return cfg


def save_config(**values):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if path.is_file():
        try:
            cfg = json.loads(path.read_text())
        except ValueError:
            cfg = {}
    cfg.update({k: v for k, v in values.items() if v is not None})
    path.write_text(json.dumps(cfg, indent=1) + '\n')
    os.chmod(path, 0o600)
    return cfg


def set_fake(fn):
    """Tests: fn(role, system, user) -> text."""
    _fake[0] = fn


def complete(role, system, user, review=None, max_tokens=16000):
    """Return the model's text. `review` gets a usage line per call (tasks/usage.jsonl)."""
    cfg = load_config()
    provider = 'fake' if _fake[0] else cfg['provider']
    if provider == 'fake':
        text, usage = _fake[0](role, system, user), {'input': 0, 'cached': 0, 'output': 0}
    elif provider == 'anthropic':
        from . import llm_anthropic
        text, usage = llm_anthropic.complete(system, user, cfg, max_tokens)
    elif provider == 'openai':
        from . import llm_openai
        text, usage = llm_openai.complete(system, user, cfg, max_tokens)
    else:
        raise PipelineError(f'unknown provider {provider!r}; use anthropic or openai')
    if review is not None:
        review.append('usage.jsonl', {'role': role, 'provider': provider, 'model': cfg.get('model'), **usage})
    return text


def describe():
    cfg = load_config()
    return f"{cfg['provider']} / {cfg['model']}" + (f" (effort {cfg['effort']})" if cfg.get('effort') else '')


def parse_json(text):
    """Tolerant JSON: strips code fences and leading prose; returns the first object or array."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    start = min([i for i in (text.find('{'), text.find('[')) if i >= 0] or [0])
    body = text[start:]
    try:
        return json.loads(body)
    except ValueError:
        pass
    # JSON lines
    rows = []
    for line in body.splitlines():
        line = line.strip().rstrip(',')
        if line.startswith('{'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    if rows:
        return rows
    raise PipelineError('model reply was not valid JSON')


def usage_totals(review):
    rows = review.lines('usage.jsonl')
    return {'calls': len(rows), 'input': sum(r.get('input', 0) for r in rows), 'cached': sum(r.get('cached', 0) for r in rows),
            'output': sum(r.get('output', 0) for r in rows), 'models': sorted({f"{r.get('provider')}/{r.get('model')}" for r in rows})}
