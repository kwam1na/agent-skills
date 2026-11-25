"""The hosted line-map snapshot and stable source-cut guard.

The hosted page shows what the local live view shows, from the same record: the snapshot carries the same cut the
local page renders from, story, journal facts, files, left-open work and the forge's checks on the change request included, paths on the publishing machine included. The one
rule kept is that no credential is published. A configured secret's value anywhere in the cut, or a field under a
credential-named key, refuses the cut with a bounded code. A sibling delivery's hosted link is a read capability, so
it is left out and the page names the sibling without one. The byte, list and text bounds keep the snapshot within
what the service accepts. No scanner claims to recognize every secret someone may type. No network or subprocess is
used here. Callers supply the complete selected source inventory, including run inventory and review inputs, before
collecting from those sources.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'references/hosted-snapshot.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
MAX_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
CREDENTIAL_KEY = re.compile(r'^(?:password|token|secret|api[_-]?key|credential|authorization|cookie)$', re.I)
# A file link's key: the commit and the reference as the note wrote it. The schema holds the rule, so the receiver keeps
# it too: a path that starts at the root or climbs out of the tree with a `.` or `..` segment never names a file in it.
FILE_KEY = re.compile(SCHEMA['properties']['links']['properties']['files']['propertyNames']['pattern'])


class ProjectionError(ValueError):
    """A safe bounded code; never includes rejected source values."""


class SourceChanged(ProjectionError):
    """The source moved while being collected; discard and retry a fresh cut."""


def _resolve(schema: dict) -> dict:
    while '$ref' in schema:
        schema = SCHEMA['$defs'][schema['$ref'].removeprefix('#/$defs/')]
    return schema


def _typed(value, kind):
    return {'null': lambda: value is None, 'object': lambda: isinstance(value, dict),
            'array': lambda: isinstance(value, list), 'string': lambda: isinstance(value, str),
            'boolean': lambda: type(value) is bool,
            'integer': lambda: type(value) is int and abs(value) <= 2**53 - 1,
            'number': lambda: type(value) in (int, float) and math.isfinite(value) and abs(value) <= 2**53 - 1}[kind]()


def _check(value, schema):
    """The closed schema's small standard-library subset, mirrored by JSON Schema in service tests."""
    schema = _resolve(schema)
    for keyword in ('anyOf', 'oneOf'):
        if keyword in schema:
            matches, typed = 0, None
            for branch in schema[keyword]:
                try:
                    _check(value, branch)
                    matches += 1
                except ProjectionError as error:
                    # The branch for this value's type says why it failed: a credential field, a long text or list.
                    kinds = _resolve(branch).get('type')
                    if typed is None and kinds and any(_typed(value, kind) for kind in (kinds if isinstance(kinds, list) else [kinds])):
                        typed = error
            if not matches:
                raise ProjectionError(str(typed) if typed is not None else 'snapshot.schema')
            if keyword == 'oneOf' and matches != 1:
                raise ProjectionError('snapshot.schema')
    kinds = schema.get('type')
    if kinds and not any(_typed(value, kind) for kind in (kinds if isinstance(kinds, list) else [kinds])):
        raise ProjectionError('snapshot.type')
    if 'const' in schema and (value != schema['const'] or type(value) is not type(schema['const'])):
        raise ProjectionError('snapshot.const')
    if 'enum' in schema and value not in schema['enum']:
        raise ProjectionError('snapshot.enum')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', MAX_BYTES):
            raise ProjectionError('snapshot.text_bound')
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            raise ProjectionError('snapshot.pattern')
    elif type(value) in (int, float):
        if value < schema.get('minimum', -math.inf):
            raise ProjectionError('snapshot.number_bound')
    elif isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            raise ProjectionError('snapshot.list_bound')
        if schema.get('uniqueItems') and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise ProjectionError('snapshot.duplicate')
        for entry in value:
            _check(entry, schema.get('items', {}))
    elif isinstance(value, dict) and ('properties' in schema or 'additionalProperties' in schema):
        if len(value) > schema.get('maxProperties', 10000) or not set(schema.get('required', [])) <= value.keys():
            raise ProjectionError('snapshot.object_bound')
        for key, entry in value.items():
            if not isinstance(key, str):
                raise ProjectionError('snapshot.key')
            if CREDENTIAL_KEY.fullmatch(key):
                raise ProjectionError('snapshot.credential_field')
            if 'propertyNames' in schema:
                _check(key, schema['propertyNames'])
            child = schema.get('properties', {}).get(key, schema.get('additionalProperties', False))
            if child is False:
                raise ProjectionError('snapshot.unknown_field')
            _check(entry, child)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, entry in value.items():
            yield key
            yield from _strings(entry)
    elif isinstance(value, list):
        for entry in value:
            yield from _strings(entry)


