"""
P6 — Verifier Agent
Interface : verify(case: InputCase, verdict: OutputVerdict, loader: OlistDataLoader) -> VerificationResult
Đọc      : Toàn bộ CSV để check evidence IDs có thật không.
LLM model: Không dùng LLM (thuần Python logic để gate chặt).
"""

from __future__ import annotations

import logging

from ..data_loader import OlistDataLoader
from ..schemas import InputCase, OutputVerdict, VerificationResult, ROOT_CAUSE_CODES

logger = logging.getLogger(__name__)

def verify(case: InputCase, verdict: OutputVerdict, loader: OlistDataLoader) -> VerificationResult:
    errors = []

    # 1. Check confidence
    confidence = verdict.assessment.get("confidence", -1)
    if not (0.0 <= confidence <= 1.0):
        errors.append(f"Confidence {confidence} not in [0, 1]")

    # 2. Check limits
    if len(verdict.evidence_ids) > 10:
        errors.append(f"Too many evidence_ids: {len(verdict.evidence_ids)} > 10")
    
    entities = verdict.affected_entities
    for entity_type, entity_list in entities.items():
        if len(entity_list) > 5:
            errors.append(f"Too many {entity_type}: {len(entity_list)} > 5")

    rca = verdict.root_cause_analysis
    if len(rca.get("ranked_causes", [])) > 3:
        errors.append("Too many ranked_causes > 3")
    if len(rca.get("responsible_parties", [])) > 3:
        errors.append("Too many responsible_parties > 3")
    
    if len(verdict.resolution_actions) > 5:
        errors.append("Too many resolution_actions > 5")

    # 3. Check evidence existence
    # "order:<id>", "item:<order_id>:<item_id>", "payment:<order_id>:<seq>", "seller:<id>", "policy:<code>"
    for ev in verdict.evidence_ids:
        parts = ev.split(":")
        if len(parts) < 2:
            errors.append(f"Invalid evidence format: {ev}")
            continue
        
        prefix = parts[0]
        if prefix == "order":
            try:
                loader.require_order(parts[1])
            except Exception:
                errors.append(f"Evidence order not found: {parts[1]}")
        elif prefix == "item":
            if len(parts) != 3:
                errors.append(f"Invalid item evidence format: {ev}")
                continue
            o_id, i_id = parts[1], parts[2]
            items = loader.order_items(o_id)
            if not any(i.get("order_item_id") == i_id for i in items):
                errors.append(f"Evidence item not found: {ev}")
        elif prefix == "payment":
            if len(parts) != 3:
                errors.append(f"Invalid payment evidence format: {ev}")
                continue
            o_id, seq = parts[1], parts[2]
            payments = loader.order_payments(o_id)
            if not any(p.get("payment_sequential") == seq for p in payments):
                errors.append(f"Evidence payment not found: {ev}")
        elif prefix == "seller":
            if loader.seller(parts[1]) is None:
                errors.append(f"Evidence seller not found: {parts[1]}")
        elif prefix == "policy":
            if parts[1] not in ROOT_CAUSE_CODES:
                errors.append(f"Invalid policy code in evidence: {parts[1]}")
        else:
            errors.append(f"Unknown evidence prefix: {prefix}")

    # 4. Check money rounding is already handled by money() in schemas, but we can check if it's a float
    financials = verdict.financial_resolution
    for key, val in financials.items():
        if key.endswith("_brl"):
            if not isinstance(val, (int, float)):
                errors.append(f"{key} must be a number, got {type(val)}")
            elif round(val, 2) != val:
                errors.append(f"{key} is not rounded to 2 decimal places: {val}")

    if errors:
        return VerificationResult(passed=False, errors=tuple(errors))
    return VerificationResult(passed=True)
