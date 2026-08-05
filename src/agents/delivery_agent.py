"""
P3 — Delivery Agent
Interface : analyze(case: InputCase, loader: OlistDataLoader) -> DeliveryEvidence
Đọc      : orders.csv (qua loader)
LLM model: abab6.5s-chat via Minimax (OpenAI SDK compatible)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

from ..data_loader import OlistDataLoader
from ..schemas import DeliveryEvidence, InputCase

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

MODEL_NAME = "MiniMax-M2.5-highspeed"
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Delivery Agent in an e-commerce dispute resolution pipeline.

Your task is to determine if the order was delivered AFTER the estimated delivery date.
You are given the delivery date and estimated date in ISO format.

Rules:
1. Compare order_delivered_customer_date vs order_estimated_delivery_date.
2. If order_delivered_customer_date is missing/empty → carrier_delivered_late = false
3. If order_estimated_delivery_date is missing/empty → carrier_delivered_late = false  
4. If order_status is "canceled" or "unavailable" → carrier_delivered_late = false
5. Otherwise: carrier_delivered_late = (delivered date > estimated date)

Think carefully step by step, then output the final decision.
Reply with ONLY valid JSON, no markdown fences, no extra text:
{"reasoning": "<step by step thought process>", "carrier_delivered_late": <true|false>}
"""

def analyze(case: InputCase, loader: OlistDataLoader) -> DeliveryEvidence:
    order_id = case.claimed_order_id
    order_row = dict(loader.require_order(order_id))

    raw = {
        "order_id": order_id,
        "order_status": order_row.get("order_status", ""),
        "order_delivered_customer_date": order_row.get("order_delivered_customer_date", ""),
        "order_estimated_delivery_date": order_row.get("order_estimated_delivery_date", ""),
        "order_delivered_carrier_date": order_row.get("order_delivered_carrier_date", ""),
    }

    # Agent is fully driven by LLM decision
    llm_result = _call_llm(raw)

    return DeliveryEvidence(
        order_id=order_id,
        carrier_delivered_late=bool(llm_result.get("carrier_delivered_late", False)),
        order_delivered_carrier_date=raw["order_delivered_carrier_date"] or None,
        order_delivered_customer_date=raw["order_delivered_customer_date"] or None,
        order_estimated_delivery_date=raw["order_estimated_delivery_date"] or None,
        evidence_ids=(f"order:{order_id}",),
    )

def _call_llm(raw: dict) -> dict:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        logger.warning("[DeliveryAgent] MINIMAX_API_KEY not set, using Python fallback")
        return _python_fallback(raw)

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.minimax.io/v1")
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(raw, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        import re
        text = response.choices[0].message.content.strip()
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        return json.loads(text)
    except Exception as exc:
        logger.warning("[DeliveryAgent] LLM call failed (%s), using Python fallback", exc)
        return _python_fallback(raw)

def _python_fallback(raw: dict) -> dict:
    """Fallback only when API crashes or rate limits."""
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
        return {
            "carrier_delivered_late": delivered > estimated,
            "reasoning": "Fallback Python logic"
        }
    except Exception:
        return {"carrier_delivered_late": False, "reasoning": "Fallback parse error"}
