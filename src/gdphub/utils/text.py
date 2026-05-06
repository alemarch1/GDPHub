# Shared text-normalization helpers used across the pipeline.
# Consolidates the previously duplicated `clean_text` implementations from
# `extract_text.py`, `classify_text.py`, and `identify_ropa.py`.

import re

_PRINTABLE_KEEP_CR = re.compile(r'[^\x20-\x7E\n\r\t]')
_PRINTABLE_DROP_CR = re.compile(r'[^\x20-\x7E\n\t]')
_WHITESPACE_RUN = re.compile(r'\s+')


def clean_text(text, *, keep_carriage_return: bool = False) -> str:
    """Normalize text by stripping non-printable bytes and collapsing whitespace.

    Args:
        text: Input value. Non-strings are coerced via ``str()``.
        keep_carriage_return: If True, ``\\r`` is preserved during the
            non-printable strip (it is still folded into a single space by the
            subsequent whitespace collapse). Set True to match the historical
            behavior of ``extract_text.py``; leave False to match
            ``classify_text.py`` and ``identify_ropa.py``.

    Returns:
        The cleaned string. Returns ``""`` for falsy input.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
        if not text:
            return ""
    pattern = _PRINTABLE_KEEP_CR if keep_carriage_return else _PRINTABLE_DROP_CR
    text = pattern.sub('', text)
    return _WHITESPACE_RUN.sub(' ', text).strip()
