# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
import os

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "plugins", "filter", "workitems.py")
_SPEC = importlib.util.spec_from_file_location("workitems_filters", _PATH)
wf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wf)

BASE = "https://dev.azure.com/contoso"


def test_field_patch_create_skips_nothing():
    ops = wf.field_patch({"System.Title": "x", "System.Tags": ["a", "b"]})
    assert {"op": "add", "path": "/fields/System.Tags", "value": "a; b"} in ops
    assert {"op": "add", "path": "/fields/System.Title", "value": "x"} in ops


def test_field_patch_idempotent_types():
    current = {
        "System.Title": "x ",
        "System.Tags": "B; a",
        "Microsoft.VSTS.Common.Priority": 2,
        "System.AssignedTo": {"uniqueName": "Ops@Example.com", "displayName": "Ops"},
    }
    desired = {
        "System.Title": "x",
        "System.Tags": ["a", "b"],
        "Microsoft.VSTS.Common.Priority": "2",
        "System.AssignedTo": "ops@example.com",
    }
    assert wf.field_patch(desired, current) == []


def test_field_patch_null_removes_only_when_set():
    assert wf.field_patch({"Custom.X": None}, {"Custom.X": "v"}) == [{"op": "remove", "path": "/fields/Custom.X"}]
    assert wf.field_patch({"Custom.X": None}, {}) == []


def test_relation_patch_parent_replace():
    current = [
        {"rel": "System.LinkTypes.Related", "url": BASE + "/_apis/wit/workItems/9"},
        {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": BASE + "/_apis/wit/workItems/5"},
    ]
    new_parent = {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": wf.work_item_url(BASE, 7)}
    ops = wf.relation_patch(current, add=[new_parent], exclusive=[new_parent["rel"]])
    assert ops[0] == {"op": "remove", "path": "/relations/1"}
    assert ops[1]["value"]["url"].endswith("/7")


def test_relation_patch_matches_project_scoped_urls():
    current = [{"rel": "System.LinkTypes.Related", "url": BASE + "/abc-guid/_apis/wit/workItems/9"}]
    spec = {"rel": "System.LinkTypes.Related", "url": wf.work_item_url(BASE, 9)}
    assert wf.relation_patch(current, add=[spec]) == []
    assert wf.relation_patch(current, remove=[spec]) == [{"op": "remove", "path": "/relations/0"}]


def test_relation_patch_removes_descending():
    current = [
        {"rel": "AttachedFile", "url": "u1", "attributes": {"name": "a.log"}},
        {"rel": "AttachedFile", "url": "u2", "attributes": {"name": "b.log"}},
        {"rel": "AttachedFile", "url": "u3", "attributes": {"name": "a.log"}},
    ]
    ops = wf.relation_patch(current, remove=[{"rel": "AttachedFile", "name": "a.log"}])
    assert [o["path"] for o in ops] == ["/relations/2", "/relations/0"]


def test_wiql_where_quotes():
    where = wf.wiql_where({"System.Title": "O'Brien", "System.Tags": {"op": "contains", "value": "INC1"}, "Custom.N": 3})
    assert where == "[Custom.N] = 3 AND [System.Tags] CONTAINS 'INC1' AND [System.Title] = 'O''Brien'"


def test_link_type_map():
    assert wf.link_type("child") == "System.LinkTypes.Hierarchy-Forward"
    assert wf.link_type("Custom.Link-Forward") == "Custom.Link-Forward"


def test_record_flatten():
    item = {
        "id": 3, "rev": 2, "url": "u",
        "_links": {"html": {"href": "h"}},
        "fields": {"System.Title": "t", "System.Tags": "b; a", "System.AssignedTo": {"uniqueName": "x@y"}},
        "relations": [{"rel": "AttachedFile", "url": "a", "attributes": {"name": "f", "resourceSize": 4}}],
    }
    rec = wf.record(item)
    assert rec["tags"] == ["a", "b"]
    assert rec["assigned_to"] == "x@y"
    assert rec["attachments"][0]["name"] == "f"
    assert rec["web_url"] == "h"
