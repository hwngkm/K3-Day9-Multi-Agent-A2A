"""P3 Delivery Agent: deterministic date comparison, no direct model calls."""

from __future__ import annotations

from datetime import datetime

from ..data_loader import OlistDataLoader
from ..schemas import DeliveryEvidence, InputCase


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def analyze(case: InputCase, loader: OlistDataLoader) -> DeliveryEvidence:
    """Determine late delivery from authoritative Olist timestamps only."""

    order_id = case.claimed_order_id
    order = loader.require_order(order_id)
    delivered_at = order["order_delivered_customer_date"] or None
    estimated_at = order["order_estimated_delivery_date"] or None
    delivered_value = _parse_timestamp(delivered_at)
    estimated_value = _parse_timestamp(estimated_at)
    delivered_late = bool(
        delivered_value is not None
        and estimated_value is not None
        and delivered_value > estimated_value
    )
    return DeliveryEvidence(
        order_id=order_id,
        carrier_delivered_late=delivered_late,
        order_delivered_carrier_date=order["order_delivered_carrier_date"] or None,
        order_delivered_customer_date=delivered_at,
        order_estimated_delivery_date=estimated_at,
        evidence_ids=(f"order:{order_id}",),
    )
