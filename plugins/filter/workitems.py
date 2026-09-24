# -*- coding: utf-8 -*-
# Copyright: Contributors to the ado.workitems collection
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
"""Filters that build Azure DevOps JSON Patch documents and WIQL clauses.

Illustrative example: not tested or verified against a live Azure DevOps
organization.

The roles in this collection are the user-facing interface. These filters
exist only because computing an idempotent JSON Patch diff (identity
fields, tag sets, relation indexes) is unreadable in Jinja.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

from ansible.errors import AnsibleFilterError
from ansible.module_utils.six import string_types

TAGS_FIELD = "System.Tags"
PARENT_REL = "System.LinkTypes.Hierarchy-Reverse"

LINK_TYPES = {
    "parent": "System.LinkTypes.Hierarchy-Reverse",
    "child": "System.LinkTypes.Hierarchy-Forward",
    "related": "System.LinkTypes.Related",
    "predecessor": "System.LinkTypes.Dependency-Reverse",
    "successor": "System.LinkTypes.Dependency-Forward",
    "duplicate": "System.LinkTypes.Duplicate-Forward",
    "duplicate_of": "System.LinkTypes.Duplicate-Reverse",
    "tested_by": "Microsoft.VSTS.Common.TestedBy-Forward",
    "tests": "Microsoft.VSTS.Common.TestedBy-Reverse",
    "hyperlink": "Hyperlink",
}

_WI_URL_RE = re.compile(r"/_apis/wit/workItems/(\d+)$", re.IGNORECASE)


def _split_tags(value):
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).split(";")
    return set(t.strip().lower() for t in items if str(t).strip())


def _is_number(value):
    if isinstance(value, bool):
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _values_equal(field, desired, current):
    if field == TAGS_FIELD:
        return _split_tags(desired) == _split_tags(current)
    if isinstance(current, dict):
        # Identity reference (System.AssignedTo, System.CreatedBy, ...)
        candidates = [
            str(current.get(k, "")).strip().lower()
            for k in ("uniqueName", "displayName", "id")
        ]
        if isinstance(desired, dict):
            desired = desired.get("uniqueName") or desired.get("displayName") or ""
        return str(desired).strip().lower() in candidates
    if isinstance(desired, bool) or isinstance(current, bool):
        return str(desired).lower() == str(current).lower()
    if _is_number(desired) and _is_number(current):
        return float(desired) == float(current)
    if current is None:
        return desired is None or desired == ""
    return str(desired).strip() == str(current).strip()


def _render_value(field, value):
    if field == TAGS_FIELD and isinstance(value, (list, tuple, set)):
        return "; ".join(str(v) for v in value)
    return value


def field_patch(desired, current=None):
    """Return JSON Patch ops that converge ``current`` fields to ``desired``."""
    if desired is None:
        return []
    if not isinstance(desired, dict):
        raise AnsibleFilterError("field_patch expects a dict of field reference names")
    current = current or {}
    ops = []
    for field in sorted(desired):
        value = desired[field]
        path = "/fields/%s" % field
        if value is None:
            if field in current and current[field] not in (None, ""):
                ops.append({"op": "remove", "path": path})
            continue
        if field in current and _values_equal(field, value, current[field]):
            continue
        ops.append({"op": "add", "path": path, "value": _render_value(field, value)})
    return ops


def work_item_url(base_url, work_item_id):
    """Organization-level REST URL for a work item, as used in relations."""
    return "%s/_apis/wit/workItems/%s" % (str(base_url).rstrip("/"), int(work_item_id))


def link_type(name):
    """Map a friendly link name to its relation reference name."""
    if name in LINK_TYPES:
        return LINK_TYPES[name]
    return name


def _same_target(a, b):
    ma = _WI_URL_RE.search(str(a).rstrip("/"))
    mb = _WI_URL_RE.search(str(b).rstrip("/"))
    if ma and mb:
        return ma.group(1) == mb.group(1)
    return str(a).rstrip("/").lower() == str(b).rstrip("/").lower()


def _matches(rel, spec):
    if rel.get("rel") != spec.get("rel"):
        return False
    if spec.get("url"):
        return _same_target(rel.get("url", ""), spec["url"])
    if spec.get("name"):
        return rel.get("attributes", {}).get("name") == spec["name"]
    return False


def relation_patch(current, add=None, remove=None, exclusive=None):
    """Return JSON Patch ops that add and remove relations idempotently.

    current   -- the work item ``relations`` list (may be None)
    add       -- list of {rel, url, attributes} to ensure present
    remove    -- list of {rel, url} or {rel, name} to ensure absent
    exclusive -- relation types where only the entries in ``add`` may exist
                 (for example the single parent link)
    """
    current = current or []
    add = [a for a in (add or []) if a]
    remove = [r for r in (remove or []) if r]
    exclusive = exclusive or []

    remove_idx = set()
    for idx, rel in enumerate(current):
        if any(_matches(rel, spec) for spec in remove):
            remove_idx.add(idx)
        if rel.get("rel") in exclusive and not any(_matches(rel, spec) for spec in add):
            remove_idx.add(idx)

    ops = [{"op": "remove", "path": "/relations/%d" % i} for i in sorted(remove_idx, reverse=True)]

    for spec in add:
        present = any(
            _matches(rel, spec) for i, rel in enumerate(current) if i not in remove_idx
        )
        if present:
            continue
        value = {"rel": spec["rel"], "url": spec["url"]}
        if spec.get("attributes"):
            value["attributes"] = spec["attributes"]
        ops.append({"op": "add", "path": "/relations/-", "value": value})
    return ops


def wiql_literal(value):
    """Quote a value for use in a WIQL clause."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_number(value) and not isinstance(value, string_types):
        return str(value)
    return "'%s'" % str(value).replace("'", "''")


