"""Provider-agnostic agentic tool-use loop.

Supports Anthropic and OpenAI. Set LLM_PROVIDER=anthropic|openai in the environment.
The loop: send the conversation -> if the model calls a tool, execute it locally,
append the tool_result, send again. Repeat until the model returns end_turn.
"""
import json
from typing import Any, Callable

from . import config

MAX_TOOL_TURNS = 12  # Hard cap to prevent runaway loops

if config.LLM_PROVIDER == "openai":
    from openai import OpenAI
    _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
else:
    from anthropic import Anthropic
    _anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool defs (input_schema) to OpenAI format (parameters)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _run_anthropic_loop(
    *,
    model: str,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Any],
    max_tokens: int,
    verbose: bool,
) -> tuple[str, list[dict]]:
    messages: list[dict] = [{"role": "user", "content": user_message}]

    for _ in range(MAX_TOOL_TURNS):
        response = _anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = "".join(b.text for b in response.content if b.type == "text")
            return text, messages

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if verbose:
                    print(f"  [tool] {block.name}({json.dumps(block.input)})")
                result = tool_executor(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        text = "".join(b.text for b in response.content if b.type == "text")
        return text or f"[stopped: {response.stop_reason}]", messages

    raise RuntimeError(f"Agent exceeded {MAX_TOOL_TURNS} tool turns without finishing.")


def _run_openai_loop(
    *,
    model: str,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Any],
    max_tokens: int,
    verbose: bool,
) -> tuple[str, list[dict]]:
    openai_tools = _anthropic_tools_to_openai(tools)
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    for _ in range(MAX_TOOL_TURNS):
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = _openai_client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # Append assistant turn (serialize to plain dict for history)
        assistant_entry: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if finish_reason == "stop":
            return msg.content or "", messages

        if finish_reason == "tool_calls" and msg.tool_calls:
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                if verbose:
                    print(f"  [tool] {tc.function.name}({json.dumps(args)})")
                result = tool_executor(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
            continue

        return msg.content or f"[stopped: {finish_reason}]", messages

    raise RuntimeError(f"Agent exceeded {MAX_TOOL_TURNS} tool turns without finishing.")


def run_agent_loop(
    *,
    model: str,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Any],
    max_tokens: int = 4096,
    verbose: bool = False,
) -> tuple[str, list[dict]]:
    """Run an agent until it returns a final text response.

    Returns (final_text, full_message_history).
    """
    if config.LLM_PROVIDER == "openai":
        return _run_openai_loop(
            model=model,
            system=system,
            user_message=user_message,
            tools=tools,
            tool_executor=tool_executor,
            max_tokens=max_tokens,
            verbose=verbose,
        )
    return _run_anthropic_loop(
        model=model,
        system=system,
        user_message=user_message,
        tools=tools,
        tool_executor=tool_executor,
        max_tokens=max_tokens,
        verbose=verbose,
    )
