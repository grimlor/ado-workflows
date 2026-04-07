"""
Shared error classification for Azure DevOps SDK exceptions.

Provides :func:`classify_ado_error` — a pure function that maps any
exception raised by the ADO Python SDK into a structured
:class:`~actionable_errors.ActionableError` with the most specific
error kind available.
"""

from __future__ import annotations

from actionable_errors import ActionableError
from azure.devops.exceptions import (
    AzureDevOpsAuthenticationError,
    AzureDevOpsServiceError,
)

_NOT_FOUND_MARKERS = ("TF401174", "does not exist")
"""Substrings in error messages that indicate a not-found condition."""

_PERMISSION_TYPE_KEYS = ("Security", "Permission")
"""Substrings in AzureDevOpsServiceError.type_key that indicate a permission failure."""


def classify_ado_error(
    exc: Exception,
    *,
    operation: str,
    context_hint: str = "",
) -> ActionableError:
    """
    Classify an Azure DevOps SDK exception into a structured ActionableError.

    Inspects exception type hierarchy and structured fields (``type_key``,
    ``error_code``, message) to produce the most specific ActionableError
    kind.

    Args:
        exc: The caught exception.
        operation: What was being attempted (e.g., ``"list items at '/src'"``).
        context_hint: Additional context for the suggestion (e.g., a file path).

    Returns:
        An :class:`ActionableError` of the appropriate kind — never raises.

    """
    error_str = str(exc)
    hint_suffix = f" Path: '{context_hint}'." if context_hint else ""

    # 1. Authentication errors — distinct SDK exception class
    if isinstance(exc, AzureDevOpsAuthenticationError):
        return ActionableError.authentication(
            service="AzureDevOps",
            raw_error=f"Failed to {operation}: {error_str}",
            suggestion=(
                f"Authentication failed while trying to {operation}. "
                f"Check that your PAT or Azure CLI token has not expired and "
                f"has the required scopes.{hint_suffix}"
            ),
        )

    # 2. Service errors — inspect structured type_key first, then message
    if isinstance(exc, AzureDevOpsServiceError):
        type_key = getattr(exc, "type_key", "") or ""

        # 2a. Not-found by type_key
        if "NotFound" in type_key:
            return ActionableError.not_found(
                service="AzureDevOps",
                resource_type="item",
                resource_id=context_hint or operation,
                raw_error=f"Failed to {operation}: {error_str}",
                suggestion=(
                    f"The resource was not found while trying to {operation}. "
                    f"Verify the path, repository name, and branch reference "
                    f"are correct.{hint_suffix}"
                ),
            )

        # 2b. Permission/security by type_key
        if any(marker in type_key for marker in _PERMISSION_TYPE_KEYS):
            return ActionableError.permission(
                service="AzureDevOps",
                resource=context_hint or operation,
                raw_error=f"Failed to {operation}: {error_str}",
                suggestion=(
                    f"Access denied while trying to {operation}. "
                    f"Check that your PAT has the Code (Read) scope and "
                    f"you have access to the project.{hint_suffix}"
                ),
            )

        # 2c. Not-found by message fallback (for errors without typed type_key)
        if _is_not_found_message(error_str):
            return ActionableError.not_found(
                service="AzureDevOps",
                resource_type="item",
                resource_id=context_hint or operation,
                raw_error=f"Failed to {operation}: {error_str}",
                suggestion=(
                    f"The resource was not found while trying to {operation}. "
                    f"Verify the path, repository name, and branch reference "
                    f"are correct.{hint_suffix}"
                ),
            )

        # 2d. Unclassified service error → internal
        return ActionableError.internal(
            service="AzureDevOps",
            operation=operation,
            raw_error=error_str,
            suggestion=(
                f"An unexpected server error occurred while trying to "
                f"{operation}. This may be transient — retry the operation. "
                f"If it persists, check Azure DevOps service health.{hint_suffix}"
            ),
        )

    # 3. Not-found by message (non-ADO exceptions, e.g., generic Exception)
    if _is_not_found_message(error_str):
        return ActionableError.not_found(
            service="AzureDevOps",
            resource_type="item",
            resource_id=context_hint or operation,
            raw_error=f"Failed to {operation}: {error_str}",
            suggestion=(
                f"The resource was not found while trying to {operation}. "
                f"Verify the path, repository name, and branch reference "
                f"are correct.{hint_suffix}"
            ),
        )

    # 4. Everything else — connection/network
    return ActionableError.connection(
        service="AzureDevOps",
        url=operation,
        raw_error=f"Failed to {operation}: {error_str}",
        suggestion=(
            f"A connection error occurred while trying to {operation}. "
            f"Check network connectivity to Azure DevOps.{hint_suffix}"
        ),
    )


def _is_not_found_message(error_str: str) -> bool:
    """Check if an error message indicates a not-found condition."""
    lower = error_str.lower()
    return "TF401174" in error_str or "does not exist" in lower or "was not found" in lower
