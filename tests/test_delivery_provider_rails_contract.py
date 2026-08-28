from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "delivery-provider-rails.schema.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "delivery-provider-rails-v1.json"
SPECIFICATION_PATH = ROOT / "docs" / "delivery-provider-rails-v1.md"
CONTRACT_VERSION = "delivery-provider-rails/1"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _has_exact_fields(
    message: object,
    required: set[str],
    optional: set[str] = frozenset(),
) -> bool:
    return (
        isinstance(message, dict)
        and required <= set(message)
        and set(message) <= required | optional
    )


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 64


def _is_versioned(message: dict[str, object]) -> bool:
    return message.get("version") == CONTRACT_VERSION and _is_identifier(
        message.get("requestId")
    )


def _valid_request(message: object) -> bool:
    if not _has_exact_fields(
        message,
        {"kind", "version", "requestId", "idempotencyKey", "payload"},
    ):
        return False
    assert isinstance(message, dict)
    return (
        message["kind"] == "request"
        and _is_versioned(message)
        and _is_identifier(message["idempotencyKey"])
        and isinstance(message["payload"], dict)
    )


def _valid_cancel(message: object) -> bool:
    if not _has_exact_fields(
        message,
        {"kind", "version", "requestId", "cancellationId"},
        {"reason"},
    ):
        return False
    assert isinstance(message, dict)
    return (
        message["kind"] == "cancel"
        and _is_versioned(message)
        and _is_identifier(message["cancellationId"])
        and ("reason" not in message or _is_identifier(message["reason"]))
    )


