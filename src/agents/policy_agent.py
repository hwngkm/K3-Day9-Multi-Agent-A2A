"""
P5 — Policy Agent
Interface : analyze(evidence: EvidenceBundle) -> PolicyResult
Đọc      : Không đọc CSV, chỉ dùng evidence
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

from ..schemas import (
    EvidenceBundle, PolicyResult, RankedCause, ResponsibleParty,
    ACTION_REQUIRED, NO_ACTION, PLATFORM_PARTY_ID, LOGISTICS_PARTY_ID
)

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

MODEL_NAME = "MiniMax-M2.5-highspeed"
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Policy Agent in an e-commerce dispute resolution pipeline.

You are given order status, payment totals, and late delivery/split payment flags.
Your task is to determine the policy resolution based strictly on these priority rules:

1. If order_status == "canceled" AND payment_total > 0:
   - primary_issue = "canceled_order_paid"
   - case_status = "action_required"
   - refund = payment_total
   - action = "issue_full_refund"
   - cause_code = "ORDER_CANCELED_AFTER_PAYMENT"
   - party_type = "platform", party_id = "PLATFORM"

2. If order_status == "unavailable" AND payment_total > 0:
   - primary_issue = "unavailable_order_paid"
   - case_status = "action_required"
   - refund = payment_total
   - action = "issue_full_refund"
   - cause_code = "ORDER_UNAVAILABLE_AFTER_PAYMENT"
   - party_type = "platform", party_id = "PLATFORM"

3. If carrier_delivered_late == true AND seller_handoff_late == true:
   - primary_issue = "late_delivery_seller"
   - case_status = "action_required"
   - refund = freight_total
   - action = "refund_freight"
   - cause_code = "SELLER_HANDOFF_AFTER_LIMIT"
   - party_type = "seller", party_id = late_seller_ids[0] (or "UNKNOWN_SELLER")

4. If carrier_delivered_late == true AND seller_handoff_late == false:
   - primary_issue = "late_delivery_logistics"
   - case_status = "action_required"
   - refund = freight_total
   - action = "refund_freight"
   - cause_code = "CARRIER_DELIVERED_AFTER_ESTIMATE"
   - party_type = "logistics_provider", party_id = "LOGISTICS"

5. If valid_split_payment == true:
   - primary_issue = "valid_split_payment"
   - case_status = "no_action"
   - refund = 0.00
   - action = "explain_valid_split_payment"
   - cause_code = "MULTIPLE_PAYMENTS_RECONCILED"
   - party_type = null

6. Otherwise:
   - primary_issue = "unsupported_late_claim"
   - case_status = "no_action"
   - refund = 0.00
   - action = "reject_late_refund"
   - cause_code = "DELIVERY_WITHIN_ESTIMATE"
   - party_type = null

7. STRICT SCHEMA RULES:
   - "case_status" MUST BE exactly "action_required" OR "no_action"
   - "resolution_actions" MUST ONLY contain values from this list: ["issue_full_refund", "refund_freight", "explain_valid_split_payment", "reject_late_refund"]
   - "primary_issue" MUST ONLY be one of: ["canceled_order_paid", "unavailable_order_paid", "late_delivery_seller", "late_delivery_logistics", "valid_split_payment", "unsupported_late_claim"]

Think step-by-step. Reply ONLY with valid JSON matching this structure:
{
  "reasoning": "your reasoning here",
  "primary_issue": "issue_name",
  "case_status": "action_required|no_action",
  "recommended_refund_brl": 12.34,
  "resolution_actions": ["action"],
  "ranked_causes": [{"cause_code": "CODE", "rank": 1}],
  "responsible_parties": [{"party_type": "type", "party_id": "id"}] (empty if party_type is null)
}
"""

