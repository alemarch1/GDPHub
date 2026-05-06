# Centralized Ollama chat helper used by classification (script 2) and ROPA
# identification (script 4). Consolidates client construction, model selection,
# GPU/model-profile application, threaded timeout enforcement, <think>-tag
# cleanup, and the ``think=False`` TypeError fallback that arises with older
# versions of the ollama Python library.
#
# The previous implementations in ``2_classify_text.py`` and
# ``4_identify_ROPA.py`` were near-duplicates that drifted slightly
# (e.g. only script 2 implemented the "last line of <think> block" fallback).
# This module preserves the union of both behaviors.

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

import ollama

from gdphub.core.config_manager import get_config
from gdphub.utils.model import apply_model_profile, get_model_profile


# --- THINK-TAG CLEANUP ----------------------------------------------------

_THINK_BLOCK_RE = re.compile(r'<think>.*?</think>', flags=re.DOTALL)
_THINK_OPEN_RE = re.compile(r'<think>(.*?)(?:</think>|$)', flags=re.DOTALL)


def strip_think_block(raw: str) -> str:
    """Remove ``<think>...</think>`` blocks, with the multi-stage fallback
    used by ``_execute_ollama_request_with_timeout`` in script 2.

    Preserves identical resolution rules:
      1. Standard strip of the entire ``<think>...</think>`` region.
      2. If empty, take the substring after the last ``</think>``.
      3. If still empty, take the last line of the last unclosed ``<think>``
         block as a best-effort answer.
      4. If no ``<think>`` markers were found, return ``raw`` verbatim.
    """
    cleaned = _THINK_BLOCK_RE.sub('', raw).strip()
    if cleaned:
        return cleaned
    if not raw:
        return ""
    if "</think>" in raw:
        cleaned = raw.split("</think>")[-1].strip()
        if cleaned:
            return cleaned
    matches = _THINK_OPEN_RE.findall(raw)
    if matches:
        last_block_lines = matches[-1].strip().split('\n')
        return last_block_lines[-1].strip()
    return raw


# --- THREADED TIMEOUT WRAPPER ---------------------------------------------

def _run_with_timeout(target, timeout: int) -> tuple[bool, float]:
    """Execute ``target`` (a no-arg callable) on a worker thread and join with
    ``timeout``. Returns ``(timed_out, elapsed_seconds)``.
    """
    start = time.time()
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout=timeout)
    return th.is_alive(), time.time() - start


# --- CHAT SERVICE ---------------------------------------------------------

