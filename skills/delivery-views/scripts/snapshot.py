#!/usr/bin/env python3
"""Print a delivery's tracker or forge snapshot as one ``work-snapshot/1``.

    python3 -B snapshot.py --kind jira --site <https origin> --parent <key> --user-env <name> --token-env <name>
        [--canceled-statuses "Won't Do,Duplicate"] [--blocks-link Blocks]
    python3 -B snapshot.py --kind bitbucket --workspace <w> --repo <r> --binding <path> --subject-pattern <regex>
        --token-env <name> [--user-env <name>] [--since <ISO time>] [--host bitbucket.org]

Every kind also takes ``--timeout-seconds`` (one deadline for the whole run)
and ``--now`` (the snapshot's time, for tests). On success the snapshot is the
only stdout and the exit code is 0; on failure stdout is empty, the exit code is
non-zero, and stderr holds one line naming the cause. Credentials come only
from the named environment variables and are sent only to the provider's own
origin; neither a token nor its encoded form is ever printed.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from views_common import ConfigError, repository_root


MAX_ITEMS = 500
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RETRIES = 2
DEFAULT_TIMEOUT_SECONDS = 25
SUMMARY_LIMIT = 1000
RETRYABLE = frozenset({429, 500, 502, 503, 504})
COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SHORT_HASH = re.compile(r"^[0-9a-f]{4,64}$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class Failure(Exception):
    """A refusal written never to hold a credential; its text is the stderr line."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


Transport = Callable[[str, str, dict, float], Response]


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80)


