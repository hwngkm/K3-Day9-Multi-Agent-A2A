"""Read-only Order/Seller lookup tool for the future P2 LLM agent.

The tool performs exact CSV filtering and timestamp comparisons.  It does not
make a policy decision, choose a refund, or create the final agent handoff.
Its return value contains only JSON-serializable values so it can be registered
with whichever <=10B agent framework the team selects later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, TypedDict

from ..data_loader import DataLoadError, OlistDataLoader


class OrderSellerItem(TypedDict):
    """One verified item returned to the Order & Seller Agent."""

    order_item_id: str
    affected_item_id: str
    seller_id: str
    shipping_limit_date: str | None
    handoff_evaluable: bool
    seller_handoff_late: bool | None
    item_evidence_id: str
    seller_evidence_id: str


class OrderSellerToolResponse(TypedDict):
    """JSON contract returned by :func:`query_order_seller`."""

    order_id: str
    order_found: bool
    order_status: str | None
    order_delivered_carrier_date: str | None
    handoff_evaluable: bool
    seller_handoff_late: bool | None
    items: list[OrderSellerItem]
    seller_ids: list[str]
    late_seller_ids: list[str]
    evidence_ids: list[str]


def _parse_timestamp(value: str | None, field: str, order_id: str) -> datetime | None:
    """Parse an Olist timestamp without applying a timezone conversion."""

    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise DataLoadError(
            f"Invalid {field} for order {order_id}: {value!r}"
        ) from exc


def _format_timestamp(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value is not None else None


def _item_sort_key(item: Mapping[str, str]) -> tuple[int, str]:
    item_id = item.get("order_item_id", "")
    try:
        return int(item_id), item_id
    except ValueError:
        return 2**31 - 1, item_id


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _not_found(order_id: str) -> OrderSellerToolResponse:
    """Return a structured miss that an LLM tool caller can handle safely."""

    return {
        "order_id": order_id,
        "order_found": False,
        "order_status": None,
        "order_delivered_carrier_date": None,
        "handoff_evaluable": False,
        "seller_handoff_late": None,
        "items": [],
        "seller_ids": [],
        "late_seller_ids": [],
        "evidence_ids": [],
    }


def query_order_seller(
    claimed_order_id: str,
    loader: OlistDataLoader,
) -> OrderSellerToolResponse:
    """Filter Order/Seller data for one claimed order ID.

    The comparison required by EC_POLICY_V1 is performed here rather than by
    the LLM: a handoff is late only when ``order_delivered_carrier_date`` is
    strictly later than an item's ``shipping_limit_date``.

    This function intentionally reads no payment, customer-delivery, review,
    product, or geolocation data.
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

    carrier_date = _parse_timestamp(
        order.get("order_delivered_carrier_date"),
        "order_delivered_carrier_date",
        order_id,
    )
    items: list[OrderSellerItem] = []
    seen_item_ids: set[str] = set()

    for raw_item in sorted(
        loader.items_by_order_id.get(order_id, ()),
        key=_item_sort_key,
    ):
        item_id = raw_item.get("order_item_id", "").strip()
        seller_id = raw_item.get("seller_id", "").strip()
        if not item_id:
            raise DataLoadError(f"Missing order_item_id for order {order_id}")
        if item_id in seen_item_ids:
            raise DataLoadError(
                f"Duplicate order_item_id {item_id!r} for order {order_id}"
            )
        seen_item_ids.add(item_id)

        if not seller_id:
            raise DataLoadError(
                f"Missing seller_id for item {order_id}:{item_id}"
            )
        if loader.seller(seller_id) is None:
            raise DataLoadError(
                f"Unknown seller_id {seller_id!r} for item {order_id}:{item_id}"
            )

        shipping_limit = _parse_timestamp(
            raw_item.get("shipping_limit_date"),
            "shipping_limit_date",
            order_id,
        )
        handoff_evaluable = carrier_date is not None and shipping_limit is not None
        handoff_late = (
            carrier_date > shipping_limit if handoff_evaluable else None
        )
        items.append(
            {
                "order_item_id": item_id,
                "affected_item_id": f"{order_id}:{item_id}",
                "seller_id": seller_id,
                "shipping_limit_date": _format_timestamp(shipping_limit),
                "handoff_evaluable": handoff_evaluable,
                "seller_handoff_late": handoff_late,
                "item_evidence_id": f"item:{order_id}:{item_id}",
                "seller_evidence_id": f"seller:{seller_id}",
            }
        )

    seller_ids = _unique([item["seller_id"] for item in items])
    late_seller_ids = _unique(
        [
            item["seller_id"]
            for item in items
            if item["seller_handoff_late"] is True
        ]
    )
    handoff_evaluable = bool(items) and all(
        item["handoff_evaluable"] for item in items
    )
    if late_seller_ids:
        seller_handoff_late: bool | None = True
    elif handoff_evaluable:
        seller_handoff_late = False
    else:
        seller_handoff_late = None

    evidence_ids = _unique(
        [f"order:{order_id}"]
        + [item["item_evidence_id"] for item in items]
        + [f"seller:{seller_id}" for seller_id in seller_ids]
    )

    return {
        "order_id": order_id,
        "order_found": True,
        "order_status": order_status,
        "order_delivered_carrier_date": _format_timestamp(carrier_date),
        "handoff_evaluable": handoff_evaluable,
        "seller_handoff_late": seller_handoff_late,
        "items": items,
        "seller_ids": seller_ids,
        "late_seller_ids": late_seller_ids,
        "evidence_ids": evidence_ids,
    }


class OrderSellerTool:
    """Bound tool whose public model-facing method needs only an order ID."""

    name = "query_order_seller"
    description = (
        "Look up an Olist order, its items and sellers, and determine whether "
        "the seller handed any item to the carrier after shipping_limit_date."
    )

    def __init__(self, loader: OlistDataLoader) -> None:
        self._loader = loader

    def lookup(self, claimed_order_id: str) -> OrderSellerToolResponse:
        """Return verified Order/Seller facts for one claimed order ID."""

        return query_order_seller(claimed_order_id, self._loader)

