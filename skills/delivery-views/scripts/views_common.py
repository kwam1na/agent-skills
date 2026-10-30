#!/usr/bin/env python3
"""What every delivery-views script shares: its configuration, links, bounded
commands, paths, the run cache and the round report format.

Standard library only. ``python3 -B views_common.py check --config <path>
--binding <path>`` validates a repository's configuration and prints where the
views will read and write. ``python3 -B views_common.py check-plan <path>``
validates a ``delivery-plan/1`` file, the plan chapter's one input.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


CONFIG_SPEC = "delivery-views-config/1"
REPORT_SPEC = "review-round-report/1"
RUNS_CACHE_SPEC = "delivery-views-runs/2"
DEFAULT_TITLE = "Delivery"
# The icons the pages ship; a lens with no icon shows its initial.
ICONS = frozenset({"bullseye", "spy", "shield", "bug", "gauge", "lock", "book"})
DEFAULT_LENSES = {
    "lens.outcome-correctness": {"name": "Outcome correctness", "icon": "bullseye"},
    "lens.adversarial-testing": {"name": "Adversarial testing", "icon": "spy"},
}
OUTPUT_LIMIT_BYTES = 16 * 1024 * 1024
# The state tool's default renderer timeout, and the margin a renderer keeps
# after its runs to write its page.
RENDERER_TIMEOUT_SECONDS = 60
RENDER_MARGIN_SECONDS = 5
MAX_RUNS_TIMEOUT_SECONDS = 600
MAX_KEY_PATTERN = 200
MAX_TEMPLATE = 500
MAX_NAME = 40
MAX_TITLE = 80
MAX_TEXT = 200
MAX_ARGUMENT = 4096

PROVIDER_ID = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
TREE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# C0 and C1 controls, plus the Unicode line and paragraph separators.
CONTROL = re.compile("[\u0000-\u001f\u007f-\u009f  ]")


class ConfigError(ValueError):
    """A refused configuration or input; the message starts with the key path or the file."""


class RunUnavailable(RuntimeError):
    """A bounded command gave no usable answer."""


# Configuration


def _object(value: object, where: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be an object")
    for key in sorted(value):
        if key not in allowed:
            raise ConfigError(f"{_join(where, key)}: unknown key")
    for key in sorted(required):
        if key not in value:
            raise ConfigError(f"{_join(where, key)}: is required")
    return value


def _join(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


def _text(value: object, where: str, cap: int) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: must be non-empty text")
    if len(value) > cap:
        raise ConfigError(f"{where}: holds {len(value)} characters, over the cap of {cap}")
    if CONTROL.search(value):
        raise ConfigError(f"{where}: holds a control character")
    return value


def _origin(url: str) -> tuple[str, str, int] | None:
    """A URL's scheme, lowercased host and port, with the default port made explicit."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
        return None
    return parts.scheme, parts.hostname.lower(), port if port is not None else 443


def _template(value: object, where: str, placeholders: set[str]) -> str:
    template = _text(value, where, MAX_TEMPLATE)
    for name in PLACEHOLDER.findall(template):
        if name not in placeholders:
            raise ConfigError(f"{where}: unknown placeholder {{{name}}}; this key takes "
                              + ", ".join(f"{{{known}}}" for known in sorted(placeholders)))
    sample = PLACEHOLDER.sub("x", template)
    scheme = urllib.parse.urlsplit(sample).scheme
    if scheme != "https":
        raise ConfigError(f"{where}: must be an https URL, not {scheme or 'a relative one'}")
    if _origin(sample) is None:
        raise ConfigError(f"{where}: must name a host")
    if _origin(sample) != _origin(PLACEHOLDER.sub("y", template)):
        raise ConfigError(f"{where}: the host must not hold a placeholder")
    return template


def _relative(value: object, where: str) -> str:
    text = _text(value, where, MAX_TEXT)
    path = PurePosixPath(text)
    if path.is_absolute() or "\\" in text or any(part in ("", "..") for part in text.split("/")):
        raise ConfigError(f"{where}: must be a relative path inside the repository")
    return text


def _list(value: object, where: str, *, non_empty: bool = False) -> list:
    if not isinstance(value, list) or (non_empty and not value):
        raise ConfigError(f"{where}: must be {'a non-empty' if non_empty else 'an'} array")
    return value


