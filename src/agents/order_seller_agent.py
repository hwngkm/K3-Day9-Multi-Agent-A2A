"""Tool-calling Order & Seller Agent backed by a <=10B local model."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..data_loader import OlistDataLoader
from ..schemas import (
    MAX_ENTITY_IDS,
    MAX_EVIDENCE_IDS,
    InputCase,
    OrderSellerEvidence,
)
from ..tools.order_seller_tool import OrderSellerTool, OrderSellerToolResponse


# The assignment requires the model name in source code, not in .env.
MODEL_NAME = "qwen2.5:7b-instruct"
MODEL_PARAMETER_SIZE = "7.62B"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

TOOL_NAME = "query_order_seller"
TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Look up one Olist order and return verified order, item, seller, "
            "handoff-timeliness, and evidence facts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claimed_order_id": {
                    "type": "string",
                    "description": "Exact claimed_order_id from the input case.",
                }
            },
            "required": ["claimed_order_id"],
            "additionalProperties": False,
        },
    },
}

AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_status": {"type": "string"},
        "item_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_ENTITY_IDS,
        },
        "seller_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_ENTITY_IDS,
        },
        "seller_handoff_late": {"type": "boolean"},
        "late_seller_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "order_status",
        "item_ids",
        "seller_ids",
        "seller_handoff_late",
        "late_seller_ids",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the Order & Seller Agent for an Olist dispute system.
You may analyze only order, item, seller, and seller-handoff evidence returned by
the query_order_seller tool. Never use payment data and never decide a primary
issue, responsible-party type, refund, or resolution action.

Call query_order_seller exactly once with the claimed_order_id. After receiving
the tool result, select at most five affected item IDs and five seller IDs,
prioritizing any late item and late seller. Use only IDs present in the tool
result. Do not create evidence IDs; deterministic code derives them from your
verified entity selection. A null tool-level handoff result means there is no
positive evidence of late handoff, so the handoff boolean in the agent contract
must be false. Return JSON matching the supplied schema only.
"""


class AgentRuntimeError(RuntimeError):
    """Raised when the model runtime or tool-calling loop fails."""


class AgentOutputError(ValueError):
    """Raised when an LLM response conflicts with verified tool evidence."""


