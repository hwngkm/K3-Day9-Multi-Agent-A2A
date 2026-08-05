"""Post-verification explanation agent; it cannot mutate the submission verdict."""

from __future__ import annotations

from ..model_gateway import ModelGateway
from ..schemas import EvidenceBundle, InputCase, OutputVerdict


def analyze(
    case: InputCase, verdict: OutputVerdict, evidence: EvidenceBundle
) -> dict[str, str]:
    """Create an explanation only after a verifier-approved verdict exists."""

    output = verdict.to_dict()
    assessment = output["assessment"]
    financial = output["financial_resolution"]
    fallback = (
        f"Case {case.case_id}: {assessment['primary_issue']}; "
        f"recommended refund is {financial['recommended_refund_brl']:.2f} BRL."
    )
    return ModelGateway().explain(
        {
            "case_id": case.case_id,
            "primary_issue": assessment["primary_issue"],
            "case_status": assessment["case_status"],
            "responsible_parties": output["root_cause_analysis"]["responsible_parties"],
            "recommended_refund_brl": financial["recommended_refund_brl"],
            "actions": output["resolution_actions"],
            "evidence_ids": output["evidence_ids"],
        },
        fallback,
    )
