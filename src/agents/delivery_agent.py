"""
P3 — Delivery Agent
Interface : analyze(case: InputCase, loader: OlistDataLoader) -> DeliveryEvidence
Đọc      : orders.csv (qua loader)
Không đọc: payment, sellers, products, order_items
LLM model: llama-3.1-8b-instant via Groq (hard-coded, ≤10B params)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from groq import Groq
from dotenv import load_dotenv

from ..data_loader import OlistDataLoader
from ..schemas import DeliveryEvidence, InputCase

# Load .env từ root repo (2 levels up: agents/ -> src/ -> root)
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

# ── Hard-coded model name (bắt buộc theo README mục 9, không để vào .env) ──
MODEL_NAME = "llama-3.1-8b-instant"

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Delivery Agent in an e-commerce dispute resolution pipeline.

You are given order delivery data and the COMPUTED result of whether the delivery was late.
Your task: write a clear, concise ONE-sentence explanation of why the delivery is late or on time.

The 'carrier_delivered_late' field is already computed for you (do NOT change it).
Just confirm it and explain the date comparison in plain language.

Reply with ONLY valid JSON, no markdown fences, no extra text:
{"carrier_delivered_late": <bool as given>, "reasoning": "<one sentence explanation>"}
"""


def analyze(case: InputCase, loader: OlistDataLoader) -> DeliveryEvidence:
    """Entry point called by Coordinator (parallel with P2 and P4)."""
    order_id = case.claimed_order_id
    order_row = dict(loader.require_order(order_id))

    raw = _extract_fields(order_row, order_id)

    # Python computes the authoritative boolean (always accurate)
    python_result = _python_compute(raw)

    # LLM generates the reasoning explanation (and confirms boolean)
    llm_result = _call_llm(raw, python_result["carrier_delivered_late"])
    if llm_result.get("carrier_delivered_late") != python_result["carrier_delivered_late"]:
        logger.warning(
            "[DeliveryAgent] LLM disagreed with Python computation (LLM=%s, Python=%s) "
            "— trusting Python for order %s",
            llm_result.get("carrier_delivered_late"),
            python_result["carrier_delivered_late"],
            order_id,
        )

    return DeliveryEvidence(
        order_id=order_id,
        carrier_delivered_late=python_result["carrier_delivered_late"],
        order_delivered_carrier_date=raw["order_delivered_carrier_date"] or None,
        order_delivered_customer_date=raw["order_delivered_customer_date"] or None,
        order_estimated_delivery_date=raw["order_estimated_delivery_date"] or None,
        evidence_ids=(f"order:{order_id}",),
    )


def _extract_fields(row: dict, order_id: str) -> dict:
    return {
        "order_id": order_id,
        "order_status": row.get("order_status", ""),
        "order_delivered_customer_date": row.get("order_delivered_customer_date", ""),
        "order_estimated_delivery_date": row.get("order_estimated_delivery_date", ""),
        "order_delivered_carrier_date": row.get("order_delivered_carrier_date", ""),
    }


def _call_llm(raw: dict, computed_late: bool) -> dict:
    """Gọi Groq LLM để lấy reasoning. Fallback nếu lỗi."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning("[DeliveryAgent] GROQ_API_KEY not set, skipping LLM call")
        return {"carrier_delivered_late": computed_late, "reasoning": "API key not set"}

    user_msg = dict(raw)
    user_msg["carrier_delivered_late"] = computed_late  # tell LLM the computed answer

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_msg, ensure_ascii=False)},
            ],
            temperature=0.0,
            max_tokens=128,
        )
        text = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        return json.loads(text)
    except Exception as exc:
        logger.warning("[DeliveryAgent] LLM call failed (%s), using Python result only", exc)
        return {"carrier_delivered_late": computed_late, "reasoning": f"LLM unavailable: {type(exc).__name__}"}


def _python_compute(raw: dict) -> dict:
    """Deterministic, authoritative computation of carrier_delivered_late."""
    status = raw.get("order_status", "")
    if status in ("canceled", "unavailable"):
        return {"carrier_delivered_late": False, "reasoning": f"Order status is {status}"}
    try:
        delivered_str = raw["order_delivered_customer_date"]
        estimated_str = raw["order_estimated_delivery_date"]
        if not delivered_str or not estimated_str:
            return {"carrier_delivered_late": False, "reasoning": "Missing timestamp(s)"}
        delivered = datetime.fromisoformat(delivered_str)
        estimated = datetime.fromisoformat(estimated_str)
        late = delivered > estimated
        delta = (delivered - estimated).total_seconds() / 86400
        return {
            "carrier_delivered_late": late,
            "reasoning": f"Delivered {delivered_str[:10]}, estimated {estimated_str[:10]}, delta {delta:+.2f} days",
        }
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("[DeliveryAgent] Computation error: %s", exc)
        return {"carrier_delivered_late": False, "reasoning": "Parse error, defaulting to not late"}