def _url(value, *, fragment=False):
    """An https address with a host and nothing else to it: no user, password, port other than 443 or query, and no
    fragment unless ``fragment``, which a file link uses to open its file at a line."""
    if not isinstance(value, str) or re.search(r'[\x00-\x20\x7f\\]', value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
                and url.port in (None, 443) and not url.query and (fragment or not url.fragment))
    except ValueError:
        return False


def _file_links(links: dict) -> dict:
    """The file links a hosted page may carry: each keyed by a reference inside the reviewed tree and opening an https
    address. Any other is left out, so one odd reference never costs the page its other links."""
    return {key: href for key, href in links.items()
            if isinstance(key, str) and FILE_KEY.fullmatch(key) and _url(href, fragment=True)}


def encode(value: dict) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ProjectionError('snapshot.json') from error


def validate(value: object, *, secret_values=()) -> None:
    """Validate an upload before sending; the receiver independently enforces this contract, all but the secrets."""
    secret_values = tuple(secret_values)
    if len(encode(value)) > MAX_BYTES:
        raise ProjectionError('snapshot.byte_bound')
    try:
        _check(value, SCHEMA)
        if secret_values and any(secret and secret in text for text in _strings(value) for secret in secret_values):
            raise ProjectionError('snapshot.configured_secret')
        links = value['links']
        urls = [*links['issues'].values(), *links['commits'].values(),
                *(row['href'] for row in links['changes'].values())]
        if not all(_url(url) for url in urls):
            raise ProjectionError('snapshot.unsafe_link')
        files = links.get('files', {})
        if not all(FILE_KEY.fullmatch(key) and _url(href, fragment=True) for key, href in files.items()):
            raise ProjectionError('snapshot.unsafe_link')
    except (RecursionError, KeyError, TypeError) as error:
        raise ProjectionError('snapshot.structure') from error


def _without_capabilities(value: dict) -> None:
    """Leave out each sibling delivery's hosted link: anyone holding it can read that delivery. The link goes from the
    sibling's row, its group and the journal entry that added it, and anywhere else the cut repeats it, the sibling's
    name stands in its place."""
    document = value.get('document') if isinstance(value.get('document'), dict) else {}
    state = document.get('state') if isinstance(document.get('state'), dict) else {}
    across = document.get('across') if isinstance(document.get('across'), dict) else {}
    holders = [row for rows in (state.get('siblings'), across.get('groups')) if isinstance(rows, list)
               for row in rows if isinstance(row, dict)]
    holders += [entry['data'] for entry in document.get('journal') or []
                if isinstance(entry, dict) and entry.get('kind') == 'sibling.added' and isinstance(entry.get('data'), dict)]
    names = {}
    for row in holders:
        link = row.pop('link', None)
        if isinstance(link, str) and link:
            names[link] = str(row.get('name') or row.get('sibling') or 'the sibling')

    def scrub(node):
        if isinstance(node, str):
            for link, name in names.items():
                node = node.replace(link, name)
            return node
        if isinstance(node, list):
            return [scrub(entry) for entry in node]
        if isinstance(node, dict):
            return {scrub(key): scrub(entry) for key, entry in node.items()}
        return node
    if names:
        value.update(scrub(value))