def wiql_where(match):
    """Build an AND-joined WIQL WHERE fragment from a field match dict.

    Values may be scalars (``=``) or dicts ``{op: CONTAINS, value: x}``.
    """
    clauses = []
    for field in sorted(match or {}):
        value = match[field]
        op = "="
        if isinstance(value, dict):
            op = str(value.get("op", "=")).upper()
            value = value.get("value")
        clauses.append("[%s] %s %s" % (field, op, wiql_literal(value)))
    return " AND ".join(clauses)


def record(item):
    """Flatten a work item into a servicenow.itsm-style record."""
    if not item:
        return {}
    fields = item.get("fields", {}) or {}
    assigned = fields.get("System.AssignedTo")
    if isinstance(assigned, dict):
        assigned = assigned.get("uniqueName") or assigned.get("displayName")
    relations = item.get("relations") or []
    return {
        "id": item.get("id"),
        "rev": item.get("rev"),
        "url": item.get("url"),
        "web_url": ((item.get("_links") or {}).get("html") or {}).get("href"),
        "type": fields.get("System.WorkItemType"),
        "title": fields.get("System.Title"),
        "state": fields.get("System.State"),
        "reason": fields.get("System.Reason"),
        "assigned_to": assigned,
        "area_path": fields.get("System.AreaPath"),
        "iteration_path": fields.get("System.IterationPath"),
        "tags": sorted(_split_tags(fields.get(TAGS_FIELD))),
        "parent_id": fields.get("System.Parent"),
        "attachments": [
            {
                "name": r.get("attributes", {}).get("name"),
                "url": r.get("url"),
                "size": r.get("attributes", {}).get("resourceSize"),
                "comment": r.get("attributes", {}).get("comment"),
            }
            for r in relations
            if r.get("rel") == "AttachedFile"
        ],
        "relations": relations,
        "fields": fields,
    }


class FilterModule(object):
    """ado.workitems filters."""

    def filters(self):
        return {
            "field_patch": field_patch,
            "relation_patch": relation_patch,
            "work_item_url": work_item_url,
            "link_type": link_type,
            "wiql_literal": wiql_literal,
            "wiql_where": wiql_where,
            "record": record,
        }