def validate_config(value: object) -> dict:
    """The configuration with its defaults filled in, or a ConfigError naming the key path."""
    config = _object(value, "", {"spec", "title", "tracker", "forges", "runs", "findings", "lenses", "scrubEnv",
                                 "exports"}, {"spec"})
    if config["spec"] != CONFIG_SPEC:
        raise ConfigError(f"spec: must be {CONFIG_SPEC}")
    result: dict = {"spec": CONFIG_SPEC, "title": _text(config.get("title", DEFAULT_TITLE), "title", MAX_TITLE)}

    tracker = None
    if "tracker" in config:
        raw = _object(config["tracker"], "tracker", {"name", "issueUrl", "keyPattern"}, {"name", "issueUrl"})
        tracker = {"name": _text(raw["name"], "tracker.name", MAX_NAME),
                   "issueUrl": _template(raw["issueUrl"], "tracker.issueUrl", {"key"}), "keyPattern": None}
        if "keyPattern" in raw:
            pattern = _text(raw["keyPattern"], "tracker.keyPattern", MAX_KEY_PATTERN)
            try:
                re.compile(pattern)
            except re.error as error:
                raise ConfigError(f"tracker.keyPattern: does not compile: {error}") from None
            tracker["keyPattern"] = pattern
    result["tracker"] = tracker

    forges = []
    for index, entry in enumerate(_list(config.get("forges", []), "forges")):
        where = f"forges[{index}]"
        raw = _object(entry, where, {"name", "repository", "pullUrl", "commitUrl"},
                      {"name", "repository", "pullUrl", "commitUrl"})
        repository = _text(raw["repository"], f"{where}.repository", MAX_TEXT)
        forges.append({
            "name": _text(raw["name"], f"{where}.name", MAX_NAME),
            "repository": repository,
            "pullUrl": _template(raw["pullUrl"], f"{where}.pullUrl", {"repository", "number"}),
            "commitUrl": _template(raw["commitUrl"], f"{where}.commitUrl", {"repository", "sha"}),
        })
    result["forges"] = forges

    runs = None
    if "runs" in config:
        raw = _object(config["runs"], "runs", {"argv", "timeoutSeconds"}, {"argv"})
        argv = _list(raw["argv"], "runs.argv", non_empty=True)
        for index, part in enumerate(argv):
            where = f"runs.argv[{index}]"
            _text(part, where, MAX_ARGUMENT)
            for name in PLACEHOLDER.findall(part):
                if name != "root":
                    raise ConfigError(f"{where}: unknown placeholder {{{name}}}; this key takes {{root}}")
        timeout = raw.get("timeoutSeconds", 10)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_RUNS_TIMEOUT_SECONDS:
            raise ConfigError(f"runs.timeoutSeconds: must be a whole number from 1 to {MAX_RUNS_TIMEOUT_SECONDS}")
        runs = {"argv": list(argv), "timeoutSeconds": timeout}
    result["runs"] = runs

    raw = _object(config.get("findings", {}), "findings", {"reportSpecs"})
    specs = _list(raw.get("reportSpecs", [REPORT_SPEC]), "findings.reportSpecs", non_empty=True)
    for index, spec in enumerate(specs):
        _text(spec, f"findings.reportSpecs[{index}]", 64)
    if len(set(specs)) != len(specs):
        raise ConfigError("findings.reportSpecs: names a spec twice")
    result["findings"] = {"reportSpecs": list(specs)}

    lenses = {lens: dict(entry) for lens, entry in DEFAULT_LENSES.items()}
    raw = config.get("lenses", {})
    if not isinstance(raw, dict):
        raise ConfigError("lenses: must be an object")
    for lens, entry in raw.items():
        where = f"lenses.{lens}"
        if not isinstance(lens, str) or len(lens) > 128 or not PROVIDER_ID.match(lens):
            raise ConfigError(f"{where}: is not a lens id")
        entry = _object(entry, where, {"name", "icon"}, {"name"})
        lenses[lens] = {"name": _text(entry["name"], f"{where}.name", MAX_NAME),
                        "icon": DEFAULT_LENSES.get(lens, {}).get("icon")}
        if "icon" in entry:
            if entry["icon"] not in ICONS:
                raise ConfigError(f"{where}.icon: must be one of {', '.join(sorted(ICONS))}")
            lenses[lens]["icon"] = entry["icon"]
    result["lenses"] = lenses

    scrub = _list(config.get("scrubEnv", []), "scrubEnv")
    for index, name in enumerate(scrub):
        if not isinstance(name, str) or not ENV_NAME.match(name) or len(name) > MAX_TEXT:
            raise ConfigError(f"scrubEnv[{index}]: must be an environment variable name")
    result["scrubEnv"] = list(scrub)

    exports = None
    if "exports" in config:
        raw = _object(config["exports"], "exports", {"directory", "ignore"}, {"directory"})
        ignore = _list(raw.get("ignore", []), "exports.ignore")
        exports = {"directory": _relative(raw["directory"], "exports.directory"),
                   "ignore": [_text(item, f"exports.ignore[{index}]", MAX_TEXT) for index, item in enumerate(ignore)]}
    result["exports"] = exports
    return result


# Links


def _segments(name: str, value: str) -> list[str]:
    # A repository keeps its single slash: owner/name.
    return value.split("/", 1) if name == "repository" else [value]


def _encode(name: str, value: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in _segments(name, value))


def fill(template: str, values: dict[str, str]) -> str:
    """The template with each placeholder's value percent-encoded."""
    return PLACEHOLDER.sub(lambda match: _encode(match.group(1), values[match.group(1)]), template)


