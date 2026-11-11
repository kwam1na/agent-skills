#!/usr/bin/env python3
"""run-delivery tracker adapter for Jira Cloud's REST API, under `tracker-adapter/1` (references/config.md).

  --site <https origin> --token-env <name> [--user-env <name>], then one of:
  status      --ticket KEY | --project P  -> {"status", "transitions": [{name, to, offered}], "commentLimit"}
  markers     --ticket KEY                -> {"markers": [...]}: the markers stored on the ticket
  markers     --project P --marker LABEL  -> {"items": [{"key"}]}: the project's items carrying that label
  comment     --ticket KEY --marker M --body-file F    -> {}
  transition  --ticket KEY --name NAME --marker M      -> {}
  create      --project P --title T --body-file F --marker LABEL [--parent KEY] [--type Task] -> {"key"}

The same command-line shape and credential variables as the delivery-views Jira snapshot: with --user-env, basic
auth with that user and the token; without it, a bearer token. A ticket's markers are issue entity properties, and a
comment's marker is also set on the comment itself, in the same request, so a crash between the post and the property
leaves the marker readable. A created item's marker is a label, searched by JQL within the project: Jira without an
app can't search issue properties. Prints one JSON object; on a failure exits 3 and prints {"error", "httpStatus"},
httpStatus null when Jira wasn't reached. Neither a token nor its encoded form is ever printed.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
COMMENT_LIMIT = 32767
TIMEOUT_SECONDS = 25
MAX_PAGES = 50


class Failed(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def authorization(args):
    token = os.environ.get(args.token_env or "")
    if not token:
        raise Failed(f"the environment variable {args.token_env} is not set")
    if not args.user_env:
        return f"Bearer {token}"
    user = os.environ.get(args.user_env)
    if not user:
        raise Failed(f"the environment variable {args.user_env} is not set")
    return "Basic " + base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")


def check_site(site):
    parts = urllib.parse.urlsplit(site)
    loopback = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise Failed("--site must be an https origin, such as https://example.atlassian.net")
    if not parts.hostname or parts.path not in ("", "/"):
        raise Failed("--site must be an origin, with no path")
    return site.rstrip("/")


class Jira:
    def __init__(self, site, auth):
        self.site, self.auth = site, auth

    def request(self, method, path, document=None, query=None):
        url = f"{self.site}/rest/api/3/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        headers = {"Accept": "application/json", "Authorization": self.auth}
        body = None
        if document is not None:
            body = json.dumps(document).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                answer = json.loads(error.read().decode("utf-8"))
                detail = "; ".join([*answer.get("errorMessages", []), *map(str, (answer.get("errors") or {}).values())])
            except (ValueError, AttributeError, UnicodeDecodeError):
                pass
            raise Failed(f"{method} {path.split('?')[0]}: HTTP {error.code}" + (f": {detail[:300]}" if detail else ""),
                         error.code) from None
        except (urllib.error.URLError, OSError) as error:
            raise Failed(f"could not reach {urllib.parse.urlsplit(self.site).hostname}: "
                         f"{type(getattr(error, 'reason', error)).__name__}") from None
        try:
            return json.loads(text) if text.strip() else {}
        except ValueError:
            raise Failed(f"{method} {path}: the answer was not JSON") from None


def quote(value):
    return urllib.parse.quote(value, safe="")


def adf(text):
    """Plain text as an Atlassian document: one paragraph per line."""
    content = [{"type": "paragraph", "content": [{"type": "text", "text": line}] if line else []}
               for line in text.split("\n")]
    return {"type": "doc", "version": 1, "content": content}


def read_body(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def status(jira, args):
    if args.project:
        jira.request("GET", f"project/{quote(args.project)}")
        return {"commentLimit": COMMENT_LIMIT}
    issue = jira.request("GET", f"issue/{quote(args.ticket)}", query={"fields": "status"})
    current = ((issue.get("fields") or {}).get("status") or {}).get("name")
    listed = jira.request("GET", f"issue/{quote(args.ticket)}/transitions",
                          query={"includeUnavailableTransitions": "true"}).get("transitions") or []
    transitions = [{"name": t.get("name"), "to": (t.get("to") or {}).get("name"), "offered": t.get("isAvailable", True)}
                   for t in listed if isinstance(t, dict)]
    return {"status": current, "transitions": transitions, "commentLimit": COMMENT_LIMIT}


def markers(jira, args):
    if args.project:
        jql = f'project = "{args.project}" AND labels = "{args.marker}"'
        page = jira.request("GET", "search/jql", query={"jql": jql, "fields": "labels", "maxResults": "10"})
        return {"items": [{"key": issue["key"]} for issue in page.get("issues") or [] if isinstance(issue, dict)]}
    found = {entry["key"] for entry in jira.request("GET", f"issue/{quote(args.ticket)}/properties").get("keys") or []
             if isinstance(entry, dict) and entry.get("key")}
    start = 0
    for _ in range(MAX_PAGES):
        page = jira.request("GET", f"issue/{quote(args.ticket)}/comment",
                            query={"expand": "properties", "startAt": str(start), "maxResults": "100"})
        comments = page.get("comments") or []
        for comment in comments:
            found |= {prop.get("key") for prop in comment.get("properties") or [] if isinstance(prop, dict)}
        start += len(comments)
        if not comments or start >= page.get("total", 0):
            break
    return {"markers": sorted(key for key in found if key)}


def comment(jira, args):
    text = read_body(args.body_file)[:COMMENT_LIMIT]
    jira.request("POST", f"issue/{quote(args.ticket)}/comment",
                 {"body": adf(text), "properties": [{"key": args.marker, "value": {"marker": args.marker}}]})
    jira.request("PUT", f"issue/{quote(args.ticket)}/properties/{quote(args.marker)}", {"marker": args.marker})
    return {}


def transition(jira, args):
    listed = jira.request("GET", f"issue/{quote(args.ticket)}/transitions").get("transitions") or []
    match = next((t for t in listed if isinstance(t, dict) and t.get("name") == args.name), None)
    if match is None:
        raise Failed(f"the transition {args.name!r} isn't offered on {args.ticket}", 400)
    # The marker is set with the move, in one request.
    jira.request("POST", f"issue/{quote(args.ticket)}/transitions",
                 {"transition": {"id": match["id"]}, "properties": [{"key": args.marker, "value": {"marker": args.marker}}]})
    return {}


def create(jira, args):
    fields = {"project": {"key": args.project}, "summary": args.title[:255], "issuetype": {"name": args.type},
              "labels": [args.marker], "description": adf(read_body(args.body_file)[:COMMENT_LIMIT])}
    if args.parent:
        fields["parent"] = {"key": args.parent}
    answer = jira.request("POST", "issue", {"fields": fields})
    if not answer.get("key"):
        raise Failed("Jira created an item but named no key")
    return {"key": answer["key"]}


def parser():
    p = argparse.ArgumentParser(description="run-delivery's Jira tracker adapter")
    p.add_argument("--site", required=True)
    p.add_argument("--token-env", required=True)
    p.add_argument("--user-env")
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
    if args.command in ("status", "markers") and not (args.ticket or args.project):
        args.ticket = None
    try:
        if args.command in ("status", "markers") and bool(args.ticket) == bool(args.project):
            raise Failed(f"{args.command} takes exactly one of --ticket and --project")
        if args.command == "markers" and args.project and not args.marker:
            raise Failed("markers --project takes --marker")
        jira = Jira(check_site(args.site), authorization(args))
        answer = {"status": status, "markers": markers, "comment": comment, "transition": transition,
                  "create": create}[args.command](jira, args)
    except Failed as failure:
        print(json.dumps({"error": str(failure), "httpStatus": failure.status}))
        return 3
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
