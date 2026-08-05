"""P3 — Delivery Agent  (tool-calling worker, EC_POLICY_V1)

Interface : analyze(case: InputCase, loader: OlistDataLoader) -> DeliveryEvidence
Reads     : orders.csv, order_items.csv (via DeliveryTool — evidence IDs only)
Forbidden : order_payments.csv, sellers.csv, products.csv
LLM model : qwen2.5:7b-instruct via Ollama local  (hard-coded, ≤10B params)

Agent loop
──────────
1. User message  → LLM receives case_id + claimed_order_id
2. LLM calls     → query_delivery(claimed_order_id)   [exactly once]
3. Tool executes → DeliveryTool.lookup() returns timestamps + computed flag
4. LLM outputs   → JSON with confirmed carrier_delivered_late + reasoning
5. Python guard  → rejects any LLM output that contradicts the tool result
6. Handoff       → DeliveryEvidence returned to Coordinator (P1)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..data_loader import OlistDataLoader
from ..schemas import (
    MAX_EVIDENCE_IDS,
    DeliveryEvidence,
    InputCase,
)
from ..tools.delivery_tool import DeliveryTool, DeliveryToolResponse

# ── Hard-coded model name (bắt buộc theo README mục 9, không để vào .env) ──
MODEL_NAME = "qwen2.5:7b-instruct"
MODEL_PARAMETER_SIZE = "7.62B"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

TOOL_NAME = "query_delivery"
TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Look up delivery timestamps for an Olist order and determine "
            "whether the carrier delivered the package after the estimated "
            "delivery date."
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

# Structured-output schema for the LLM final response
AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "carrier_delivered_late": {"type": "boolean"},
        "reasoning": {
            "type": "string",
            "description": "One concise sentence explaining the date comparison.",
        },
    },
    "required": ["carrier_delivered_late", "reasoning"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the Delivery Agent for an Olist e-commerce dispute system.

Your sole responsibility: determine whether the carrier delivered the order
AFTER the estimated delivery date and explain why with one concise sentence.

Rules:
- Call query_delivery exactly once with the claimed_order_id.
- After receiving the tool result, read `carrier_delivered_late` (already
  computed by the tool from the CSV data). You MUST confirm this exact boolean
  value — do NOT override or invent a different value.
- Write a single `reasoning` sentence that cites the actual ISO dates from the
  tool result (e.g. "Delivered 2018-01-15, estimated 2018-01-10 → 5 days late").
- If `delivery_evaluable` is false or timestamps are null, set reasoning to
  explain the missing data.
- NEVER access or infer payment, seller, or product information.
- NEVER decide the primary_issue, refund amount, or responsible party.
- Return ONLY valid JSON matching the schema: no markdown fences, no extra text.
"""

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom error types (mirrors order_seller_agent pattern)
# ---------------------------------------------------------------------------


class AgentRuntimeError(RuntimeError):
    """Raised when the model runtime or tool-calling loop fails."""


class AgentOutputError(ValueError):
    """Raised when an LLM response conflicts with verified tool evidence."""


# ---------------------------------------------------------------------------
# Ollama chat client (identical pattern to P2 — injectable via Protocol)
# ---------------------------------------------------------------------------


class ChatClient(Protocol):
    """Minimal injectable interface used by the agent and offline tests."""

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
            raise AgentRuntimeError(
                f"Ollama returned HTTP {exc.code}: {detail}"
            ) from exc
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _response_message(response: Mapping[str, Any]) -> dict[str, Any]:
    message = response.get("message")
    if not isinstance(message, Mapping):
        raise AgentRuntimeError("Model response is missing a message object")
    return dict(message)


