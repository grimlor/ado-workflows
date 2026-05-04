"""
Layer 3 — Work item context resolution.

Mirrors :mod:`ado_workflows.pr` for work items: composes Layer 1
(:func:`parse_ado_work_item_url`) and Layer 2 (:class:`RepositoryContext`)
to establish a fully resolved work item context from either a URL or a
numeric work item ID.

Typical usage::

    from ado_workflows.work_items import establish_work_item_context

    ctx = establish_work_item_context(
        "https://msazure.visualstudio.com/One/_workitems/edit/37453680"
    )
    ctx = establish_work_item_context("42", working_directory="/path/to/repo")
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from actionable_errors import ActionableError

from ado_workflows.context import RepositoryContext
from ado_workflows.parsing import parse_ado_work_item_url

_SERVICE = "Azure DevOps"

_REQUIRED_FIELDS = ("organization", "project", "work_item_id")

_URL_SHAPE_HINT = (
    "https://dev.azure.com/{org}/{project}/_workitems/edit/{id} "
    "or https://{org}.visualstudio.com/{project}/_workitems/edit/{id}"
)


@dataclass
class AzureDevOpsWorkItemContext:
    """
    Resolved work item context — everything needed to address a single work item.

    Constructed via :meth:`from_url` or :meth:`from_work_item_id`, or
    through :func:`establish_work_item_context`.
    """

    work_item_url: str
    organization: str
    project: str
    work_item_id: int
    source: str  # "url" or "repository_context"

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, work_item_url: str) -> AzureDevOpsWorkItemContext:
        """
        Create context by parsing a work item URL.

        Delegates to :func:`parse_ado_work_item_url` and validates that
        every required field was extracted. Raises
        :class:`ActionableError` naming any missing fields.
        """
        org, project, work_item_id_str = parse_ado_work_item_url(work_item_url)

        parsed = {
            "organization": org,
            "project": project,
            "work_item_id": work_item_id_str,
        }
        missing = [name for name in _REQUIRED_FIELDS if not parsed[name]]

        if missing:
            raise ActionableError.validation(
                service=_SERVICE,
                field_name="work_item_url",
                reason=(f"Could not extract {', '.join(missing)} from URL: {work_item_url}"),
                suggestion=f"Provide a full work item URL like {_URL_SHAPE_HINT}",
            )

        return cls(
            work_item_url=work_item_url,
            organization=org,
            project=project,
            work_item_id=int(work_item_id_str),
            source="url",
        )

    @classmethod
    def from_work_item_id(
        cls,
        work_item_id: int,
        working_directory: str | None = None,
    ) -> AzureDevOpsWorkItemContext:
        """
        Create context from a numeric work item ID using :class:`RepositoryContext`.

        Calls :meth:`RepositoryContext.get` to discover the org and
        project, then constructs the canonical ``dev.azure.com`` work
        item URL. Any :class:`ActionableError` raised by
        ``RepositoryContext.get`` (including the multi-repo ambiguity
        error) propagates unchanged.

        Caveat — work board ≠ code repo: when the work board lives in
        a different organization from any of the discovered code repos,
        ``working_directory`` disambiguation produces the wrong answer.
        Prefer the URL form (:meth:`from_url`) for cross-org work items.
        """
        repo_info = RepositoryContext.get(working_directory=working_directory)

        org = repo_info["organization"]
        project = repo_info["project"]

        work_item_url = f"https://dev.azure.com/{org}/{project}/_workitems/edit/{work_item_id}"

        return cls(
            work_item_url=work_item_url,
            organization=org,
            project=project,
            work_item_id=work_item_id,
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
        """
        Serialize to dict including computed properties.

        Returns all dataclass fields plus ``org_url``.
        """
        result = asdict(self)
        result["org_url"] = self.org_url
        return result


def establish_work_item_context(
    url_or_id: str,
    working_directory: str | None = None,
) -> AzureDevOpsWorkItemContext:
    """
    Route ambiguous input to the correct factory method.

    - URL-shaped strings (containing ``://``, ``dev.azure.com``, or
      ``visualstudio.com``) → :meth:`AzureDevOpsWorkItemContext.from_url`
    - Numeric strings → :meth:`AzureDevOpsWorkItemContext.from_work_item_id`
    - Everything else → :class:`ActionableError`
    """
    if not url_or_id or not url_or_id.strip():
        raise ActionableError.validation(
            service=_SERVICE,
            field_name="url_or_id",
            reason="Input is empty. Provide a work item URL or numeric work item ID.",
            suggestion=(
                f"Pass a work item URL like {_URL_SHAPE_HINT} or a numeric work item ID like '42'."
            ),
        )

    url_indicators = ("://", "dev.azure.com", "visualstudio.com")
    if any(indicator in url_or_id for indicator in url_indicators):
        return AzureDevOpsWorkItemContext.from_url(url_or_id)

    if url_or_id.strip().isdigit():
        return AzureDevOpsWorkItemContext.from_work_item_id(
            int(url_or_id.strip()),
            working_directory=working_directory,
        )

    raise ActionableError.validation(
        service=_SERVICE,
        field_name="url_or_id",
        reason=f"'{url_or_id}' is not a valid work item URL or numeric work item ID.",
        suggestion=(
            f"Pass a work item URL like {_URL_SHAPE_HINT} or a numeric work item ID like '42'."
        ),
    )