class ChatClient(Protocol):
    """Small injectable interface used by the agent and offline tests."""

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class OllamaChatClient:
    """Dependency-free client for Ollama's local ``/api/chat`` endpoint."""

    def __init__(
        self,
        host: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._host = (host or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip(
            "/"
        )
        self._timeout_seconds = timeout_seconds

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "model": MODEL_NAME,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": 0},
        }
        if tools is not None:
            payload["tools"] = list(tools)
        if response_format is not None:
            payload["format"] = dict(response_format)

        request = Request(
            f"{self._host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                result = json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AgentRuntimeError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise AgentRuntimeError(
                "Cannot reach Ollama. Install/start Ollama and pull "
                f"{MODEL_NAME!r}; endpoint: {self._host}"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentRuntimeError(f"Invalid Ollama response: {exc}") from exc

        if not isinstance(result, Mapping):
            raise AgentRuntimeError("Ollama response must be a JSON object")
        return result


def _response_message(response: Mapping[str, Any]) -> dict[str, Any]:
    message = response.get("message")
    if not isinstance(message, Mapping):
        raise AgentRuntimeError("Model response is missing a message object")
    return dict(message)


def _tool_arguments(
    message: Mapping[str, Any],
    expected_order_id: str,
) -> str:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise AgentRuntimeError(f"{MODEL_NAME} must call {TOOL_NAME} exactly once")

    function = (
        tool_calls[0].get("function")
        if isinstance(tool_calls[0], Mapping)
        else None
    )
    if not isinstance(function, Mapping) or function.get("name") != TOOL_NAME:
        raise AgentRuntimeError(f"Model called an unexpected tool; expected {TOOL_NAME}")

    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise AgentRuntimeError("Tool arguments are not valid JSON") from exc
    if not isinstance(arguments, Mapping):
        raise AgentRuntimeError("Tool arguments must be a JSON object")

    order_id = arguments.get("claimed_order_id")
    if not isinstance(order_id, str) or order_id != expected_order_id:
        raise AgentRuntimeError(
            "Model must pass the unchanged claimed_order_id to the tool"
        )
    return order_id


def _parse_agent_json(message: Mapping[str, Any]) -> Mapping[str, Any]:
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AgentOutputError("Model returned empty structured output")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentOutputError("Model output is not valid JSON") from exc
    if not isinstance(result, Mapping):
        raise AgentOutputError("Model output must be a JSON object")
    return result


def _string_list(
    output: Mapping[str, Any],
    field: str,
    maximum: int | None = None,
) -> tuple[str, ...]:
    values = output.get(field)
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value for value in values
    ):
        raise AgentOutputError(f"{field} must be an array of non-empty strings")
    if len(values) != len(set(values)):
        raise AgentOutputError(f"{field} must not contain duplicates")
    if maximum is not None and len(values) > maximum:
        raise AgentOutputError(f"{field} cannot contain more than {maximum} values")
    return tuple(values)


def _validated_evidence(
    output: Mapping[str, Any],
    facts: OrderSellerToolResponse,
) -> OrderSellerEvidence:
    """Reject hallucinated IDs or conclusions before the P2 handoff."""

    order_status = output.get("order_status")
    if order_status != facts["order_status"]:
        raise AgentOutputError("order_status conflicts with tool evidence")

    item_ids = _string_list(output, "item_ids", MAX_ENTITY_IDS)
    seller_ids = _string_list(output, "seller_ids", MAX_ENTITY_IDS)
    late_seller_ids = _string_list(output, "late_seller_ids")

    valid_item_ids = {item["affected_item_id"] for item in facts["items"]}
    valid_seller_ids = set(facts["seller_ids"])
    if not set(item_ids).issubset(valid_item_ids):
        raise AgentOutputError("item_ids contains an ID absent from tool evidence")
    if not set(seller_ids).issubset(valid_seller_ids):
        raise AgentOutputError("seller_ids contains an ID absent from tool evidence")
    if facts["items"] and (not item_ids or not seller_ids):
        raise AgentOutputError("item_ids and seller_ids cannot omit all known entities")

    expected_late = facts["seller_handoff_late"] is True
    reported_late = output.get("seller_handoff_late")
    if not isinstance(reported_late, bool) or reported_late is not expected_late:
        raise AgentOutputError("seller_handoff_late conflicts with tool evidence")
    if set(late_seller_ids) != set(facts["late_seller_ids"]):
        raise AgentOutputError("late_seller_ids conflicts with tool evidence")
    if not set(late_seller_ids).issubset(set(seller_ids)):
        raise AgentOutputError("seller_ids must include every reported late seller")

    late_item_ids = {
        item["affected_item_id"]
        for item in facts["items"]
        if item["seller_handoff_late"] is True
    }
    if expected_late:
        if not late_item_ids.intersection(item_ids):
            raise AgentOutputError("item_ids must include a late item")

    # Evidence IDs are canonical data products, never copied or invented by the
    # model. Put rule-relevant late evidence first, then fill remaining slots.
    prioritized_item_ids = [
        *[item_id for item_id in item_ids if item_id in late_item_ids],
        *[item_id for item_id in item_ids if item_id not in late_item_ids],
    ]
    prioritized_seller_ids = [
        *[seller_id for seller_id in seller_ids if seller_id in late_seller_ids],
        *[seller_id for seller_id in seller_ids if seller_id not in late_seller_ids],
    ]
    evidence_ids = tuple(
        dict.fromkeys(
            [f"order:{facts['order_id']}"]
            + [f"item:{item_id}" for item_id in prioritized_item_ids]
            + [f"seller:{seller_id}" for seller_id in prioritized_seller_ids]
        )
    )[:MAX_EVIDENCE_IDS]

    valid_evidence_ids = set(facts["evidence_ids"])
    if not set(evidence_ids).issubset(valid_evidence_ids):
        raise AgentOutputError("derived evidence conflicts with tool evidence")

    return OrderSellerEvidence(
        order_id=facts["order_id"],
        order_status=order_status,
        item_ids=item_ids,
        seller_ids=seller_ids,
        seller_handoff_late=reported_late,
        late_seller_ids=late_seller_ids,
        evidence_ids=evidence_ids,
    )


def analyze(
    case: InputCase,
    loader: OlistDataLoader,
    *,
    client: ChatClient | None = None,
) -> OrderSellerEvidence:
    """Run the P2 tool-calling loop and return a verified agent handoff."""

    chat_client = client or OllamaChatClient()
    tool = OrderSellerTool(loader)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Analyze case {case.case_id}. The claimed_order_id is "
                f"{case.claimed_order_id}."
            ),
        },
    ]

    tool_call_response = chat_client.chat(messages, tools=[TOOL_DEFINITION])
    assistant_tool_call = _response_message(tool_call_response)
    tool_order_id = _tool_arguments(assistant_tool_call, case.claimed_order_id)
    facts = tool.lookup(tool_order_id)
    if not facts["order_found"]:
        raise AgentOutputError(f"Unknown claimed_order_id: {tool_order_id}")

    messages.append(assistant_tool_call)
    messages.append(
        {
            "role": "tool",
            "tool_name": TOOL_NAME,
            "content": json.dumps(facts, ensure_ascii=False),
        }
    )
    final_response = chat_client.chat(
        messages,
        response_format=AGENT_OUTPUT_SCHEMA,
    )
    output = _parse_agent_json(_response_message(final_response))
    return _validated_evidence(output, facts)
