"""Bounded data-only hosted line-map projection and stable source-cut guard.

The versioned schema is shared with the service. Projection drops fields the
schema does not name; validation refuses them. Public prose is intentional:
known configured secrets cause a bounded refusal. Producer prose containing
recognizable local references is explicitly omitted; raw uploads with paths
remain invalid,
not a claim to recognize every secret someone may type. No network or subprocess
is used here. Callers supply the complete selected source inventory, including
run inventory/review inputs, before collecting from those sources.
"""
from __future__ import annotations

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
# Absolute workstation/temp paths and file URLs are never public view content.
# Option assignments use the same local-reference boundary as quoted prose.
# Explicit relative paths are local too; validated HTTPS tokens are handled below.
LOCAL_PATH = re.compile(r'(?:file://|(?:^|[\s\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e“”‘’])(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/]|\\\\|~[/\\]|\.{1,2}[/\\]|(?:\.?agents|\.agent-skills|\.orchestration|docs|scripts|skills|tests)[/\\]))')
def _has_local_path(text):
    # HTTPS path components may themselves contain punctuation and names such as
    # docs/. They are public URL tokens, not local references. Secret scans still
    # inspect the original text independently.
    def public_url(match):
        token = match.group()
        unwrapped = re.sub(r'[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e“”‘’]+$', '', token)
        return ' ' if _url(token) or _url(unwrapped) else token
    prose = re.sub(r'https://(?!/)[^\s<>"\'`“”‘’\\]+', public_url, text, flags=re.I)
    return LOCAL_PATH.search(prose) is not None


CREDENTIAL_KEY = re.compile(r'^(?:password|token|secret|api[_-]?key|credential|authorization|cookie)$', re.I)
OMITTED_LOCAL_REFERENCE = '[Local reference omitted]'
PROSE_FIELDS = frozenset(('headline title text summary label description detail reason why waitingOn clears').split())


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
            matches = 0
            for branch in schema[keyword]:
                try:
                    _check(value, branch)
                    matches += 1
                except ProjectionError:
                    pass
            if not matches or keyword == 'oneOf' and matches != 1:
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


def _project(value, schema):
    schema = _resolve(schema)
    for keyword in ('anyOf', 'oneOf'):
        if keyword in schema:
            for branch in schema[keyword]:
                try:
                    projected = _project(value, branch)
                    _check(projected, branch)
                    return projected
                except ProjectionError:
                    continue
            raise ProjectionError('snapshot.projection')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        extra = schema.get('additionalProperties', False)
        return {key: _project(entry, props.get(key, extra)) for key, entry in value.items()
                if key in props or isinstance(extra, dict)}
    if isinstance(value, list):
        return [_project(entry, schema.get('items', {})) for entry in value]
    return value


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


def _omit_local_prose(value, schema, path=()):
    """Omit whole sensitive fields only where ordinary prose is schema-valid.

    Identifiers, enums and links retain their constraints: a marker that cannot
    satisfy their schema leaves the original value to fail strict validation.
    """
    schema=_resolve(schema)
    for keyword in ('anyOf','oneOf'):
        if keyword in schema:
            for branch in schema[keyword]:
                try:_check(value,branch)
                except ProjectionError:continue
                return _omit_local_prose(value,branch,path)
            return value
    prose=(bool(path) and path[-1] in PROSE_FIELDS
           or len(path)>1 and isinstance(path[-1],int) and path[-2] in PROSE_FIELDS
           or len(path)==3 and path[:2]==('document','focus'))
    if prose and isinstance(value,str) and _has_local_path(value):
        try:_check(OMITTED_LOCAL_REFERENCE,schema)
        except ProjectionError:return value
        return OMITTED_LOCAL_REFERENCE
    if isinstance(value,dict):
        return {key:_omit_local_prose(entry,schema.get('properties',{}).get(key,schema.get('additionalProperties',{})),(*path,key))
                for key,entry in value.items()}
    if isinstance(value,list):return [_omit_local_prose(entry,schema.get('items',{}),(*path,index)) for index,entry in enumerate(value)]
    return value


def _url(value):
    if not isinstance(value, str) or re.search(r'[\x00-\x20\x7f\\]', value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
                and url.port in (None, 443) and not url.query and not url.fragment)
    except ValueError:
        return False


def encode(value: dict) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ProjectionError('snapshot.json') from error


