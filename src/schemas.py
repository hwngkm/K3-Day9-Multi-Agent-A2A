"""Canonical contracts shared by the e-commerce dispute agents.

The constants and public JSON shape in this module intentionally mirror the
assignment README.  Agent modules exchange dataclass instances, while only
``OutputVerdict.to_dict`` is used at the JSON boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


POLICY_VERSION = "EC_POLICY_V1"
CURRENCY_BRL = "BRL"

ACTION_REQUIRED = "action_required"
NO_ACTION = "no_action"

PRIMARY_ISSUES = frozenset(
    {
        "canceled_order_paid",
        "unavailable_order_paid",
        "late_delivery_seller",
        "late_delivery_logistics",
        "valid_split_payment",
        "unsupported_late_claim",
    }
)
ROOT_CAUSE_CODES = frozenset(
    {
        "SELLER_HANDOFF_AFTER_LIMIT",
        "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "ORDER_CANCELED_AFTER_PAYMENT",
        "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "MULTIPLE_PAYMENTS_RECONCILED",
        "DELIVERY_WITHIN_ESTIMATE",
    }
)
RESOLUTION_ACTIONS = frozenset(
    {
        "issue_full_refund",
        "refund_freight",
        "explain_valid_split_payment",
        "reject_late_refund",
    }
)
PARTY_TYPES = frozenset({"platform", "seller", "logistics_provider"})
PLATFORM_PARTY_ID = "OLIST_PLATFORM"
LOGISTICS_PARTY_ID = "LOGISTICS_PROVIDER"

MAX_ENTITY_IDS = 5
MAX_EVIDENCE_IDS = 10
MAX_ROOT_CAUSES = 3
MAX_RESPONSIBLE_PARTIES = 3
MAX_RESOLUTION_ACTIONS = 5

MONEY_QUANTUM = Decimal("0.01")


class ContractError(ValueError):
    """Raised when data does not comply with the CP0 contract."""


def money(value: Decimal | int | float | str) -> Decimal:
    """Round a BRL monetary value deterministically to two decimal places."""

    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _required_string(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value


def _at_most(values: Sequence[str], maximum: int, field: str) -> tuple[str, ...]:
    result = tuple(values)
    if len(result) > maximum:
        raise ContractError(f"{field} cannot contain more than {maximum} values")
    return result


@dataclass(frozen=True)
class InputCase:
    """A validated case supplied by ``input/EC_XXX.json``."""

    case_id: str
    opened_at: str
    language: str
    message: str
    claimed_order_id: str
    policy_version: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InputCase":
        customer_request = data.get("customer_request")
        if not isinstance(customer_request, Mapping):
            raise ContractError("customer_request must be an object")

        policy_version = _required_string(data, "policy_version")
        if policy_version != POLICY_VERSION:
            raise ContractError(
                f"policy_version must be {POLICY_VERSION}, got {policy_version}"
            )

        return cls(
            case_id=_required_string(data, "case_id"),
            opened_at=_required_string(data, "opened_at"),
            language=_required_string(customer_request, "language"),
            message=_required_string(customer_request, "message"),
            claimed_order_id=_required_string(customer_request, "claimed_order_id"),
            policy_version=policy_version,
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "InputCase":
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"Cannot read input case {path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ContractError(f"Input case {path} must be a JSON object")
        return cls.from_mapping(data)


@dataclass(frozen=True)
class OrderSellerEvidence:
    """Handoff from P2 to P1/P5; no raw CSV is passed to Policy Agent."""

    order_id: str
    order_status: str
    item_ids: tuple[str, ...]
    seller_ids: tuple[str, ...]
    seller_handoff_late: bool
    late_seller_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _at_most(self.item_ids, MAX_ENTITY_IDS, "item_ids")
        _at_most(self.seller_ids, MAX_ENTITY_IDS, "seller_ids")


@dataclass(frozen=True)
class DeliveryEvidence:
    """Handoff from P3 with the derived delivery-timeliness flag."""

    order_id: str
    carrier_delivered_late: bool
    order_delivered_carrier_date: str | None
    order_delivered_customer_date: str | None
    order_estimated_delivery_date: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class PaymentEvidence:
    """Handoff from P4 with reconciled monetary totals."""

    order_id: str
    item_total_brl: Decimal
    freight_total_brl: Decimal
    payment_total_brl: Decimal
    valid_split_payment: bool
    payment_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _at_most(self.payment_ids, MAX_ENTITY_IDS, "payment_ids")
        object.__setattr__(self, "item_total_brl", money(self.item_total_brl))
        object.__setattr__(self, "freight_total_brl", money(self.freight_total_brl))
        object.__setattr__(self, "payment_total_brl", money(self.payment_total_brl))


@dataclass(frozen=True)
class EvidenceBundle:
    """The sole input to P5; it prevents Policy Agent reads of raw CSV data."""

    order_seller: OrderSellerEvidence
    delivery: DeliveryEvidence
    payment: PaymentEvidence

    def __post_init__(self) -> None:
        order_ids = {
            self.order_seller.order_id,
            self.delivery.order_id,
            self.payment.order_id,
        }
        if len(order_ids) != 1:
            raise ContractError("All evidence records must belong to the same order")

    @property
    def order_id(self) -> str:
        return self.order_seller.order_id


@dataclass(frozen=True)
class RankedCause:
    cause_code: str
    rank: int

    def __post_init__(self) -> None:
        if self.cause_code not in ROOT_CAUSE_CODES:
            raise ContractError(f"Unknown root-cause code: {self.cause_code}")
        if self.rank < 1:
            raise ContractError("Cause rank must be at least 1")

    def to_dict(self) -> dict[str, Any]:
        return {"cause_code": self.cause_code, "rank": self.rank}


@dataclass(frozen=True)
class ResponsibleParty:
    party_type: str
    party_id: str

    def __post_init__(self) -> None:
        if self.party_type not in PARTY_TYPES:
            raise ContractError(f"Unknown party type: {self.party_type}")
        if not self.party_id:
            raise ContractError("Responsible party ID must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {"party_type": self.party_type, "party_id": self.party_id}


@dataclass(frozen=True)
class PolicyResult:
    """P5 decision.  P1 derives entity and financial blocks from evidence."""

    primary_issue: str
    case_status: str
    confidence: float
    ranked_causes: tuple[RankedCause, ...]
    responsible_parties: tuple[ResponsibleParty, ...]
    recommended_refund_brl: Decimal
    resolution_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.primary_issue not in PRIMARY_ISSUES:
            raise ContractError(f"Unknown primary issue: {self.primary_issue}")
        if self.case_status not in {ACTION_REQUIRED, NO_ACTION}:
            raise ContractError(f"Unknown case status: {self.case_status}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractError("confidence must be in [0, 1]")
        _at_most(self.ranked_causes, MAX_ROOT_CAUSES, "ranked_causes")
        _at_most(
            self.responsible_parties,
            MAX_RESPONSIBLE_PARTIES,
            "responsible_parties",
        )
        actions = _at_most(
            self.resolution_actions,
            MAX_RESOLUTION_ACTIONS,
            "resolution_actions",
        )
        unknown_actions = set(actions).difference(RESOLUTION_ACTIONS)
        if unknown_actions:
            raise ContractError(f"Unknown resolution actions: {sorted(unknown_actions)}")
        object.__setattr__(self, "recommended_refund_brl", money(self.recommended_refund_brl))


@dataclass(frozen=True)
class VerificationResult:
    """P6 response. Failed verdicts must never be written to ``output/``."""

    passed: bool
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.passed and self.errors:
            raise ContractError("A passed verification result cannot contain errors")
        if not self.passed and not self.errors:
            raise ContractError("A failed verification result must explain its errors")


@dataclass(frozen=True)
class OutputVerdict:
    """Final output that exactly follows README section 6."""

    case_id: str
    assessment: Mapping[str, Any]
    affected_entities: Mapping[str, Sequence[str]]
    root_cause_analysis: Mapping[str, Sequence[Any]]
    evidence_ids: tuple[str, ...]
    financial_resolution: Mapping[str, Any]
    resolution_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "assessment": dict(self.assessment),
            "affected_entities": {
                key: list(values) for key, values in self.affected_entities.items()
            },
            "root_cause_analysis": {
                "ranked_causes": [
                    cause.to_dict() if isinstance(cause, RankedCause) else dict(cause)
                    for cause in self.root_cause_analysis["ranked_causes"]
                ],
                "responsible_parties": [
                    party.to_dict() if isinstance(party, ResponsibleParty) else dict(party)
                    for party in self.root_cause_analysis["responsible_parties"]
                ],
            },
            "evidence_ids": list(self.evidence_ids),
            "financial_resolution": dict(self.financial_resolution),
            "resolution_actions": list(self.resolution_actions),
        }
