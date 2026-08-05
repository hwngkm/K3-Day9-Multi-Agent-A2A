"""Validated, versioned loading for the externally configured policy table."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

from .schemas import (
    ACTION_REQUIRED,
    NO_ACTION,
    PARTY_TYPES,
    POLICY_VERSION,
    PRIMARY_ISSUES,
    RESOLUTION_ACTIONS,
    ROOT_CAUSE_CODES,
    ContractError,
    RuleSpec,
)


POLICY_PATH = Path(__file__).resolve().parent.parent / "policy" / "EC_POLICY_V1.json"
REFUND_STRATEGIES = frozenset({"payment_total", "freight_total", "zero"})


@dataclass(frozen=True)
class ConfiguredRule:
    """A canonical policy rule plus the evaluation and refund strategies."""

    condition: str
    spec: RuleSpec
    refund_strategy: str


@lru_cache(maxsize=1)
def load_policy_rules(path: Path = POLICY_PATH) -> tuple[ConfiguredRule, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Cannot load policy configuration {path}: {exc}") from exc
    if data.get("policy_version") != POLICY_VERSION:
        raise ContractError(f"Policy configuration must declare {POLICY_VERSION}")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ContractError("Policy configuration must contain a non-empty rules list")

    rules: list[ConfiguredRule] = []
    seen_issues: set[str] = set()
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ContractError("Each policy rule must be an object")
        issue = raw.get("primary_issue")
        cause = raw.get("root_cause_code")
        action = raw.get("action")
        status = raw.get("case_status")
        condition = raw.get("condition")
        party = raw.get("responsible_party")
        strategy = raw.get("refund_strategy")
        if issue not in PRIMARY_ISSUES or issue in seen_issues:
            raise ContractError(f"Invalid or duplicate primary issue in policy: {issue}")
        if cause not in ROOT_CAUSE_CODES or action not in RESOLUTION_ACTIONS:
            raise ContractError(f"Invalid cause or action for policy issue {issue}")
        if status not in {ACTION_REQUIRED, NO_ACTION} or not isinstance(condition, str):
            raise ContractError(f"Invalid status or condition for policy issue {issue}")
        if strategy not in REFUND_STRATEGIES:
            raise ContractError(f"Invalid refund strategy for policy issue {issue}")
        party_type: str | None = None
        party_id: str | None = None
        if party is not None:
            if not isinstance(party, dict):
                raise ContractError(f"Invalid responsible party for policy issue {issue}")
            party_type = party.get("party_type")
            party_id = party.get("party_id")
            if party_type not in PARTY_TYPES or (party_id is not None and not isinstance(party_id, str)):
                raise ContractError(f"Invalid responsible party for policy issue {issue}")
        rules.append(
            ConfiguredRule(
                condition=condition,
                spec=RuleSpec(issue, cause, party_type, party_id, action, status),
                refund_strategy=strategy,
            )
        )
        seen_issues.add(issue)
    if seen_issues != PRIMARY_ISSUES:
        raise ContractError("Policy configuration must define every canonical issue")
    return tuple(rules)


def policy_by_issue() -> dict[str, ConfiguredRule]:
    return {rule.spec.primary_issue: rule for rule in load_policy_rules()}
