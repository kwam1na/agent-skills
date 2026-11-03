#!/usr/bin/env python3
"""run-delivery forge adapter for GitHub, through the `gh` CLI (which keeps its own sign-in).

  find --source BRANCH                                   -> {"url": ...} for its open PR, or {}
  open --source B --destination D --title T --body-file F -> {"url": ...}  (opened as a draft)
  get  --url URL                                          -> {"url", "state": open|merged|closed, "head", "source", "destination"}

Environment: FLOW_FORGE_REPOSITORY (owner/name). Prints JSON on stdout; exits 1 with a message on stderr otherwise.
Its only write is opening a pull request as a draft. It has no `reviews` command. A token gh reads from the
environment is never printed.
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


def main():
    if not REPO:
        fail("github: set FLOW_FORGE_REPOSITORY (owner/name)")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("find"); p.add_argument("--source", required=True)
    p = sub.add_parser("open"); p.add_argument("--source", required=True); p.add_argument("--destination", required=True)
    p.add_argument("--title", required=True); p.add_argument("--body-file", required=True)
    p = sub.add_parser("get"); p.add_argument("--url", required=True)
    args = parser.parse_args()
    if args.command == "find":
        found = json.loads(gh("pr", "list", "--head", args.source, "--state", "open", "--json", "url"))
        print(json.dumps({"url": found[0]["url"]} if found else {}))
    elif args.command == "open":
        url = gh("pr", "create", "--draft", "--head", args.source, "--base", args.destination, "--title", args.title,
                 "--body-file", args.body_file).strip().splitlines()[-1]
        print(json.dumps({"url": url}))
    else:
        if not re.fullmatch(rf"https://github\.com/{re.escape(REPO)}/pull/\d+/?", args.url):
            fail(f"github: {args.url!r} is not a pull request of {REPO}")
        pr = json.loads(gh("pr", "view", args.url, "--json", "url,state,headRefOid,headRefName,baseRefName"))
        print(json.dumps({"url": pr["url"], "state": {"OPEN": "open", "MERGED": "merged"}.get(pr["state"], "closed"),
                          "head": pr["headRefOid"], "source": pr["headRefName"], "destination": pr["baseRefName"]}))


if __name__ == "__main__":
    main()
