"""Layer 3 — PR context resolution.

Composes Layer 1 (URL parsing) and Layer 2 (RepositoryContext) to establish
a fully resolved PR context from either a URL or a numeric PR ID.
No SDK calls — just context resolution.

Typical usage::

    from ado_workflows.pr import establish_pr_context

    ctx = establish_pr_context("https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42")
    ctx = establish_pr_context("42", working_directory="/path/to/repo")
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from actionable_errors import ActionableError

from ado_workflows.context import RepositoryContext
from ado_workflows.parsing import parse_ado_url

_SERVICE = "Azure DevOps"

_REQUIRED_FIELDS = ("organization", "project", "repository", "pr_id")


@dataclass
class AzureDevOpsPRContext:
    """Resolved PR context — everything needed to address a single PR.

    Constructed via :meth:`from_url` or :meth:`from_pr_id`, or through
    the :func:`establish_pr_context` convenience factory.
    """

    pr_url: str
    organization: str
    project: str
    repository: str
    pr_id: int
    source: str  # "url" or "repository_context"

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, pr_url: str) -> AzureDevOpsPRContext:
        """Create context by parsing a PR URL.

        Delegates to :func:`parse_ado_url` and validates that all required
        fields were extracted.  Raises :class:`ActionableError` naming any
        missing fields.
        """
        org, project, repository, pr_id_str = parse_ado_url(pr_url)

        parsed = {
            "organization": org,
            "project": project,
            "repository": repository,
            "pr_id": pr_id_str,
        }
        missing = [name for name in _REQUIRED_FIELDS if not parsed[name]]

        if missing:
            raise ActionableError.validation(
                service=_SERVICE,
                field_name="pr_url",
                reason=(
                    f"Could not extract {', '.join(missing)} from URL: {pr_url}"
                ),
                suggestion=(
                    "Provide a full PR URL like "
                    "https://dev.azure.com/{{org}}/{{project}}/_git/{{repo}}/pullrequest/{{id}}"
                ),
            )

        return cls(
            pr_url=pr_url,
            organization=org,
            project=project,
            repository=repository,
            pr_id=int(pr_id_str),
            source="url",
        )

    @classmethod
    def from_pr_id(
        cls,
        pr_id: int,
        working_directory: str | None = None,
    ) -> AzureDevOpsPRContext:
        """Create context from a numeric PR ID using RepositoryContext.

        Calls :meth:`RepositoryContext.get` to discover the org, project,
        and repository, then constructs the PR URL.  Raises
        :class:`ActionableError` if context resolution fails.
        """
        repo_info = RepositoryContext.get(working_directory=working_directory)

        if not repo_info.get("success", True) or "name" not in repo_info:
            error_msg = repo_info.get("error", "Unknown error")
            suggestion = repo_info.get(
                "suggestion",
                "Call set_repository_context() to configure the repository.",
            )
            raise ActionableError.validation(
                service=_SERVICE,
                field_name="repository_context",
                reason=f"{error_msg}. {suggestion}",
                suggestion=suggestion,
            )

        org = repo_info["organization"]
        project = repo_info["project"]
        repository = repo_info["name"]

        pr_url = (
            f"https://dev.azure.com/{org}/{project}"
            f"/_git/{repository}/pullrequest/{pr_id}"
        )

        return cls(
            pr_url=pr_url,
            organization=org,
            project=project,
            repository=repository,
            pr_id=pr_id,
            source="repository_context",
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def org_url(self) -> str:
        """Organization base URL for SDK operations."""
        return f"https://dev.azure.com/{self.organization}"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict including computed properties.

        Returns all dataclass fields plus ``org_url``.
        """
        result = asdict(self)
        result["org_url"] = self.org_url
        return result


def establish_pr_context(
    url_or_id: str,
    working_directory: str | None = None,
) -> AzureDevOpsPRContext:
    """Route ambiguous input to the correct factory method.

    - URL-shaped strings (containing ``://``, ``dev.azure.com``, or
      ``visualstudio.com``) → :meth:`AzureDevOpsPRContext.from_url`
    - Numeric strings → :meth:`AzureDevOpsPRContext.from_pr_id`
    - Everything else → :class:`ActionableError`
    """
    if not url_or_id or not url_or_id.strip():
        raise ActionableError.validation(
            service=_SERVICE,
            field_name="url_or_id",
            reason="Input is empty. Provide a PR URL or numeric PR ID.",
            suggestion=(
                "Pass a PR URL like "
                "https://dev.azure.com/{{org}}/{{project}}/_git/{{repo}}/pullrequest/{{id}} "
                "or a numeric PR ID like '42'."
            ),
        )

    url_indicators = ("://", "dev.azure.com", "visualstudio.com")
    if any(indicator in url_or_id for indicator in url_indicators):
        return AzureDevOpsPRContext.from_url(url_or_id)

    if url_or_id.strip().isdigit():
        return AzureDevOpsPRContext.from_pr_id(
            int(url_or_id.strip()),
            working_directory=working_directory,
        )

    raise ActionableError.validation(
        service=_SERVICE,
        field_name="url_or_id",
        reason=(
            f"'{url_or_id}' is not a valid PR URL or numeric PR ID."
        ),
        suggestion=(
            "Pass a PR URL like "
            "https://dev.azure.com/{{org}}/{{project}}/_git/{{repo}}/pullrequest/{{id}} "
            "or a numeric PR ID like '42'."
        ),
    )
