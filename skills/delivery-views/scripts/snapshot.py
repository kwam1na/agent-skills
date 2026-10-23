#!/usr/bin/env python3
"""Print a delivery's tracker or forge snapshot as one ``work-snapshot/1``.

    python3 -B snapshot.py --kind jira --site <https origin> --parent <key> --user-env <name> --token-env <name>
        [--canceled-statuses "Won't Do,Duplicate"] [--blocks-link Blocks]
    python3 -B snapshot.py --kind bitbucket --workspace <w> --repo <r> --binding <path> --subject-pattern <regex>
        --token-env <name> [--user-env <name>] [--since <ISO time>] [--host bitbucket.org]
    python3 -B snapshot.py --kind linear --parent <identifier> --token-env <name>
    python3 -B snapshot.py --kind github --owner <o> --repo <r> --binding <path> --subject-pattern <regex>
        --token-env <name> [--since <ISO time>] [--host github.com]

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
import email.utils
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
COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SHORT_HASH = re.compile(r"^[0-9a-f]{4,64}$")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class Failure(Exception):
    """A refusal written never to hold a credential; its text is the stderr line."""


def _shown(value: object) -> str:
    """A provider value as a refusal quotes it: escaped, so it stays on one line. The line is capped
    only after redaction, so a cut never leaves part of a credential unmatched."""
    return repr(value)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


# method, url, headers, timeout and an optional body.
Transport = Callable[..., Response]


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80)


class Client:
    """JSON over HTTPS to one origin, within one deadline, retrying only what may pass on a retry."""

    def __init__(self, base: str, authorization: str, transport: Transport, deadline: float,
                 clock: Callable[[], float], sleep: Callable[[float], None], headers: dict | None = None) -> None:
        self.origin = _origin(base)
        self.authorization = authorization
        self.headers = headers or {"Accept": "application/json"}
        # The headers of the last answer read, for links a provider pages by.
        self.last_headers: dict = {}
        self.transport = transport
        self.deadline = deadline
        self.clock = clock
        self.sleep = sleep

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def get(self, url: str, *, missing_ok: bool = False) -> object | None:
        """The JSON at ``url``; None for a 404 when ``missing_ok``."""
        return self.request("GET", url, missing_ok=missing_ok)

    def post(self, url: str, document: object) -> object | None:
        return self.request("POST", url, json.dumps(document).encode("utf-8"))

    def request(self, method: str, url: str, body: bytes | None = None, *, missing_ok: bool = False) -> object | None:
        retries = 0
        redirects = 0
        while True:
            if _origin(url) != self.origin:
                raise Failure(f"refused a request to another origin: {_shown(_origin(url)[1])}")
            remaining = self.remaining()
            if remaining <= 0:
                raise Failure("the deadline passed before the snapshot was complete")
            headers = {**self.headers, "Authorization": self.authorization}
            if body is not None:
                headers["Content-Type"] = "application/json"
            response = self.transport(method, url, headers, remaining, body)
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
            if status == 429 or 500 <= status <= 599:
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
            self.last_headers = response.headers
            try:
                return json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise Failure("the provider's answer was not JSON") from None


def _retry_after(value: str | None, retries: int) -> float:
    if value is None:
        return float(retries + 1)
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = email.utils.parsedate_to_datetime(value)
            seconds = (moment - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = float(retries + 1)
    return max(0.0, seconds) if seconds == seconds else 0.0


def urllib_transport(method: str, url: str, headers: dict, timeout: float, body: bytes | None = None) -> Response:
    """The network: one request, redirects left to the client, the body read within a bound."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise Failure(f"could not reach {urllib.parse.urlsplit(url).hostname}: {type(reason).__name__}") from None
    ends = time.monotonic() + timeout
    with response:
        # The socket timeout bounds each read; this bounds the whole body.
        body = bytearray()
        while True:
            if time.monotonic() > ends:
                raise Failure("the deadline passed while the provider was answering")
            chunk = response.read(65536)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise Failure("the provider's answer was over 16 MiB")
        return Response(response.status, {key.lower(): value for key, value in response.headers.items()},
                        bytes(body))


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
    if not JIRA_KEY.match(args.parent):
        raise Failure("usage: --parent must be an issue key, such as SHFT-10")
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
        key = _text(issue.get("key") if isinstance(issue, dict) else None, "an issue's key")
        if key in keys:
            raise Failure(f"the search listed {_shown(key)} twice")
        keys.add(key)
    items = []
    for issue in issues:
        key = issue["key"]
        fields = issue.get("fields")
        if not isinstance(fields, dict):
            raise Failure(f"{_shown(key)} has no fields")
        status = fields.get("status")
        category = status.get("statusCategory") if isinstance(status, dict) else None
        category_key = category.get("key") if isinstance(category, dict) else None
        name = status.get("name") if isinstance(status, dict) else None
        if not isinstance(name, str) or not isinstance(category_key, str):
            raise Failure(f"{_shown(key)} has no status or status category")
        if name in canceled:
            state = "canceled"
        elif category_key in JIRA_STATES:
            state = JIRA_STATES[category_key]
        else:
            raise Failure(f"{_shown(key)} has a status category the snapshot does not know: {_shown(category_key)}")
        item = {"subject": key, "title": _text(fields.get("summary"), f"{_shown(key)}'s summary"), "state": state}
        description = fields.get("description")
        if description is not None:
            summary = _shorten(flatten_adf(description))
            if summary:
                item["summary"] = summary
        blockers = []
        links = fields.get("issuelinks")
        if not isinstance(links, list):
            raise Failure(f"{_shown(key)}'s issue links are missing or not a list")
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
        where = f"pull request {_shown(number)}"
        raw_state = pull.get("state")
        if raw_state not in BITBUCKET_STATES:
            raise Failure(f"{where} is in a state the snapshot does not know: {_shown(raw_state)}")
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


