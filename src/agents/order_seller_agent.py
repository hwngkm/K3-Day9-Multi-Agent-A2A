"""P2: Order & Seller Agent.

Access: olist_orders_dataset.csv, olist_order_items_dataset.csv,
olist_sellers_dataset.csv (all reachable via ``OlistDataLoader``).
Must NOT read payment data — that is Payment Agent's (P4) domain only.
"""

from __future__ import annotations

from datetime import datetime

from ..data_loader import OlistDataLoader
from ..schemas import InputCase, OrderSellerEvidence


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an Olist timestamp, preserving missing values as unknown."""

    return datetime.fromisoformat(value) if value else None


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def analyze(case: InputCase, loader: OlistDataLoader) -> OrderSellerEvidence:
    """Return order/item/seller evidence for ``case.claimed_order_id``.

    This deterministic implementation is the active runtime for the submitted
    verdict: CSV facts remain reproducible even when no local LLM is running.
    The separately merged ``src.tools.order_seller_tool`` remains available as
    an optional audited tool interface for future model-assisted explanation.
    """
    order_id = case.claimed_order_id
    order = loader.require_order(order_id)
    items = loader.order_items(order_id)
    carrier_received_at = _parse_timestamp(order["order_delivered_carrier_date"])

    item_ids: list[str] = []
    seller_ids: list[str] = []
    late_seller_ids: list[str] = []
    evidence_ids = [f"order:{order_id}"]
    for item in items:
        item_id = item["order_item_id"]
        seller_id = item["seller_id"]
        item_ids.append(f"{order_id}:{item_id}")
        seller_ids.append(seller_id)
        evidence_ids.append(f"item:{order_id}:{item_id}")

        if loader.seller(seller_id) is None:
            raise LookupError(f"Order {order_id} references unknown seller {seller_id}")
        if (
            carrier_received_at is not None
            and (shipping_limit := _parse_timestamp(item["shipping_limit_date"]))
            is not None
            and carrier_received_at > shipping_limit
        ):
            late_seller_ids.append(seller_id)

    unique_sellers = _unique(seller_ids)
    evidence_ids.extend(f"seller:{seller_id}" for seller_id in unique_sellers)
    return OrderSellerEvidence(
        order_id=order_id,
        order_status=order["order_status"],
        item_ids=tuple(item_ids),
        seller_ids=unique_sellers,
        seller_handoff_late=bool(late_seller_ids),
        late_seller_ids=_unique(late_seller_ids),
        evidence_ids=tuple(evidence_ids),
    )
