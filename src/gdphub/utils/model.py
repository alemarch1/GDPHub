"""Model-aware Ollama configuration utilities.

Provides profile-based parameter tuning so each script can adapt
num_ctx, timeouts, and thinking-mode to the selected model.
"""

# Model name substrings that indicate a reasoning/thinking model
_THINKING_MODEL_HINTS = ('qwen3', 'deepseek-r1', ':r1', 'qwq', '-think')

# Parameter profiles keyed by detected size tier
_PROFILES = {
    'large':  {'num_ctx': 16384, 'timeout_multiplier': 3.0},
    'medium': {'num_ctx': 8192,  'timeout_multiplier': 2.0},
    'small':  {'num_ctx': 4096,  'timeout_multiplier': 1.0},
}

# Substrings that identify large / medium models by parameter count
_LARGE_HINTS  = ('70b', '72b', '32b', '22b', '14b')
_MEDIUM_HINTS = ('12b', '9b', '8b', '7b')


def get_model_profile(model_name: str) -> dict:
    """Return a profile dict for *model_name* with keys:

    - ``num_ctx``            Recommended context window size
    - ``is_thinking``        True if the model does chain-of-thought by default
    - ``timeout_multiplier`` Scale factor for base timeouts
    """
    name = model_name.lower()

    is_thinking = any(hint in name for hint in _THINKING_MODEL_HINTS)

    if any(hint in name for hint in _LARGE_HINTS):
        tier = 'large'
    elif any(hint in name for hint in _MEDIUM_HINTS):
        tier = 'medium'
    else:
        tier = 'small'

    return {
        'num_ctx': _PROFILES[tier]['num_ctx'],
        'is_thinking': is_thinking,
        'timeout_multiplier': _PROFILES[tier]['timeout_multiplier'],
    }


def apply_model_profile(options: dict, model_name: str) -> dict:
    """Return a copy of *options* with ``num_ctx`` raised to match the model profile.

    Never lowers an explicitly configured value — only increases it when
    the detected model tier warrants a larger context window.
    """
    profile = get_model_profile(model_name)
    updated = options.copy()
    if profile['num_ctx'] > updated.get('num_ctx', 0):
        updated['num_ctx'] = profile['num_ctx']
    return updated