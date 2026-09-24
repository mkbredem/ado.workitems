#!/usr/bin/env python3
"""Minimal in-memory Azure DevOps Boards REST mock for offline testing and demos.

Covers only the endpoints the ado.workitems roles call. Authentication is a
personal access token checked against MOCK_ADO_PAT (default "mock-pat").

    python3 tests/mock/mock_ado_server.py --port 8765
    ADO_PAT=mock-pat ansible-playbook tests/playbooks/smoke.yml \
        -e auth_organization_url=http://127.0.0.1:8765/mockorg
"""

import argparse
import base64
import copy
import json
import os
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

STATE = {"next_id": 1, "items": {}, "comments": {}, "attachments": {}, "next_comment": 1}
PAT = os.environ.get("MOCK_ADO_PAT", "mock-pat")
BEARER = "mock-entra-token"
TOKEN_REQUESTS = {"count": 0}


class Handler(BaseHTTPRequestHandler):
    server_version = "MockADO/1.0"

    def log_message(self, fmt, *args):  # quieter test output
        if os.environ.get("MOCK_ADO_VERBOSE"):
            super().log_message(fmt, *args)

    # ---------- helpers ----------
    def _base(self):
        return "http://%s/%s" % (self.headers["Host"], self.org)

    def _send(self, status, body=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message):
        self._send(status, {"message": message, "typeKey": "MockError"})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _authorized(self):
        expected = "Basic " + base64.b64encode((":" + PAT).encode()).decode()
        return self.headers.get("Authorization") in (expected, "Bearer " + BEARER)

    def _item_view(self, item, expand):
        view = copy.deepcopy(item)
        view["url"] = "%s/_apis/wit/workItems/%d" % (self._base(), item["id"])
        view["_links"] = {"html": {"href": "%s/_workitems/edit/%d" % (self._base(), item["id"])}}
        if expand.lower() not in ("relations", "all"):
            view.pop("relations", None)
        return view

    def _apply_patch(self, item, ops):
        for op in ops:
            path = op["path"]
            if op["op"] == "test":
                if path == "/rev" and int(op["value"]) != item["rev"]:
                    return "TF26071: This work item has been changed by someone else since you opened it."
                continue
            if path.startswith("/fields/"):
                field = path[len("/fields/"):]
                if op["op"] == "remove":
                    item["fields"].pop(field, None)
                elif field == "System.History":
                    self._add_comment(item["id"], op["value"])
                elif field == "System.AssignedTo":
                    item["fields"][field] = {"uniqueName": op["value"], "displayName": op["value"].split("@")[0]}
                else:
                    item["fields"][field] = op["value"]
            elif path.startswith("/relations/"):
                idx = path.split("/")[-1]
                if op["op"] == "remove":
                    item["relations"].pop(int(idx))
                else:
                    rel = copy.deepcopy(op["value"])
                    if rel["rel"] == "AttachedFile":
                        aid = rel["url"].rsplit("/", 1)[1].split("?")[0]
                        attrs = rel.setdefault("attributes", {})
                        attrs["name"] = STATE.get("attachment_names", {}).get(aid, aid)
                        attrs["resourceSize"] = len(STATE["attachments"].get(aid, b""))
                    item["relations"].append(rel)
        parents = [r for r in item["relations"] if r["rel"] == "System.LinkTypes.Hierarchy-Reverse"]
        if len(parents) > 1:
            return "TF201036: You cannot add a Parent link because the work item already has a Parent."
        item["fields"]["System.Parent"] = int(parents[0]["url"].rsplit("/", 1)[1]) if parents else None
        if item["fields"]["System.Parent"] is None:
            item["fields"].pop("System.Parent")
        return None

    def _add_comment(self, wid, text):
        cid = STATE["next_comment"]
        STATE["next_comment"] += 1
        comment = {"id": cid, "workItemId": wid, "text": text, "version": 1}
        STATE["comments"].setdefault(wid, []).append(comment)
        return comment

    def _route(self, method):
        parsed = urlparse(self.path)
        self.query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        self.org = parts[0]
        rest = parts[1:]
        if method == "POST" and rest[-3:] == ["oauth2", "v2.0", "token"]:
            # Entra ID client credentials grant (authority host pointed at the mock).
            form = {k: v[0] for k, v in parse_qs(self._body().decode()).items()}
            if form.get("grant_type") != "client_credentials" or form.get("client_secret") != "mock-secret":
                return self._send(401, {"error": "invalid_client"})
            if form.get("scope") != "499b84ac-1321-427f-aa17-267ca6975798/.default":
                return self._send(400, {"error": "invalid_scope"})
            TOKEN_REQUESTS["count"] += 1
            return self._send(200, {"token_type": "Bearer", "expires_in": 3599, "access_token": BEARER})
        if rest[:2] == ["_apis", "tokenRequests"]:
            return self._send(200, {"count": TOKEN_REQUESTS["count"]})
        if not self._authorized():
            # Azure DevOps answers a bad PAT with 203 and a sign-in page.
            return self._send(203, raw=b"<html>Sign in</html>", ctype="text/html")
        if rest[:2] == ["_apis", "connectionData"]:
            return self._send(200, {"authenticatedUser": {"id": "0000", "providerDisplayName": "Mock SP"}})
        if "_apis" not in rest:
            return self._error(404, "not found")
        api = rest[rest.index("_apis") + 1:]
        return self._dispatch(method, api)

    def _dispatch(self, method, api):
        expand = self.query.get("$expand", "none")
        validate_only = self.query.get("validateOnly", "false") == "true"
        # Attachments
        if api[:2] == ["wit", "attachments"]:
            if method == "POST":
                aid = str(uuid.uuid4())
                STATE["attachments"][aid] = self._body()
                STATE.setdefault("attachment_names", {})[aid] = self.query.get("fileName", aid)
                return self._send(201, {"id": aid, "url": "%s/_apis/wit/attachments/%s" % (self._base(), aid)})
            if method == "GET" and len(api) == 3 and api[2] in STATE["attachments"]:
                return self._send(200, raw=STATE["attachments"][api[2]], ctype="application/octet-stream")
            return self._error(404, "attachment not found")
        # WIQL
        if api[:2] == ["wit", "wiql"] and method == "POST":
            query = json.loads(self._body())["query"]
            ids = []
            clauses = re.findall(r"\[([\w.]+)\]\s*(=|CONTAINS)\s*'((?:[^']|'')*)'", query)
            for item in STATE["items"].values():
                ok = True
                for field, op, value in clauses:
                    value = value.replace("''", "'")
                    current = str(item["fields"].get(field, ""))
                    if field == "System.TeamProject":
                        continue
                    ok = ok and ((value in current) if op == "CONTAINS" else current == value)
                if ok:
                    ids.append(item["id"])
            top = int(self.query.get("$top", 20000))
            return self._send(200, {"queryType": "flat", "workItems": [{"id": i} for i in sorted(ids)[:top]]})
        if api[:2] == ["wit", "workitemsbatch"] and method == "POST":
            body = json.loads(self._body())
            out = []
            for i in body["ids"]:
                item = STATE["items"].get(int(i))
                out.append(self._item_view(item, body.get("$expand", "none")) if item else None)
            return self._send(200, {"count": len(out), "value": out})
        # Comments
        if len(api) >= 4 and api[0] == "wit" and api[1].lower() == "workitems" and api[3] == "comments":
            wid = int(api[2])
            comments = STATE["comments"].setdefault(wid, [])
            if method == "GET":
                return self._send(200, {"totalCount": len(comments), "count": len(comments), "comments": comments})
            if method == "POST":
                return self._send(200, self._add_comment(wid, json.loads(self._body())["text"]))
            cid = int(api[4])
            match = [c for c in comments if c["id"] == cid]
            if not match:
                return self._error(404, "comment not found")
            if method == "PATCH":
                match[0]["text"] = json.loads(self._body())["text"]
                match[0]["version"] += 1
                return self._send(200, match[0])
            if method == "DELETE":
                comments.remove(match[0])
                return self._send(204)
        # Work items
        if len(api) == 3 and api[0] == "wit" and api[1].lower() == "workitems":
            target = api[2]
            if target.startswith("$") and method == "POST":
                item = {"id": STATE["next_id"], "rev": 1, "fields": {
                    "System.WorkItemType": target[1:], "System.State": "New"}, "relations": []}
                err = self._apply_patch(item, json.loads(self._body()))
                if err:
                    return self._error(400, err)
                if not validate_only:
                    STATE["next_id"] += 1
                    STATE["items"][item["id"]] = item
                else:
                    item["id"] = 0
                return self._send(200, self._item_view(item, expand))
            wid = int(target)
            item = STATE["items"].get(wid)
            if item is None:
                return self._error(404, "TF401232: Work item %d does not exist." % wid)
            if method == "GET":
                return self._send(200, self._item_view(item, expand))
            if method == "PATCH":
                candidate = copy.deepcopy(item)
                err = self._apply_patch(candidate, json.loads(self._body()))
                if err:
                    return self._error(400, err)
                candidate["rev"] += 1
                if not validate_only:
                    STATE["items"][wid] = candidate
                return self._send(200, self._item_view(candidate, expand))
            if method == "DELETE":
                del STATE["items"][wid]
                return self._send(200, {"id": wid, "code": 200})
        return self._error(404, "unsupported mock route %s %s" % (method, "/".join(api)))

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
