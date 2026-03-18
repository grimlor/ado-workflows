"""Layer 1 — URL and date parsing primitives for Azure DevOps.

Pure functions, no state, no SDK dependency.
"""

from __future__ import annotations

import re
from datetime import datetime


def parse_ado_url(url: str) -> tuple[str, str, str, str]:
    """Parse an Azure DevOps URL into its constituent parts.

    Supports:
    - ``https://dev.azure.com/{org}/{project}/_git/{repo}``
    - ``https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{id}``
    - ``https://{org}.visualstudio.com/[DefaultCollection/]{project}/_git/{repo}``
    - ``git@ssh.dev.azure.com:v3/{org}/{project}/{repo}``

    Args:
        url: Azure DevOps URL (HTTPS, SSH, or PR URL).

    Returns:
        ``(organization, project, repository, pr_id)``
        — all empty strings when *url* is not a recognised format.
        ``pr_id`` is an empty string when the URL is not a PR URL.
    """
    org = project = repository = pr_id = ""

    if "dev.azure.com" in url:
        org, project, repository, pr_id = _parse_dev_azure_com(url)
    elif ".visualstudio.com" in url:
        org, project, repository, pr_id = _parse_visualstudio_com(url)

    return org, project, repository, pr_id


def parse_ado_date(date_str: str) -> datetime | None:
    """Parse an Azure DevOps API date string into a local-time *datetime*.

    Handles ISO-8601 variants returned by the API:

    * ``2025-03-15T14:30:00Z``
    * ``2025-03-15T14:30:00.1234567Z``
    * ``2025-03-15T14:30:00``

    Millisecond / sub-second fragments are truncated before parsing.
    The result is converted to local time with ``tzinfo`` stripped so that
    callers can compare naive datetimes without timezone arithmetic.

    Args:
        date_str: Date string from an Azure DevOps API response.

    Returns:
        A naive *datetime* in local time, or ``None`` if *date_str* is
        empty or cannot be parsed.
    """
    if not date_str:
        return None

    try:
        # Truncate sub-second fragments: "…T14:30:00.1234567Z" → "…T14:30:00Z"
        date_clean = date_str.split(".")[0] + "Z" if "." in date_str else date_str

        parsed = datetime.fromisoformat(date_clean.replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None)
    except (ValueError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_dev_azure_com(url: str) -> tuple[str, str, str, str]:
    """Extract components from a ``dev.azure.com`` URL."""
    org = project = repository = pr_id = ""

    org_match = re.search(r"dev\.azure\.com/([^/]+)", url)
    project_match = re.search(r"dev\.azure\.com/[^/]+/([^/]+)", url)

    org = org_match.group(1) if org_match else ""
    project = project_match.group(1).replace("%20", " ") if project_match else ""

    # PR URL: /_git/{repo}/pullrequest/{id}
    pr_match = re.search(r"/_git/([^/]+?)/pullrequest/(\d+)", url)
    if pr_match:
        repository = pr_match.group(1)
        pr_id = pr_match.group(2)
    else:
        # Plain repo URL: /_git/{repo}[.git]
        repo_match = re.search(r"/_git/([^/]+?)(?:\.git)?$", url)
        repository = repo_match.group(1) if repo_match else ""

    # SSH fallback: git@ssh.dev.azure.com:v3/{org}/{project}/{repo}
    if not repository and "ssh.dev.azure.com" in url:
        ssh_match = re.search(r"ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)", url)
        if ssh_match:
            org = ssh_match.group(1)
            project = ssh_match.group(2).replace("%20", " ")
            repository = ssh_match.group(3).replace(".git", "")

    return org, project, repository, pr_id


def _parse_visualstudio_com(url: str) -> tuple[str, str, str, str]:
    """Extract components from a ``visualstudio.com`` URL."""
    org = project = repository = pr_id = ""

    org_match = re.search(r"([^/]+?)\.visualstudio\.com", url)
    org = org_match.group(1) if org_match else ""

    # Legacy: …/DefaultCollection/{project}/…
    dc_match = re.search(r"DefaultCollection/([^/]+)", url)
    if dc_match:
        project = dc_match.group(1).replace("%20", " ")
    else:
        # Modern: …/{project}/_git/…
        modern_match = re.search(r"\.visualstudio\.com/([^/]+?)/_git", url)
        project = modern_match.group(1).replace("%20", " ") if modern_match else ""

    # PR URL: /_git/{repo}/pullrequest/{id}
    pr_match = re.search(r"/_git/([^/]+?)/pullrequest/(\d+)", url)
    if pr_match:
        repository = pr_match.group(1)
        pr_id = pr_match.group(2)
    else:
        repo_match = re.search(r"/_git/([^/]+?)(?:\.git)?$", url)
        repository = repo_match.group(1) if repo_match else ""

    return org, project, repository, pr_id
