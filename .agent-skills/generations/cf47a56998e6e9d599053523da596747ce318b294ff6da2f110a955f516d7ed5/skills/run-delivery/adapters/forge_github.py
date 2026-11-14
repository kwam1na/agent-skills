#!/usr/bin/env python3
"""run-delivery forge adapter for GitHub, through the `gh` CLI (which keeps its own sign-in).

  candidate --key KEY [--prefix P]                       -> {"branch": P+KEY, "head": its tip} (P defaults to feature/)
  find --source BRANCH                                   -> {"url": ...} for its open PR, or {}
  open --source B --destination D --title T --body-file F -> {"url": ...}  (opened as a draft)
  get  --url URL                                          -> {"url", "state": open|merged|closed, "draft", "head", "source",
                                                              "destination"}
  reviews --url URL --head SHA                            -> {"checks": [{"name", "state": passed|failed|pending, "at",
                                                                         "doneAt"?}],
                                                              "reviews": [{"author", "account", "at", "body"}],
                                                              "approvals": [{"account", "name", "at", "commit"}],
                                                              "reports": null}
  ready --url URL                                         -> {"url", "draft": false}
  merge --url URL --strategy merge|squash [--head SHA]    -> {"url", "state": "merged"}
  merge --source BRANCH --head SHA --strategy merge|squash [--destination D] -> {"url", "state": "merged"}

Environment: FLOW_FORGE_REPOSITORY (owner/name). Prints JSON on stdout; exits 1 with a message on stderr otherwise, or
75 (EX_TEMPFAIL) when gh's failure is transient: a timeout, a connection error, or an HTTP 5xx or 429, which a
PR-review wait retries. A review's `account` is its author's login. A check's `doneAt` is its completedAt, once it
has a real one (an unfinished check run reports 0001-01-01).
`candidate` is a merge queue's candidate step for the conventional branch: it prints the branch `--prefix` + `--key`
names and that branch's tip, and refuses when no branch has that name or when several answer to it, differing only in
case, as a case-insensitive checkout would mix them. It writes three ways: it opens a pull request as a draft (a lane's open-pr), marks one ready (the lead's acceptance,
under the delivery's readyPullRequests grant), and merges one (the binding's merge step), refusing a head that moved
when --head is given. `merge --source` merges the branch's one open pull request at exactly `--head`, so a binding's
merge step, which knows the branch and the checked head but not the pull request, can call it; it refuses a branch
with no open pull request or with more than one. With the optional `--destination`, which a binding serving several
bases passes as `{base}`, it also refuses unless that pull request targets that branch, so a pull request retargeted to another base is never merged. A token gh reads from the environment is never printed.
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


TRANSIENT = 75  # EX_TEMPFAIL
# What gh says on a failure worth trying again: an HTTP 5xx or 429, a timeout, or a connection that failed.
TRANSIENT_SAID = re.compile(r"\bHTTP (5\d\d|429)\b|timed? ?out|timeout|connection (refused|reset)|could not connect|"
                            r"dial tcp|no such host|network is unreachable|unexpected EOF|TLS handshake", re.IGNORECASE)


def fail(message, code=1):
    if code == 1:
        sys.exit(redact(message))
    print(redact(message), file=sys.stderr)
    sys.exit(code)


def gh_failed(said):
    """Exit for a gh failure: 75 when it is transient, else 1."""
    fail(said, TRANSIENT if TRANSIENT_SAID.search(said) else 1)


def gh_api(path):
    """A `gh api` call, which names its repository in the path rather than through --repo."""
    done = subprocess.run(["gh", "api", path], text=True, capture_output=True)
    if done.returncode != 0:
        gh_failed(f"gh api: {redact(done.stderr.strip())[-300:]}")
    return done.stdout


def gh(*args):
    done = subprocess.run(["gh", *args, "--repo", REPO], text=True, capture_output=True)
    if done.returncode != 0:
        gh_failed(f"gh {args[0]} {args[1]}: {redact(done.stderr.strip())[-300:]}")
    return done.stdout


# A check run's conclusion, or a status context's state, as the pipeline review reads it.
CHECK_STATES = {"SUCCESS": "passed", "NEUTRAL": "passed", "SKIPPED": "passed", "FAILURE": "failed", "ERROR": "failed",
                "CANCELLED": "failed", "TIMED_OUT": "failed", "ACTION_REQUIRED": "failed", "STARTUP_FAILURE": "failed"}


def finished(entry):
    """Whether a rollup entry has finished: a real completedAt, not the zero time an unfinished check run reports."""
    return bool(entry.get("completedAt")) and not entry["completedAt"].startswith("0001-")


def resolve_candidate(forge, branch, tips):
    """The candidate step's answer from the forge's branches and their tips: exactly the one named, at a full hash."""
    answering = sorted(name for name in tips if name.lower() == branch.lower())
    if len(answering) > 1:
        fail(f"{forge}: {len(answering)} branches on {REPO} answer to {branch} ({', '.join(answering)}), not one; "
             "not resolved")
    if branch not in tips:
        fail(f"{forge}: no branch {branch} on {REPO}; not resolved")
    if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", tips[branch]):
        fail(f"{forge}: {branch}'s tip {tips[branch]!r} is not a full commit hash; not resolved")
    return {"branch": branch, "head": tips[branch]}


def pull_request(url):
    if not re.fullmatch(rf"https://github\.com/{re.escape(REPO)}/pull/\d+/?", url):
        fail(f"github: {url!r} is not a pull request of {REPO}")
    return url


def main():
    if not REPO:
        fail("github: set FLOW_FORGE_REPOSITORY (owner/name)")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("candidate"); p.add_argument("--key", required=True); p.add_argument("--prefix", default="feature/")
    p = sub.add_parser("find"); p.add_argument("--source", required=True)
    p = sub.add_parser("open"); p.add_argument("--source", required=True); p.add_argument("--destination", required=True)
    p.add_argument("--title", required=True); p.add_argument("--body-file", required=True)
    p = sub.add_parser("get"); p.add_argument("--url", required=True)
    p = sub.add_parser("reviews"); p.add_argument("--url", required=True); p.add_argument("--head", required=True)
    p = sub.add_parser("ready"); p.add_argument("--url", required=True)
    p = sub.add_parser("merge"); target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--url"); target.add_argument("--source")
    p.add_argument("--destination", help="with --source: the branch the pull request must target")
    p.add_argument("--strategy", choices=("merge", "squash"), required=True); p.add_argument("--head")
    args = parser.parse_args()
    if args.command == "candidate":
        branch = args.prefix + args.key
        # Every head, in one answer: GitHub matches a ref prefix with its case, and a branch that differs from this one
        # only in its prefix's case answers to the name too.
        refs = json.loads(gh_api(f"repos/{REPO}/git/matching-refs/heads/"))
        tips = {ref["ref"][len("refs/heads/"):]: (ref.get("object") or {}).get("sha", "") for ref in refs
                if ref.get("ref", "").lower().startswith("refs/heads/" + args.prefix.lower())}
        print(json.dumps(resolve_candidate("github", branch, tips)))
    elif args.command == "find":
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
                   "at": entry.get("completedAt") or entry.get("startedAt") or "",
                   **({"doneAt": entry["completedAt"]} if finished(entry) else {})} for entry in rollup]
        reviews = [{"author": (entry.get("author") or {}).get("login", ""),
                    "account": (entry.get("author") or {}).get("login", ""), "at": entry.get("submittedAt", ""),
                    "body": entry.get("body", "")} for entry in pr.get("reviews") or []]
        # An approval is an account's latest deciding review, when it is APPROVED, tied to the commit it was given at:
        # a later CHANGES_REQUESTED withdraws it, as GitHub counts it, and a dismissed one is DISMISSED already.
        deciding = {}
        for entry in sorted(pr.get("reviews") or [], key=lambda entry: entry.get("submittedAt") or ""):
            if entry.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                deciding[(entry.get("author") or {}).get("login", "")] = entry
        approvals = [{"account": login, "name": (entry.get("author") or {}).get("name") or login,
                      "at": entry.get("submittedAt", ""), "commit": (entry.get("commit") or {}).get("oid", "")}
                     for login, entry in deciding.items() if entry.get("state") == "APPROVED"]
        # GitHub has no Code Insights reports: null, not empty, so a reviewer with `report` is refused, not waited on.
        print(json.dumps({"checks": checks, "reviews": reviews, "approvals": approvals, "reports": None}))
    elif args.command == "ready":
        gh("pr", "ready", pull_request(args.url))
        print(json.dumps({"url": args.url, "draft": False}))
    elif args.command == "merge":
        url = args.url
        if args.destination is not None and not args.source:
            fail("github: merge --destination applies only with --source; not merged")
        if args.source:
            if not args.head:
                fail("github: merge --source needs --head, the head the merge is pinned to")
            found = json.loads(gh("pr", "list", "--head", args.source, "--state", "open", "--json", "url,baseRefName"))
            if len(found) != 1:
                fail(f"github: {args.source} has {'no' if not found else len(found)} open pull requests, not one; "
                     "not merged")
            target = found[0].get("baseRefName")
            if args.destination is not None and target != args.destination:
                fail(f"github: {args.source}'s open pull request targets {target}, not {args.destination}; not merged")
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
