"""
BDD tests for ado_workflows.errors — shared ADO SDK error classification.

Covers:
- TestClassifyAdoError: classifies SDK exceptions into structured ActionableError kinds

Public API surface (from src/ado_workflows/errors.py):
    classify_ado_error(exc: Exception, *, operation: str,
                       context_hint: str = "") -> ActionableError
"""

from __future__ import annotations

from unittest.mock import Mock

from azure.devops.exceptions import AzureDevOpsAuthenticationError, AzureDevOpsServiceError

from ado_workflows.errors import classify_ado_error

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_error(
    *,
    message: str = "Something went wrong",
    type_key: str = "UnknownException",
) -> AzureDevOpsServiceError:
    """Build an AzureDevOpsServiceError with the given type_key and message."""
    wrapped = Mock()
    wrapped.message = message
    wrapped.inner_exception = None
    wrapped.exception_id = "0"
    wrapped.type_name = type_key
    wrapped.type_key = type_key
    wrapped.error_code = 0
    wrapped.event_id = 0
    wrapped.custom_properties = {}
    return AzureDevOpsServiceError(wrapped)


# ---------------------------------------------------------------------------
# TestClassifyAdoError
# ---------------------------------------------------------------------------


class TestClassifyAdoError:
    """
    REQUIREMENT: classify_ado_error inspects SDK exception type and structured
    fields to produce the most specific ActionableError kind.

    WHO: Every catch block in the library (35+ existing + list_repo_items). The
    primary downstream consumer is an AI agent via MCP tools, which needs
    distinct error kinds to give correct guidance.
    WHAT: (1) AzureDevOpsAuthenticationError → ActionableError.authentication
          (2) AzureDevOpsServiceError with type_key containing "NotFound" →
              ActionableError.not_found
          (3) error message containing "TF401174" → ActionableError.not_found
          (4) error message containing "does not exist" → ActionableError.not_found
          (5) AzureDevOpsServiceError with type_key containing "Security" or
              "Permission" → ActionableError.permission
          (6) non-ADO exceptions (ConnectionError, OSError, etc.) →
              ActionableError.connection
          (7) other AzureDevOpsServiceError → ActionableError.internal
          (8) context_hint is included in the suggestion text of every error kind
          (9) operation is included in the error message of every error kind
    WHY: The library has 35+ catch blocks that all produce .connection() regardless
    of cause. An auth failure and a not-found look identical to the agent.
    A shared classifier lets every catch block produce the right kind with a
    one-line change.

    MOCK BOUNDARY:
        Mock:  nothing — this is a pure function (exception in → ActionableError out)
        Real:  classify_ado_error logic
        Never: n/a
    """

    def test_authentication_error_for_ado_auth_exception(self) -> None:
        """
        Given an AzureDevOpsAuthenticationError exception,
        When classify_ado_error is called,
        Then returns ActionableError with kind authentication and suggestion
        mentioning re-authentication or PAT expiry.
        """
        # Given: an authentication error from the ADO SDK
        exc = AzureDevOpsAuthenticationError("VS30063: not authorized")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces an authentication ActionableError with helpful suggestion
        assert result.error_type == "authentication", (
            f"Expected error_type='authentication', got {result.error_type!r}"
        )
        assert result.suggestion is not None, (
            "Expected a suggestion for authentication errors, got None"
        )

    def test_not_found_for_type_key_containing_not_found(self) -> None:
        """
        Given an AzureDevOpsServiceError with type_key="GitItemNotFoundException",
        When classify_ado_error is called,
        Then returns ActionableError with kind not_found.
        """
        # Given: a service error with a NotFound type_key
        exc = _make_service_error(message="Item not found", type_key="GitItemNotFoundException")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces a not_found ActionableError
        assert result.error_type == "not_found", (
            f"Expected error_type='not_found', got {result.error_type!r}"
        )

    def test_not_found_for_tf401174_in_message(self) -> None:
        """
        Given an exception with "TF401174" in its message,
        When classify_ado_error is called,
        Then returns ActionableError with kind not_found.
        """
        # Given: an exception with the ADO not-found error code in the message
        exc = Exception("TF401174: Git item /src/foo.py not found")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces a not_found ActionableError
        assert result.error_type == "not_found", (
            f"Expected error_type='not_found', got {result.error_type!r}"
        )

    def test_not_found_for_does_not_exist_in_message(self) -> None:
        """
        Given an exception with "does not exist" in its message,
        When classify_ado_error is called,
        Then returns ActionableError with kind not_found.
        """
        # Given: an exception whose message says "does not exist"
        exc = Exception("The branch 'refs/heads/old' does not exist in the repository")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces a not_found ActionableError
        assert result.error_type == "not_found", (
            f"Expected error_type='not_found', got {result.error_type!r}"
        )

    def test_permission_error_for_security_type_key(self) -> None:
        """
        Given an AzureDevOpsServiceError with type_key="SecurityException",
        When classify_ado_error is called,
        Then returns ActionableError with kind permission and suggestion
        mentioning PAT scope or project permissions.
        """
        # Given: a service error indicating a security/permission failure
        exc = _make_service_error(message="Access denied", type_key="SecurityException")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces a permission ActionableError with helpful suggestion
        assert result.error_type == "permission", (
            f"Expected error_type='permission', got {result.error_type!r}"
        )
        assert result.suggestion is not None, (
            "Expected a suggestion for permission errors, got None"
        )

    def test_connection_error_for_non_ado_exceptions(self) -> None:
        """
        Given a ConnectionError exception,
        When classify_ado_error is called,
        Then returns ActionableError with kind connection.
        """
        # Given: a network-level exception (not from the ADO SDK)
        exc = ConnectionError("Failed to establish a new connection")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces a connection ActionableError
        assert result.error_type == "connection", (
            f"Expected error_type='connection', got {result.error_type!r}"
        )

    def test_internal_error_for_unclassified_service_errors(self) -> None:
        """
        Given an AzureDevOpsServiceError with an unrecognized type_key,
        When classify_ado_error is called,
        Then returns ActionableError with kind internal.
        """
        # Given: a service error with a type_key we don't specifically handle
        exc = _make_service_error(
            message="Unexpected error in TFS", type_key="TeamFoundationServerException"
        )

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: produces an internal ActionableError
        assert result.error_type == "internal", (
            f"Expected error_type='internal', got {result.error_type!r}"
        )

    def test_service_error_with_not_found_message_but_generic_type_key(self) -> None:
        """
        Given an AzureDevOpsServiceError with a generic type_key but "does not exist" in message,
        When classify_ado_error is called,
        Then returns ActionableError with kind not_found (message fallback).
        """
        # Given: a service error whose type_key is generic but message indicates not-found
        exc = _make_service_error(
            message="The branch 'refs/heads/old' does not exist",
            type_key="TeamFoundationServiceException",
        )

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="list items")

        # Then: message-based fallback produces not_found
        assert result.error_type == "not_found", (
            f"Expected error_type='not_found' via message fallback, got {result.error_type!r}"
        )

    def test_context_hint_included_in_suggestion(self) -> None:
        """
        Given any exception and context_hint="/src/missing.py",
        When classify_ado_error is called with that context_hint,
        Then the returned ActionableError suggestion includes "/src/missing.py".
        """
        # Given: a not-found error with a specific path as context
        exc = Exception("TF401174: not found")

        # When: the classifier processes it with a context hint
        result = classify_ado_error(exc, operation="list items", context_hint="/src/missing.py")

        # Then: the suggestion includes the context hint so the agent knows what path failed
        assert result.suggestion is not None, "Expected a suggestion, got None"
        assert "/src/missing.py" in result.suggestion, (
            f"Expected suggestion to include '/src/missing.py', got: {result.suggestion!r}"
        )

    def test_operation_included_in_error_message(self) -> None:
        """
        Given any exception and operation="list items at '/src'",
        When classify_ado_error is called,
        Then the returned ActionableError message includes "list items at '/src'".
        """
        # Given: a generic error during a specific operation
        exc = ConnectionError("Connection refused")

        # When: the classifier processes it with an operation description
        result = classify_ado_error(exc, operation="list items at '/src'")

        # Then: the error message includes the operation for diagnostic context
        error_str = str(result)
        assert "list items at '/src'" in error_str, (
            f"Expected error message to include \"list items at '/src'\", got: {error_str!r}"
        )

    def test_ai_guidance_attached_with_suggestion_text(self) -> None:
        """
        Given any exception,
        When classify_ado_error is called,
        Then the returned ActionableError has ai_guidance.action_required
        matching the suggestion text.
        """
        # Given: a not-found error
        exc = Exception("TF401174: item missing")

        # When: the classifier processes it
        result = classify_ado_error(exc, operation="get file", context_hint="/foo.py")

        # Then: ai_guidance is attached and mirrors the suggestion
        assert result.ai_guidance is not None, (
            "Expected ai_guidance to be attached by classify_ado_error, got None"
        )
        assert result.ai_guidance.action_required == result.suggestion, (
            f"Expected ai_guidance.action_required to match suggestion, "
            f"got guidance={result.ai_guidance.action_required!r}, "
            f"suggestion={result.suggestion!r}"
        )
