"""P6: Verifier Agent.

Access: full CSV access via ``OlistDataLoader`` (to confirm evidence really
exists), plus the assembled ``OutputVerdict`` and originating ``InputCase``.
This is the last gate before Coordinator writes a case to ``output/``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..data_loader import OlistDataLoader
from ..policy_config import ConfiguredRule, policy_by_issue
from ..schemas import (
    ACTION_REQUIRED,
    CURRENCY_BRL,
    LOGISTICS_PARTY_ID,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE_IDS,
    MAX_RESOLUTION_ACTIONS,
    MAX_RESPONSIBLE_PARTIES,
    MAX_ROOT_CAUSES,
    NO_ACTION,
    PLATFORM_PARTY_ID,
    PRIMARY_ISSUES,
    RESOLUTION_ACTIONS,
    InputCase,
    OutputVerdict,
    VerificationResult,
    money,
)


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _as_decimal(value: Any, field: str, errors: list[str]) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        errors.append(f"{field} is not a decimal value")
        return None
    if not amount.is_finite():
        errors.append(f"{field} must be finite")
        return None
    if amount < 0:
        errors.append(f"{field} must not be negative")
    if money(amount) != amount:
        errors.append(f"{field} must be rounded to two decimals")
    return amount


def _expected_primary_issue(loader: OlistDataLoader, order_id: str) -> str | None:
    """Independently re-evaluate EC_POLICY_V1 from the verifier's CSV access."""

    order = loader.require_order(order_id)
    items = loader.order_items(order_id)
    payments = loader.order_payments(order_id)
    item_total = money(sum((Decimal(item["price"]) for item in items), Decimal()))
    freight_total = money(
        sum((Decimal(item["freight_value"]) for item in items), Decimal())
    )
    payment_total = money(
        sum((Decimal(payment["payment_value"]) for payment in payments), Decimal())
    )
    reconciled = abs(payment_total - money(item_total + freight_total)) <= Decimal("0.10")
    delivered_at = _parse_timestamp(order["order_delivered_customer_date"])
    estimated_at = _parse_timestamp(order["order_estimated_delivery_date"])
    delivery_late = bool(
        delivered_at is not None and estimated_at is not None and delivered_at > estimated_at
    )
    carrier_at = _parse_timestamp(order["order_delivered_carrier_date"])
    seller_late = any(
        carrier_at is not None
        and (shipping_limit := _parse_timestamp(item["shipping_limit_date"]))
        is not None
        and carrier_at > shipping_limit
        for item in items
    )
    if order["order_status"] == "canceled" and payment_total > 0:
        return "canceled_order_paid"
    if order["order_status"] == "unavailable" and payment_total > 0:
        return "unavailable_order_paid"
    if delivery_late and seller_late:
        return "late_delivery_seller"
    if delivery_late and not seller_late:
        return "late_delivery_logistics"
    if len(payments) >= 2 and reconciled:
        return "valid_split_payment"
    if not delivery_late and reconciled:
        return "unsupported_late_claim"
    return None


def _validate_entity_ids(
    entities: Mapping[str, Sequence[str]], loader: OlistDataLoader, order_id: str, errors: list[str]
) -> None:
    expected_items = {
        f"{order_id}:{item['order_item_id']}" for item in loader.order_items(order_id)
    }
    expected_sellers = {
        item["seller_id"] for item in loader.order_items(order_id)
    }
    expected_payments = {
        f"{order_id}:{payment['payment_sequential']}"
        for payment in loader.order_payments(order_id)
    }
    expected = {
        "order_ids": {order_id},
        "item_ids": expected_items,
        "seller_ids": expected_sellers,
        "payment_ids": expected_payments,
    }
    for key, expected_values in expected.items():
        actual_values = entities.get(key)
        if not isinstance(actual_values, Sequence) or isinstance(actual_values, str):
            errors.append(f"affected_entities.{key} must be a list")
            continue
        if len(actual_values) > MAX_ENTITY_IDS:
            errors.append(f"affected_entities.{key} exceeds maximum {MAX_ENTITY_IDS}")
        if set(actual_values) != expected_values:
            errors.append(f"affected_entities.{key} does not match CSV rows for {order_id}")