def validate(value: object, *, secret_values=()) -> None:
    """Validate an upload before sending; the receiver independently enforces this contract."""
    secret_values = tuple(secret_values)
    if len(encode(value)) > MAX_BYTES:
        raise ProjectionError('snapshot.byte_bound')
    try:
        _check(value, SCHEMA)
        for text in _strings(value):
            if _has_local_path(text):
                raise ProjectionError('snapshot.local_path')
            if any(secret and secret in text for secret in secret_values):
                raise ProjectionError('snapshot.configured_secret')
        links = value['links']
        urls = [*links['issues'].values(), *links['commits'].values(),
                *(row['href'] for row in links['changes'].values())]
        if not all(_url(url) for url in urls):
            raise ProjectionError('snapshot.unsafe_link')
        subjects = {row['subject'] for name in ('queue', 'planned')
                    for row in value['document']['state'].get(name, [])}
        named = {row.get('runId') for name in ('queue', 'planned')
                 for row in value['document']['state'].get(name, [])}
        if not set(value['runs']) <= named or not set(value['reviews']) <= subjects:
            raise ProjectionError('snapshot.unrelated_evidence')
    except (RecursionError, KeyError, TypeError) as error:
        raise ProjectionError('snapshot.structure') from error


def project(document: dict, runs: dict, config: dict, *, source_fingerprint: str,
            plan: dict | None = None, needs: dict | None = None, secret_values=()) -> dict:
    """Allowlist one current delivery's shared bounded line-map data, never history."""
    named = render.named_runs(document)
    selected = {key: value for key, value in runs.items() if key in named}
    data = render.line_map_data(document, selected, config, theme_key='hosted-delivery', history=None,
                                plan=plan, needs=needs)
    # Keep the existing per-lens null/unavailable distinction without publishing
    # artifact text. A shortened source also uses the existing round-level
    # clipping note, which the trusted shared consumer already displays.
    for summary in [*data['runs'].values(), *data['reviews'].values()]:
        for row in summary.get('roundDetail', []):
            briefs = row.get('briefs')
            if not isinstance(briefs, dict):
                continue
            markers = {}
            shortened = False
            for lens, pair in briefs.items():
                markers[lens] = {}
                for part in ('brief', 'result'):
                    if not isinstance(pair, dict) or part not in pair:
                        continue
                    raw = pair[part]
                    marker = None if raw is None else {'unavailable': True}
                    if isinstance(raw, dict) and type(raw.get('shortened')) is bool:
                        marker['shortened'] = raw['shortened']
                        shortened = shortened or raw['shortened']
                    markers[lens][part] = marker
            row['briefs'] = markers
            if shortened:
                row.setdefault('clipped', {})['texts'] = True
    # Paths and parser diagnostics are local-only. Preserve the indicator, not the diagnostic.
    if isinstance(data.get('plan'), dict):
        if 'unreadable' in data['plan']:
            data['plan'] = {'unreadable': 'plan.json unavailable'}
        elif 'absent' in data['plan']:
            data['plan'] = {'absent': 'plan.json'}
    value = _project({'schemaVersion': 'hosted-delivery-snapshot/1',
                      'sourceFingerprint': source_fingerprint, **data}, SCHEMA)
    meta = value['document']['state'].get('meta', {})
    if 'planFile' in meta:
        meta['planFile'] = 'plan.json'
    public_plan = (value.get('plan') or {}).get('plan')
    if isinstance(public_plan, dict):
        # Optional provenance references may name local files. The review itself,
        # its candidate identities and verdicts stay in the public plan chapter.
        holders = [(public_plan.get('approved', {}), 'review')]
        holders.extend((change, key) for change in public_plan.get('changes', []) for key in ('source', 'ref'))
        for holder, key in holders:
            if isinstance(holder.get(key), str) and _has_local_path(holder[key]):
                del holder[key]
    subjects = {row['subject'] for name in ('queue', 'planned')
                for row in value['document']['state'].get(name, [])}
    parent = meta.get('trackerParent')
    for key in ('tickets',):
        if key in value['document']['state']:
            value['document']['state'][key] = {k: v for k, v in value['document']['state'][key].items()
                                               if k in subjects or k == parent}
    for key in ('stats', 'estimates'):
        if key in value['document']:
            value['document'][key] = {k: v for k, v in value['document'][key].items() if k in subjects}
    value['reviews'] = {key: row for key, row in value['reviews'].items() if key in subjects}
    # Known secrets still fail closed even when a path in the same field would
    # otherwise cause its prose to be omitted.
    secrets=tuple(secret_values)
    if any(secret and secret in text for text in _strings(value) for secret in secrets):
        raise ProjectionError('snapshot.configured_secret')
    value=_omit_local_prose(value,SCHEMA)
    validate(value, secret_values=secrets)
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
