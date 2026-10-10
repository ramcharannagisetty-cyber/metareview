"""OpenAI models via the official `openai` SDK (pip install openai). One chat completion per call."""
from . import PipelineError


def complete(system, user, cfg, max_tokens=16000):
    try:
        import openai
    except ImportError:
        raise PipelineError('the openai package is missing: run  pip install openai')
    key = cfg.get('openai_api_key')
    client = openai.OpenAI(api_key=key) if key else openai.OpenAI()
    model = cfg.get('model') or 'gpt-5'
    kwargs = dict(model=model, messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                  max_completion_tokens=max_tokens)
    if cfg.get('effort'):
        kwargs['reasoning_effort'] = cfg['effort']
    try:
        response = client.chat.completions.create(**kwargs)
    except openai.AuthenticationError:
        raise PipelineError('OpenAI rejected the API key (OPENAI_API_KEY or  mr.py config --openai-key ...)')
    except openai.NotFoundError:
        raise PipelineError(f'model {model!r} not found; set MR_MODEL or  mr.py config --model ...')
    except openai.RateLimitError as exc:
        raise PipelineError(f'OpenAI rate limit: {exc}; rerun  run  to continue')
    except openai.APIStatusError as exc:
        raise PipelineError(f'OpenAI API error {exc.status_code}: {exc.message}')
    except openai.APIConnectionError as exc:
        raise PipelineError(f'could not reach the OpenAI API: {exc}')
    choice = response.choices[0]
    if choice.finish_reason == 'length':
        raise PipelineError(f'reply cut off at {max_tokens} tokens; the batch is too large')
    u = response.usage
    cached = 0
    details = getattr(u, 'prompt_tokens_details', None)
    if details is not None:
        cached = getattr(details, 'cached_tokens', 0) or 0
    return choice.message.content or '', {'input': u.prompt_tokens or 0, 'cached': cached, 'output': u.completion_tokens or 0}
