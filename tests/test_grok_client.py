"""Usage parsing for Grok research telemetry. No live API."""

from grok_client import USD_TICKS, extract_research_usage


def test_extracts_responses_usage_and_ticks():
    usage = extract_research_usage({
        "id": "resp_123",
        "usage": {
            "input_tokens": 80000,
            "output_tokens": 400,
            "cost_in_usd_ticks": int(0.22 * USD_TICKS),
            "input_tokens_details": {"cached_tokens": 50000},
            "output_tokens_details": {"reasoning_tokens": 1200},
            "server_side_tool_usage": {
                "SERVER_SIDE_TOOL_WEB_SEARCH": 4,
                "SERVER_SIDE_TOOL_X_SEARCH": 2,
            },
        },
        "output": [
            {"type": "web_search_call"},
            {"type": "web_search_call"},
            {"type": "web_search_call"},
            {"type": "web_search_call"},
            {"type": "web_search_call"},
            {"type": "x_search_call"},
            {"type": "x_search_call"},
            {"type": "message", "content": [{"type": "output_text", "text": "{}"}]},
        ],
    })
    assert usage["request_id"] == "resp_123"
    assert usage["prompt_tokens"] == 80000
    assert usage["cached_prompt_tokens"] == 50000
    assert usage["cache_ratio"] == 50000 / 80000
    assert usage["reasoning_tokens"] == 1200
    assert usage["completion_tokens"] == 400
    assert usage["web_calls_billable"] == 4
    assert usage["x_calls_billable"] == 2
    assert usage["web_calls_attempted"] == 5
    assert usage["x_calls_attempted"] == 2
    assert abs(usage["cost_usd"] - 0.22) < 1e-9
    assert "prompt_ge_200k" not in usage
    # cached is a subset of prompt, not added to it
    assert usage["prompt_tokens"] + usage["cached_prompt_tokens"] != usage["prompt_tokens"]


def test_chat_style_prompt_token_names():
    usage = extract_research_usage({
        "usage": {
            "prompt_tokens": 1000,
            "cached_prompt_text_tokens": 200,
            "reasoning_tokens": 50,
            "completion_tokens": 30,
        },
        "tool_calls": [
            {"function": {"name": "browse_page"}},
            {"function": {"name": "x_thread_fetch"}},
        ],
        "server_side_tool_usage": {
            "SERVER_SIDE_TOOL_WEB_SEARCH": 1,
            "SERVER_SIDE_TOOL_X_SEARCH": 1,
        },
    })
    assert usage["prompt_tokens"] == 1000
    assert usage["cached_prompt_tokens"] == 200
    assert usage["reasoning_tokens"] == 50
    assert usage["completion_tokens"] == 30
    assert usage["web_calls_attempted"] == 1
    assert usage["x_calls_attempted"] == 1
    assert usage["web_calls_billable"] == 1
    assert usage["x_calls_billable"] == 1


def test_x_search_custom_tool_call_and_usage_details():
    usage = extract_research_usage({
        "usage": {
            "input_tokens": 10,
            "server_side_tool_usage_details": {
                "web_search_calls": 2,
                "x_search_calls": 6,
            },
        },
        "output": [
            {"type": "custom_tool_call", "name": "x_keyword_search"},
            {"type": "custom_tool_call", "name": "x_thread_fetch"},
            {"type": "x_search_call"},
            {"type": "web_search_call"},
        ],
    })
    assert usage["x_calls_attempted"] == 3
    assert usage["web_calls_attempted"] == 1
    assert usage["x_calls_billable"] == 6
    assert usage["web_calls_billable"] == 2
    usage = extract_research_usage({})
    assert usage["prompt_tokens"] is None
    assert usage["cost_usd"] is None
    assert usage["cache_ratio"] is None
    assert usage["web_calls_billable"] == 0
    assert usage["web_calls_attempted"] == 0