# Linear

LINEAR_API = "https://api.linear.app/graphql"
LINEAR_PAGE_SIZE = 100
LINEAR_STATES = {"triage": "backlog", "backlog": "backlog", "unstarted": "backlog", "started": "started",
                 "completed": "completed", "canceled": "canceled"}
# Linear's personal keys: any of them in a refusal is taken out, not only the one in use.
LINEAR_KEY = re.compile(r"lin_api_[A-Za-z0-9]+", re.IGNORECASE)
LINEAR_QUERY = f"""query Children($parent: String!, $after: String) {{
  issue(id: $parent) {{
    createdAt
    children(first: {LINEAR_PAGE_SIZE}, after: $after) {{
      nodes {{
        identifier
        createdAt
        title
        description
        state {{ type }}
        inverseRelations(first: 50) {{ nodes {{ type issue {{ identifier }} }} }}
      }}
      pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}"""
# A follow-up often opens with where it came from; its summary says what the work is.
PROVENANCE = re.compile(r"^(Deferred from|Proposed by|Filed by|These are the|Split out of|Moved from)\b", re.ASCII)
# JavaScript's whitespace, which its \s and trim() share: the summary is read as the
# tracker command it ports read it, so Python's own whitespace set is not used.
JS_SPACE = "\t\n\v\f\r    -     　﻿"
JS_SPACE_CHARS = "\t\n\v\f\r   " + "".join(map(chr, range(0x2000, 0x200b))) \
    + "    　﻿"
# The longest paragraph the summary's patterns read: past it they would take far longer
# than the deadline allows, and only the first SUMMARY_LIMIT characters are kept anyway.
MAX_SUMMARY_SOURCE = 8 * SUMMARY_LIMIT


