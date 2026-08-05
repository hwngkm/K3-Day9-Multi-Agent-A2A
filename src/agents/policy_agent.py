from __future__ import annotations

from decimal import Decimal
from typing import Tuple

from .schemas import (
    ACTION_REQUIRED,
    CURRENCY_BRL,
    LOGISTICS_PARTY_ID,
    PLATFORM_PARTY_ID,
    POLICY_VERSION,
    PRIMARY_ISSUES,
    RESOLUTION_ACTIONS,
    ROOT_CAUSE_CODES,
    PARTY_TYPES,
    PolicyResult,
    ResponsibleParty,
    RankedCause,
    EvidenceBundle,
    money,
)


def analyze(evidence: EvidenceBundle) -> PolicyResult:
    """Apply EC_POLICY_V1 to the evidence bundle and return a CP0 policy decision."""

    order_seller = evidence.order_seller
    delivery = evidence.delivery
    payment = evidence.payment

    if order_seller.order_status == "canceled" and payment.payment_total_brl > Decimal("0"):
        return _platform_full_refund(
            primary_issue="canceled_order_paid",
            root_cause="ORDER_CANCELED_AFTER_PAYMENT",
            refund_amount=payment.payment_total_brl,
            evidence=evidence,
        )

    if order_seller.order_status == "unavailable" and payment.payment_total_brl > Decimal("0"):
        return _platform_full_refund(
            primary_issue="unavailable_order_paid",
            root_cause="ORDER_UNAVAILABLE_AFTER_PAYMENT",
            refund_amount=payment.payment_total_brl,
            evidence=evidence,
        )

    if delivery.carrier_delivered_late:
        if order_seller.seller_handoff_late:
            return _late_delivery_seller(evidence)
        return _late_delivery_logistics(evidence)

    if payment.valid_split_payment:
        return _valid_split_payment(evidence)

    return _unsupported_late_claim(evidence)


def _platform_full_refund(
    primary_issue: str,
    root_cause: str,
    refund_amount: Decimal,
    evidence: EvidenceBundle,
) -> PolicyResult:
    return PolicyResult(
        primary_issue=primary_issue,
        case_status=ACTION_REQUIRED,
        confidence=0.95,
        ranked_causes=(RankedCause(cause_code=root_cause, rank=1),),
        responsible_parties=(ResponsibleParty(party_type="platform", party_id=PLATFORM_PARTY_ID),),
        recommended_refund_brl=refund_amount,
        resolution_actions=("issue_full_refund",),
    )


def _late_delivery_seller(evidence: EvidenceBundle) -> PolicyResult:
    return PolicyResult(
        primary_issue="late_delivery_seller",
        case_status=ACTION_REQUIRED,
        confidence=0.90,
        ranked_causes=(RankedCause(cause_code="SELLER_HANDOFF_AFTER_LIMIT", rank=1),),
        responsible_parties=(
            ResponsibleParty(party_type="seller", party_id=next(iter(evidence.order_seller.late_seller_ids), "")),
        ),
        recommended_refund_brl=evidence.payment.freight_total_brl,
        resolution_actions=("refund_freight",),
    )


def _late_delivery_logistics(evidence: EvidenceBundle) -> PolicyResult:
    return PolicyResult(
        primary_issue="late_delivery_logistics",
        case_status=ACTION_REQUIRED,
        confidence=0.88,
        ranked_causes=(RankedCause(cause_code="CARRIER_DELIVERED_AFTER_ESTIMATE", rank=1),),
        responsible_parties=(
            ResponsibleParty(party_type="logistics_provider", party_id=LOGISTICS_PARTY_ID),
        ),
        recommended_refund_brl=evidence.payment.freight_total_brl,
        resolution_actions=("refund_freight",),
    )


def _valid_split_payment(evidence: EvidenceBundle) -> PolicyResult:
    return PolicyResult(
        primary_issue="valid_split_payment",
        case_status="no_action",
        confidence=0.75,
        ranked_causes=(RankedCause(cause_code="MULTIPLE_PAYMENTS_RECONCILED", rank=1),),
        responsible_parties=(),
        recommended_refund_brl=Decimal("0.00"),
        resolution_actions=("explain_valid_split_payment",),
    )


def _unsupported_late_claim(evidence: EvidenceBundle) -> PolicyResult:
    return PolicyResult(
        primary_issue="unsupported_late_claim",
        case_status="no_action",
        confidence=0.80,
        ranked_causes=(RankedCause(cause_code="DELIVERY_WITHIN_ESTIMATE", rank=1),),
        responsible_parties=(),
        recommended_refund_brl=Decimal("0.00"),
        resolution_actions=("reject_late_refund",),
    )
