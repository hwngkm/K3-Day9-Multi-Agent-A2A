"""
P4 — Payment Agent
Interface : analyze(case: InputCase, loader: OlistDataLoader) -> PaymentEvidence
Đọc      : order_items.csv, order_payments.csv (qua loader)
LLM model: abab6.5s-chat via Minimax (OpenAI SDK)
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

from ..data_loader import OlistDataLoader
from ..schemas import PaymentEvidence, InputCase, money

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

MODEL_NAME = "MiniMax-M2.5-highspeed"
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Payment Agent in an e-commerce dispute resolution pipeline.

You are given a list of item prices, freight values, and payment values.
Your task is to calculate the totals and determine if it is a valid split payment.

Rules:
1. item_total = sum of all item prices.
2. freight_total = sum of all item freight_values.
3. payment_total = sum of all payment_values.
4. is_split = there are 2 or more payments.
5. diff = absolute value of (payment_total - (item_total + freight_total)).
6. valid_split_payment is TRUE if and only if (is_split is TRUE) AND (diff <= 0.10).

Think carefully step by step. Show your math.
Reply with ONLY valid JSON, no markdown fences:
{"reasoning": "<step by step math>", "item_total_brl": <float>, "freight_total_brl": <float>, "payment_total_brl": <float>, "valid_split_payment": <true|false>}
"""

def analyze(case: InputCase, loader: OlistDataLoader) -> PaymentEvidence:
    order_id = case.claimed_order_id
    items = [dict(item) for item in loader.order_items(order_id)]
    payments = [dict(payment) for payment in loader.order_payments(order_id)]

    payment_ids = []
    evidence_ids = []
    for p in payments:
        seq = p.get("payment_sequential")
        if seq:
            p_id = f"{order_id}:{seq}"
            payment_ids.append(p_id)
            evidence_ids.append(f"payment:{p_id}")

    raw = {
        "order_id": order_id,
        "items": [{"price": i.get("price"), "freight_value": i.get("freight_value")} for i in items],
        "payments": [{"payment_sequential": p.get("payment_sequential"), "payment_value": p.get("payment_value")} for p in payments]
    }

    llm_result = _call_llm(raw)

    return PaymentEvidence(
        order_id=order_id,
        item_total_brl=money(Decimal(str(llm_result.get("item_total_brl", 0)))),
        freight_total_brl=money(Decimal(str(llm_result.get("freight_total_brl", 0)))),
        payment_total_brl=money(Decimal(str(llm_result.get("payment_total_brl", 0)))),
        valid_split_payment=bool(llm_result.get("valid_split_payment", False)),
        payment_ids=tuple(payment_ids)[:5],
        evidence_ids=tuple(evidence_ids),
    )

def _call_llm(raw: dict) -> dict:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        logger.warning("[PaymentAgent] MINIMAX_API_KEY not set, using fallback")
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
        logger.warning("[PaymentAgent] LLM call failed (%s), using fallback", exc)
        return _python_fallback(raw)

def _python_fallback(raw: dict) -> dict:
    item_total = sum(Decimal(str(i.get("price", 0) or 0)) for i in raw["items"])
    freight_total = sum(Decimal(str(i.get("freight_value", 0) or 0)) for i in raw["items"])
    payment_total = sum(Decimal(str(p.get("payment_value", 0) or 0)) for p in raw["payments"])
    is_split = len(raw["payments"]) >= 2
    diff = abs(payment_total - (item_total + freight_total))
    valid_split = is_split and (diff <= Decimal("0.10"))

    return {
        "item_total_brl": float(item_total),
        "freight_total_brl": float(freight_total),
        "payment_total_brl": float(payment_total),
        "valid_split_payment": valid_split,
        "reasoning": "Fallback Python logic"
    }