def _valid_event(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    common = {"kind", "version", "requestId", "sequence", "summary"}
    optional_by_kind = {
        "progress": {"details"},
        "evidence": {"evidenceId", "details"},
        "blocker": {"blockerId", "action", "details"},
        "terminal": {"outcome", "result", "action", "details"},
    }
    kind = message.get("kind")
    if kind not in optional_by_kind or not _has_exact_fields(
        message, common, optional_by_kind[kind]
    ):
        return False
    if not (
        _is_versioned(message)
        and isinstance(message.get("sequence"), int)
        and message["sequence"] >= 1
        and _is_identifier(message.get("summary"))
    ):
        return False
    if kind == "evidence" and not _is_identifier(message.get("evidenceId")):
        return False
    if kind == "blocker" and not _is_identifier(message.get("blockerId")):
        return False
    if kind == "terminal" and message.get("outcome") not in {
        "success",
        "blocked",
        "failed",
        "cancelled",
        "indeterminate",
    }:
        return False
    for field in ("details", "result"):
        if field in message and not isinstance(message[field], dict):
            return False
    return "action" not in message or _is_identifier(message["action"])


@dataclass(frozen=True)
class ProviderResult:
    status: str
    execution_count: int
    duplicate_count: int
    cancellation_count: int


class FakeProvider:
    """Inbound-only conformance double; it does not read expected consumer state."""

    def consume(self, messages: list[object]) -> ProviderResult:
        negotiated = False
        status = "malformed"
        requests: dict[str, str] = {}
        cancellations: set[str] = set()
        execution_count = 0
        duplicate_count = 0
        cancellation_count = 0
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
                status = "malformed"
                break
            if message["kind"] == "negotiate":
                if not _has_exact_fields(message, {"kind", "supportedVersions"}) or not isinstance(
                    message["supportedVersions"], list
                ):
                    status = "malformed"
                    break
                negotiated = CONTRACT_VERSION in message["supportedVersions"]
                status = "supported" if negotiated else "unsupported"
                if not negotiated:
                    break
            elif message["kind"] == "request":
                if not negotiated or not _valid_request(message):
                    status = "malformed"
                    break
                identity = f'{message["requestId"]}:{message["idempotencyKey"]}'
                encoded = _canonical(message)
                prior = requests.get(identity)
                if prior is None:
                    requests[identity] = encoded
                    execution_count += 1
                    status = "accepted"
                elif prior == encoded:
                    duplicate_count += 1
                else:
                    status = "malformed"
                    break
            elif message["kind"] == "cancel":
                if not negotiated or not _valid_cancel(message):
                    status = "malformed"
                    break
                cancellation_id = str(message["cancellationId"])
                if cancellation_id in cancellations:
                    duplicate_count += 1
                else:
                    cancellations.add(cancellation_id)
                    cancellation_count += 1
                status = "cancelled"
            else:
                status = "malformed"
                break
        return ProviderResult(
            status,
            execution_count,
            duplicate_count,
            cancellation_count,
        )


@dataclass(frozen=True)
class ConsumerResult:
    status: str
    accepted_count: int
    duplicate_count: int
    rejected_count: int


class FakeConsumer:
    """Outbound-only conformance double; it does not read expected provider state."""

    def consume(
        self,
        messages: list[object],
        *,
        interrupted: bool = False,
        after_interruption: list[object] | None = None,
    ) -> ConsumerResult:
        negotiated = False
        status = "malformed"
        terminal = False
        seen: dict[int, str] = {}
        accepted_count = 0
        duplicate_count = 0
        rejected_count = 0

        def accept(message: object) -> None:
            nonlocal negotiated, status, terminal
            nonlocal accepted_count, duplicate_count, rejected_count
            if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
                status = "malformed"
                return
            if message["kind"] == "negotiation":
                if not _has_exact_fields(
                    message,
                    {"kind", "outcome", "selectedVersion", "supportedVersions"},
                ):
                    status = "malformed"
                    return
                if message["supportedVersions"] != [CONTRACT_VERSION]:
                    status = "malformed"
                    return
                if message["outcome"] == "supported" and message["selectedVersion"] == CONTRACT_VERSION:
                    negotiated = True
                    status = "supported"
                elif message["outcome"] == "unsupported" and message["selectedVersion"] is None:
                    status = "unsupported"
                else:
                    status = "malformed"
                return
            if not negotiated or not _valid_event(message):
                status = "malformed"
                return
            encoded = _canonical(message)
            sequence = int(message["sequence"])
            if sequence in seen:
                if seen[sequence] == encoded:
                    duplicate_count += 1
                else:
                    status = "malformed"
                return
            if terminal:
                rejected_count += 1
                return
            expected = max(seen, default=0) + 1
            if sequence != expected:
                status = "malformed"
                return
            seen[sequence] = encoded
            accepted_count += 1
            if message["kind"] == "terminal":
                status = str(message["outcome"])
                terminal = True

        for item in messages:
            accept(item)
        if interrupted and negotiated and not terminal and status != "malformed":
            status = "indeterminate"
            terminal = True
        for item in after_interruption or []:
            accept(item)
        return ConsumerResult(status, accepted_count, duplicate_count, rejected_count)


class DeliveryProviderRailsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_schema_is_one_closed_versioned_message_union(self) -> None:
        self.assertEqual(
            self.schema["$id"],
            "urn:delivery-provider-rails:schema:1",
        )
        refs = {item["$ref"] for item in self.schema["oneOf"]}
        self.assertEqual(
            refs,
            {
                f"#/$defs/{name}"
                for name in (
                    "cancel",
                    "evidence",
                    "negotiate",
                    "negotiation",
                    "progress",
                    "request",
                    "blocker",
                    "terminal",
                )
            },
        )
        for reference in refs:
            definition = self.schema["$defs"][reference.rsplit("/", 1)[-1]]
            self.assertFalse(definition.get("additionalProperties", True), reference)
        self.assertEqual(self.schema["$defs"]["version"]["const"], CONTRACT_VERSION)
        self.assertEqual(
            set(self.schema["$defs"]["terminal"]["properties"]["outcome"]["enum"]),
            {"success", "blocked", "failed", "cancelled", "indeterminate"},
        )

    def test_shared_fixture_covers_required_cases_and_all_event_outcomes(self) -> None:
        self.assertEqual(self.fixture["contractVersion"], CONTRACT_VERSION)
        cases = {case["id"]: case for case in self.fixture["cases"]}
        for required in (
            "supported-complete",
            "unsupported-negotiation",
            "malformed-request",
            "malformed-event",
            "duplicate-request",
            "duplicate-event",
            "interrupted-attempt",
            "cancelled-then-late-success",
            "indeterminate-then-late-success",
            "terminal-blocked",
            "terminal-failed",
            "terminal-indeterminate",
        ):
            self.assertIn(required, cases)
        terminal_outcomes = {
            message["outcome"]
            for case in cases.values()
            for message in case["outbound"]
            if isinstance(message, dict) and message.get("kind") == "terminal"
        }
        self.assertEqual(
            terminal_outcomes,
            {"success", "blocked", "failed", "cancelled", "indeterminate"},
        )
        event_kinds = {
            message.get("kind")
            for case in cases.values()
            for message in case["outbound"]
            if isinstance(message, dict)
        }
        self.assertTrue({"progress", "evidence", "blocker"} <= event_kinds)

    def test_one_fake_provider_independently_conforms_to_shared_cases(self) -> None:
        provider = FakeProvider()
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                actual = provider.consume(case["inbound"])
                expected = case["expected"]["provider"]
                self.assertEqual(actual.status, expected["status"])
                self.assertEqual(actual.execution_count, expected["executionCount"])
                self.assertEqual(actual.duplicate_count, expected["duplicateCount"])
                self.assertEqual(actual.cancellation_count, expected["cancellationCount"])

    def test_one_fake_consumer_independently_conforms_to_shared_cases(self) -> None:
        consumer = FakeConsumer()
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                actual = consumer.consume(
                    case["outbound"],
                    interrupted=case.get("interrupted", False),
                    after_interruption=case.get("afterInterruption"),
                )
                expected = case["expected"]["consumer"]
                self.assertEqual(actual.status, expected["status"])
                self.assertEqual(actual.accepted_count, expected["acceptedCount"])
                self.assertEqual(actual.duplicate_count, expected["duplicateCount"])
                self.assertEqual(actual.rejected_count, expected["rejectedCount"])

    def test_cancellation_and_indeterminate_states_never_become_success(self) -> None:
        cases = {case["id"]: case for case in self.fixture["cases"]}
        consumer = FakeConsumer()
        for case_id, terminal_status in (
            ("cancelled-then-late-success", "cancelled"),
            ("indeterminate-then-late-success", "indeterminate"),
        ):
            with self.subTest(case=case_id):
                case = cases[case_id]
                result = consumer.consume(
                    case["outbound"],
                    interrupted=case.get("interrupted", False),
                    after_interruption=case.get("afterInterruption"),
                )
                self.assertEqual(result.status, terminal_status)
                self.assertGreater(result.rejected_count, 0)

    def test_specification_names_closed_envelopes_and_absorbing_terminals(self) -> None:
        text = SPECIFICATION_PATH.read_text(encoding="utf-8")
        for statement in (
            "Unknown envelope fields are malformed",
            "Terminal outcomes are absorbing",
            "Cancellation cannot become success",
            "Interruption becomes indeterminate",
        ):
            self.assertIn(statement, text)


if __name__ == "__main__":
    unittest.main()