def _without_merge_nonces(document: dict) -> dict:
    """The record less each base merge authorization's `token`: the nonce its merge commit carries. It is no credential,
    but the name is one, and no view draws it, so it is left out rather than refusing every cut after a base merge. It
    goes before the links are worked out, so it is not taken for a commit either."""
    journal = document.get('journal') if isinstance(document.get('journal'), list) else None
    if not journal or not any(isinstance(entry, dict) and entry.get('kind') == 'base.merge.authorized' for entry in journal):
        return document
    entries = []
    for entry in journal:
        if isinstance(entry, dict) and entry.get('kind') == 'base.merge.authorized' and isinstance(entry.get('data'), dict):
            entry = {**entry, 'data': {key: fact for key, fact in entry['data'].items() if key != 'token'}}
        entries.append(entry)
    return {**document, 'journal': entries}


def project(document: dict, runs: dict, config: dict, *, source_fingerprint: str, plan: dict | None = None,
            needs: dict | None = None, files: dict | None = None, repository: Path | None = None,
            forge: dict | None = None, secret_values=()) -> dict:
    """The cut the local live view renders from, for one current delivery, less the credentials. It never carries
    history: the hosted page has none to link to."""
    document = _without_merge_nonces(document)
    named = render.named_runs(document)
    selected = {key: value for key, value in runs.items() if key in named}
    data = render.line_map_data(document, selected, config, theme_key='hosted-delivery', history=None,
                                plan=plan, needs=needs, files=files, repository=repository, forge=forge)
    # A review note's file reference opens its file on the forge, as on the local page, when the forge names a fileUrl
    # and the reference names one file in the reviewed tree. Without one the links hold no files at all.
    links = {key: value for key, value in data['links'].items() if key != 'files'}
    files = _file_links(data['links'].get('files') or {})
    if files:
        links['files'] = files
    data = {**data, 'links': links}
    value = copy.deepcopy({'schemaVersion': SCHEMA['properties']['schemaVersion']['const'],
                           'sourceFingerprint': source_fingerprint, **data})
    _without_capabilities(value)
    validate(value, secret_values=secret_values)
    return value


def capture(root: Path, paths, collect, *, deadline_seconds: float = 60, secret_values=()) -> dict:
    """Collect only across an unchanged complete source inventory rooted under an approved root.

    ``paths`` is a fixed iterable, or a function returning the selected inventory
    each time (needed to detect added/deleted review or run inputs). Include absent
    fixed plan/needs paths. The collector must read fresh canonical sources and
    use bounded subprocess deadlines; no shared lock spans collection or I/O.
    The fingerprint includes bytes plus filesystem identity, so replacement with
    identical bytes during collection also invalidates the cut.
    """
    root = root.resolve(strict=True)
    started = time.monotonic()

    def inventory():
        entries, total = [], 0
        names = sorted(set(paths() if callable(paths) else paths))
        for name in names:
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ProjectionError('source.path')
            path = root / relative
            if not path.resolve().is_relative_to(root):
                raise ProjectionError('source.path')
            try:
                stat = path.stat()
                if not path.is_file() or stat.st_size > MAX_SOURCE_BYTES - total:
                    raise ProjectionError('source.byte_bound')
                # No unbounded read if a writer grows the file between stat and read.
                with path.open('rb') as stream:
                    data = stream.read(MAX_SOURCE_BYTES - total + 1)
                total += len(data)
                if total > MAX_SOURCE_BYTES:
                    raise ProjectionError('source.byte_bound')
                entries.append([str(relative), stat.st_dev, stat.st_ino, stat.st_mtime_ns,
                                stat.st_ctime_ns, hashlib.sha256(data).hexdigest()])
            except FileNotFoundError:
                entries.append([str(relative), None])
            if time.monotonic() - started > deadline_seconds:
                raise SourceChanged('source.deadline')
        return entries

    before = inventory()
    value = collect()
    if inventory() != before or time.monotonic() - started > deadline_seconds:
        raise SourceChanged('source.changed')
    value['sourceFingerprint'] = hashlib.sha256(encode(before)).hexdigest()
    validate(value, secret_values=secret_values)
    return value
