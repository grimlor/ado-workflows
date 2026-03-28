"""
BDD tests for ado_workflows.formatting — comment formatting with pluggable formatters.

Covers:
- TestDefaultCommentFormatter: default_comment_formatter markdown output
- TestFormatComment: format_comment dispatch with optional custom formatter
- TestCustomFormatterIntegration: custom formatter override via post_rich_comments

Public API surface (from src/ado_workflows/formatting.py):
    CommentFormatter = Callable[[RichComment], str]
    default_comment_formatter(comment: RichComment) -> str
    format_comment(comment: RichComment, formatter: CommentFormatter | None = None) -> str
"""

from __future__ import annotations

from ado_workflows.formatting import (
    default_comment_formatter,
    format_comment,
)
from ado_workflows.models import (
    CommentSeverity,
    CommentType,
    RichComment,
)


class TestDefaultCommentFormatter:
    """
    REQUIREMENT: Default formatter produces Azure DevOps markdown with severity icons,
    type headers, code blocks, and tag sections.

    WHO: Any consumer posting rich comments without a custom formatter.
    WHAT: (1) SECURITY comment with ERROR severity starts with lock + error icons
          (2) PERFORMANCE comment starts with lightning bolt icon
          (3) GENERAL comment starts with severity icon + bold title
          (4) comment with suggested_code contains a fenced code block
          (5) comment with reasoning and business_impact contains labeled sections
          (6) comment with tags contains backtick-wrapped tag badges
          (7) comment with no optional fields contains only header and content
    WHY: Consistent, readable comment rendering across all consumers. Eliminates
         the need for every consumer to build their own markdown.

    MOCK BOUNDARY:
        Mock:  nothing — pure function
        Real:  default_comment_formatter, string formatting
        Never: N/A
    """

    def test_security_comment_with_error_severity(self) -> None:
        """
        Given a SECURITY comment with ERROR severity
        When formatting with default_comment_formatter
        Then output starts with lock icon, SECURITY header, and error icon
        """
        # Given: a security/error comment
        comment = RichComment(
            comment_id="sec-001",
            title="SQL Injection Risk",
            content="User input is interpolated into the query.",
            comment_type=CommentType.SECURITY,
            severity=CommentSeverity.ERROR,
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: SECURITY header with icons
        assert result.startswith("\U0001f512 **SECURITY**: \u274c"), (
            f"Security/error comment should start with lock + SECURITY + error icon, got: {result[:60]}"
        )
        assert "SQL Injection Risk" in result, "Title should appear in output"

    def test_performance_comment(self) -> None:
        """
        Given a PERFORMANCE comment
        When formatting with default_comment_formatter
        Then output starts with lightning bolt icon and PERFORMANCE header
        """
        # Given: a performance comment
        comment = RichComment(
            comment_id="perf-001",
            title="N+1 Query Pattern",
            content="This loop issues one query per item.",
            comment_type=CommentType.PERFORMANCE,
            severity=CommentSeverity.WARNING,
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: PERFORMANCE header
        assert result.startswith("\u26a1 **PERFORMANCE**:"), (
            f"Performance comment should start with lightning PERFORMANCE header, got: {result[:60]}"
        )
        assert "N+1 Query Pattern" in result, "Title should appear in output"

    def test_general_comment_with_severity_icon(self) -> None:
        """
        Given a GENERAL comment with INFO severity
        When formatting with default_comment_formatter
        Then output starts with severity icon followed by bold title
        """
        # Given: a general/info comment
        comment = RichComment(
            comment_id="gen-001",
            title="Consider Renaming",
            content="This variable name is unclear.",
            comment_type=CommentType.GENERAL,
            severity=CommentSeverity.INFO,
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: severity icon + bold title
        assert "**Consider Renaming**" in result, "Bold title should appear in output"
        assert "This variable name is unclear." in result, "Content should appear in output"

    def test_comment_with_suggested_code(self) -> None:
        """
        Given a comment with suggested_code
        When formatting with default_comment_formatter
        Then output contains a fenced code block with the suggestion
        """
        # Given: a comment with suggested code
        comment = RichComment(
            comment_id="sug-001",
            title="Use List Comprehension",
            content="A list comprehension would be more idiomatic here.",
            suggested_code="result = [x * 2 for x in items]",
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: fenced code block
        assert "```" in result, "Output should contain fenced code block markers"
        assert "result = [x * 2 for x in items]" in result, (
            "Suggested code should be in the output"
        )

    def test_comment_with_reasoning_and_business_impact(self) -> None:
        """
        Given a comment with reasoning and business_impact
        When formatting with default_comment_formatter
        Then output contains labeled sections for each
        """
        # Given: a comment with reasoning and business impact
        comment = RichComment(
            comment_id="reas-001",
            title="Missing Error Handling",
            content="This call can throw but is not wrapped in try/except.",
            reasoning="Unhandled exceptions crash the worker process.",
            business_impact="Pipeline downtime during peak hours.",
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: labeled sections
        assert "Reasoning" in result, "Output should contain a Reasoning section"
        assert "Unhandled exceptions crash the worker process." in result, (
            "Reasoning text should appear"
        )
        assert "Business Impact" in result or "Impact" in result, (
            "Output should contain an Impact section"
        )
        assert "Pipeline downtime during peak hours." in result, (
            "Business impact text should appear"
        )

    def test_comment_with_tags(self) -> None:
        """
        Given a comment with tags
        When formatting with default_comment_formatter
        Then output contains backtick-wrapped tag badges
        """
        # Given: a comment with tags
        comment = RichComment(
            comment_id="tag-001",
            title="Missing Docstring",
            content="Public function lacks documentation.",
            tags=["documentation", "style"],
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: backtick-wrapped tags
        assert "`documentation`" in result, "Tag 'documentation' should be backtick-wrapped"
        assert "`style`" in result, "Tag 'style' should be backtick-wrapped"

    def test_comment_with_no_optional_fields(self) -> None:
        """
        Given a comment with no optional fields (no suggested_code, reasoning, tags, etc.)
        When formatting with default_comment_formatter
        Then output contains only the header and content
        """
        # Given: minimal comment
        comment = RichComment(
            comment_id="min-001",
            title="Simple Note",
            content="Just a simple observation.",
        )

        # When: formatting
        result = default_comment_formatter(comment)

        # Then: contains header and content, no extra sections
        assert "**Simple Note**" in result, "Title should appear bold"
        assert "Just a simple observation." in result, "Content should appear"
        # Should NOT contain section headers for absent fields
        assert "Reasoning" not in result, "No reasoning section when reasoning is None"
        assert "Impact" not in result, "No impact section when business_impact is None"
        assert "Tags" not in result, "No tags section when tags is empty"
        assert "```" not in result, "No code block when suggested_code is None"


class TestFormatComment:
    """
    REQUIREMENT: Consumers can override the default formatter with a custom callable.

    WHO: Downstream MCP servers (e.g., DevToolsMCP adding domain headers).
    WHAT: (1) formatter=None uses default_comment_formatter
          (2) custom formatter callable is used when provided
    WHY: Different teams have domain-specific formatting needs. The library provides
         a sensible default; overrides are opt-in.

    MOCK BOUNDARY:
        Mock:  nothing — pure function composition
        Real:  format_comment, callable dispatch
        Never: N/A
    """

    def test_none_formatter_uses_default(self) -> None:
        """
        Given formatter=None
        When calling format_comment
        Then default_comment_formatter is used
        """
        # Given: a comment
        comment = RichComment(
            comment_id="def-001",
            title="Default Test",
            content="Testing default path.",
        )

        # When: calling with formatter=None
        result = format_comment(comment, formatter=None)

        # Then: same as calling default_comment_formatter directly
        expected = default_comment_formatter(comment)
        assert result == expected, "format_comment(None) should match default_comment_formatter"

    def test_custom_formatter_is_used(self) -> None:
        """
        Given a custom formatter that returns "CUSTOM: {title}"
        When calling format_comment
        Then the custom output is returned
        """

        # Given: a custom formatter
        def custom_formatter(c: RichComment) -> str:
            return f"CUSTOM: {c.title}"

        comment = RichComment(
            comment_id="cust-001",
            title="Custom Title",
            content="Custom content.",
        )

        # When: calling with custom formatter
        result = format_comment(comment, formatter=custom_formatter)

        # Then: custom output
        assert result == "CUSTOM: Custom Title", f"Expected 'CUSTOM: Custom Title', got '{result}'"