def same_origin(url: str, template: str) -> bool:
    """Whether a URL's scheme, host and port equal the template's, ``:443`` being no port."""
    produced = _origin(url)
    return produced is not None and produced == _origin(PLACEHOLDER.sub("x", template))


def link(template: str, values: dict[str, str]) -> str | None:
    """The filled template, or None when it leaves the template's origin or a value is a dot
    segment, which a browser would follow even encoded; the value then renders as text."""
    if any(part in (".", "..") for name, value in values.items() for part in _segments(name, value)):
        return None
    url = fill(template, values)
    return url if same_origin(url, template) else None


def issue_link(key: str, tracker: dict | None) -> str | None:
    if tracker is None or not key:
        return None
    if tracker["keyPattern"] is not None and not re.fullmatch(tracker["keyPattern"], key):
        return None
    return link(tracker["issueUrl"], {"key": key})


def commit_link(sha: str, forge: dict | None) -> str | None:
    if forge is None or not COMMIT.match(sha):
        return None
    return link(forge["commitUrl"], {"repository": forge["repository"], "sha": sha})


def _pull_path(template: str) -> re.Pattern:
    path = urllib.parse.urlsplit(PLACEHOLDER.sub("\0\\1\0", template)).path
    pattern = ""
    seen: set[str] = set()
    for index, piece in enumerate(path.split("\0")):
        if index % 2 == 0:
            pattern += re.escape(piece)
        elif piece in seen:
            pattern += f"(?P={piece})"
        else:
            segment = r"(?:[A-Za-z0-9_.~-]|%[0-9A-F]{2})+"
            capture = f"{segment}/{segment}" if piece == "repository" else "[0-9]+"
            pattern += f"(?P<{piece}>{capture})"
            seen.add(piece)
    return re.compile(pattern)


def change_request_identity(value: str, forges: list[dict]) -> dict[str, str] | None:
    """Resolve a configured forge's pull path, retaining its actual repository."""
    if not forges or not isinstance(value, str) or CONTROL.search(value) or value != value.strip():
        return None
    first = forges[0]
    match = re.fullmatch(r"([^#\s]*)#([0-9]+)", value)
    if match:
        name, number = match.groups()
        if name in ("", first["repository"], first["repository"].rsplit("/", 1)[-1]):
            href = link(first["pullUrl"], {"repository": first["repository"], "number": number})
            if href:
                return {"href": href, "number": number, "repository": first["repository"]}
        return None
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parts.query or parts.fragment:
        return None
    for forge in forges:
        if not same_origin(value, forge["pullUrl"]):
            continue
        match = _pull_path(forge["pullUrl"]).fullmatch(parts.path)
        if not match or "number" not in match.groupdict():
            continue
        encoded_repository = match.groupdict().get("repository")
        repository = forge["repository"]
        if encoded_repository is not None:
            try:
                segments = [urllib.parse.unquote(segment, errors="strict")
                            for segment in encoded_repository.split("/")]
            except UnicodeDecodeError:
                continue
            if any(segment in (".", "..") or "/" in segment or "\\" in segment
                   or CONTROL.search(segment) for segment in segments):
                continue
            repository = "/".join(segments)
            # Match the existing template encoder exactly: reject alternate
            # spellings and escapes which browsers might normalize differently.
            if _encode("repository", repository) != encoded_repository:
                continue
        return {"href": value, "number": match.group("number"), "repository": repository}
    return None


def change_request_link(value: str, forges: list[dict]) -> str | None:
    """A short reference on the first repository, or a recognized forge pull URL."""
    identity = change_request_identity(value, forges)
    return identity["href"] if identity else None


# Bounded commands


