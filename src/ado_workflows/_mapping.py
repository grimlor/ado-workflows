"""
Internal mapping helpers for work item detail extraction.

Shared by :mod:`listing` (read operations) and :mod:`mutations`
(create/update/clone operations). Not part of the public API.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ado_workflows.models import WorkItemDetail

if TYPE_CHECKING:
    from azure.devops.v7_1.work_item_tracking.models import WorkItem

_HIERARCHY_REVERSE = "System.LinkTypes.Hierarchy-Reverse"

_WORK_ITEM_ID_RE = re.compile(r"/workItems/(\d+)$")


def extract_parent_id(wi: WorkItem) -> int | None:
    """Extract parent work item ID from relations, if present."""
    if not wi.relations:
        return None
    for rel in wi.relations:
        if rel.rel == _HIERARCHY_REVERSE:
            match = _WORK_ITEM_ID_RE.search(rel.url)
            if match:
                return int(match.group(1))
    return None


def map_work_item_detail(wi: WorkItem) -> WorkItemDetail:
    """Map an SDK ``WorkItem`` to a :class:`WorkItemDetail`."""
    fields: dict[str, Any] = wi.fields
    assigned_to = fields.get("System.AssignedTo")
    ap = fields.get("System.AreaPath")
    ip = fields.get("System.IterationPath")
    cw = fields.get("Microsoft.VSTS.Scheduling.CompletedWork")
    rw = fields.get("Microsoft.VSTS.Scheduling.RemainingWork")
    return WorkItemDetail(
        id=wi.id,
        title=str(fields.get("System.Title", "")),
        state=str(fields.get("System.State", "")),
        work_item_type=str(fields.get("System.WorkItemType", "")),
        assigned_to=str(assigned_to) if assigned_to is not None else None,
        area_path=str(ap) if ap is not None else None,
        iteration_path=str(ip) if ip is not None else None,
        completed_work=float(cw) if cw is not None else None,
        remaining_work=float(rw) if rw is not None else None,
        parent_id=extract_parent_id(wi),
        url=wi.url,
        fields=dict(fields),
    )
