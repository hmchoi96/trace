"""Small xAI/Grok research client (x_search + web_search). Replaceable."""

from __future__ import annotations

import os
from typing import Any

import requests

XAI_API_URL = "https://api.x.ai/v1/responses"
DEFAULT_GROK_MODEL = "grok-4.6"
USD_TICKS = 10_000_000_000

_WEB_TOOL_KEYS = (
    "SERVER_SIDE_TOOL_WEB_SEARCH",
    "web_search",
    "web_search_with_snippets",
    "browse_page",
    "open_page",
    "open_page_with_find",
    "web_search_call",
)
_X_TOOL_KEYS = (
    "SERVER_SIDE_TOOL_X_SEARCH",
    "x_search",
    "x_user_search",
    "x_keyword_search",
    "x_semantic_search",
    "x_thread_fetch",
    "x_search_call",
)


def grok_research(
    prompt: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    timeout: int = 360,
    prompt_cache_key: str | None = None,
    max_turns: int | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Call Grok Responses API with server-side search tools. Returns text + citations + usage."""
    key = api_key or os.getenv("XAI_API_KEY")
    if not key:
        raise EnvironmentError("XAI_API_KEY is missing from .env")

    payload: dict[str, Any] = {
        "model": model or os.getenv("XAI_MODEL") or DEFAULT_GROK_MODEL,
        "stream": False,
        "store": False,
        "include": ["no_inline_citations"],
        "input": [{"role": "user", "content": prompt}],
        "tools": tools or [{"type": "x_search"}, {"type": "web_search"}],
    }
    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key
    if max_turns is not None:
        payload["max_turns"] = max_turns
    response = requests.post(
        XAI_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "text": extract_output_text(data),
        "citations": list(data.get("citations") or []),
        "usage": extract_research_usage(data),
    }


def extract_output_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"])
    chunks: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                chunks.append(str(content.get("text") or ""))
    return "\n".join(chunks).strip()


def extract_research_usage(data: dict[str, Any] | None) -> dict[str, Any]:
    """Copy provider usage fields as-is. Do not infer long-context pricing."""
    data = data or {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = (
        usage.get("prompt_tokens_details")
        or usage.get("input_tokens_details")
        or {}
    )
    if not isinstance(details, dict):
        details = {}
    out_details = (
        usage.get("output_tokens_details")
        or usage.get("completion_tokens_details")
        or {}
    )
    if not isinstance(out_details, dict):
        out_details = {}

    prompt_tokens = _first_int(usage.get("prompt_tokens"), usage.get("input_tokens"))
    cached_prompt_tokens = _first_int(
        usage.get("cached_prompt_text_tokens"),
        usage.get("cached_prompt_tokens"),
        details.get("cached_tokens"),
        details.get("cached_prompt_text_tokens"),
    )
    reasoning_tokens = _first_int(
        usage.get("reasoning_tokens"),
        out_details.get("reasoning_tokens"),
    )
    completion_tokens = _first_int(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
    )
    ticks = _as_int(usage.get("cost_in_usd_ticks"))
    cost_usd = (ticks / USD_TICKS) if ticks is not None else None
    cache_ratio = None
    if prompt_tokens and cached_prompt_tokens is not None:
        cache_ratio = cached_prompt_tokens / prompt_tokens

    web_billable, x_billable = _count_billable_tools(data, usage)
    web_attempted, x_attempted = _count_attempted_tools(data)
    return {
        "request_id": str(data["id"]) if data.get("id") not in (None, "") else None,
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "cache_ratio": cache_ratio,
        "reasoning_tokens": reasoning_tokens,
        "completion_tokens": completion_tokens,
        "web_calls_attempted": web_attempted,
        "web_calls_billable": web_billable,
        "x_calls_attempted": x_attempted,
        "x_calls_billable": x_billable,
        "cost_usd": cost_usd,
    }


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None


def _tool_bucket(name: str) -> str | None:
    key = (name or "").strip()
    if not key:
        return None
    upper = key.upper().replace("-", "_")
    if key in _WEB_TOOL_KEYS or "WEB_SEARCH" in upper or upper in (
        "BROWSE_PAGE", "OPEN_PAGE", "OPEN_PAGE_WITH_FIND", "WEB_SEARCH_WITH_SNIPPETS",
    ):
        return "web"
    if (
        key in _X_TOOL_KEYS
        or "X_SEARCH" in upper
        or upper in ("X_USER_SEARCH", "X_KEYWORD_SEARCH", "X_SEMANTIC_SEARCH", "X_THREAD_FETCH")
        or upper.startswith("X_KEYWORD")
        or upper.startswith("X_USER")
        or upper.startswith("X_SEMANTIC")
        or upper.startswith("X_THREAD")
    ):
        return "x"
    return None


def _count_billable_tools(data: dict[str, Any], usage: dict[str, Any]) -> tuple[int, int]:
    billable = data.get("server_side_tool_usage")
    if not isinstance(billable, dict):
        billable = usage.get("server_side_tool_usage")
    if not isinstance(billable, dict):
        billable = {}
    web, x = _count_tool_map(billable)
    details = (
        data.get("server_side_tool_usage_details")
        or usage.get("server_side_tool_usage_details")
        or {}
    )
    if isinstance(details, dict):
        web += _as_int(details.get("web_search_calls")) or 0
        x += _as_int(details.get("x_search_calls")) or 0
    return web, x


def _count_tool_map(usage_map: dict[str, Any]) -> tuple[int, int]:
    web = 0
    x = 0
    for key, raw in usage_map.items():
        n = _as_int(raw) or 0
        bucket = _tool_bucket(str(key))
        if bucket == "web":
            web += n
        elif bucket == "x":
            x += n
    return web, x


def _count_attempted_tools(data: dict[str, Any]) -> tuple[int, int]:
    web = 0
    x = 0
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        names = [
            str(item.get("type") or ""),
            str(item.get("name") or ""),
        ]
        action = item.get("action")
        if isinstance(action, dict):
            names.append(str(action.get("type") or ""))
            names.append(str(action.get("name") or ""))
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        names.append(str(fn.get("name") or ""))
        bucket = None
        for name in names:
            bucket = _tool_bucket(name)
            if bucket:
                break
        if bucket == "web":
            web += 1
        elif bucket == "x":
            x += 1
    for call in data.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(fn.get("name") or call.get("name") or call.get("type") or "")
        bucket = _tool_bucket(name)
        if bucket == "web":
            web += 1
        elif bucket == "x":
            x += 1
    return web, x
