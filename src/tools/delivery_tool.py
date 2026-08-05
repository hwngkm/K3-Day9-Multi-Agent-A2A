"""Read-only Delivery lookup tool for the P3 Delivery Agent.

The tool performs exact CSV filtering and timestamp comparisons against
``orders.csv`` (and ``order_items.csv`` for evidence IDs only).  It does not
make a policy decision, choose a refund, or create the final agent handoff.

Allowed reads  : orders.csv, order_items.csv (evidence IDs only)
Forbidden reads: order_payments.csv, sellers.csv, products.csv
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from ..data_loader import DataLoadError, OlistDataLoader


# ---------------------------------------------------------------------------
# TypedDict contracts exposed to the LLM tool caller
# ---------------------------------------------------------------------------


class DeliveryToolResponse(TypedDict):
    """JSON contract returned by :func:`query_delivery`."""

    order_id: str
    order_found: bool
    order_status: str | None
    # Raw timestamps (ISO strings) for LLM reasoning
    order_delivered_customer_date: str | None
    order_estimated_delivery_date: str | None
    order_delivered_carrier_date: str | None
    # Deterministic computation result — LLM must confirm, not override
    carrier_delivered_late: bool | None
    delivery_evaluable: bool
    # Pre-built evidence IDs for this order
    evidence_ids: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_ts(value: str | None, field: str, order_id: str) -> datetime | None:
    """Parse an Olist ISO timestamp without timezone conversion."""
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise DataLoadError(
            f"Invalid {field} for order {order_id}: {value!r}"
        ) from exc


def _fmt(dt: datetime | None) -> str | None:
    return dt.isoformat(sep=" ") if dt is not None else None


def _not_found(order_id: str) -> DeliveryToolResponse:
    return {
        "order_id": order_id,
        "order_found": False,
        "order_status": None,
        "order_delivered_customer_date": None,
        "order_estimated_delivery_date": None,
        "order_delivered_carrier_date": None,
        "carrier_delivered_late": None,
        "delivery_evaluable": False,
        "evidence_ids": [],
    }


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def query_delivery(
    claimed_order_id: str,
    loader: OlistDataLoader,
) -> DeliveryToolResponse:
    """Look up delivery timestamps for one order and compute lateness.

    Comparison rule (EC_POLICY_V1):
        ``carrier_delivered_late = order_delivered_customer_date > order_estimated_delivery_date``

    Both timestamps must be present for the flag to be ``True``/``False``; if
    either is missing the result is ``None`` and ``delivery_evaluable`` is
    ``False``.

    Reads only ``orders_by_id`` and ``items_by_order_id`` (for evidence IDs).
    Never touches payment, seller, product, or geolocation data.
    """

    order_id = claimed_order_id.strip()
    if not order_id:
        raise ValueError("claimed_order_id must be a non-empty string")

    order = loader.orders_by_id.get(order_id)
    if order is None:
        return _not_found(order_id)

    order_status = order.get("order_status", "").strip()
    if not order_status:
        raise DataLoadError(f"Missing order_status for order {order_id}")

    delivered_customer = _parse_ts(
        order.get("order_delivered_customer_date"),
        "order_delivered_customer_date",
        order_id,
    )
    estimated_delivery = _parse_ts(
        order.get("order_estimated_delivery_date"),
        "order_estimated_delivery_date",
        order_id,
    )
    delivered_carrier = _parse_ts(
        order.get("order_delivered_carrier_date"),
        "order_delivered_carrier_date",
        order_id,
    )

    # Deterministic lateness flag
    delivery_evaluable = delivered_customer is not None and estimated_delivery is not None
    if delivery_evaluable:
        # Canceled / unavailable orders cannot be "late" in the delivery sense
        carrier_delivered_late: bool | None = (
            False
            if order_status in ("canceled", "unavailable")
            else delivered_customer > estimated_delivery  # type: ignore[operator]
        )
    else:
        carrier_delivered_late = None

    # Evidence IDs: order + items (no payment/seller IDs — not our domain)
    items = loader.items_by_order_id.get(order_id, ())
    evidence_ids: list[str] = [f"order:{order_id}"]
    for item in items:
        item_seq = item.get("order_item_id", "").strip()
        if item_seq:
            evidence_ids.append(f"item:{order_id}:{item_seq}")

    return {
        "order_id": order_id,
        "order_found": True,
        "order_status": order_status,
        "order_delivered_customer_date": _fmt(delivered_customer),
        "order_estimated_delivery_date": _fmt(estimated_delivery),
        "order_delivered_carrier_date": _fmt(delivered_carrier),
        "carrier_delivered_late": carrier_delivered_late,
        "delivery_evaluable": delivery_evaluable,
        "evidence_ids": evidence_ids,
    }


# ---------------------------------------------------------------------------
# Bound tool class (mirrors OrderSellerTool pattern)
# ---------------------------------------------------------------------------


class DeliveryTool:
    """Bound tool whose public model-facing method needs only an order ID."""

    name = "query_delivery"
    description = (
        "Look up delivery timestamps for an Olist order and determine whether "
        "the carrier delivered after the estimated delivery date."
    )

    def __init__(self, loader: OlistDataLoader) -> None:
        self._loader = loader

    def lookup(self, claimed_order_id: str) -> DeliveryToolResponse:
        """Return verified delivery facts for one claimed order ID."""
        return query_delivery(claimed_order_id, self._loader)