def _process_group() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop(process: subprocess.Popen) -> None:
    """Stop a command and every process it started."""
    with contextlib.suppress(OSError):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def run_json(argv: list[str], *, timeout: float, scrub: list[str] = (), cwd: Path | None = None,
             limit: int = OUTPUT_LIMIT_BYTES) -> object:
    """Run a command in its own process group with stdin closed and the scrubbed variables
    removed, and return its stdout as JSON; RunUnavailable for a timeout, oversize output,
    a non-zero exit or output that is not JSON."""
    if timeout <= 0:
        raise RunUnavailable("no time left")
    removed = set(scrub)
    environment = {name: value for name, value in os.environ.items() if name not in removed}
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment, cwd=cwd, **_process_group())
    except OSError as error:
        raise RunUnavailable(f"could not start: {error.strerror or error}") from None
    kept = bytearray()
    over = threading.Event()

    def read() -> None:
        with contextlib.suppress(OSError, ValueError):
            while True:
                chunk = process.stdout.read1(65536)
                if not chunk:
                    return
                kept.extend(chunk)
                if len(kept) > limit:
                    over.set()
                    _stop(process)
                    return

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        try:
            code = process.wait(timeout)
        except subprocess.TimeoutExpired:
            _stop(process)
            raise RunUnavailable(f"timed out after {timeout:g}s") from None
        # The command has exited; anything it left behind holding stdout open is stopped.
        reader.join(1)
        if reader.is_alive():
            _stop(process)
            reader.join(1)
    finally:
        _stop(process)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(1)
        reader.join(1)
        # A descendant that left the group may still hold stdout; closing it would
        # wait for that descendant, so the daemon reader is left behind instead.
        if not reader.is_alive():
            with contextlib.suppress(OSError):
                process.stdout.close()
    if over.is_set():
        raise RunUnavailable("output over 16 MiB")
    if code != 0:
        raise RunUnavailable(f"exited with {code}")
    try:
        return json.loads(bytes(kept).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise RunUnavailable("output is not JSON") from None


def runs_argv(config: dict, root: Path) -> list[str] | None:
    """The configured runs command with ``{root}`` filled, or None when none is configured."""
    if config["runs"] is None:
        return None
    return [part.replace("{root}", str(root)) for part in config["runs"]["argv"]]


def runs_deadline(binding: dict, script: str, requested: float) -> float:
    """The runs deadline: the requested seconds, capped at the declared renderer's timeout less a margin."""
    timeout = RENDERER_TIMEOUT_SECONDS
    state = binding.get("state") if isinstance(binding, dict) else None
    renderers = state.get("renderers") if isinstance(state, dict) else None
    for renderer in renderers if isinstance(renderers, list) else []:
        argv = renderer.get("argv") if isinstance(renderer, dict) else None
        if isinstance(argv, list) and any(isinstance(part, str) and PurePosixPath(part).name == script
                                          for part in argv):
            declared = renderer.get("timeoutSeconds", RENDERER_TIMEOUT_SECONDS)
            if isinstance(declared, int) and not isinstance(declared, bool):
                timeout = declared
            break
    return max(0, min(requested, timeout - RENDER_MARGIN_SECONDS))


# Paths


@dataclass(frozen=True)
class Inputs:
    root: Path
    config: dict
    binding: dict
    state_directory: Path


def repository_root(cwd: Path | None = None) -> Path:
    """The repository top level of the working directory."""
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConfigError(f"the repository top level could not be read: {error}") from None
    if result.returncode != 0 or not result.stdout.strip():
        raise ConfigError("start inside the repository: the working directory is not in a Git repository")
    return Path(result.stdout.strip()).resolve()


def _read_json(root: Path, given: str | None, option: str) -> dict:
    if not given:
        raise ConfigError(f"{option}: is required")
    path = root / given
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{option} {given}: not found under {root}") from None
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{option} {given}: could not be read: {error}") from None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{option} {given}: is not JSON: {error}") from None
    if not isinstance(value, dict):
        raise ConfigError(f"{option} {given}: must be a JSON object")
    return value


def load_inputs(config: str | None, binding: str | None, cwd: Path | None = None,
                state_dir: str | None = None) -> Inputs:
    """The validated configuration and the binding, both resolved against the repository top level.

    ``state_dir``, when given, is the record being drawn and wins over the binding's ``state.directory``."""
    root = repository_root(cwd)
    document = _read_json(root, config, "--config")
    bound = _read_json(root, binding, "--binding")
    directory = (bound.get("state") or {}).get("directory") if isinstance(bound.get("state"), dict) else None
    if not isinstance(directory, str) or not directory:
        raise ConfigError(f"--binding {binding}: state.directory is missing")
    try:
        validated = validate_config(document)
    except ConfigError as error:
        raise ConfigError(f"--config {config}: {error}") from None
    return Inputs(root, validated, bound, (root / (state_dir or directory)).resolve())


def add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="the delivery-views configuration, relative to the repository top level")
    parser.add_argument("--binding", help="the orchestration binding, relative to the repository top level")
    parser.add_argument("--state-dir", help="the state directory being drawn, relative to the repository top level unless absolute, "
                             "over the binding's state.directory")


# Cache and storage


def runs_cache_path(state_directory: Path) -> Path:
    return state_directory / ".cache" / "delivery-views" / "runs.json"