def analyze(evidence: EvidenceBundle) -> PolicyResult:
    raw = {
        "order_status": evidence.order_seller.order_status,
        "payment_total_brl": float(evidence.payment.payment_total_brl),
        "freight_total_brl": float(evidence.payment.freight_total_brl),
        "carrier_delivered_late": evidence.delivery.carrier_delivered_late,
        "seller_handoff_late": evidence.order_seller.seller_handoff_late,
        "late_seller_ids": evidence.order_seller.late_seller_ids,
        "valid_split_payment": evidence.payment.valid_split_payment
    }

    llm_result = _call_llm(raw)

    causes = llm_result.get("ranked_causes", [])
    parties = llm_result.get("responsible_parties", [])
    
    return PolicyResult(
        primary_issue=llm_result.get("primary_issue", "unsupported_late_claim"),
        case_status=llm_result.get("case_status", NO_ACTION),
        confidence=0.90,
        ranked_causes=tuple(RankedCause(**c) for c in causes),
        responsible_parties=tuple(ResponsibleParty(**p) for p in parties),
        recommended_refund_brl=Decimal(str(llm_result.get("recommended_refund_brl", 0.0))),
        resolution_actions=tuple(llm_result.get("resolution_actions", []))
    )

def _call_llm(raw: dict) -> dict:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        logger.warning("[PolicyAgent] MINIMAX_API_KEY not set, using fallback")
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
        logger.warning("[PolicyAgent] LLM call failed (%s), using fallback", exc)
        return _python_fallback(raw)

def _python_fallback(raw: dict) -> dict:
    status = raw["order_status"]
    payment = raw["payment_total_brl"]
    freight = raw["freight_total_brl"]
    carrier_late = raw["carrier_delivered_late"]
    seller_late = raw["seller_handoff_late"]
    valid_split = raw["valid_split_payment"]

    if status == "canceled" and payment > 0:
        return {
            "primary_issue": "canceled_order_paid",
            "case_status": ACTION_REQUIRED,
            "ranked_causes": [{"cause_code": "ORDER_CANCELED_AFTER_PAYMENT", "rank": 1}],
            "responsible_parties": [{"party_type": "platform", "party_id": PLATFORM_PARTY_ID}],
            "recommended_refund_brl": payment,
            "resolution_actions": ["issue_full_refund"],
            "reasoning": "Fallback Python logic"
        }
    
    if status == "unavailable" and payment > 0:
        return {
            "primary_issue": "unavailable_order_paid",
            "case_status": ACTION_REQUIRED,
            "ranked_causes": [{"cause_code": "ORDER_UNAVAILABLE_AFTER_PAYMENT", "rank": 1}],
            "responsible_parties": [{"party_type": "platform", "party_id": PLATFORM_PARTY_ID}],
            "recommended_refund_brl": payment,
            "resolution_actions": ["issue_full_refund"],
            "reasoning": "Fallback Python logic"
        }
    
    if carrier_late:
        if seller_late:
            late_sellers = raw.get("late_seller_ids", [])
            s_id = late_sellers[0] if late_sellers else "UNKNOWN_SELLER"
            return {
                "primary_issue": "late_delivery_seller",
                "case_status": ACTION_REQUIRED,
                "ranked_causes": [{"cause_code": "SELLER_HANDOFF_AFTER_LIMIT", "rank": 1}],
                "responsible_parties": [{"party_type": "seller", "party_id": s_id}],
                "recommended_refund_brl": freight,
                "resolution_actions": ["refund_freight"],
                "reasoning": "Fallback Python logic"
            }
        else:
            return {
                "primary_issue": "late_delivery_logistics",
                "case_status": ACTION_REQUIRED,
                "ranked_causes": [{"cause_code": "CARRIER_DELIVERED_AFTER_ESTIMATE", "rank": 1}],
                "responsible_parties": [{"party_type": "logistics_provider", "party_id": LOGISTICS_PARTY_ID}],
                "recommended_refund_brl": freight,
                "resolution_actions": ["refund_freight"],
                "reasoning": "Fallback Python logic"
            }

    if valid_split:
        return {
            "primary_issue": "valid_split_payment",
            "case_status": NO_ACTION,
            "ranked_causes": [{"cause_code": "MULTIPLE_PAYMENTS_RECONCILED", "rank": 1}],
            "responsible_parties": [],
            "recommended_refund_brl": 0.00,
            "resolution_actions": ["explain_valid_split_payment"],
            "reasoning": "Fallback Python logic"
        }

    return {
        "primary_issue": "unsupported_late_claim",
        "case_status": NO_ACTION,
        "ranked_causes": [{"cause_code": "DELIVERY_WITHIN_ESTIMATE", "rank": 1}],
        "responsible_parties": [],
        "recommended_refund_brl": 0.00,
        "resolution_actions": ["reject_late_refund"],
        "reasoning": "Fallback Python logic"
    }
