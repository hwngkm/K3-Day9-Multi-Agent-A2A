"""
P2 — Order & Seller Agent
Interface : analyze(case: InputCase, loader: OlistDataLoader) -> OrderSellerEvidence
Đọc      : orders.csv, order_items.csv (qua loader)
LLM model: abab6.5s-chat via Minimax (OpenAI SDK)
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
from ..schemas import OrderSellerEvidence, InputCase

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

MODEL_NAME = "MiniMax-M2.5-highspeed"
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Order & Seller Agent in an e-commerce dispute resolution pipeline.

You are given order details and a list of items with their 'shipping_limit_date'.
Your task: determine if the seller handed off the items to the carrier late ('seller_handoff_late').

Rules:
1. Compare order_delivered_carrier_date vs shipping_limit_date for each item.
2. If ANY item's shipping_limit_date is earlier than the order_delivered_carrier_date, then the seller was late for that item.
3. If the seller was late for ANY item, the overall seller_handoff_late is true.
4. Also return a list of seller_ids who were late ('late_seller_ids').

Think carefully step by step, then output the final decision.
Reply with ONLY valid JSON, no markdown fences:
{"reasoning": "<step by step thought process>", "seller_handoff_late": <true|false>, "late_seller_ids": ["seller1", "seller2"]}
"""

def analyze(case: InputCase, loader: OlistDataLoader) -> OrderSellerEvidence:
    order_id = case.claimed_order_id
    order_row = dict(loader.require_order(order_id))
    items = [dict(item) for item in loader.order_items(order_id)]

    item_ids = []
    seller_ids = []
    evidence_ids = [f"order:{order_id}"]
    
    for item in items:
        i_id = f"{order_id}:{item['order_item_id']}"
        s_id = item['seller_id']
        if i_id not in item_ids:
            item_ids.append(i_id)
            evidence_ids.append(f"item:{i_id}")
        if s_id not in seller_ids:
            seller_ids.append(s_id)
            evidence_ids.append(f"seller:{s_id}")

    raw = {
        "order_id": order_id,
        "order_delivered_carrier_date": order_row.get("order_delivered_carrier_date", ""),
        "items": [
            {
                "seller_id": item.get("seller_id"),
                "shipping_limit_date": item.get("shipping_limit_date")
            }
            for item in items
        ]
    }

    llm_result = _call_llm(raw)

    return OrderSellerEvidence(
        order_id=order_id,
        order_status=order_row.get("order_status", ""),
        item_ids=tuple(item_ids)[:5],
        seller_ids=tuple(seller_ids)[:5],
        seller_handoff_late=bool(llm_result.get("seller_handoff_late", False)),
        late_seller_ids=tuple(llm_result.get("late_seller_ids", []))[:5],
        evidence_ids=tuple(evidence_ids),
    )

def _call_llm(raw: dict) -> dict:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        logger.warning("[OrderSellerAgent] MINIMAX_API_KEY not set, using fallback")
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
        logger.warning("[OrderSellerAgent] LLM call failed (%s), using fallback", exc)
        return _python_fallback(raw)

def _python_fallback(raw: dict) -> dict:
    late_seller_ids = set()
    carrier_date_str = raw["order_delivered_carrier_date"]
    if carrier_date_str:
        try:
            carrier_date = datetime.fromisoformat(carrier_date_str)
            for item in raw["items"]:
                limit_str = item.get("shipping_limit_date")
                if limit_str:
                    limit_date = datetime.fromisoformat(limit_str)
                    if carrier_date > limit_date:
                        late_seller_ids.add(item['seller_id'])
        except Exception:
            pass

    return {
        "seller_handoff_late": len(late_seller_ids) > 0,
        "late_seller_ids": list(late_seller_ids),
        "reasoning": "Fallback Python logic"
    }
