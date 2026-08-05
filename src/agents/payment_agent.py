"""P4: Payment Agent.

Access: olist_order_payments_dataset.csv, olist_order_items_dataset.csv (via
``OlistDataLoader``). Must NOT read seller or delivery raw data.
"""

from __future__ import annotations

from decimal import Decimal

from ..data_loader import OlistDataLoader
from ..schemas import InputCase, PaymentEvidence, money


def analyze(case: InputCase, loader: OlistDataLoader) -> PaymentEvidence:
    """Return payment-reconciliation evidence for ``case.claimed_order_id``.

    Field contract (see src/schemas.py:PaymentEvidence):
    - item_total_brl / freight_total_brl: sum of this order's order_items
      price / freight_value (0.0 if no item rows — real case in the data).
    - payment_total_brl: sum of this order's order_payments payment_value.
    - valid_split_payment: ``len(payments) >= 2`` AND
      ``abs(payment_total_brl - (item_total_brl + freight_total_brl)) <= 0.10``.
    - payment_ids: ``"<order_id>:<payment_sequential>"`` per row (no
      "payment:" prefix, max 5) -> feeds affected_entities.payment_ids.
    - evidence_ids: ``"payment:<order_id>:<payment_sequential>"`` per row.

    Use ``schemas.money(...)`` (Decimal, ROUND_HALF_UP, 2dp) for every total —
    PaymentEvidence.__post_init__ re-rounds them anyway, but compute with
    Decimal from the start to avoid float precision drift before rounding.
    """
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