def _tool_arguments(
    message: Mapping[str, Any],
    expected_order_id: str,
) -> str:
    """Validate and extract claimed_order_id from the LLM tool call."""
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise AgentRuntimeError(f"{MODEL_NAME} must call {TOOL_NAME} exactly once")

    function = (
        tool_calls[0].get("function")
        if isinstance(tool_calls[0], Mapping)
        else None
    )
    if not isinstance(function, Mapping) or function.get("name") != TOOL_NAME:
        raise AgentRuntimeError(
            f"Model called an unexpected tool; expected {TOOL_NAME}"
        )

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


def _validated_evidence(
    output: Mapping[str, Any],
    facts: DeliveryToolResponse,
) -> DeliveryEvidence:
    """Reject hallucinated boolean or missing keys before the P3 handoff.

    The tool's ``carrier_delivered_late`` is the authoritative value.
    The LLM must confirm it exactly; any disagreement raises AgentOutputError.
    """
    # --- carrier_delivered_late must match the tool (Python-computed) value ---
    reported_late = output.get("carrier_delivered_late")
    if not isinstance(reported_late, bool):
        raise AgentOutputError(
            "carrier_delivered_late must be a boolean in the agent output"
        )
    tool_late = facts["carrier_delivered_late"]
    # tool_late is None when delivery_evaluable is False; treat as False
    expected_late: bool = bool(tool_late)
    if reported_late is not expected_late:
        raise AgentOutputError(
            f"carrier_delivered_late conflicts with tool evidence "
            f"(LLM={reported_late}, tool={tool_late})"
        )

    # --- reasoning must be a non-empty string ---
    reasoning = output.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise AgentOutputError("reasoning must be a non-empty string")

    # --- evidence IDs: use tool's pre-built list, capped at MAX_EVIDENCE_IDS ---
    evidence_ids = tuple(facts["evidence_ids"])[:MAX_EVIDENCE_IDS]

    return DeliveryEvidence(
        order_id=facts["order_id"],
        carrier_delivered_late=reported_late,
        order_delivered_carrier_date=facts["order_delivered_carrier_date"],
        order_delivered_customer_date=facts["order_delivered_customer_date"],
        order_estimated_delivery_date=facts["order_estimated_delivery_date"],
        evidence_ids=evidence_ids,
    )


# ---------------------------------------------------------------------------
# Public entry point — called by Coordinator (P1) in parallel with P2 & P4
# ---------------------------------------------------------------------------


def analyze(
    case: InputCase,
    loader: OlistDataLoader,
    *,
    client: ChatClient | None = None,
) -> DeliveryEvidence:
    """Run the P3 tool-calling loop and return a verified delivery handoff.

    The LLM is given the case context and must call ``query_delivery`` exactly
    once.  Python executes the tool (deterministic CSV lookup + timestamp
    comparison) and feeds the result back.  The LLM then confirms the computed
    boolean and writes a one-sentence reasoning.  If the LLM's ``carrier_
    delivered_late`` disagrees with the tool result, AgentOutputError is raised
    so the Coordinator can handle the failure gracefully.
    """
    chat_client = client or OllamaChatClient()
    tool = DeliveryTool(loader)

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

    # ── Turn 1: LLM issues tool call ──────────────────────────────────────
    tool_call_response = chat_client.chat(messages, tools=[TOOL_DEFINITION])
    assistant_tool_call = _response_message(tool_call_response)
    tool_order_id = _tool_arguments(assistant_tool_call, case.claimed_order_id)

    # ── Tool execution (deterministic Python) ─────────────────────────────
    facts = tool.lookup(tool_order_id)
    if not facts["order_found"]:
        raise AgentOutputError(f"Unknown claimed_order_id: {tool_order_id}")

    logger.debug(
        "[DeliveryAgent] order=%s  delivered=%s  estimated=%s  late=%s",
        tool_order_id,
        facts["order_delivered_customer_date"],
        facts["order_estimated_delivery_date"],
        facts["carrier_delivered_late"],
    )

    # ── Turn 2: Feed tool result back, get structured final answer ────────
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

    # ── Guard: reject hallucination before handoff ─────────────────────────
    return _validated_evidence(output, facts)
