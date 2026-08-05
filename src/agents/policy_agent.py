"""P5 policy evaluation driven by ``policy/EC_POLICY_V1.json``."""

from __future__ import annotations

from decimal import Decimal

from ..policy_config import ConfiguredRule, load_policy_rules
from ..schemas import ContractError, EvidenceBundle, PolicyResult, RankedCause, ResponsibleParty


def _payment_reconciles(evidence: EvidenceBundle) -> bool:
    payment = evidence.payment
    return abs(
        payment.payment_total_brl - (payment.item_total_brl + payment.freight_total_brl)
    ) <= Decimal("0.10")


def _matches(rule: ConfiguredRule, evidence: EvidenceBundle) -> bool:
    order = evidence.order_seller
    delivery = evidence.delivery
    payment = evidence.payment
    condition = rule.condition
    if condition == "canceled_order_paid":
        return order.order_status == "canceled" and payment.payment_total_brl > 0
    if condition == "unavailable_order_paid":
        return order.order_status == "unavailable" and payment.payment_total_brl > 0
    if condition == "late_delivery_seller":
        return delivery.carrier_delivered_late and order.seller_handoff_late
    if condition == "late_delivery_logistics":
        return delivery.carrier_delivered_late and not order.seller_handoff_late
    if condition == "valid_split_payment":
        return payment.valid_split_payment
    if condition == "unsupported_late_claim":
        return not delivery.carrier_delivered_late and _payment_reconciles(evidence)
    raise ContractError(f"Policy config has an unsupported condition: {condition}")


def _responsible_parties(
    rule: ConfiguredRule, evidence: EvidenceBundle
) -> tuple[ResponsibleParty, ...]:
    spec = rule.spec
    if spec.party_type is None:
        return ()
    if spec.party_id is not None:
        return (ResponsibleParty(spec.party_type, spec.party_id),)
    if not evidence.order_seller.late_seller_ids:
        raise ContractError("Late-seller rule matched without a late seller ID")
    return (ResponsibleParty(spec.party_type, evidence.order_seller.late_seller_ids[0]),)


def _refund(rule: ConfiguredRule, evidence: EvidenceBundle) -> Decimal:
    if rule.refund_strategy == "payment_total":
        return evidence.payment.payment_total_brl
    if rule.refund_strategy == "freight_total":
        return evidence.payment.freight_total_brl
    if rule.refund_strategy == "zero":
        return Decimal("0.00")
    raise ContractError(f"Unsupported refund strategy: {rule.refund_strategy}")


def analyze(evidence: EvidenceBundle) -> PolicyResult:
    """Apply externally configured policy rules in their declared priority order."""
    for rule in load_policy_rules():
        if _matches(rule, evidence):
            return PolicyResult(
                primary_issue=rule.spec.primary_issue,
                case_status=rule.spec.case_status,
                confidence=1.0,
                ranked_causes=(RankedCause(rule.spec.root_cause_code, 1),),
                responsible_parties=_responsible_parties(rule, evidence),
                recommended_refund_brl=_refund(rule, evidence),
                resolution_actions=(rule.spec.action,),
            )
    raise ContractError("No EC_POLICY_V1 rule matched; refusing to invent a verdict")
