#!/usr/bin/env python3
"""run-delivery tracker adapter for Linear's GraphQL API, under `tracker-adapter/1` (references/config.md).

  [--site <https origin>] --token-env <name>, then one of:
  status      --ticket KEY | --project TEAM -> {"status", "transitions": [{name, to, offered}], "commentLimit"}
  markers     --ticket KEY                  -> {"markers": [...]}: the markers carried in the ticket's comments
  markers     --project TEAM --marker LABEL -> {"items": [{"key"}]}: the team's items carrying that label
  comment     --ticket KEY --marker M --body-file F    -> {}
  transition  --ticket KEY --name STATE --marker M     -> {}
  create      --project TEAM --title T --body-file F --marker LABEL [--parent KEY] [--type T] -> {"key"}

The same credential variable as the delivery-views Linear snapshot, sent as it is in `Authorization`, as Linear takes
a personal key. --site defaults to https://api.linear.app. Linear has no per-issue properties, so a marker is carried
in the comment body, as a trailing `<!-- run-delivery:<marker> -->`. A status is a workflow state on the ticket's team:
each is listed as a transition named for it, offered only when its workflow type is the current one's or later, and
none once the ticket is completed or canceled. A move is the state update, then its marker comment, in that order, so a
failure between them leaves the ticket in its target status with no marker. A created item's marker is a label;
--project is the team key, and --type is accepted and ignored, as Linear has no issue types. Linear reports failures as
GraphQL `errors`, at HTTP 200 or otherwise; each is mapped to an httpStatus whatever the HTTP status: a rate limit to
429, an authentication error to 401, a forbidden one to 403, and any other to 400. Prints one JSON object; on a failure
exits 3 and prints {"error", "httpStatus"}, httpStatus null when Linear wasn't reached. The token is never printed.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
SITE = "https://api.linear.app"
# Linear refuses a comment body over 100,000 characters ("body must be shorter than or equal to 100000 characters"),
# counted in UTF-16 code units, as JavaScript counts and tracker_publisher.py counts. The marker's carrier is added
# after the publisher's cut, so the limit reported leaves it room.
BODY_LIMIT = 100000
MARKER_ROOM = 1000
COMMENT_LIMIT = BODY_LIMIT - MARKER_ROOM
TIMEOUT_SECONDS = 25
MAX_PAGES = 50
PAGE_SIZE = 100
# Workflow types in the order a ticket moves through them; a ticket of the last two offers no move.
ORDER = ("triage", "backlog", "unstarted", "started", "completed", "canceled")
ENDED = ("completed", "canceled")
CARRIER = re.compile(r"<!-- run-delivery:(.+?) -->\s*\Z")
# Linear's personal keys: any of them in a refusal is taken out, not only the one in use.
LINEAR_KEY = re.compile(r"lin_api_[A-Za-z0-9]+", re.IGNORECASE)
CODES = {"RATELIMITED": 429, "AUTHENTICATION_ERROR": 401, "FORBIDDEN": 403}

STATUS = """query Status($id: String!) {
  issue(id: $id) { identifier state { id name type } team { states(first: 100) { nodes { id name type position } } } }
}"""
TEAM = """query Team($key: String!) { teams(filter: {key: {eq: $key}}) { nodes { id key } } }"""
COMMENTS = """query Comments($id: String!, $after: String) {
  issue(id: $id) { comments(first: %d, after: $after) { nodes { body } pageInfo { hasNextPage endCursor } } }
}""" % PAGE_SIZE
LABELLED = """query Labelled($team: String!, $label: String!) {
  issues(first: 10, filter: {team: {key: {eq: $team}}, labels: {name: {eq: $label}}}) { nodes { identifier } }
}"""
LABEL = """query Label($name: String!) { issueLabels(filter: {name: {eq: $name}}) { nodes { id team { id } } } }"""
COMMENT_CREATE = """mutation CommentCreate($input: CommentCreateInput!) { commentCreate(input: $input) { success } }"""
ISSUE_UPDATE = """mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}"""
LABEL_CREATE = """mutation IssueLabelCreate($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) { success issueLabel { id } }
}"""
ISSUE_CREATE = """mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { identifier } }
}"""


class Failed(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def credential(args):
    token = os.environ.get(args.token_env or "")
    if not token:
        raise Failed(f"the environment variable {args.token_env} is not set")
    return token


def check_site(site):
    parts = urllib.parse.urlsplit(site)
    loopback = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise Failed("--site must be an https origin, such as https://api.linear.app")
    if not parts.hostname or parts.path not in ("", "/"):
        raise Failed("--site must be an origin, with no path")
    return site.rstrip("/")


def units(text):
    return len(text.encode("utf-16-le")) // 2


def cut(text, limit):
    return text.encode("utf-16-le")[:2 * max(0, limit)].decode("utf-16-le", errors="ignore")


def refusal(errors):
    """(httpStatus, message) for a GraphQL `errors` list: the first rate limit, authentication or forbidden code
    found, in that order, else 400."""
    listed = [error for error in errors if isinstance(error, dict)] if isinstance(errors, list) else []
    codes = [str((error.get("extensions") or {}).get("code") or "").upper() for error in listed]
    status = next((CODES[code] for code in CODES if code in codes), 400)
    messages = [str(error.get("message") or "error") for error in listed] or ["error"]
    return status, "; ".join(messages)


class Linear:
    def __init__(self, site, token):
        self.endpoint, self.token = f"{site}/graphql", token

    def shown(self, text):
        return LINEAR_KEY.sub("[redacted]", text.replace(self.token, "[redacted]"))[:300]

    def request(self, operation, query, variables):
        """The `data` of one GraphQL operation. An answer carrying `errors` is never success."""
        body = json.dumps({"operationName": operation, "query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Accept": "application/json", "Content-Type": "application/json", "Authorization": self.token})
        status, text = 200, ""
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                text = error.read().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
        except (urllib.error.URLError, OSError) as error:
            raise Failed(f"could not reach {urllib.parse.urlsplit(self.endpoint).hostname}: "
                         f"{type(getattr(error, 'reason', error)).__name__}") from None
        try:
            answer = json.loads(text) if text.strip() else None
        except ValueError:
            answer = None
        if isinstance(answer, dict) and answer.get("errors"):
            mapped, message = refusal(answer["errors"])
            raise Failed(f"{operation}: Linear refused: {self.shown(message)}", mapped)
        if status != 200:
            raise Failed(f"{operation}: HTTP {status}", status)
        if not isinstance(answer, dict) or not isinstance(answer.get("data"), dict):
            raise Failed(f"{operation}: the answer was not a GraphQL result")
        return answer["data"]

    def issue(self, ticket):
        issue = self.request("Status", STATUS, {"id": ticket}).get("issue")
        if not isinstance(issue, dict):
            raise Failed(f"Linear has no issue {ticket}", 404)
        return issue

    def team(self, key):
        nodes = ((self.request("Team", TEAM, {"key": key}).get("teams") or {}).get("nodes") or [])
        if not nodes or not isinstance(nodes[0], dict):
            raise Failed(f"Linear has no team {key}", 404)
        return nodes[0]

    def mutate(self, operation, query, variables, field):
        answer = self.request(operation, query, variables).get(field) or {}
        if answer.get("success") is not True:
            raise Failed(f"{operation}: Linear did not report success", 400)
        return answer


def rank(kind):
    return ORDER.index(kind) if kind in ORDER else None


def states(issue):
    """The team's states, each as a transition named for it, in workflow order."""
    current = issue.get("state") or {}
    here = rank(current.get("type"))
    nodes = [node for node in ((issue.get("team") or {}).get("states") or {}).get("nodes") or [] if isinstance(node, dict)]
    nodes.sort(key=lambda node: (rank(node.get("type")) if rank(node.get("type")) is not None else len(ORDER),
                                 node.get("position") or 0))
    listed = []
    for node in nodes:
        there = rank(node.get("type"))
        offered = (here is not None and there is not None and current.get("type") not in ENDED and there >= here
                   and node.get("name") != current.get("name"))
        listed.append({"id": node.get("id"), "name": node.get("name"), "to": node.get("name"), "offered": offered})
    return listed