def theme_key(state_directory: Path) -> str:
    digest = hashlib.sha256(str(Path(state_directory).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"delivery-views:{digest}:theme"


def read_runs_cache(path: Path) -> dict:
    """The cached runs, or an empty cache when it is absent, corrupt or of another spec."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return {}
    if not isinstance(value, dict) or value.get("spec") != RUNS_CACHE_SPEC or not isinstance(value.get("runs"), dict):
        return {}
    return value["runs"]


def write_runs_cache(path: Path, runs: dict) -> None:
    """Write the cache atomically; an unwritable cache only costs a re-read."""
    data = json.dumps({"spec": RUNS_CACHE_SPEC, "runs": runs}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".runs-", suffix=".tmp")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
        temporary = None
    except OSError:
        pass
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


# Round reports


def _id_problem(value: object, at: str, before_round: int | None = None, expected: str | None = None) -> str | None:
    if expected is not None:
        return None if value == expected else f"{at}: must be {expected}"
    match = re.fullmatch(r"r([1-9][0-9]*)-f([1-9][0-9]*)", value) if isinstance(value, str) else None
    if not match or int(match.group(1)) >= before_round:
        return f"{at}: must name a finding of an earlier round, r<round>-f<k>"
    return None


def _issue_problem(value: object, at: str, deferred: bool) -> str | None:
    if deferred:
        if isinstance(value, str) and 1 <= len(value) <= 64 and not CONTROL.search(value):
            return None
        return f"{at}: is required as text of 1 to 64 characters exactly when the state is deferred"
    return None if value is None else f"{at}: must be null unless the state is deferred"


def report_problem(value: object, spec: str = REPORT_SPEC) -> str | None:
    """The first problem with a round report, starting with its key path, or None when it is valid."""
    keys = {"spec", "runId", "ticket", "round", "roundId", "candidateTreeSha", "closedAt", "raised", "resolved"}
    if not isinstance(value, dict):
        return "report: must be an object"
    for key in sorted(value):
        if key not in keys | {"briefs"}:
            return f"{key}: unknown key"
    for key in sorted(keys):
        if key not in value:
            return f"{key}: is required"
    if value["spec"] != spec:
        return f"spec: must be {spec}"
    for key, cap in (("runId", 128), ("ticket", 64), ("roundId", 128)):
        text = value[key]
        if key == "ticket" and text is None:
            continue
        if not isinstance(text, str) or not 1 <= len(text) <= cap or CONTROL.search(text):
            return f"{key}: must be text of 1 to {cap} characters" + (" or null" if key == "ticket" else "")
    round_number = value["round"]
    if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number < 1:
        return "round: must be a whole number from 1"
    if not isinstance(value["candidateTreeSha"], str) or not TREE.match(value["candidateTreeSha"]):
        return "candidateTreeSha: must be a lowercase hex object id"
    if not isinstance(value["closedAt"], str) or not TIMESTAMP.match(value["closedAt"]):
        return "closedAt: must be a UTC timestamp"
    raised, resolved = value["raised"], value["resolved"]
    if not isinstance(raised, list) or len(raised) > 50:
        return "raised: must be an array of at most 50 findings"
    for index, finding in enumerate(raised):
        at = f"raised[{index}]"
        if not isinstance(finding, dict) or set(finding) != {"id", "lens", "severity", "summary", "state",
                                                              "deferredIssue"}:
            return f"{at}: must hold exactly id, lens, severity, summary, state and deferredIssue"
        problem = _id_problem(finding["id"], f"{at}.id", expected=f"r{round_number}-f{index + 1}")
        if problem:
            return problem
        lens = finding["lens"]
        if not isinstance(lens, str) or len(lens) > 128 or not PROVIDER_ID.match(lens):
            return f"{at}.lens: is not a lens id"
        if finding["severity"] not in ("P0", "P1", "P2", "P3"):
            return f"{at}.severity: must be P0, P1, P2 or P3"
        summary = finding["summary"]
        if not isinstance(summary, str) or not 1 <= len(summary) <= 280 or CONTROL.search(summary):
            return f"{at}.summary: must be 1 to 280 code points with no control character or line break"
        if finding["state"] not in ("open", "deferred", "dropped"):
            return f"{at}.state: must be open, deferred or dropped"
        problem = _issue_problem(finding["deferredIssue"], f"{at}.deferredIssue", finding["state"] == "deferred")
        if problem:
            return problem
    if not isinstance(resolved, list):
        return "resolved: must be an array"
    seen: set[str] = set()
    for index, resolution in enumerate(resolved):
        at = f"resolved[{index}]"
        if not isinstance(resolution, dict) or set(resolution) != {"id", "state", "fixCommit", "deferredIssue"}:
            return f"{at}: must hold exactly id, state, fixCommit and deferredIssue"
        problem = _id_problem(resolution["id"], f"{at}.id", before_round=round_number)
        if problem:
            return problem
        if resolution["id"] in seen:
            return f"{at}.id: is resolved twice"
        seen.add(resolution["id"])
        state = resolution["state"]
        if state not in ("fixed", "dropped", "deferred"):
            return f"{at}.state: must be fixed, dropped or deferred"
        commit = resolution["fixCommit"]
        if (state == "fixed") != (isinstance(commit, str) and bool(COMMIT.match(commit))) or (
                state != "fixed" and commit is not None):
            return f"{at}.fixCommit: is required as 40 lowercase hex characters exactly when the state is fixed"
        problem = _issue_problem(resolution["deferredIssue"], f"{at}.deferredIssue", state == "deferred")
        if problem:
            return problem
    if "briefs" in value:
        briefs = value["briefs"]
        if not isinstance(briefs, dict):
            return "briefs: must be an object"
        for lens, entry in briefs.items():
            at = f"briefs.{lens}"
            if len(lens) > 128 or not PROVIDER_ID.match(lens):
                return f"{at}: is not a lens id"
            if not isinstance(entry, dict) or set(entry) != {"brief", "result"}:
                return f"{at}: must hold exactly brief and result"
            for key in ("brief", "result"):
                if entry[key] is not None and not (isinstance(entry[key], str) and ARTIFACT_ID.match(entry[key])):
                    return f"{at}.{key}: must be an artifact id or null"
    return None


# The plan chapter's input


PLAN_SPEC = "delivery-plan/1"
PLAN_LIMIT_BYTES = 1024 * 1024
PLAN_OUTCOME_TEXT, PLAN_TEXT, PLAN_LABEL = 2000, 600, 200
PLAN_UNITS, PLAN_CRITERIA, PLAN_CHANGES, PLAN_FINISH = 200, 1000, 500, 30
# Bounds the contract leaves to the validator: kinds, lenses, approvals, out-of-scope texts, items per proof.
PLAN_LIST = 100
PLAN_RECORD_KINDS = ("handed", "merged", "review")
PLAN_CHANGE_KINDS = ("added", "removed", "relaxed", "changed", "finish")
PLAN_KEYS = ("schemaVersion", "updated", "outcome", "requiredLenses", "proofKinds", "units", "finish", "approved",
             "changes", "evidence", "approvals", "outOfScope")
PLAN_REQUIRED = ("schemaVersion", "updated", "outcome", "requiredLenses", "proofKinds", "units", "finish", "approved",
                 "changes", "evidence")
PLAN_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _plan_object(value: object, where: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    """Like _object, but an unknown key is not named: it is the file's text, and the message reaches the page."""
    if not isinstance(value, dict):
        raise ConfigError(f"{where or '(top level)'}: must be an object")
    if any(key not in allowed for key in value):
        raise ConfigError(f"{where or '(top level)'}: holds an unknown key")
    for key in sorted(required):
        if key not in value:
            raise ConfigError(f"{_join(where, key)}: is required")
    return value


def _bounded(value: object, where: str, cap: int) -> list:
    items = _list(value, where)
    if len(items) > cap:
        raise ConfigError(f"{where}: holds {len(items)} entries, over the cap of {cap}")
    return items


def _plan_id(value: object, where: str) -> str:
    if not isinstance(value, str) or not PLAN_ID.fullmatch(value):
        raise ConfigError(f"{where}: must be an id of 1 to 64 letters, digits, dots, dashes or underscores")
    return value


def _plan_time(value: object, where: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ConfigError(f"{where}: must be a UTC time such as 2026-01-01T00:00:00Z")
    return value


def _plan_units(value: object, where: str, kinds: dict[str, str]) -> set[str]:
    """Validate a unit list; return its criterion ids, each unique across the list."""
    criteria: set[str] = set()
    subjects: set[str] = set()
    count = 0
    for i, unit in enumerate(_bounded(value, where, PLAN_UNITS)):
        at = f"{where}[{i}]"
        _plan_object(unit, at, {"subject", "label", "title", "removed", "criteria", "proofs"},
                {"subject", "title", "criteria", "proofs"})
        subject = _text(unit["subject"], f"{at}.subject", PLAN_LABEL)
        if subject in subjects:
            raise ConfigError(f"{at}.subject: repeats another unit's subject")
        subjects.add(subject)
        if "label" in unit:
            _text(unit["label"], f"{at}.label", PLAN_LABEL)
        _text(unit["title"], f"{at}.title", PLAN_TEXT)
        if "removed" in unit and not isinstance(unit["removed"], bool):
            raise ConfigError(f"{at}.removed: must be true or false")
        for j, criterion in enumerate(_list(unit["criteria"], f"{at}.criteria")):
            spot = f"{at}.criteria[{j}]"
            _plan_object(criterion, spot, {"id", "text"}, {"id", "text"})
            ident = _plan_id(criterion["id"], f"{spot}.id")
            if ident in criteria:
                raise ConfigError(f"{spot}.id: repeats a criterion id")
            criteria.add(ident)
            _text(criterion["text"], f"{spot}.text", PLAN_TEXT)
        count += len(unit["criteria"])
        if count > PLAN_CRITERIA:
            raise ConfigError(f"{where}: holds over the cap of {PLAN_CRITERIA} criteria")
        owed = _bounded(unit["proofs"], f"{at}.proofs", PLAN_LIST)
        for j, kind in enumerate(owed):
            if not isinstance(kind, str) or kind not in kinds:
                raise ConfigError(f"{at}.proofs[{j}]: names no declared proof kind")
        if len(set(owed)) != len(owed):
            raise ConfigError(f"{at}.proofs: repeats a proof kind")
    return criteria


def _plan_covers(value: object, where: str, criteria: set[str], kinds: dict[str, str]) -> None:
    _plan_object(value, where, {"criteria", "proofs"}, {"criteria", "proofs"})
    covered = value["criteria"]
    if covered != "all":
        for i, ident in enumerate(_bounded(covered, f"{where}.criteria", PLAN_CRITERIA)):
            if not isinstance(ident, str) or ident not in criteria:
                raise ConfigError(f"{where}.criteria[{i}]: names no criterion of the plan's units")
    for i, kind in enumerate(_bounded(value["proofs"], f"{where}.proofs", PLAN_LIST)):
        if not isinstance(kind, str) or kind not in kinds:
            raise ConfigError(f"{where}.proofs[{i}]: names no declared proof kind")


def _plan_finish(value: object, where: str, criteria: set[str], kinds: dict[str, str]) -> None:
    seen: set[str] = set()
    for i, line in enumerate(_bounded(value, where, PLAN_FINISH)):
        at = f"{where}[{i}]"
        _plan_object(line, at, {"id", "label", "text", "at", "covers"}, {"id", "label", "text", "at", "covers"})
        ident = _plan_id(line["id"], f"{at}.id")
        if ident in seen:
            raise ConfigError(f"{at}.id: repeats a finish line id")
        seen.add(ident)
        _text(line["label"], f"{at}.label", PLAN_LABEL)
        _text(line["text"], f"{at}.text", PLAN_TEXT)
        _plan_time(line["at"], f"{at}.at")
        _plan_covers(line["covers"], f"{at}.covers", criteria, kinds)


def validate_plan(value: object) -> dict:
    """A `delivery-plan/1` document, unchanged, or a ConfigError naming the key path and echoing no value."""
    plan = _plan_object(value, "", set(PLAN_KEYS), set(PLAN_REQUIRED))
    if plan["schemaVersion"] != PLAN_SPEC:
        raise ConfigError(f"schemaVersion: must be {PLAN_SPEC}")
    _plan_time(plan["updated"], "updated")
    outcome = _plan_object(plan["outcome"], "outcome", {"summary", "text"}, {"summary", "text"})
    _text(outcome["summary"], "outcome.summary", PLAN_TEXT)
    _text(outcome["text"], "outcome.text", PLAN_OUTCOME_TEXT)
    lenses = _bounded(plan["requiredLenses"], "requiredLenses", PLAN_LIST)
    for i, lens in enumerate(lenses):
        _plan_id(lens, f"requiredLenses[{i}]")
    kinds: dict[str, str] = {}
    for i, kind in enumerate(_bounded(plan["proofKinds"], "proofKinds", PLAN_LIST)):
        at = f"proofKinds[{i}]"
        _plan_object(kind, at, {"id", "label", "source"}, {"id", "label", "source"})
        ident = _plan_id(kind["id"], f"{at}.id")
        if kind["source"] not in ("record", "producer"):
            raise ConfigError(f"{at}.source: must be record or producer")
        if kind["source"] == "record" and ident not in PLAN_RECORD_KINDS:
            raise ConfigError(f"{at}.id: a record kind must be one of {', '.join(PLAN_RECORD_KINDS)}")
        if ident in kinds:
            raise ConfigError(f"{at}.id: repeats a proof kind")
        _text(kind["label"], f"{at}.label", PLAN_LABEL)
        kinds[ident] = kind["source"]
    criteria = _plan_units(plan["units"], "units", kinds)
    _plan_finish(plan["finish"], "finish", criteria, kinds)
    approved = _plan_object(plan["approved"], "approved", {"at", "review", "snapshot"}, {"at", "snapshot"})
    _plan_time(approved["at"], "approved.at")
    if "review" in approved:
        _text(approved["review"], "approved.review", PLAN_TEXT)
    snapshot = _plan_object(approved["snapshot"], "approved.snapshot", {"units", "finish"}, {"units", "finish"})
    frozen = _plan_units(snapshot["units"], "approved.snapshot.units", kinds)
    _plan_finish(snapshot["finish"], "approved.snapshot.finish", frozen, kinds)
    seen: set[str] = set()
    for i, change in enumerate(_bounded(plan["changes"], "changes", PLAN_CHANGES)):
        at = f"changes[{i}]"
        _plan_object(change, at, {"id", "at", "kind", "subjects", "criteria", "by", "why", "title", "source", "ref",
                             "before", "after"}, {"id", "at", "kind", "subjects", "by"})
        ident = _plan_id(change["id"], f"{at}.id")
        if ident in seen:
            raise ConfigError(f"{at}.id: repeats a change id")
        seen.add(ident)
        _plan_time(change["at"], f"{at}.at")
        if change["kind"] not in PLAN_CHANGE_KINDS:
            raise ConfigError(f"{at}.kind: must be one of {', '.join(PLAN_CHANGE_KINDS)}")
        for j, subject in enumerate(_bounded(change["subjects"], f"{at}.subjects", PLAN_UNITS)):
            _text(subject, f"{at}.subjects[{j}]", PLAN_LABEL)
        for j, ident in enumerate(_bounded(change.get("criteria", []), f"{at}.criteria", PLAN_CRITERIA)):
            _plan_id(ident, f"{at}.criteria[{j}]")
        _text(change["by"], f"{at}.by", PLAN_LABEL)
        for key, cap in (("why", PLAN_TEXT), ("title", PLAN_TEXT), ("source", PLAN_LABEL), ("ref", PLAN_TEXT)):
            if key in change:
                _text(change[key], f"{at}.{key}", cap)
        for key in ("before", "after"):
            if key in change:
                if isinstance(change[key], dict):
                    _plan_object(change[key], f"{at}.{key}", {"criteria", "proofs"}, {"criteria", "proofs"})
                    covered = change[key]["criteria"]
                    if covered != "all":
                        for j, ident in enumerate(_bounded(covered, f"{at}.{key}.criteria", PLAN_CRITERIA)):
                            _plan_id(ident, f"{at}.{key}.criteria[{j}]")
                    for j, kind in enumerate(_bounded(change[key]["proofs"], f"{at}.{key}.proofs", PLAN_LIST)):
                        _plan_id(kind, f"{at}.{key}.proofs[{j}]")
                else:
                    _text(change[key], f"{at}.{key}", PLAN_TEXT)
    owed = {unit["subject"]: set(unit["proofs"]) for unit in plan["units"]}
    evidence = plan["evidence"]
    if not isinstance(evidence, dict) or not all(isinstance(entry, dict) for entry in evidence.values()):
        raise ConfigError("evidence: must map subjects to objects")
    for subject in sorted(evidence):
        at = _join("evidence", subject)
        if subject not in owed:
            raise ConfigError("evidence: names a subject that is no unit of the plan")
        for kind in sorted(evidence[subject]):
            if kinds.get(kind) != "producer" or kind not in owed[subject]:
                raise ConfigError(f"{at}: names a kind that is no producer kind the unit owes")
            spot = _join(at, kind)
            entry = _plan_object(evidence[subject][kind], spot, {"state", "head", "at", "reason", "items"}, {"state"})
            if entry["state"] not in ("done", "todo", "waived"):
                raise ConfigError(f"{spot}.state: must be done, todo or waived")
            if entry["state"] in ("done", "waived") and "head" not in entry:
                raise ConfigError(f"{spot}.head: is required when the state is {entry['state']}")
            if "head" in entry and (not isinstance(entry["head"], str) or not TREE.fullmatch(entry["head"])):
                raise ConfigError(f"{spot}.head: must be a full commit id")
            if entry["state"] == "waived" and "reason" not in entry:
                raise ConfigError(f"{spot}.reason: is required when the state is waived")
            if "reason" in entry:
                _text(entry["reason"], f"{spot}.reason", PLAN_TEXT)
            if "at" in entry:
                _plan_time(entry["at"], f"{spot}.at")
            for j, item in enumerate(_bounded(entry.get("items", []), f"{spot}.items", PLAN_LIST)):
                _text(item, f"{spot}.items[{j}]", PLAN_LABEL)
    for i, approval in enumerate(_bounded(plan.get("approvals", []), "approvals", PLAN_LIST)):
        at = f"approvals[{i}]"
        _plan_object(approval, at, {"text", "state", "who"}, {"text", "state"})
        _text(approval["text"], f"{at}.text", PLAN_TEXT)
        if approval["state"] not in ("done", "waiting"):
            raise ConfigError(f"{at}.state: must be done or waiting")
        if "who" in approval:
            _text(approval["who"], f"{at}.who", PLAN_LABEL)
    for i, text in enumerate(_bounded(plan.get("outOfScope", []), "outOfScope", PLAN_LIST)):
        _text(text, f"outOfScope[{i}]", PLAN_TEXT)
    return plan


def read_plan(path: Path) -> dict:
    """The validated plan at `path`. A file over 1 MiB is refused unread; nothing from the file is echoed."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ConfigError(f"{path}: doesn't exist") from None
    except OSError:
        raise ConfigError(f"{path}: couldn't be read") from None
    if size > PLAN_LIMIT_BYTES:
        raise ConfigError(f"{path}: holds {size} bytes, over the 1 MiB a plan may hold")
    try:
        data = path.read_bytes()
    except OSError:
        raise ConfigError(f"{path}: couldn't be read") from None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"{path}: isn't JSON (at line {getattr(error, 'lineno', '?')})") from None
    except RecursionError:
        raise ConfigError(f"{path}: nests too deeply to read") from None
    return validate_plan(value)


# Command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add_input_arguments(commands.add_parser("check", help="validate the configuration and binding"))
    checking = commands.add_parser("check-plan", help=f"validate a {PLAN_SPEC} file, as the line map reads it")
    checking.add_argument("plan", help="the plan file, usually <state directory>/plan.json")
    args = parser.parse_args(argv)
    if args.command == "check-plan":
        try:
            plan = read_plan(Path(args.plan))
        except ConfigError as error:
            print(f"delivery-views: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, "plan": args.plan, "units": len(plan["units"]),
                          "criteria": sum(len(unit["criteria"]) for unit in plan["units"])}, indent=2))
        return 0
    try:
        inputs = load_inputs(args.config, args.binding, state_dir=args.state_dir)
    except ConfigError as error:
        print(f"delivery-views: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # One line, never a traceback.
        print(f"delivery-views: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "root": str(inputs.root), "stateDirectory": str(inputs.state_directory),
                      "runsCache": str(runs_cache_path(inputs.state_directory))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