class ChatService:
    """A thin façade over the Ollama HTTP client tailored to GDPHub's needs.

    The class is intentionally stateful: the active model, options, and
    timeouts are configured once at construction and reused per ``chat()``
    call. Two public chat-style methods are provided:

      * :meth:`chat`         — generic prompt → text helper
      * :meth:`chat_options` — same with explicit per-call options override

    Both return ``(text, elapsed_seconds)``. Timeouts produce a tuple where
    ``text`` starts with ``"Timeout: "`` followed by ``error_default``.
    """

    def __init__(
        self,
        *,
        url: str,
        model: str,
        options: dict,
        api_timeout: int,
        operation_timeout: int,
        disable_thinking: bool = False,
    ) -> None:
        self.url = url
        self.model = model
        self.options = options
        self.api_timeout = api_timeout
        self.operation_timeout = operation_timeout
        self.disable_thinking = disable_thinking
        self._client = ollama.Client(host=url, timeout=api_timeout)

    # -- Construction helpers --------------------------------------------

    @classmethod
    def from_config(
        cls,
        *,
        section: str = "classify_text.py",
        cli_model: Optional[str] = None,
        cli_gpu_profile: Optional[str] = None,
        cli_no_think: bool = False,
        timeout_key: str = "timeout_seconds",
        api_timeout_key: str = "api_request_timeout",
        default_model: str = "mistral:latest",
        default_timeout: int = 60,
        default_api_timeout: int = 45,
    ) -> "ChatService":
        """Build a :class:`ChatService` using values pulled from the config DB.

        ``cli_model`` / ``cli_gpu_profile`` / ``cli_no_think`` mirror the
        existing CLI flags in scripts 2 and 4. When ``cli_model`` is ``None``,
        the section's ``ollama_model_default`` is used (interactive selection
        is left to the caller — only forced/default models flow through here).
        """
        cfg = get_config(section, {}) or {}
        url = cfg.get('ollama_url', 'http://localhost:11434')
        options = dict(cfg.get('ollama_options', {}))
        if 'num_ctx' not in options:
            options['num_ctx'] = 2048
        if cli_gpu_profile:
            profile = (get_config('gpu_profiles', {}) or {}).get(cli_gpu_profile)
            if profile:
                options = dict(profile)
                logging.info(f"Applied GPU profile '{cli_gpu_profile}': {options}")

        model = cli_model or cfg.get('ollama_model_default', default_model)
        options = apply_model_profile(options, model)

        profile = get_model_profile(model)
        api_timeout = int(cfg.get(api_timeout_key, default_api_timeout) * profile['timeout_multiplier'])
        operation_timeout = int(cfg.get(timeout_key, default_timeout) * profile['timeout_multiplier'])

        disable_thinking = bool(profile['is_thinking'] or cli_no_think)
        if profile['is_thinking'] and not cli_no_think:
            logging.info(f"Thinking model detected ({model}): auto-disabling chain-of-thought.")
        logging.info(
            f"ChatService — model={model} num_ctx={options.get('num_ctx')} "
            f"timeout x{profile['timeout_multiplier']} thinking={profile['is_thinking']} "
            f"no_think={disable_thinking}"
        )
        return cls(
            url=url,
            model=model,
            options=options,
            api_timeout=api_timeout,
            operation_timeout=operation_timeout,
            disable_thinking=disable_thinking,
        )

    # -- Public chat API -------------------------------------------------

    @property
    def client(self) -> ollama.Client:
        """Underlying ``ollama.Client``. Exposed for callers that need the
        raw ``client.list()`` API (model enumeration in scripts 2 & 4)."""
        return self._client

    def chat(
        self,
        prompt: str,
        *,
        error_default: str,
        log_context: str,
        operation_timeout: Optional[int] = None,
        response_format: Optional[str] = None,
    ) -> tuple[str, float]:
        """Single-shot prompt → text. Returns ``(text, elapsed_seconds)``."""
        return self.chat_options(
            prompt,
            options=self.options,
            error_default=error_default,
            log_context=log_context,
            operation_timeout=operation_timeout,
            response_format=response_format,
        )

    def chat_options(
        self,
        prompt: str,
        *,
        options: dict,
        error_default: str,
        log_context: str,
        operation_timeout: Optional[int] = None,
        response_format: Optional[str] = None,
    ) -> tuple[str, float]:
        """As :meth:`chat`, but with an explicit per-call ``options`` override
        (used by script 4 for dynamic ``num_ctx`` scaling on large prompts)."""
        timeout = operation_timeout if operation_timeout is not None else self.operation_timeout
        logging.info(f"Sending '{log_context}' request to Ollama (model: {self.model}).")

        result_holder: list[str | None] = [None]

        processed_prompt = (
            "DO NOT use <think> tags. Answer directly.\n\n" + prompt
            if self.disable_thinking
            else prompt
        )

        def worker():
            try:
                kw: dict = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": processed_prompt}],
                    "options": options,
                }
                if response_format:
                    kw["format"] = response_format

                if self.disable_thinking:
                    try:
                        response = self._client.chat(**kw, think=False)
                    except TypeError:
                        logging.info(
                            "'think' parameter not supported by ollama library, "
                            "using prompt-only approach."
                        )
                        response = self._client.chat(**kw)
                else:
                    response = self._client.chat(**kw)

                raw_content = response.get("message", {}).get("content", "").strip()
                cleaned = strip_think_block(raw_content)
                result_holder[0] = cleaned if cleaned else error_default
            except Exception as exc:
                logging.error(f"Error during request for '{log_context}': {exc}", exc_info=False)
                result_holder[0] = f"{error_default} (Error)"

        timed_out, elapsed = _run_with_timeout(worker, timeout)
        if timed_out:
            logging.error(f"General timeout for '{log_context}' after {timeout}s.")
            return f"Timeout: {error_default}", elapsed

        return result_holder[0] or error_default, elapsed

    # -- Convenience helpers ---------------------------------------------

    def list_models(self) -> list[str]:
        """Return available model names from the Ollama server (best-effort)."""
        try:
            response = self._client.list()
            models_list = response.get("models", [])
            names = [m.get("model") or m.get("name") for m in models_list]
            return [m for m in names if m]
        except Exception as exc:
            logging.error(f"Error listing Ollama models: {exc}", exc_info=True)
            return []

    def rebuild_with_model(self, model: str) -> None:
        """Re-target this service at a different model (used after the
        interactive prompt picks a model). Updates options/timeouts to match
        the new model's profile and recreates the underlying HTTP client.
        """
        self.model = model
        self.options = apply_model_profile(self.options, model)
        profile = get_model_profile(model)
        # Re-derive timeouts from the *current* values, *not* re-multiplied.
        # Callers that want the multiplier applied should construct a fresh
        # service via from_config().
        self.disable_thinking = bool(profile['is_thinking']) or self.disable_thinking
        self._client = ollama.Client(host=self.url, timeout=self.api_timeout)
