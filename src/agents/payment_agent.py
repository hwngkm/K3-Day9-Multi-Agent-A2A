"""
P4 — Payment Agent
==================
Interface  : analyze(case: InputCase, loader: OlistDataLoader) -> PaymentEvidence
Đọc        : order_payments (qua loader), order_items (qua loader)
Không đọc  : orders thô, sellers, delivery data

Quy tắc nghiệp vụ (EC_POLICY_V1):
  - valid_split_payment = True khi:
      * Có từ 2 payment row trở lên
      * |payment_total − (item_total + freight_total)| ≤ 0.10 BRL
  - Mọi số tiền làm tròn 2 chữ số thập phân (dùng money() từ schemas)

Evidence IDs (README mục 5):
  - payment_ids   : "<order_id>:<payment_sequential>"  (cho affected_entities)
  - evidence_ids  : "payment:<order_id>:<payment_sequential>"  (cho evidence block)
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ..data_loader import OlistDataLoader
from ..schemas import (
    MAX_ENTITY_IDS,
    MAX_EVIDENCE_IDS,
    InputCase,
    PaymentEvidence,
    money,
)

# ── Hard-coded model name (không dùng LLM cho P4 — pure deterministic) ──
# P4 là thuần tính toán, không cần LLM. Ghi nhận để metadata.json tham chiếu.
MODEL_NAME = "none (deterministic)"

logger = logging.getLogger(__name__)

# Ngưỡng sai số đối soát theo EC_POLICY_V1 (README bảng rule hàng valid_split_payment)
_TOLERANCE = Decimal("0.10")


def analyze(case: InputCase, loader: OlistDataLoader) -> PaymentEvidence:
    """
    Entry point được Coordinator gọi song song với P2 và P3.

    Parameters
    ----------
    case   : InputCase  — chứa claimed_order_id
    loader : OlistDataLoader  — đã load sẵn toàn bộ CSV

    Returns
    -------
    PaymentEvidence  — khớp đúng contract schemas.py
    """
    order_id = case.claimed_order_id

    # 1. Lấy payment rows và item rows từ loader (không đọc CSV trực tiếp)
    payment_rows = loader.order_payments(order_id)  # tuple[Mapping[str,str], ...]
    item_rows    = loader.order_items(order_id)     # tuple[Mapping[str,str], ...]

    # 2. Tính tổng tiền (Decimal, làm tròn qua money())
    item_total    = money(sum(Decimal(r["price"])         for r in item_rows)    if item_rows else Decimal("0"))
    freight_total = money(sum(Decimal(r["freight_value"]) for r in item_rows)    if item_rows else Decimal("0"))
    payment_total = money(sum(Decimal(r["payment_value"]) for r in payment_rows) if payment_rows else Decimal("0"))

    item_freight_total = money(item_total + freight_total)

    # 3. Cờ valid_split_payment (EC_POLICY_V1)
    has_multiple = len(payment_rows) >= 2
    amounts_match = abs(payment_total - item_freight_total) <= _TOLERANCE
    valid_split_payment = has_multiple and amounts_match

    logger.debug(
        "[PaymentAgent] order=%s  payment_total=%s  item+freight=%s  "
        "rows=%d  valid_split=%s",
        order_id, payment_total, item_freight_total,
        len(payment_rows), valid_split_payment,
    )

    # 4. Build payment_ids  →  "<order_id>:<payment_sequential>"
    #    (dùng trong affected_entities.payment_ids của output JSON)
    sorted_payments = sorted(payment_rows, key=lambda r: int(r["payment_sequential"]))
    payment_ids: tuple[str, ...] = tuple(
        f"{order_id}:{r['payment_sequential']}"
        for r in sorted_payments
    )[:MAX_ENTITY_IDS]

    # 5. Build evidence_ids  →  "payment:<order_id>:<payment_sequential>"
    #    (dùng trong evidence_ids block của output JSON)
    evidence_ids: tuple[str, ...] = tuple(
        f"payment:{order_id}:{r['payment_sequential']}"
        for r in sorted_payments
    )[:MAX_EVIDENCE_IDS]

    return PaymentEvidence(
        order_id=order_id,
        item_total_brl=item_total,
        freight_total_brl=freight_total,
        payment_total_brl=payment_total,
        valid_split_payment=valid_split_payment,
        payment_ids=payment_ids,
        evidence_ids=evidence_ids,
    )
