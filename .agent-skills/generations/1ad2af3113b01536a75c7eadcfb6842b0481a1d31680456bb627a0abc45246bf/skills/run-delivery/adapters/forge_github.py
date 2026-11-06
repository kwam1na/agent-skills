#!/usr/bin/env python3
"""run-delivery forge adapter for GitHub, through the `gh` CLI (which keeps its own sign-in).

  find --source BRANCH                                   -> {"url": ...} for its open PR, or {}
  open --source B --destination D --title T --body-file F -> {"url": ...}  (opened as a draft)
  get  --url URL                                          -> {"url", "state": open|merged|closed, "draft", "head", "source",
                                                              "destination"}
  reviews --url URL --head SHA                            -> {"checks": [{"name", "state": passed|failed|pending, "at"}],
                                                              "reviews": [{"author", "at", "body"}]}
  ready --url URL                                         -> {"url", "draft": false}
  merge --url URL --strategy merge|squash [--head SHA]    -> {"url", "state": "merged"}
  merge --source BRANCH --head SHA --strategy merge|squash -> {"url", "state": "merged"}

Environment: FLOW_FORGE_REPOSITORY (owner/name). Prints JSON on stdout; exits 1 with a message on stderr otherwise.
It writes three ways: it opens a pull request as a draft (a lane's open-pr), marks one ready (the lead's acceptance,
under the delivery's readyPullRequests grant), and merges one (the binding's merge step), refusing a head that moved
when --head is given. `merge --source` merges the branch's one open pull request at exactly `--head`, so a binding's
merge step, which knows the branch and the checked head but not the pull request, can call it; it refuses a branch
with no open pull request or with more than one. A token gh reads from the environment is never printed.
"""

import argparse
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
REPO = os.environ.get("FLOW_FORGE_REPOSITORY", "")


def redact(text):
    """The text without any token gh reads from the environment, whatever gh echoed. Redact before cutting."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        if os.environ.get(name):
            text = text.replace(os.environ[name], "[redacted]")
    return text


def fail(message):
    sys.exit(redact(message))


def gh(*args):
    done = subprocess.run(["gh", *args, "--repo", REPO], text=True, capture_output=True)
    if done.returncode != 0:
        fail(f"gh {args[0]} {args[1]}: {redact(done.stderr.strip())[-300:]}")
    return done.stdout


# A check run's conclusion, or a status context's state, as the pipeline review reads it.
CHECK_STATES = {"SUCCESS": "passed", "NEUTRAL": "passed", "SKIPPED": "passed", "FAILURE": "failed", "ERROR": "failed",
                "CANCELLED": "failed", "TIMED_OUT": "failed", "ACTION_REQUIRED": "failed", "STARTUP_FAILURE": "failed"}


def pull_request(url):
    if not re.fullmatch(rf"https://github\.com/{re.escape(REPO)}/pull/\d+/?", url):
        fail(f"github: {url!r} is not a pull request of {REPO}")
    return url


def main():
    if not REPO:
        fail("github: set FLOW_FORGE_REPOSITORY (owner/name)")
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
    p.add_argument("--strategy", choices=("merge", "squash"), required=True); p.add_argument("--head")
    args = parser.parse_args()
    if args.command == "find":
        found = json.loads(gh("pr", "list", "--head", args.source, "--state", "open", "--json", "url"))
        print(json.dumps({"url": found[0]["url"]} if found else {}))
    elif args.command == "open":
        url = gh("pr", "create", "--draft", "--head", args.source, "--base", args.destination, "--title", args.title,
                 "--body-file", args.body_file).strip().splitlines()[-1]
        print(json.dumps({"url": url}))
    elif args.command == "reviews":
        pr = json.loads(gh("pr", "view", pull_request(args.url), "--json", "headRefOid,reviews,statusCheckRollup"))
        # The rollup is the pull request's current head's: at any other head nothing has run yet.
        rollup = (pr.get("statusCheckRollup") or []) if pr["headRefOid"].startswith(args.head) else []
        checks = [{"name": entry.get("name") or entry.get("context") or "",
                   "state": CHECK_STATES.get(entry.get("conclusion") or entry.get("state") or "", "pending"),
                   "at": entry.get("completedAt") or entry.get("startedAt") or ""} for entry in rollup]
        reviews = [{"author": (entry.get("author") or {}).get("login", ""), "at": entry.get("submittedAt", ""),
                    "body": entry.get("body", "")} for entry in pr.get("reviews") or []]
        print(json.dumps({"checks": checks, "reviews": reviews}))
    elif args.command == "ready":
        gh("pr", "ready", pull_request(args.url))
        print(json.dumps({"url": args.url, "draft": False}))
    elif args.command == "merge":
        url = args.url
        if args.source:
            if not args.head:
                fail("github: merge --source needs --head, the head the merge is pinned to")
            found = json.loads(gh("pr", "list", "--head", args.source, "--state", "open", "--json", "url"))
            if len(found) != 1:
                fail(f"github: {args.source} has {'no' if not found else len(found)} open pull requests, not one; "
                     "not merged")
            url = found[0]["url"]
        guard = ["--match-head-commit", args.head] if args.head else []
        gh("pr", "merge", pull_request(url), f"--{args.strategy}", *guard)
        print(json.dumps({"url": url, "state": "merged"}))
    else:
        pr = json.loads(gh("pr", "view", pull_request(args.url), "--json",
                           "url,state,isDraft,headRefOid,headRefName,baseRefName"))
        print(json.dumps({"url": pr["url"], "state": {"OPEN": "open", "MERGED": "merged"}.get(pr["state"], "closed"),
                          "draft": bool(pr.get("isDraft")), "head": pr["headRefOid"], "source": pr["headRefName"],
                          "destination": pr["baseRefName"]}))


if __name__ == "__main__":
    main()
