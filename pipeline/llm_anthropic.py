"""Claude via the official `anthropic` SDK (pip install anthropic). One streamed request per call."""
from . import PipelineError


def _client(cfg):
    try:
        import anthropic
    except ImportError:
        raise PipelineError('the anthropic package is missing: run  pip install anthropic')
    key = cfg.get('anthropic_api_key')
    return anthropic, (anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic())


def complete(system, user, cfg, max_tokens=16000):
    anthropic, client = _client(cfg)
    model = cfg.get('model') or 'claude-opus-5-5'
    effort = cfg.get('effort') or 'medium'
    kwargs = dict(
        model=model, max_tokens=max_tokens,
        system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
        messages=[{'role': 'user', 'content': user}],
        output_config={'effort': effort},
    )
    if model.startswith(('claude-opus-5', 'claude-sonnet-5-5', 'claude-fable-5')):
        kwargs.update(betas=['server-side-fallback-2026-07-01'], fallbacks='default')
    try:
        with client.beta.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise PipelineError('Anthropic rejected the API key (ANTHROPIC_API_KEY or  mr.py config --anthropic-key ...)')
    except anthropic.NotFoundError:
        raise PipelineError(f'model {model!r} not found; set MR_MODEL or  mr.py config --model ...')
    except anthropic.RateLimitError as exc:
        raise PipelineError(f'Anthropic rate limit: {exc.message}; rerun  run  to continue')
    except anthropic.APIStatusError as exc:
        raise PipelineError(f'Anthropic API error {exc.status_code}: {exc.message}')
    except anthropic.APIConnectionError as exc:
        raise PipelineError(f'could not reach the Anthropic API: {exc}')
    if message.stop_reason == 'refusal':
        detail = getattr(message, 'stop_details', None)
        raise PipelineError('the model declined this request' + (f' ({detail.category})' if detail and getattr(detail, 'category', None) else ''))
    if message.stop_reason == 'max_tokens':
        raise PipelineError(f'reply cut off at {max_tokens} tokens; the batch is too large')
    text = ''.join(block.text for block in message.content if block.type == 'text')
    u = message.usage
    usage = {'input': (u.input_tokens or 0) + (getattr(u, 'cache_creation_input_tokens', 0) or 0) + (getattr(u, 'cache_read_input_tokens', 0) or 0),
             'cached': getattr(u, 'cache_read_input_tokens', 0) or 0, 'output': u.output_tokens or 0}
    return text, usage