def _validate_evidence_id(
    evidence_id: str,
    loader: OlistDataLoader,
    order_id: str,
    expected_cause: str,
    errors: list[str],
) -> None:
    parts = evidence_id.split(":")
    if not parts:
        errors.append("empty evidence ID")
        return
    kind = parts[0]
    if kind == "order" and parts == ["order", order_id]:
        return
    if kind == "item" and len(parts) == 3 and parts[1] == order_id:
        item_ids = {item["order_item_id"] for item in loader.order_items(order_id)}
        if parts[2] in item_ids:
            return
    if kind == "payment" and len(parts) == 3 and parts[1] == order_id:
        payment_ids = {
            payment["payment_sequential"] for payment in loader.order_payments(order_id)
        }
        if parts[2] in payment_ids:
            return
    if kind == "seller" and len(parts) == 2:
        order_sellers = {item["seller_id"] for item in loader.order_items(order_id)}
        if parts[1] in order_sellers and loader.seller(parts[1]) is not None:
            return
    if kind == "policy" and parts == ["policy", expected_cause]:
        return
    errors.append(f"Invalid or non-existent evidence ID: {evidence_id}")


def _validate_responsible_parties(
    parties: Sequence[Mapping[str, str]], rule: ConfiguredRule | None, order_id: str,
    loader: OlistDataLoader, errors: list[str]
) -> None:
    expected_sellers = {item["seller_id"] for item in loader.order_items(order_id)}
    if rule is None or rule.spec.party_type is None:
        expected: list[dict[str, str]] = []
    elif rule.spec.party_id is None:
        if len(parties) != 1 or parties[0].get("party_type") != "seller":
            errors.append("late_delivery_seller must identify one seller")
            return
        if parties[0].get("party_id") not in expected_sellers:
            errors.append("late_delivery_seller party_id is not a seller on this order")
        return
    else:
        expected = [{"party_type": rule.spec.party_type, "party_id": rule.spec.party_id}]
    if list(parties) != expected:
        errors.append("responsible_parties does not match EC_POLICY_V1")


