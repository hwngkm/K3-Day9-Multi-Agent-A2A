"""Config-driven orchestration of the evidence, policy, verification, and explanation DAG."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

from .agent_runtime import AgentRegistry, AgentSpec, TaskGraph
from .data_loader import OlistDataLoader
from .schemas import (
    CURRENCY_BRL,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE_IDS,
    ContractError,
    DeliveryEvidence,
    EvidenceBundle,
    HandoffEnvelope,
    InputCase,
    OrderSellerEvidence,
    OutputVerdict,
    PaymentEvidence,
    PolicyResult,
    VerificationResult,
)


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "agents.json"


class AgentIntegrationError(RuntimeError):
    """Raised when a registered agent violates its stage contract."""


class VerificationFailedError(RuntimeError):
    """Raised instead of writing a verdict rejected by the Verifier agent."""


class AgentTaskFailure(RuntimeError):
    """Carries a failed handoff envelope so the audit trail remains complete."""

    def __init__(self, envelope: HandoffEnvelope) -> None:
        super().__init__(envelope.error or f"Agent {envelope.agent_name} failed")
        self.envelope = envelope


@dataclass(frozen=True)
class AgentRun:
    spec: AgentSpec
    value: Any
    envelope: HandoffEnvelope


@dataclass(frozen=True)
class CaseExecution:
    """A verifier-approved verdict plus every task-graph handoff."""

    verdict: OutputVerdict
    evidence: EvidenceBundle
    policy: PolicyResult
    verification: VerificationResult
    explanation: Mapping[str, str]
    handoffs: tuple[HandoffEnvelope, ...]


def _unique_limited(values: list[str] | tuple[str, ...], maximum: int) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
        if len(result) == maximum:
            break
    return tuple(result)


def _evidence_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, PolicyResult):
        return tuple(f"policy:{cause.cause_code}" for cause in value.ranked_causes)
    raw = getattr(value, "evidence_ids", ())
    return tuple(raw) if isinstance(raw, (tuple, list)) else ()


def _bundle_from_runs(runs: Mapping[str, AgentRun]) -> EvidenceBundle:
    values = [run.value for run in runs.values()]
    order_seller = next((value for value in values if isinstance(value, OrderSellerEvidence)), None)
    delivery = next((value for value in values if isinstance(value, DeliveryEvidence)), None)
    payment = next((value for value in values if isinstance(value, PaymentEvidence)), None)
    if not all((order_seller, delivery, payment)):
        raise AgentIntegrationError("Evidence layer must return order, delivery, and payment evidence")
    return EvidenceBundle(order_seller, delivery, payment)


def assemble_verdict(case: InputCase, evidence: EvidenceBundle, policy: PolicyResult) -> OutputVerdict:
    """Build the strict README section 6 JSON from typed handoffs only."""

    policy_evidence = tuple(f"policy:{cause.cause_code}" for cause in policy.ranked_causes)
    order_evidence = (f"order:{evidence.order_id}",)
    item_evidence = tuple(f"item:{item_id}" for item_id in evidence.order_seller.item_ids)
    seller_evidence = tuple(f"seller:{seller_id}" for seller_id in evidence.order_seller.seller_ids)
    payment_evidence = evidence.payment.evidence_ids
    if policy.primary_issue in {"canceled_order_paid", "unavailable_order_paid"}:
        candidates = (*order_evidence, *payment_evidence)
    elif policy.primary_issue == "late_delivery_seller":
        candidates = (*order_evidence, *item_evidence, *seller_evidence)
    elif policy.primary_issue == "late_delivery_logistics":
        candidates = (*order_evidence, *item_evidence)
    else:
        candidates = (*order_evidence, *item_evidence, *payment_evidence)
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
        evidence_ids=_unique_limited([*candidates, *policy_evidence], MAX_EVIDENCE_IDS),
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
    """Runs registry-defined DAG layers; business logic remains in specialist agents."""

    def __init__(self, loader: OlistDataLoader, registry: AgentRegistry | None = None) -> None:
        self._loader = loader
        self._registry = registry or AgentRegistry.from_file(DEFAULT_REGISTRY_PATH)
        self._graph = TaskGraph(self._registry)

    @property
    def task_graph(self) -> TaskGraph:
        return self._graph

    def run_case(self, case: InputCase) -> OutputVerdict:
        return self.run_case_with_execution(case).verdict

    def run_case_with_execution(self, case: InputCase) -> CaseExecution:
        self._loader.require_order(case.claimed_order_id)
        runs: dict[str, AgentRun] = {}
        evidence: EvidenceBundle | None = None
        policy: PolicyResult | None = None
        verdict: OutputVerdict | None = None
        verification: VerificationResult | None = None
        explanation: Mapping[str, str] = {}

        for layer in self._graph.layers():
            with ThreadPoolExecutor(max_workers=len(layer)) as executor:
                futures = {
                    spec.name: executor.submit(
                        self._run_agent, spec, case, runs, evidence, verdict
                    )
                    for spec in layer
                }
                for spec in layer:
                    try:
                        run = futures[spec.name].result()
                    except AgentTaskFailure as exc:
                        runs[spec.name] = AgentRun(spec, None, exc.envelope)
                        raise AgentIntegrationError(str(exc)) from exc
                    runs[spec.name] = run

            stage = layer[0].stage
            if stage == "evidence":
                evidence = _bundle_from_runs(runs)
            elif stage == "policy":
                policy_value = runs[layer[0].name].value
                if not isinstance(policy_value, PolicyResult) or evidence is None:
                    raise AgentIntegrationError("Policy stage must return PolicyResult after evidence")
                policy = policy_value
                verdict = assemble_verdict(case, evidence, policy)
            elif stage == "verifier":
                verification_value = runs[layer[0].name].value
                if not isinstance(verification_value, VerificationResult):
                    raise AgentIntegrationError("Verifier stage must return VerificationResult")
                verification = verification_value
                if not verification.passed:
                    raise VerificationFailedError("; ".join(verification.errors))
            elif stage == "explanation":
                explanation_value = runs[layer[0].name].value
                if not isinstance(explanation_value, Mapping):
                    raise AgentIntegrationError("Explanation stage must return an object")
                explanation = {str(key): str(value) for key, value in explanation_value.items()}
            else:
                raise AgentIntegrationError(f"Unknown task-graph stage: {stage}")

        if not all((evidence, policy, verdict, verification)):
            raise AgentIntegrationError("Task graph did not complete the required verdict stages")
        return CaseExecution(
            verdict=verdict,
            evidence=evidence,
            policy=policy,
            verification=verification,
            explanation=explanation,
            handoffs=tuple(run.envelope for run in runs.values()),
        )

    def _run_agent(
        self,
        spec: AgentSpec,
        case: InputCase,
        runs: Mapping[str, AgentRun],
        evidence: EvidenceBundle | None,
        verdict: OutputVerdict | None,
    ) -> AgentRun:
        function = self._registry.resolve(spec)
        started = datetime.now(timezone.utc)
        started_clock = perf_counter()
        try:
            if spec.stage == "evidence":
                value = function(case, self._loader)
            elif spec.stage == "policy":
                if evidence is None:
                    raise AgentIntegrationError("Policy agent ran before evidence was complete")
                value = function(evidence)
            elif spec.stage == "verifier":
                if verdict is None:
                    raise AgentIntegrationError("Verifier agent ran before verdict assembly")
                value = function(case, verdict, self._loader)
            elif spec.stage == "explanation":
                if verdict is None or evidence is None:
                    raise AgentIntegrationError("Explanation agent ran before verification")
                value = function(case, verdict, evidence)
            else:
                raise AgentIntegrationError(f"Unknown registered stage: {spec.stage}")
        except Exception as exc:
            finished = datetime.now(timezone.utc)
            envelope = HandoffEnvelope(
                agent_name=spec.name,
                case_id=case.case_id,
                stage=spec.stage,
                status="error",
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                duration_ms=(perf_counter() - started_clock) * 1000,
                depends_on=spec.depends_on,
                evidence_ids=(),
                payload=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise AgentTaskFailure(envelope) from exc
        finished = datetime.now(timezone.utc)
        envelope = HandoffEnvelope(
            agent_name=spec.name,
            case_id=case.case_id,
            stage=spec.stage,
            status="completed",
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_ms=(perf_counter() - started_clock) * 1000,
            depends_on=spec.depends_on,
            evidence_ids=_evidence_ids(value),
            payload=value,
        )
        return AgentRun(spec, value, envelope)
