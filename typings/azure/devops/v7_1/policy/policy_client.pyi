"""Partial stubs for azure.devops.v7_1.policy.policy_client."""

from __future__ import annotations

from azure.devops.v7_1.policy.models import PolicyEvaluationRecord

class PolicyClient:
    def get_policy_evaluations(
        self,
        project: str,
        artifact_id: str,
        include_not_applicable: bool | None = None,
        top: int | None = None,
        skip: int | None = None,
    ) -> list[PolicyEvaluationRecord]: ...
