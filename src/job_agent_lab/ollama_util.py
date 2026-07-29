"""Shared helpers for calling a local Ollama chat API."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_OLLAMA_API_BASE = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "ollama_chat/gemma4:e4b-mlx"


def normalize_ollama_model(model: str) -> str:
    """Strip LiteLLM-style prefixes so Ollama receives a native model tag.

    Args:
        model: Model id such as ``ollama_chat/gemma4:e4b-mlx`` or ``gemma4:e4b-mlx``.

    Returns:
        Model tag for the Ollama API (e.g. ``gemma4:e4b-mlx``).
    """
    name = (model or "").strip()
    for prefix in ("ollama_chat/", "ollama/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def resolve_ollama_api_base(api_base: str | None = None) -> str:
    """Resolve Ollama base URL from argument or ``OLLAMA_API_BASE``."""
    return (api_base or os.environ.get("OLLAMA_API_BASE") or DEFAULT_OLLAMA_API_BASE).rstrip(
        "/"
    )


def resolve_ollama_model(model: str | None = None) -> str:
    """Resolve and normalize Ollama model id from argument or ``OLLAMA_MODEL``."""
    raw = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    name = normalize_ollama_model(raw)
    if not name:
        raise ValueError("OLLAMA_MODEL is empty")
    return name


def ollama_chat(
    *,
    messages: list[dict[str, str]],
    api_base: str | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 120.0,
    format_json: bool = False,
) -> str:
    """Call Ollama ``POST /api/chat`` and return the assistant message text.

    Args:
        messages: Chat messages with ``role`` and ``content``.
        api_base: Ollama base URL override.
        model: Model id override (may include ``ollama_chat/`` prefix).
        client: Optional ``httpx.Client`` (tests inject a mock transport).
        timeout: Request timeout in seconds.
        format_json: If True, request JSON-object formatting from Ollama.

    Returns:
        Assistant ``message.content`` stripped of surrounding whitespace.

    Raises:
        ValueError: If the model returns empty content.
        httpx.HTTPError: If the HTTP call fails.
    """
    base = resolve_ollama_api_base(api_base)
    ollama_model = resolve_ollama_model(model)
    payload: dict[str, Any] = {
        "model": ollama_model,
        "stream": False,
        "messages": messages,
    }
    if format_json:
        payload["format"] = "json"

    url = f"{base}/api/chat"
    if client is None:
        with httpx.Client(timeout=timeout) as owned:
            response = owned.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    else:
        response = client.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()

    message = data.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise ValueError("Ollama returned empty content")
    return content