def read_body(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def carried(text, marker):
    """The body with the marker's carrier as its last line, cut to leave room for it."""
    carrier = f"\n\n<!-- run-delivery:{marker} -->"
    return cut(text, BODY_LIMIT - units(carrier)) + carrier


def status(linear, args):
    if args.project:
        linear.team(args.project)
        return {"commentLimit": COMMENT_LIMIT}
    issue = linear.issue(args.ticket)
    transitions = [{key: t[key] for key in ("name", "to", "offered")} for t in states(issue)]
    return {"status": (issue.get("state") or {}).get("name"), "transitions": transitions, "commentLimit": COMMENT_LIMIT}


def markers(linear, args):
    if args.project:
        page = linear.request("Labelled", LABELLED, {"team": args.project, "label": args.marker})
        return {"items": [{"key": node["identifier"]} for node in (page.get("issues") or {}).get("nodes") or []
                          if isinstance(node, dict) and node.get("identifier")]}
    found, after = set(), None
    for _ in range(MAX_PAGES):
        issue = linear.request("Comments", COMMENTS, {"id": args.ticket, "after": after}).get("issue")
        if not isinstance(issue, dict):
            raise Failed(f"Linear has no issue {args.ticket}", 404)
        comments = issue.get("comments") or {}
        for node in comments.get("nodes") or []:
            match = CARRIER.search((node or {}).get("body") or "")
            if match:
                found.add(match.group(1))
        info = comments.get("pageInfo") or {}
        if info.get("hasNextPage") is not True:
            break
        after = info.get("endCursor")
        if not after:
            raise Failed(f"Linear gave no next page for the comments of {args.ticket}")
    else:
        raise Failed(f"{args.ticket} has more than {MAX_PAGES} pages of comments")
    return {"markers": sorted(found)}


def comment(linear, args):
    body = carried(cut(read_body(args.body_file), COMMENT_LIMIT), args.marker)
    linear.mutate("CommentCreate", COMMENT_CREATE, {"input": {"issueId": args.ticket, "body": body}}, "commentCreate")
    return {}


def transition(linear, args):
    match = next((t for t in states(linear.issue(args.ticket)) if t["name"] == args.name), None)
    if match is None or not match["offered"]:
        raise Failed(f"the transition {args.name!r} isn't offered on {args.ticket}", 400)
    # The state first, then its marker: a failure between them leaves the move made and unmarked.
    linear.mutate("IssueUpdate", ISSUE_UPDATE, {"id": args.ticket, "input": {"stateId": match["id"]}}, "issueUpdate")
    linear.mutate("CommentCreate", COMMENT_CREATE,
                  {"input": {"issueId": args.ticket, "body": carried(f"Moved to {args.name}.", args.marker)}},
                  "commentCreate")
    return {}


def create(linear, args):
    team = linear.team(args.project)
    labels = (linear.request("Label", LABEL, {"name": args.marker}).get("issueLabels") or {}).get("nodes") or []
    label = next((node["id"] for node in labels if isinstance(node, dict) and node.get("id")
                  and ((node.get("team") or {}).get("id") in (None, team["id"]))), None)
    if label is None:
        made = linear.mutate("IssueLabelCreate", LABEL_CREATE,
                             {"input": {"name": args.marker, "teamId": team["id"]}}, "issueLabelCreate")
        label = (made.get("issueLabel") or {}).get("id")
    issue = {"teamId": team["id"], "title": args.title[:255], "labelIds": [label],
             "description": cut(read_body(args.body_file), COMMENT_LIMIT)}
    if args.parent:
        issue["parentId"] = args.parent
    made = linear.mutate("IssueCreate", ISSUE_CREATE, {"input": issue}, "issueCreate")
    key = (made.get("issue") or {}).get("identifier")
    if not key:
        raise Failed("Linear created an item but named no key")
    return {"key": key}


def parser():
    p = argparse.ArgumentParser(description="run-delivery's Linear tracker adapter")
    p.add_argument("--site", default=SITE)
    p.add_argument("--token-env", default="LINEAR_API_KEY")
    commands = p.add_subparsers(dest="command", required=True)
    s = commands.add_parser("status")
    s.add_argument("--ticket"); s.add_argument("--project")
    m = commands.add_parser("markers")
    m.add_argument("--ticket"); m.add_argument("--project"); m.add_argument("--marker")
    c = commands.add_parser("comment")
    c.add_argument("--ticket", required=True); c.add_argument("--marker", required=True)
    c.add_argument("--body-file", required=True)
    t = commands.add_parser("transition")
    t.add_argument("--ticket", required=True); t.add_argument("--name", required=True)
    t.add_argument("--marker", required=True)
    k = commands.add_parser("create")
    k.add_argument("--project", required=True); k.add_argument("--title", required=True)
    k.add_argument("--body-file", required=True); k.add_argument("--marker", required=True)
    k.add_argument("--parent"); k.add_argument("--type", default="Task")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ("status", "markers") and bool(args.ticket) == bool(args.project):
            raise Failed(f"{args.command} takes exactly one of --ticket and --project")
        if args.command == "markers" and args.project and not args.marker:
            raise Failed("markers --project takes --marker")
        linear = Linear(check_site(args.site), credential(args))
        answer = {"status": status, "markers": markers, "comment": comment, "transition": transition,
                  "create": create}[args.command](linear, args)
    except Failed as failure:
        print(json.dumps({"error": str(failure), "httpStatus": failure.status}))
        return 3
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
