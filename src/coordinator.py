"""P1 orchestration: parallel evidence collection, then policy and verification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import importlib
from typing import Any, Callable, Mapping

from .data_loader import OlistDataLoader
from .schemas import (
    CURRENCY_BRL,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE_IDS,
    ContractError,
    DeliveryEvidence,
    EvidenceBundle,
    InputCase,
    OrderSellerEvidence,
    OutputVerdict,
    PaymentEvidence,
    PolicyResult,
    VerificationResult,
)


class AgentIntegrationError(RuntimeError):
    """Raised when a P2-P6 module does not implement the CP0 interface."""


class VerificationFailedError(RuntimeError):
    """Raised instead of writing a verdict rejected by P6."""


EvidenceAnalyzer = Callable[[InputCase, OlistDataLoader], Any]
PolicyAnalyzer = Callable[[EvidenceBundle], PolicyResult]
Verifier = Callable[[InputCase, OutputVerdict, OlistDataLoader], VerificationResult]


@dataclass(frozen=True)
class AgentSet:
    """Dependency-injection point and the documented P2-P6 handoff contract.

    Required module functions:
    - ``src.agents.order_seller_agent.analyze(case, loader)``
    - ``src.agents.delivery_agent.analyze(case, loader)``
    - ``src.agents.payment_agent.analyze(case, loader)``
    - ``src.agents.policy_agent.analyze(evidence_bundle)``
    - ``src.agents.verifier_agent.verify(case, verdict, loader)``
    """

    order_seller: EvidenceAnalyzer
    delivery: EvidenceAnalyzer
    payment: EvidenceAnalyzer
    policy: PolicyAnalyzer
    verifier: Verifier

    @classmethod
    def from_default_modules(cls) -> "AgentSet":
        return cls(
            order_seller=_load_callable("src.agents.order_seller_agent", "analyze"),
            delivery=_load_callable("src.agents.delivery_agent", "analyze"),
            payment=_load_callable("src.agents.payment_agent", "analyze"),
            policy=_load_callable("src.agents.policy_agent", "analyze"),
            verifier=_load_callable("src.agents.verifier_agent", "verify"),
        )


def _load_callable(module_name: str, function_name: str) -> Callable[..., Any]:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise AgentIntegrationError(
            f"Missing {module_name}. Implement the CP0 agent interface before running."
        ) from exc
    function = getattr(module, function_name, None)
    if not callable(function):
        raise AgentIntegrationError(
            f"{module_name} must expose callable {function_name}(...)"
        )
    return function


def _unique_limited(values: tuple[str, ...] | list[str], maximum: int) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
        if len(result) == maximum:
            break
    return tuple(result)


def assemble_verdict(
    case: InputCase, evidence: EvidenceBundle, policy: PolicyResult
) -> OutputVerdict:
    """Build the README section 6 JSON from validated handoffs only."""

    evidence_ids = _unique_limited(
        [
            *evidence.order_seller.evidence_ids,
            *evidence.delivery.evidence_ids,
            *evidence.payment.evidence_ids,
            *(f"policy:{cause.cause_code}" for cause in policy.ranked_causes),
        ],
        MAX_EVIDENCE_IDS,
    )
    payment = evidence.payment
    return OutputVerdict(
        case_id=case.case_id,
        assessment={
            "primary_issue": policy.primary_issue,
            "case_status": policy.case_status,
            "confidence": policy.confidence,
        },
        affected_entities={
            "order_ids": (evidence.order_id,),
            "item_ids": _unique_limited(evidence.order_seller.item_ids, MAX_ENTITY_IDS),
            "seller_ids": _unique_limited(evidence.order_seller.seller_ids, MAX_ENTITY_IDS),
            "payment_ids": _unique_limited(payment.payment_ids, MAX_ENTITY_IDS),
        },
        root_cause_analysis={
            "ranked_causes": policy.ranked_causes,
            "responsible_parties": policy.responsible_parties,
        },
        evidence_ids=evidence_ids,
        financial_resolution={
            "currency": CURRENCY_BRL,
            "item_total_brl": float(payment.item_total_brl),
            "freight_total_brl": float(payment.freight_total_brl),
            "payment_total_brl": float(payment.payment_total_brl),
            "recommended_refund_brl": float(policy.recommended_refund_brl),
        },
        resolution_actions=policy.resolution_actions,
    )


class Coordinator:
    """Coordinates agents without making an independent business-rule decision."""

    def __init__(self, loader: OlistDataLoader, agents: AgentSet | None = None) -> None:
        self._loader = loader
        self._agents = agents or AgentSet.from_default_modules()

    def run_case(self, case: InputCase) -> OutputVerdict:
        self._loader.require_order(case.claimed_order_id)
        evidence = self._collect_evidence(case)
        policy = self._agents.policy(evidence)
        if not isinstance(policy, PolicyResult):
            raise AgentIntegrationError("Policy Agent must return PolicyResult")
        verdict = assemble_verdict(case, evidence, policy)
        verification = self._agents.verifier(case, verdict, self._loader)
        if not isinstance(verification, VerificationResult):
            raise AgentIntegrationError("Verifier Agent must return VerificationResult")
        if not verification.passed:
            raise VerificationFailedError("; ".join(verification.errors))
        return verdict

    def _collect_evidence(self, case: InputCase) -> EvidenceBundle:
        analyzers: Mapping[str, EvidenceAnalyzer] = {
            "order_seller": self._agents.order_seller,
            "delivery": self._agents.delivery,
            "payment": self._agents.payment,
        }
        with ThreadPoolExecutor(max_workers=len(analyzers)) as executor:
            futures = {
                name: executor.submit(analyzer, case, self._loader)
                for name, analyzer in analyzers.items()
            }
            results = {name: future.result() for name, future in futures.items()}

        order_seller = results["order_seller"]
        delivery = results["delivery"]
        payment = results["payment"]
        if not isinstance(order_seller, OrderSellerEvidence):
            raise AgentIntegrationError("Order & Seller Agent must return OrderSellerEvidence")
        if not isinstance(delivery, DeliveryEvidence):
            raise AgentIntegrationError("Delivery Agent must return DeliveryEvidence")
        if not isinstance(payment, PaymentEvidence):
            raise AgentIntegrationError("Payment Agent must return PaymentEvidence")
        try:
            return EvidenceBundle(order_seller, delivery, payment)
        except ContractError as exc:
            raise AgentIntegrationError(f"Evidence handoff violates CP0: {exc}") from exc
