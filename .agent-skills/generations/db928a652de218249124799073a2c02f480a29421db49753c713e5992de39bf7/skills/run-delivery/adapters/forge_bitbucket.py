#!/usr/bin/env python3
"""run-delivery forge adapter for Bitbucket Cloud.

  find --source BRANCH                                   -> {"url": ...} for its open PR, or {}
  open --source B --destination D --title T --body-file F -> {"url": ...}  (opened as a draft)
  get  --url URL                                          -> {"url", "state": open|merged|closed, "draft", "head", "source",
                                                              "destination"}
  reviews --url URL --head SHA                            -> {"checks": [{"name", "state": passed|failed|pending, "at"}],
                                                              "reviews": [{"author", "at", "body"}]}
  ready --url URL                                         -> {"url", "draft": false}
  merge --url URL --strategy merge|squash [--head SHA]    -> {"url", "state": "merged"}
  merge --source BRANCH --head SHA --strategy merge|squash -> {"url", "state": "merged"}

Environment: FLOW_FORGE_REPOSITORY (workspace/repo) and FLOW_FORGE_TOKEN_ENV (the name of the variable holding a
token Bitbucket accepts as a bearer token). Prints JSON on stdout; exits 1 with a message on stderr otherwise.
It writes three ways: it opens a pull request as a draft (a lane's open-pr), marks one ready (the lead's acceptance,
under the delivery's readyPullRequests grant), and merges one (the binding's merge step), refusing a head that moved
when --head is given. `merge --source` merges the branch's one open pull request at exactly `--head`, so a binding's
merge step, which knows the branch and the checked head but not the pull request, can call it; it refuses a branch
with no open pull request or with more than one. Neither the token nor the Authorization value is ever printed.
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
REPO = os.environ.get("FLOW_FORGE_REPOSITORY", "")
API = f"https://api.bitbucket.org/2.0/repositories/{REPO}"
STATES = {"OPEN": "open", "MERGED": "merged", "DECLINED": "closed", "SUPERSEDED": "closed"}


def token():
    return os.environ.get(os.environ.get("FLOW_FORGE_TOKEN_ENV", ""), "")


def redact(text):
    """The text without the token or the Authorization value, whatever the forge echoed. Redact before cutting."""
    secret = token()
    return text.replace(f"Bearer {secret}", "Bearer [redacted]").replace(secret, "[redacted]") if secret else text


def fail(message):
    sys.exit(redact(message))


def call(method, path, body=None):
    token_value = token()
    if not REPO or not token_value:
        fail("bitbucket: set FLOW_FORGE_REPOSITORY and the token variable FLOW_FORGE_TOKEN_ENV names")
    request = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {token_value}", "Content-Type": "application/json",
                                              "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        fail(f"bitbucket {method} {path} -> {error.code}: {redact(error.read().decode(errors='replace'))[:300]}")
    except (urllib.error.URLError, OSError) as error:
        fail(f"bitbucket {method} {path}: {redact(str(error))[:300]}")


def describe(pr):
    return {"url": pr["links"]["html"]["href"], "state": STATES.get(pr["state"], pr["state"].lower()),
            "draft": bool(pr.get("draft")), "head": pr["source"]["commit"]["hash"], "source": pr["source"]["branch"]["name"],
            "destination": pr["destination"]["branch"]["name"]}


STRATEGIES = {"merge": "merge_commit", "squash": "squash"}
CHECK_STATES = {"SUCCESSFUL": "passed", "FAILED": "failed", "STOPPED": "failed", "INPROGRESS": "pending"}


def pr_number(url):
    match = re.fullmatch(rf"https://bitbucket\.org/{re.escape(REPO)}/pull-requests/(\d+)/?", url)
    if not match:
        fail(f"bitbucket: {url!r} is not a pull request of {REPO}")
    return match.group(1)


def pages(path):
    while path:
        page = call("GET", path)
        yield from page.get("values", [])
        path = page.get("next", "").replace(API, "") or None


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("find"); p.add_argument("--source", required=True)
    p = sub.add_parser("open"); p.add_argument("--source", required=True); p.add_argument("--destination", required=True)
    p.add_argument("--title", required=True); p.add_argument("--body-file", required=True)
    p = sub.add_parser("get"); p.add_argument("--url", required=True)
    p = sub.add_parser("reviews"); p.add_argument("--url", required=True); p.add_argument("--head", required=True)
    p = sub.add_parser("ready"); p.add_argument("--url", required=True)
    p = sub.add_parser("merge"); target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--url"); target.add_argument("--source")
    p.add_argument("--strategy", choices=tuple(STRATEGIES), required=True); p.add_argument("--head")
    args = parser.parse_args()
    if args.command == "find":
        query = urllib.parse.quote(f'source.branch.name="{args.source}" AND state="OPEN"')
        values = call("GET", f"/pullrequests?q={query}").get("values", [])
        print(json.dumps({"url": values[0]["links"]["html"]["href"]} if values else {}))
    elif args.command == "open":
        with open(args.body_file) as handle:
            body = handle.read()
        pr = call("POST", "/pullrequests", {"title": args.title, "description": body, "draft": True,
                                            "source": {"branch": {"name": args.source}},
                                            "destination": {"branch": {"name": args.destination}}})
        print(json.dumps({"url": pr["links"]["html"]["href"]}))
    elif args.command == "reviews":
        number = pr_number(args.url)
        checks = [{"name": s["name"], "state": CHECK_STATES.get(s["state"], s["state"].lower()), "at": s["created_on"]}
                  for s in pages(f"/commit/{args.head}/statuses?pagelen=100")]
        reviews = [{"author": c["user"]["display_name"], "at": c["updated_on"], "body": c["content"]["raw"]}
                   for c in pages(f"/pullrequests/{number}/comments?pagelen=100")
                   if not c.get("deleted") and not c.get("inline") and not c.get("parent")]
        print(json.dumps({"checks": checks, "reviews": reviews}))
    elif args.command == "ready":
        call("PUT", f"/pullrequests/{pr_number(args.url)}", {"draft": False})
        print(json.dumps({"url": args.url, "draft": False}))
    elif args.command == "merge":
        if args.source:
            if not args.head:
                fail("bitbucket: merge --source needs --head, the head the merge is pinned to")
            query = urllib.parse.quote(f'source.branch.name="{args.source}" AND state="OPEN"')
            values = call("GET", f"/pullrequests?q={query}").get("values", [])
            if len(values) != 1:
                fail(f"bitbucket: {args.source} has {'no' if not values else len(values)} open pull requests, not one; "
                     "not merged")
            args.url = values[0]["links"]["html"]["href"]
        number = pr_number(args.url)
        if args.head:
            # Bitbucket's merge takes no expected head, so the head is checked just before it.
            head = call("GET", f"/pullrequests/{number}")["source"]["commit"]["hash"]
            if not (head.startswith(args.head) or args.head.startswith(head)):
                fail(f"bitbucket: {args.url} is at {head[:12]}, not {args.head[:12]}; not merged")
        call("POST", f"/pullrequests/{number}/merge", {"merge_strategy": STRATEGIES[args.strategy],
                                                       "close_source_branch": False})
        print(json.dumps({"url": args.url, "state": "merged"}))
    else:
        print(json.dumps(describe(call("GET", f"/pullrequests/{pr_number(args.url)}"))))


if __name__ == "__main__":
    main()