def verify(
    case: InputCase, verdict: OutputVerdict, loader: OlistDataLoader
) -> VerificationResult:
    """Return ``VerificationResult(passed=True)`` or ``passed=False, errors=(...)``.

    Checks to implement:
    - ``verdict.case_id == case.case_id``.
    - Every entry in ``verdict.evidence_ids`` resolves against real data via
      ``loader`` (order/item/payment/seller for this order_id really exist;
      ``policy:<code>`` matches a known root-cause code) — an evidence ID that
      cannot be constructed from the CSVs is a false positive per README
      section 5 and must fail verification.
    - ``financial_resolution`` values are non-negative and already rounded to
      2 decimals (schemas.money() enforces this upstream, but re-check here
      since Verifier is the final gate, not the source of truth).
    - ``assessment.confidence`` is in ``[0, 1]`` (also enforced by
      PolicyResult.__post_init__, but re-check on the assembled verdict).
    - List-length limits from README section 6 hold on the final assembled
      verdict (max 5 per entity list, 10 evidence_ids, 3 causes/parties, 5
      actions) — schemas.py dataclasses already raise ContractError on
      violation before reaching here, so this is a defense-in-depth check.

    Collect every failure into ``errors`` (do not short-circuit on the first
    one) so Coordinator/trace.jsonl surfaces the full picture for whoever
    fixes the failing case.
    """
    errors: list[str] = []
    data = verdict.to_dict()
    order_id = case.claimed_order_id
    if verdict.case_id != case.case_id:
        errors.append("case_id does not match the input case")

    assessment = data.get("assessment", {})
    primary_issue = assessment.get("primary_issue")
    if primary_issue not in PRIMARY_ISSUES:
        errors.append("assessment.primary_issue is not canonical")
        primary_issue = ""
    expected_primary = _expected_primary_issue(loader, order_id)
    if primary_issue != expected_primary:
        errors.append(
            f"primary_issue {primary_issue!r} does not match CSV-derived {expected_primary!r}"
        )
    rule = policy_by_issue().get(primary_issue)
    expected_cause = rule.spec.root_cause_code if rule else ""
    expected_action = rule.spec.action if rule else ""
    expected_status = rule.spec.case_status if rule else ""
    if assessment.get("case_status") != expected_status:
        errors.append("assessment.case_status does not match EC_POLICY_V1")
    confidence = assessment.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append("assessment.confidence must be within [0, 1]")

    entities = data.get("affected_entities", {})
    if not isinstance(entities, Mapping):
        errors.append("affected_entities must be an object")
    else:
        _validate_entity_ids(entities, loader, order_id, errors)

    root_cause_analysis = data.get("root_cause_analysis", {})
    causes = root_cause_analysis.get("ranked_causes", [])
    parties = root_cause_analysis.get("responsible_parties", [])
    if len(causes) > MAX_ROOT_CAUSES:
        errors.append(f"ranked_causes exceeds maximum {MAX_ROOT_CAUSES}")
    if len(parties) > MAX_RESPONSIBLE_PARTIES:
        errors.append(f"responsible_parties exceeds maximum {MAX_RESPONSIBLE_PARTIES}")
    if causes != [{"cause_code": expected_cause, "rank": 1}]:
        errors.append("ranked_causes does not match EC_POLICY_V1")
    if not isinstance(parties, Sequence):
        errors.append("responsible_parties must be a list")
    elif not all(isinstance(party, Mapping) for party in parties):
        errors.append("each responsible party must be an object")
    else:
        _validate_responsible_parties(parties, rule, order_id, loader, errors)

    evidence_ids = data.get("evidence_ids", [])
    if not isinstance(evidence_ids, Sequence) or isinstance(evidence_ids, str):
        errors.append("evidence_ids must be a list")
    else:
        if len(evidence_ids) > MAX_EVIDENCE_IDS:
            errors.append(f"evidence_ids exceeds maximum {MAX_EVIDENCE_IDS}")
        if len(set(evidence_ids)) != len(evidence_ids):
            errors.append("evidence_ids must not contain duplicates")
        for evidence_id in evidence_ids:
            if not isinstance(evidence_id, str):
                errors.append("evidence_ids must contain strings")
            else:
                _validate_evidence_id(
                    evidence_id, loader, order_id, expected_cause, errors
                )
        if f"policy:{expected_cause}" not in evidence_ids:
            errors.append("evidence_ids must include the selected policy cause")

    financial = data.get("financial_resolution", {})
    if financial.get("currency") != CURRENCY_BRL:
        errors.append(f"financial_resolution.currency must be {CURRENCY_BRL}")
    values = {
        name: _as_decimal(financial.get(name), name, errors)
        for name in (
            "item_total_brl",
            "freight_total_brl",
            "payment_total_brl",
            "recommended_refund_brl",
        )
    }
    item_total = money(
        sum((Decimal(item["price"]) for item in loader.order_items(order_id)), Decimal())
    )
    freight_total = money(
        sum((Decimal(item["freight_value"]) for item in loader.order_items(order_id)), Decimal())
    )
    payment_total = money(
        sum((Decimal(payment["payment_value"]) for payment in loader.order_payments(order_id)), Decimal())
    )
    expected_values = {
        "item_total_brl": item_total,
        "freight_total_brl": freight_total,
        "payment_total_brl": payment_total,
        "recommended_refund_brl": (
            payment_total
            if rule is not None and rule.refund_strategy == "payment_total"
            else freight_total
            if rule is not None and rule.refund_strategy == "freight_total"
            else Decimal("0.00")
        ),
    }
    for name, expected_value in expected_values.items():
        if values[name] != expected_value:
            errors.append(f"{name} does not match CSV-derived value")

    actions = data.get("resolution_actions", [])
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        errors.append("resolution_actions must be a list")
    else:
        if len(actions) > MAX_RESOLUTION_ACTIONS:
            errors.append(f"resolution_actions exceeds maximum {MAX_RESOLUTION_ACTIONS}")
        if any(action not in RESOLUTION_ACTIONS for action in actions):
            errors.append("resolution_actions contains an unknown action")
        if list(actions) != [expected_action]:
            errors.append("resolution_actions does not match EC_POLICY_V1")

    return VerificationResult(passed=not errors, errors=tuple(errors))