def summary_of(description: object) -> str | None:
    """A ticket's summary: the first paragraph under its "## Summary" heading, as plain text,
    within the snapshot's bound; None when there is no such paragraph."""
    lines = (description if isinstance(description, str) else "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fenced = False
    heading = -1
    for index, line in enumerate(lines):
        # A heading inside a fenced code block is code, not the ticket's heading.
        if re.match(f"^[{JS_SPACE}]*(```|~~~)", line):
            fenced = not fenced
        if not fenced and re.match(f"^##[{JS_SPACE}]*Summary[{JS_SPACE}]*$", line.strip(JS_SPACE_CHARS),
                                   re.IGNORECASE | re.ASCII):
            heading = index
            break
    if heading < 0:
        return None
    paragraph: list[str] = []
    for line in lines[heading + 1:]:
        if re.match(f"^#{{1,6}}[{JS_SPACE}]", line.strip(JS_SPACE_CHARS)):
            break
        if not line.strip(JS_SPACE_CHARS):
            if paragraph:
                break
            continue
        # A list item's marker is markup, not text.
        paragraph.append(re.sub(f"^[{JS_SPACE}]*(?:[-*+]|[0-9]+[.)])[{JS_SPACE}]+", "", line, count=1))
    text = " ".join(paragraph)[:MAX_SUMMARY_SOURCE]
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = re.sub(f"!?\\[((?:[^[\\]]|\\[[^\\]]*\\])+)\\]\\((?:[^(){JS_SPACE}]|\\([^()]*\\))*\\)", r"\1", text)
    text = re.sub(r"`|\*\*|(?<!\w)_|_(?!\w)", "", text, flags=re.ASCII)
    text = re.sub(f"[{JS_SPACE}]+", " ", text).strip(JS_SPACE_CHARS)
    while PROVENANCE.match(text):
        following = re.search(f"\\.[{JS_SPACE}]+(?=[A-Z`])", text)
        if not following:
            break
        text = text[following.end():]
    if not text:
        return None
    if len(text) <= SUMMARY_LIMIT:
        return text
    head = text[:SUMMARY_LIMIT]
    cut = head.rfind(". ")
    # The ported command measured the cut in UTF-16 units.
    if cut >= 0 and len(head[:cut].encode("utf-16-le")) // 2 > 80:
        return head[:cut + 1]
    return text[:SUMMARY_LIMIT - 1] + "…"


def linear_snapshot(args: argparse.Namespace, environ: dict, transport: Transport, deadline: float,
                    clock: Callable[[], float], sleep: Callable[[float], None]) -> dict:
    # A personal key goes in the Authorization header as it is.
    client = Client(LINEAR_API, _credential(environ, args.token_env), transport, deadline, clock, sleep)
    items: list[dict] = []
    seen: set[str] = set()
    cursors: set[str] = set()
    after = None
    while True:
        answer = client.post(LINEAR_API, {"query": LINEAR_QUERY, "variables": {"parent": args.parent, "after": after}})
        if not isinstance(answer, dict):
            raise Failure("Linear's answer could not be read")
        errors = answer.get("errors")
        if errors:
            messages = [error.get("message") if isinstance(error, dict) else None for error in errors] \
                if isinstance(errors, list) else [None]
            raise Failure("Linear refused: " + "; ".join(_shown(message or "error") for message in messages))
        data = answer.get("data")
        issue = data.get("issue") if isinstance(data, dict) else None
        if issue is None:
            raise Failure(f"the tracker has no issue {_shown(args.parent)}")
        try:
            children = issue["children"]
            nodes = children["nodes"]
            page = children["pageInfo"]
            for node in nodes:
                if client.remaining() <= 0:
                    raise Failure("the deadline passed before the snapshot was complete")
                identifier = _text(node["identifier"], "a child's identifier")
                # A child listed on two pages is kept once, as the tracker first listed it.
                if identifier in seen:
                    continue
                seen.add(identifier)
                state = LINEAR_STATES.get(node["state"]["type"])
                if state is None:
                    raise Failure(f"{_shown(identifier)} has a state type the snapshot does not know: "
                                  f"{_shown(node['state']['type'])}")
                blockers: list[str] = []
                for relation in node["inverseRelations"]["nodes"]:
                    if relation["issue"] is None:
                        continue
                    blocker = _text(relation["issue"]["identifier"], f"a blocker of {_shown(identifier)}")
                    # A child blocked by the parent itself is no dependency the record can hold.
                    if relation["type"] == "blocks" and blocker not in (identifier, args.parent) \
                            and blocker not in blockers:
                        blockers.append(blocker)
                item = {"subject": identifier, "title": _text(node["title"], f"{_shown(identifier)}'s title")}
                summary = summary_of(node.get("description"))
                if summary is not None:
                    item["summary"] = summary
                item["state"] = state
                if blockers:
                    item["blockedBy"] = blockers
                items.append(item)
                _check_cap(len(items))
            has_next = page["hasNextPage"]
            after = page.get("endCursor")
            if not isinstance(has_next, bool):
                raise TypeError("hasNextPage")
        except (KeyError, TypeError, AttributeError):
            raise Failure("Linear's answer is not the shape of a children listing") from None
        if has_next is not True:
            return {"source": "tracker", "items": items}
        # A page that names no new place to go on from would be read forever.
        if not isinstance(after, str) or not after or after in cursors:
            raise Failure(f"Linear gave no next page for the children of {_shown(args.parent)}")
        cursors.add(after)


# GitHub

GITHUB_API = "https://api.github.com"
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
GITHUB_RANK = {"open": 0, "draft": 0, "merged": 1, "closed": 2}
LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def _bracketed(title: str) -> list[str]:
    """The subjects a title names in its opening brackets, as ``[A-1]: ...`` or ``[A-1, A-2]: ...``."""
    named = re.match(r"^\[([^\]]+)\]", title.strip())
    return [part for part in re.split(r"[\s,]+", named.group(1)) if part] if named else []


def github_snapshot(args: argparse.Namespace, environ: dict, transport: Transport, deadline: float,
                    clock: Callable[[], float], sleep: Callable[[float], None], cwd: Path | None) -> dict:
    try:
        pattern = re.compile(args.subject_pattern)
    except re.error:
        raise Failure("--subject-pattern does not compile") from None
    host = args.host.lower()
    subjects, since = _record(args.binding, cwd)
    if args.since:
        since = _moment(args.since, "--since")
    client = Client(GITHUB_API, f"Bearer {_credential(environ, args.token_env)}", transport, deadline, clock, sleep,
                    headers=GITHUB_HEADERS)
    repository = f"{urllib.parse.quote(args.owner, safe='')}/{urllib.parse.quote(args.repo, safe='')}"
    query = urllib.parse.urlencode({"state": "all", "sort": "created", "direction": "desc", "per_page": "100"})
    url: str | None = f"{GITHUB_API}/repos/{repository}/pulls?{query}"
    chosen: dict[str, tuple[dict, str, str]] = {}
    count = 0
    read: set[str] = set()
    while url:
        if url in read:
            raise Failure("the pull request listing named a page it had already given")
        read.add(url)
        page = client.get(url)
        if not isinstance(page, list):
            raise Failure("the pull request answer is not a list")
        reached_since = False
        for pull in page:
            if not isinstance(pull, dict):
                raise Failure("a pull request is not an object")
            where = f"pull request {_shown(pull.get('number'))}"
            if _moment(pull.get("created_at"), f"{where}'s created_at") < since:
                # Newest first: everything after this was made before the since time too.
                reached_since = True
                continue
            # Only pull requests in the window count toward the cap.
            count += 1
            _check_cap(count)
            raw_state = pull.get("state")
            if raw_state == "open":
                state = "draft" if pull.get("draft") is True else "open"
            elif raw_state == "closed":
                state = "merged" if pull.get("merged_at") else "closed"
            else:
                raise Failure(f"{where} is in a state the snapshot does not know: {_shown(raw_state)}")
            title = _text(pull.get("title"), f"{where}'s title")
            # The latest of a kind by when it merged or closed, not by a later comment.
            when = pull.get("merged_at") or pull.get("closed_at") or pull.get("updated_at")
            when_moment = _moment(when, f"{where}'s merged_at, closed_at or updated_at").isoformat()
            for subject in _bracketed(title):
                if subject not in subjects or not pattern.search(subject):
                    continue
                current = chosen.get(subject)
                if current is None or GITHUB_RANK[state] < GITHUB_RANK[current[1]] or (
                        GITHUB_RANK[state] == GITHUB_RANK[current[1]] and when_moment > current[2]):
                    chosen[subject] = (pull, state, when_moment)
        if reached_since:
            break
        link = client.last_headers.get("link") or ""
        following = LINK_NEXT.search(link)
        if following is None and 'rel="next"' in link:
            raise Failure("the pull request listing's next link could not be read")
        url = following.group(1) if following else None
    items = []
    for subject in sorted(chosen):
        pull, state, _ = chosen[subject]
        item = {"subject": subject, "title": pull["title"], "state": state}
        href = pull.get("html_url")
        if isinstance(href, str) and _origin(href) == ("https", host, 443):
            item["changeRequest"] = href
        head = pull.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        # A head that is not a full commit, such as a deleted fork's, is left out.
        if isinstance(sha, str) and COMMIT.match(sha):
            item["head"] = sha
        items.append(item)
    return {"source": "forge", "items": items}


# Command line


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # One line, never the usage block.
        raise Failure(f"usage: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Print a delivery's tracker or forge snapshot.", add_help=False)
    parser.add_argument("--kind", required=True, choices=("jira", "bitbucket", "linear", "github"))
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
    parser.add_argument("--owner")
    parser.add_argument("--host")
    return parser


REQUIRED = {"jira": ("site", "parent", "user_env"), "bitbucket": ("workspace", "repo", "binding", "subject_pattern"),
            "linear": ("parent",), "github": ("owner", "repo", "binding", "subject_pattern")}
# The web host a forge's change request links are kept on, unless --host names another.
HOSTS = {"bitbucket": "bitbucket.org", "github": "github.com"}


def run(argv: list[str], *, environ: dict, transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
        cwd: Path | None = None) -> tuple[int, str, str]:
    """The exit code, stdout and stderr of one snapshot."""
    credentials: list[str] = []
    secrets: list[str] = []
    try:
        args = _parser().parse_args(argv)
        # Whatever a provider echoes back, the token never reaches stdout, and
        # neither the token nor the user reaches stderr.
        token = environ.get(args.token_env or "")
        user = environ.get(args.user_env or "") if args.user_env else None
        encode = lambda text: base64.b64encode(text.encode("utf-8")).decode("ascii")  # noqa: E731
        if token:
            credentials = [token, encode(token)] + ([encode(f"{user}:{token}")] if user else [])
        secrets = sorted(credentials + ([user, encode(user)] if user else []), key=len, reverse=True)
        for name in REQUIRED[args.kind]:
            if not getattr(args, name):
                raise Failure(f"usage: --{name.replace('_', '-')} is required for --kind {args.kind}")
        if not 0 < args.timeout_seconds <= 600:
            raise Failure("usage: --timeout-seconds must be above 0 and at most 600")
        now = _moment(args.now, "--now") if args.now else datetime.datetime.now(datetime.timezone.utc)
        deadline = clock() + args.timeout_seconds
        args.host = args.host or HOSTS.get(args.kind)
        if args.kind == "jira":
            document = jira_snapshot(args, environ, transport, deadline, clock, sleep)
        elif args.kind == "bitbucket":
            document = bitbucket_snapshot(args, environ, transport, deadline, clock, sleep, cwd)
        elif args.kind == "linear":
            document = linear_snapshot(args, environ, transport, deadline, clock, sleep)
        else:
            document = github_snapshot(args, environ, transport, deadline, clock, sleep, cwd)
        snapshot = {"takenAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), **document}
        try:
            json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise Failure("the provider's text holds a character that is not Unicode") from None
        out = json.dumps(snapshot, ensure_ascii=True, sort_keys=True) + "\n"
        if any(credential in out for credential in credentials):
            raise Failure("the provider's answer holds a credential")
        return 0, out, ""
    except Failure as failure:
        text = " ".join(str(failure).split())
        for secret in secrets:
            text = text.replace(secret, "[redacted]")
        text = LINEAR_KEY.sub("[redacted]", text)
        return 1, "", f"snapshot: {text[:300]}\n"
    except Exception as error:  # Only the type: an exception's text may hold what it was given.
        return 1, "", f"snapshot: unexpected failure ({type(error).__name__})\n"


def main() -> int:
    code, out, err = run(sys.argv[1:], environ=dict(os.environ))
    sys.stdout.buffer.write(out.encode("ascii"))
    sys.stderr.buffer.write(err.encode("utf-8", errors="backslashreplace"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
