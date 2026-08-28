from __future__ import annotations

import json
import re
from pathlib import Path

from .hosts.exposure import exposure_records
from .validate import canonical_json, safe_relative_path


RECEIPT_SCHEMA = "agent-skills-receipt/1"
OWNERSHIP = "agent-skills-managed"
DIGEST = re.compile(r"^[a-f0-9]{64}$")


class ReceiptError(ValueError):
    pass


def _validate_release(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "archiveSha256",
        "metadataSha256",
        "profile",
        "releaseId",
    }:
        raise ReceiptError("receipt-invalid: release identity is malformed")
    if not isinstance(value["releaseId"], str) or not value["releaseId"]:
        raise ReceiptError("receipt-invalid: release id is missing")
    if value["profile"] != "core":
        raise ReceiptError("receipt-invalid: unsupported profile")
    for field in ("archiveSha256", "metadataSha256"):
        if not isinstance(value[field], str) or DIGEST.fullmatch(value[field]) is None:
            raise ReceiptError(f"receipt-invalid: invalid {field}")


def _validate_files(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise ReceiptError("receipt-invalid: installed files are missing")
    paths: list[str] = []
    for record in value:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ReceiptError("receipt-invalid: installed file record is malformed")
        path = record["path"]
        digest = record["sha256"]
        if not safe_relative_path(path) or path == ".agent-skills-generation.json":
            raise ReceiptError("receipt-invalid: installed file path is unsafe")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise ReceiptError("receipt-invalid: installed file digest is malformed")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ReceiptError("receipt-invalid: installed files are not unique and sorted")


def validate_version(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"files", "generation", "release"}:
        raise ReceiptError("receipt-invalid: version record is malformed")
    generation = value["generation"]
    if not isinstance(generation, str) or DIGEST.fullmatch(generation) is None:
        raise ReceiptError("receipt-invalid: generation id is unsafe")
    _validate_release(value["release"])
    if value["release"]["archiveSha256"] != generation:
        raise ReceiptError("receipt-invalid: generation does not match archive")
    _validate_files(value["files"])
    return value


def validate_receipt(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "exposures",
        "files",
        "generation",
        "ownership",
        "previous",
        "release",
        "schemaVersion",
    }:
        raise ReceiptError("receipt-invalid: receipt shape is malformed")
    if value["schemaVersion"] != RECEIPT_SCHEMA:
        raise ReceiptError("receipt-invalid: unsupported receipt schema")
    if value["ownership"] != OWNERSHIP:
        raise ReceiptError("receipt-invalid: ownership marker is missing")
    if value["exposures"] != exposure_records():
        raise ReceiptError("receipt-invalid: host exposure metadata is malformed")
    validate_version(
        {
            "files": value["files"],
            "generation": value["generation"],
            "release": value["release"],
        }
    )
    if value["previous"] is not None:
        validate_version(value["previous"])
    return value


def load_receipt(path: Path) -> dict:
    try:
        contents = path.read_text(encoding="utf-8")
        value = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReceiptError("receipt-invalid: receipt is unreadable") from error
    validate_receipt(value)
    if canonical_json(value) != contents:
        raise ReceiptError("receipt-invalid: receipt is not canonical JSON")
    return value


def version_from_receipt(receipt: dict) -> dict:
    return {
        "files": receipt["files"],
        "generation": receipt["generation"],
        "release": receipt["release"],
    }


def make_receipt(version: dict, previous: dict | None) -> dict:
    validate_version(version)
    if previous is not None:
        validate_version(previous)
    receipt = {
        "exposures": exposure_records(),
        "files": version["files"],
        "generation": version["generation"],
        "ownership": OWNERSHIP,
        "previous": previous,
        "release": version["release"],
        "schemaVersion": RECEIPT_SCHEMA,
    }
    validate_receipt(receipt)
    return receipt
