"""P4: deterministic payment reconciliation agent.

It reads only payment and item records, preserving the data-access boundary
defined by the agent contract.
"""

from __future__ import annotations

from decimal import Decimal

from ..data_loader import OlistDataLoader
from ..schemas import InputCase, PaymentEvidence, money


def analyze(case: InputCase, loader: OlistDataLoader) -> PaymentEvidence:
    """Return payment totals and valid-split evidence for one order."""
    order_id = case.claimed_order_id
    items = loader.order_items(order_id)
    payments = loader.order_payments(order_id)
    item_total = money(sum((Decimal(item["price"]) for item in items), Decimal()))
    freight_total = money(
        sum((Decimal(item["freight_value"]) for item in items), Decimal())
    )
    payment_total = money(
        sum((Decimal(payment["payment_value"]) for payment in payments), Decimal())
    )
    reconciled_total = money(item_total + freight_total)
    valid_split_payment = (
        len(payments) >= 2
        and abs(payment_total - reconciled_total) <= Decimal("0.10")
    )
    payment_ids = tuple(
        f"{order_id}:{payment['payment_sequential']}" for payment in payments
    )
    return PaymentEvidence(
        order_id=order_id,
        item_total_brl=item_total,
        freight_total_brl=freight_total,
        payment_total_brl=payment_total,
        valid_split_payment=valid_split_payment,
        payment_ids=payment_ids,
        evidence_ids=tuple(f"payment:{payment_id}" for payment_id in payment_ids),
    )