class Client:
    """JSON over HTTPS to one origin, within one deadline, retrying only what may pass on a retry."""

    def __init__(self, base: str, authorization: str, transport: Transport, deadline: float,
                 clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self.origin = _origin(base)
        self.authorization = authorization
        self.transport = transport
        self.deadline = deadline
        self.clock = clock
        self.sleep = sleep

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def get(self, url: str, *, missing_ok: bool = False) -> object | None:
        """The JSON at ``url``; None for a 404 when ``missing_ok``."""
        retries = 0
        redirects = 0
        while True:
            if _origin(url) != self.origin:
                raise Failure(f"refused a request to another origin: {_origin(url)[1]}")
            remaining = self.remaining()
            if remaining <= 0:
                raise Failure("the deadline passed before the snapshot was complete")
            response = self.transport("GET", url, {"Authorization": self.authorization,
                                                   "Accept": "application/json"}, remaining)
            status = response.status
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                target = urllib.parse.urljoin(url, location) if location else None
                if target is None or _origin(target) != self.origin:
                    raise Failure("refused a redirect to another origin")
                redirects += 1
                if redirects > 5:
                    raise Failure("too many redirects")
                url = target
                continue
            if status in (401, 403):
                raise Failure(f"the provider refused the credentials: HTTP {status}")
            if status == 404 and missing_ok:
                return None
            if status in RETRYABLE:
                if retries >= MAX_RETRIES:
                    raise Failure(f"the provider kept failing: HTTP {status} after {MAX_RETRIES} retries")
                wait = _retry_after(response.headers.get("retry-after"), retries)
                if wait >= self.remaining():
                    raise Failure(f"HTTP {status}, and the retry would pass the deadline")
                self.sleep(wait)
                retries += 1
                continue
            if status != 200:
                raise Failure(f"the provider answered HTTP {status}")
            if self.remaining() <= 0:
                raise Failure("the deadline passed before the snapshot was complete")
            try:
                return json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise Failure("the provider's answer was not JSON") from None


def _retry_after(value: str | None, retries: int) -> float:
    try:
        seconds = float(value) if value is not None else float(retries + 1)
    except ValueError:
        seconds = float(retries + 1)
    return max(0.0, seconds)


def urllib_transport(method: str, url: str, headers: dict, timeout: float) -> Response:
    """The network: one request, redirects left to the client, the body read within a bound."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, method=method, headers=headers)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise Failure(f"could not reach {urllib.parse.urlsplit(url).hostname}: {type(reason).__name__}") from None
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise Failure("the provider's answer was over 16 MiB")
        return Response(response.status, {key.lower(): value for key, value in response.headers.items()}, body)


def _credential(environ: dict, name: str | None) -> str:
    value = environ.get(name or "")
    if not value:
        raise Failure(f"the environment variable {name} is not set")
    return value


def _authorization(environ: dict, token_env: str, user_env: str | None) -> str:
    token = _credential(environ, token_env)
    if user_env is None:
        return f"Bearer {token}"
    pair = f"{_credential(environ, user_env)}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(pair).decode("ascii")


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise Failure(f"{where} is missing")
    return value


def _check_cap(count: int) -> None:
    if count >= MAX_ITEMS:
        raise Failure(f"the listing reached the cap of {MAX_ITEMS} items")


def _shorten(text: str) -> str:
    text = "\n".join(line.strip() for line in text.splitlines()).strip()
    characters = list(text)
    if len(characters) <= SUMMARY_LIMIT:
        return text
    return "".join(characters[:SUMMARY_LIMIT - 1]).rstrip() + "…"


# Jira Cloud

BLOCK_NODES = frozenset({"paragraph", "heading", "listItem", "blockquote", "codeBlock", "panel", "tableCell",
                         "tableHeader", "rule", "mediaSingle"})


def flatten_adf(node: object) -> str:
    """Plain text of an Atlassian Document Format tree: blocks on their own lines."""
    lines: list[str] = []
    current: list[str] = []

    def end_line() -> None:
        if current:
            lines.append("".join(current))
            current.clear()

    def walk(value: object) -> None:
        if not isinstance(value, dict):
            return
        kind = value.get("type")
        attrs = value.get("attrs") if isinstance(value.get("attrs"), dict) else {}
        if kind == "text" and isinstance(value.get("text"), str):
            current.append(value["text"])
        elif kind == "hardBreak":
            end_line()
        elif kind in ("mention", "emoji", "status", "date") and isinstance(attrs.get("text"), str):
            current.append(attrs["text"])
        elif kind == "inlineCard" and isinstance(attrs.get("url"), str):
            current.append(attrs["url"])
        content = value.get("content")
        if isinstance(content, list):
            for child in content:
                walk(child)
        if kind in BLOCK_NODES:
            end_line()

    walk(node)
    end_line()
    return "\n".join(line for line in lines if line.strip())


JIRA_STATES = {"new": "backlog", "indeterminate": "started", "done": "completed"}


def jira_snapshot(args: argparse.Namespace, environ: dict, transport: Transport, deadline: float,
                  clock: Callable[[], float], sleep: Callable[[float], None]) -> dict:
    site = args.site.rstrip("/")
    if _origin(site)[0] != "https" or not _origin(site)[1] or urllib.parse.urlsplit(site).path:
        raise Failure("--site must be an https origin, such as https://example.atlassian.net")
    client = Client(site, _authorization(environ, args.token_env, args.user_env), transport, deadline, clock, sleep)
    canceled = {name.strip() for name in (args.canceled_statuses or "").split(",") if name.strip()}
    issues: list[dict] = []
    token = None
    while True:
        query = {"jql": f"parent = {args.parent}", "fields": "summary,description,status,issuelinks",
                 "maxResults": "100"}
        if token:
            query["nextPageToken"] = token
        page = client.get(f"{site}/rest/api/3/search/jql?{urllib.parse.urlencode(query)}")
        if not isinstance(page, dict) or not isinstance(page.get("issues"), list):
            raise Failure("the search answer holds no issues list")
        issues.extend(page["issues"])
        _check_cap(len(issues))
        token = page.get("nextPageToken")
        if page.get("isLast") is True or not token:
            break
        if not isinstance(token, str):
            raise Failure("the search answer's nextPageToken is not text")
    keys = set()
    for issue in issues:
        keys.add(_text(issue.get("key") if isinstance(issue, dict) else None, "an issue's key"))
    items = []
    for issue in issues:
        key = issue["key"]
        fields = issue.get("fields")
        if not isinstance(fields, dict):
            raise Failure(f"{key} has no fields")
        status = fields.get("status")
        category = status.get("statusCategory") if isinstance(status, dict) else None
        category_key = category.get("key") if isinstance(category, dict) else None
        name = status.get("name") if isinstance(status, dict) else None
        if not isinstance(name, str) or not isinstance(category_key, str):
            raise Failure(f"{key} has no status or status category")
        if name in canceled:
            state = "canceled"
        elif category_key in JIRA_STATES:
            state = JIRA_STATES[category_key]
        else:
            raise Failure(f"{key} has a status category the snapshot does not know: {category_key}")
        item = {"subject": key, "title": _text(fields.get("summary"), f"{key}'s summary"), "state": state}
        description = fields.get("description")
        if description is not None:
            summary = _shorten(flatten_adf(description))
            if summary:
                item["summary"] = summary
        blockers = []
        links = fields.get("issuelinks") or []
        if not isinstance(links, list):
            raise Failure(f"{key}'s issue links are not a list")
        for link in links:
            if not isinstance(link, dict):
                continue
            kind = link.get("type") if isinstance(link.get("type"), dict) else {}
            inward = link.get("inwardIssue") if isinstance(link.get("inwardIssue"), dict) else None
            blocker = inward.get("key") if inward else None
            if kind.get("name") == args.blocks_link and blocker in keys and blocker != key and blocker not in blockers:
                blockers.append(blocker)
        if blockers:
            item["blockedBy"] = blockers
        items.append(item)
    return {"source": "tracker", "items": items}


# Bitbucket Cloud

BITBUCKET_API = "https://api.bitbucket.org"
BITBUCKET_STATES = {"OPEN": "open", "MERGED": "merged", "DECLINED": "closed", "SUPERSEDED": "closed"}
# Lower ranks win: an open request, then a merged one, then any other.
RANK = {"open": 0, "draft": 0, "merged": 1, "closed": 2}


def _moment(value: object, where: str) -> datetime.datetime:
    if not isinstance(value, str) or not TIME.match(value):
        raise Failure(f"{where} is not a time")
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Failure(f"{where} is not a time") from None
    return moment.astimezone(datetime.timezone.utc)


def _record(binding: str, cwd: Path | None) -> tuple[set[str], datetime.datetime]:
    """The subjects the record holds and the time of its first journal entry."""
    try:
        root = repository_root(cwd)
        bound = json.loads((root / binding).read_text(encoding="utf-8"))
        directory = root / bound["state"]["directory"]
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        with (directory / "journal.jsonl").open(encoding="utf-8") as journal:
            first = json.loads(journal.readline())
        subjects = {item["subject"] for item in state.get("queue", []) + state.get("planned", [])}
        subjects |= set(state.get("tickets", {}))
        subjects |= {item["id"] for item in state.get("followups", [])}
        started = _moment(first["at"], "the journal's first entry")
    except (ConfigError, OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError,
            RecursionError) as error:
        raise Failure(f"the record could not be read through --binding {binding}: {type(error).__name__}") from None
    if not all(isinstance(subject, str) and subject for subject in subjects):
        raise Failure("the record holds a subject that is not text")
    return subjects, started


def bitbucket_snapshot(args: argparse.Namespace, environ: dict, transport: Transport, deadline: float,
                       clock: Callable[[], float], sleep: Callable[[float], None], cwd: Path | None) -> dict:
    try:
        pattern = re.compile(args.subject_pattern)
    except re.error:
        raise Failure("--subject-pattern does not compile") from None
    host = args.host.lower()
    subjects, since = _record(args.binding, cwd)
    if args.since:
        since = _moment(args.since, "--since")
    client = Client(BITBUCKET_API, _authorization(environ, args.token_env, args.user_env), transport, deadline,
                    clock, sleep)
    repository = f"{urllib.parse.quote(args.workspace, safe='')}/{urllib.parse.quote(args.repo, safe='')}"
    base = f"{BITBUCKET_API}/2.0/repositories/{repository}"
    query = urllib.parse.urlencode(
        [("q", f"created_on >= {since.strftime('%Y-%m-%dT%H:%M:%S+00:00')}"), ("pagelen", "50"),
         *(("state", state) for state in BITBUCKET_STATES)])
    url: str | None = f"{base}/pullrequests?{query}"
    pulls: list[dict] = []
    while url:
        page = client.get(url)
        if not isinstance(page, dict) or not isinstance(page.get("values"), list):
            raise Failure("the pull request answer holds no values list")
        pulls.extend(page["values"])
        _check_cap(len(pulls))
        url = page.get("next")
        if url is not None and not isinstance(url, str):
            raise Failure("the pull request answer's next link is not text")
    chosen: dict[str, tuple[dict, str, datetime.datetime]] = {}
    for pull in pulls:
        if not isinstance(pull, dict):
            raise Failure("a pull request is not an object")
        number = pull.get("id")
        where = f"pull request {number}"
        raw_state = pull.get("state")
        if raw_state not in BITBUCKET_STATES:
            raise Failure(f"{where} is in a state the snapshot does not know: {raw_state}")
        state = BITBUCKET_STATES[raw_state]
        if state == "open" and pull.get("draft") is True:
            state = "draft"
        title = _text(pull.get("title"), f"{where}'s title")
        if _moment(pull.get("created_on"), f"{where}'s created_on") < since:
            continue
        updated = _moment(pull.get("updated_on"), f"{where}'s updated_on")
        source = pull.get("source") if isinstance(pull.get("source"), dict) else {}
        branch = source.get("branch", {}).get("name") if isinstance(source.get("branch"), dict) else None
        subject = None
        for text in (title, branch):
            match = pattern.search(text) if isinstance(text, str) else None
            if match and match.group(0) in subjects:
                subject = match.group(0)
                break
        if subject is None:
            continue
        current = chosen.get(subject)
        if current is None or (RANK[state], -updated.timestamp()) < (RANK[current[1]], -current[2].timestamp()):
            chosen[subject] = (pull, state, updated)
    items = []
    for subject in sorted(chosen):
        pull, state, _ = chosen[subject]
        item = {"subject": subject, "title": pull["title"], "state": state}
        links = pull.get("links") if isinstance(pull.get("links"), dict) else {}
        html = links.get("html") if isinstance(links.get("html"), dict) else {}
        href = html.get("href")
        if isinstance(href, str) and _origin(href) == ("https", host, 443):
            item["changeRequest"] = href
        source = pull.get("source") if isinstance(pull.get("source"), dict) else {}
        commit = source.get("commit") if isinstance(source.get("commit"), dict) else {}
        short = commit.get("hash")
        if isinstance(short, str) and SHORT_HASH.match(short):
            resolved = client.get(f"{base}/commit/{short}", missing_ok=True)
            full = resolved.get("hash") if isinstance(resolved, dict) else None
            if isinstance(full, str) and COMMIT.match(full) and full.startswith(short):
                item["head"] = full
        items.append(item)
    return {"source": "forge", "items": items}


# Command line


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # One line, never the usage block.
        raise Failure(f"usage: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Print a delivery's tracker or forge snapshot.", add_help=False)
    parser.add_argument("--kind", required=True, choices=("jira", "bitbucket"))
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--now")
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--user-env")
    parser.add_argument("--site")
    parser.add_argument("--parent")
    parser.add_argument("--canceled-statuses")
    parser.add_argument("--blocks-link", default="Blocks")
    parser.add_argument("--workspace")
    parser.add_argument("--repo")
    parser.add_argument("--binding")
    parser.add_argument("--subject-pattern")
    parser.add_argument("--since")
    parser.add_argument("--host", default="bitbucket.org")
    return parser


REQUIRED = {"jira": ("site", "parent", "user_env"), "bitbucket": ("workspace", "repo", "binding", "subject_pattern")}


def run(argv: list[str], *, environ: dict, transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
        cwd: Path | None = None) -> tuple[int, str, str]:
    """The exit code, stdout and stderr of one snapshot."""
    try:
        args = _parser().parse_args(argv)
        for name in REQUIRED[args.kind]:
            if not getattr(args, name):
                raise Failure(f"usage: --{name.replace('_', '-')} is required for --kind {args.kind}")
        if not 0 < args.timeout_seconds <= 600:
            raise Failure("usage: --timeout-seconds must be above 0 and at most 600")
        now = _moment(args.now, "--now") if args.now else datetime.datetime.now(datetime.timezone.utc)
        deadline = clock() + args.timeout_seconds
        if args.kind == "jira":
            document = jira_snapshot(args, environ, transport, deadline, clock, sleep)
        else:
            document = bitbucket_snapshot(args, environ, transport, deadline, clock, sleep, cwd)
        snapshot = {"takenAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), **document}
        return 0, json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n", ""
    except Failure as failure:
        return 1, "", f"snapshot: {failure}\n"
    except Exception as error:  # Only the type: an exception's text may hold what it was given.
        return 1, "", f"snapshot: unexpected failure ({type(error).__name__})\n"


def main() -> int:
    code, out, err = run(sys.argv[1:], environ=dict(os.environ))
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
