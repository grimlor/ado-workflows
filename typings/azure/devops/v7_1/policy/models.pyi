"""Partial stubs for azure.devops.v7_1.policy.models."""

from __future__ import annotations

from typing import Any

class PolicyTypeRef:
    display_name: str
    id: str | None
    url: str | None


class PolicyConfiguration:
    type: PolicyTypeRef
    settings: dict[str, Any]
    id: int | None
    is_enabled: bool | None
    is_blocking: bool | None


class PolicyEvaluationRecord:
    configuration: PolicyConfiguration
    status: str | None
    artifact_id: str | None
