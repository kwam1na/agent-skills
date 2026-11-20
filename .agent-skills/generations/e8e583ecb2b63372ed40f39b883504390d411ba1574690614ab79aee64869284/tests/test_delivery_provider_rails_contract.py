from __future__ import annotations

import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "delivery-provider-rails.schema.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "delivery-provider-rails-v1.json"
SPECIFICATION_PATH = ROOT / "docs" / "delivery-provider-rails-v1.md"
CONTRACT_VERSION = "delivery-provider-rails/1"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ContractSchemaValidator:
    """Small evaluator for every assertion keyword used by this one schema."""

    SUPPORTED_KEYWORDS = {
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "enum",
        "if",
        "items",
        "maxItems",
        "maxLength",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "then",
        "type",
        "uniqueItems",
    }

    def __init__(self, schema: dict[str, object]) -> None:
        self.schema = schema
        self.unsupported_keywords = self._keywords(schema) - self.SUPPORTED_KEYWORDS

    def _keywords(self, schema: object) -> set[str]:
        if isinstance(schema, list):
            found: set[str] = set()
            for item in schema:
                found.update(self._keywords(item))
            return found
        if not isinstance(schema, dict):
            return set()
        found: set[str] = set()
        for key, value in schema.items():
            found.add(key)
            if key in {"$defs", "properties"} and isinstance(value, dict):
                for child in value.values():
                    found.update(self._keywords(child))
            elif key not in {"const", "enum", "required"}:
                found.update(self._keywords(value))
        return found

    def is_valid(self, instance: object, definition: str | None = None) -> bool:
        if self.unsupported_keywords:
            return False
        target = self.schema if definition is None else self.schema["$defs"][definition]
        assert isinstance(target, dict)
        return self._matches(instance, target)

    def _matches(self, instance: object, schema: dict[str, object]) -> bool:
        reference = schema.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                return False
            name = reference.rsplit("/", 1)[-1]
            target = self.schema["$defs"].get(name)
            if not isinstance(target, dict) or not self._matches(instance, target):
                return False

        branches = schema.get("allOf")
        if branches is not None and (
            not isinstance(branches, list)
            or not all(
                isinstance(branch, dict) and self._matches(instance, branch)
                for branch in branches
            )
        ):
            return False
        branches = schema.get("anyOf")
        if branches is not None and (
            not isinstance(branches, list)
            or not any(
                isinstance(branch, dict) and self._matches(instance, branch)
                for branch in branches
            )
        ):
            return False
        branches = schema.get("oneOf")
        if branches is not None and (
            not isinstance(branches, list)
            or sum(
                isinstance(branch, dict) and self._matches(instance, branch)
                for branch in branches
            )
            != 1
        ):
            return False
        condition = schema.get("if")
        consequence = schema.get("then")
        if (
            isinstance(condition, dict)
            and self._matches(instance, condition)
            and (
                not isinstance(consequence, dict)
                or not self._matches(instance, consequence)
            )
        ):
            return False

        expected_type = schema.get("type")
        if isinstance(expected_type, str) and not self._has_type(instance, expected_type):
            return False
        if "const" in schema and not self._equal(instance, schema["const"]):
            return False
        if "enum" in schema and (
            not isinstance(schema["enum"], list)
            or not any(self._equal(instance, choice) for choice in schema["enum"])
        ):
            return False

        if isinstance(instance, dict):
            required = schema.get("required", [])
            if not isinstance(required, list) or any(
                key not in instance for key in required
            ):
                return False
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                return False
            for key, value in instance.items():
                child = properties.get(key)
                if child is None:
                    if schema.get("additionalProperties") is False:
                        return False
                elif not isinstance(child, dict) or not self._matches(value, child):
                    return False
        if isinstance(instance, list):
            if (
                isinstance(schema.get("maxItems"), int)
                and len(instance) > schema["maxItems"]
            ):
                return False
            item_schema = schema.get("items")
            if item_schema is not None and (
                not isinstance(item_schema, dict)
                or not all(self._matches(item, item_schema) for item in instance)
            ):
                return False
            if schema.get("uniqueItems") is True and any(
                self._equal(item, prior)
                for index, item in enumerate(instance)
                for prior in instance[:index]
            ):
                return False
        if isinstance(instance, str):
            if (
                isinstance(schema.get("minLength"), int)
                and len(instance) < schema["minLength"]
            ):
                return False
            if (
                isinstance(schema.get("maxLength"), int)
                and len(instance) > schema["maxLength"]
            ):
                return False
            pattern = schema.get("pattern")
            if isinstance(pattern, str) and re.search(pattern, instance) is None:
                return False
        if (
            isinstance(instance, int)
            and not isinstance(instance, bool)
            and isinstance(schema.get("minimum"), int)
            and instance < schema["minimum"]
        ):
            return False
        return True

    @staticmethod
    def _has_type(instance: object, expected: str) -> bool:
        return {
            "array": lambda: isinstance(instance, list),
            "integer": lambda: isinstance(instance, int)
            and not isinstance(instance, bool),
            "null": lambda: instance is None,
            "object": lambda: isinstance(instance, dict),
            "string": lambda: isinstance(instance, str),
        }.get(expected, lambda: False)()

    @staticmethod
    def _equal(left: object, right: object) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return type(left) is type(right) and left == right
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left == right
        return type(left) is type(right) and left == right


SCHEMA_VALIDATOR = ContractSchemaValidator(SCHEMA)


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
        requests_by_id: dict[str, tuple[str, str]] = {}
        request_ids_by_key: dict[str, str] = {}
        cancellations: dict[str, tuple[str, str]] = {}
        execution_count = 0
        duplicate_count = 0
        cancellation_count = 0
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
                status = "malformed"
                break
            if message["kind"] == "negotiate":
                negotiated = False
                if requests_by_id or not SCHEMA_VALIDATOR.is_valid(message, "negotiate"):
                    status = "malformed"
                    break
                negotiated = CONTRACT_VERSION in message["supportedVersions"]
                status = "supported" if negotiated else "unsupported"
                if not negotiated:
                    break
            elif message["kind"] == "request":
                if not negotiated or not SCHEMA_VALIDATOR.is_valid(message, "request"):
                    status = "malformed"
                    break
                request_id = str(message["requestId"])
                idempotency_key = str(message["idempotencyKey"])
                encoded = _canonical(message)
                prior = requests_by_id.get(request_id)
                prior_request_id = request_ids_by_key.get(idempotency_key)
                if prior is None and prior_request_id is None:
                    requests_by_id[request_id] = (idempotency_key, encoded)
                    request_ids_by_key[idempotency_key] = request_id
                    execution_count += 1
                    status = "accepted"
                elif (
                    prior == (idempotency_key, encoded)
                    and prior_request_id == request_id
                ):
                    duplicate_count += 1
                else:
                    status = "malformed"
                    break
            elif message["kind"] == "cancel":
                if not negotiated or not SCHEMA_VALIDATOR.is_valid(message, "cancel"):
                    status = "malformed"
                    break
                request_id = str(message["requestId"])
                if request_id not in requests_by_id:
                    status = "malformed"
                    break
                cancellation_id = str(message["cancellationId"])
                encoded = _canonical(message)
                prior = cancellations.get(cancellation_id)
                if prior == (request_id, encoded):
                    duplicate_count += 1
                elif prior is None:
                    cancellations[cancellation_id] = (request_id, encoded)
                    cancellation_count += 1
                else:
                    status = "malformed"
                    break
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
        request_id: str | None = None,
        cancellation_accepted: bool = False,
        interrupted: bool = False,
        after_interruption: list[object] | None = None,
    ) -> ConsumerResult:
        negotiated = False
        status = "malformed"
        terminal = False
        failed_closed = False
        active_request_id = request_id
        seen_by_request: dict[str, dict[int, str]] = {}
        accepted_count = 0
        duplicate_count = 0
        rejected_count = 0

        def accept(message: object) -> None:
            nonlocal negotiated, status, terminal, failed_closed, active_request_id
            nonlocal accepted_count, duplicate_count, rejected_count
            if terminal:
                rejected_count += 1
                return
            if failed_closed:
                rejected_count += 1
                return
            if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
                status = "malformed"
                failed_closed = True
                return
            if message["kind"] == "negotiation":
                negotiated = False
                if seen_by_request or not SCHEMA_VALIDATOR.is_valid(message, "negotiation"):
                    status = "malformed"
                    failed_closed = True
                    return
                if message["outcome"] == "supported":
                    negotiated = True
                    status = "supported"
                else:
                    status = "unsupported"
                    failed_closed = True
                return
            kind = message["kind"]
            if (
                not negotiated
                or kind not in {"progress", "evidence", "blocker", "terminal"}
                or not SCHEMA_VALIDATOR.is_valid(message, str(kind))
            ):
                status = "malformed"
                failed_closed = True
                return
            event_request_id = str(message["requestId"])
            if active_request_id is None:
                active_request_id = event_request_id
            elif event_request_id != active_request_id:
                status = "malformed"
                failed_closed = True
                return
            encoded = _canonical(message)
            sequence = int(message["sequence"])
            seen = seen_by_request.setdefault(event_request_id, {})
            if sequence in seen:
                if seen[sequence] == encoded:
                    duplicate_count += 1
                else:
                    status = "malformed"
                    failed_closed = True
                return
            expected = max(seen, default=0) + 1
            if sequence != expected:
                status = "malformed"
                failed_closed = True
                return
            if (
                cancellation_accepted
                and kind == "terminal"
                and message["outcome"] not in {"cancelled", "indeterminate"}
            ):
                status = "malformed"
                rejected_count += 1
                failed_closed = True
                return
            seen[sequence] = encoded
            accepted_count += 1
            if kind == "terminal":
                status = str(message["outcome"])
                terminal = True

        for item in messages:
            accept(item)
        if interrupted and negotiated and not terminal and not failed_closed:
            status = "indeterminate"
            terminal = True
        for item in after_interruption or []:
            accept(item)
        return ConsumerResult(status, accepted_count, duplicate_count, rejected_count)


class DeliveryProviderRailsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = SCHEMA
        cls.fixture = FIXTURE

    def test_schema_is_one_closed_versioned_message_union(self) -> None:
        self.assertEqual(SCHEMA_VALIDATOR.unsupported_keywords, set())
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
                attempt = self.fixture["attempts"].get(case["id"], {})
                actual = consumer.consume(
                    case["outbound"],
                    request_id=attempt.get("requestId"),
                    cancellation_accepted=attempt.get(
                        "cancellationAccepted", False
                    ),
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
                attempt = self.fixture["attempts"][case_id]
                result = consumer.consume(
                    case["outbound"],
                    request_id=attempt["requestId"],
                    cancellation_accepted=attempt["cancellationAccepted"],
                    interrupted=case.get("interrupted", False),
                    after_interruption=case.get("afterInterruption"),
                )
                self.assertEqual(result.status, terminal_status)
                self.assertGreater(result.rejected_count, 0)

    def test_request_identity_axes_are_one_to_one_and_cancel_targets_an_attempt(self) -> None:
        negotiation = {
            "kind": "negotiate",
            "supportedVersions": [CONTRACT_VERSION],
        }
        request = {
            "idempotencyKey": "key-one",
            "kind": "request",
            "payload": {},
            "requestId": "request-one",
            "version": CONTRACT_VERSION,
        }
        conflicts = (
            {**request, "idempotencyKey": "key-two"},
            {**request, "requestId": "request-two"},
        )
        provider = FakeProvider()
        for conflict in conflicts:
            with self.subTest(conflict=conflict):
                result = provider.consume([negotiation, request, conflict])
                self.assertEqual(result.status, "malformed")
                self.assertEqual(result.execution_count, 1)
        unknown_cancel = {
            "cancellationId": "cancel-unknown",
            "kind": "cancel",
            "requestId": "request-unknown",
            "version": CONTRACT_VERSION,
        }
        self.assertEqual(
            provider.consume([negotiation, request, unknown_cancel]).status,
            "malformed",
        )

    def test_accepted_cancellation_is_correlated_to_the_same_outbound_attempt(self) -> None:
        negotiation = {
            "kind": "negotiate",
            "supportedVersions": [CONTRACT_VERSION],
        }
        request = {
            "idempotencyKey": "cancel-key",
            "kind": "request",
            "payload": {},
            "requestId": "cancel-target",
            "version": CONTRACT_VERSION,
        }
        cancel = {
            "cancellationId": "cancel-one",
            "kind": "cancel",
            "requestId": "cancel-target",
            "version": CONTRACT_VERSION,
        }
        provider = FakeProvider().consume([negotiation, request, cancel])
        self.assertEqual(provider.status, "cancelled")

        outbound_negotiation = {
            "kind": "negotiation",
            "outcome": "supported",
            "selectedVersion": CONTRACT_VERSION,
            "supportedVersions": [CONTRACT_VERSION],
        }
        for outbound_request_id in ("cancel-target", "different-attempt"):
            with self.subTest(outbound_request_id=outbound_request_id):
                success = {
                    "kind": "terminal",
                    "outcome": "success",
                    "requestId": outbound_request_id,
                    "sequence": 1,
                    "summary": "Invalid success after cancellation",
                    "version": CONTRACT_VERSION,
                }
                consumer = FakeConsumer().consume(
                    [outbound_negotiation, success],
                    request_id="cancel-target",
                    cancellation_accepted=True,
                )
                self.assertEqual(consumer.status, "malformed")

        for outcome in ("blocked", "cancelled", "failed", "indeterminate", "success"):
            with self.subTest(outcome=outcome):
                terminal = {
                    "kind": "terminal",
                    "outcome": outcome,
                    "requestId": "cancel-target",
                    "sequence": 1,
                    "summary": "Cancellation outcome",
                    "version": CONTRACT_VERSION,
                }
                consumer = FakeConsumer().consume(
                    [outbound_negotiation, terminal],
                    request_id="cancel-target",
                    cancellation_accepted=True,
                )
                expected = (
                    outcome
                    if outcome in {"cancelled", "indeterminate"}
                    else "malformed"
                )
                self.assertEqual(consumer.status, expected)

    def test_consumer_never_combines_events_from_different_attempts(self) -> None:
        messages = [
            {
                "kind": "negotiation",
                "outcome": "supported",
                "selectedVersion": CONTRACT_VERSION,
                "supportedVersions": [CONTRACT_VERSION],
            },
            {
                "kind": "progress",
                "requestId": "request-one",
                "sequence": 1,
                "summary": "First attempt",
                "version": CONTRACT_VERSION,
            },
            {
                "kind": "terminal",
                "outcome": "success",
                "requestId": "request-two",
                "sequence": 2,
                "summary": "Different attempt",
                "version": CONTRACT_VERSION,
            },
        ]
        result = FakeConsumer().consume(messages, request_id="request-one")
        self.assertEqual(result.status, "malformed")
        self.assertEqual(result.accepted_count, 1)

    def test_schema_and_fakes_agree_on_named_malformed_messages(self) -> None:
        malformed = {
            "duplicate-versions": (
                "negotiate",
                {
                    "kind": "negotiate",
                    "supportedVersions": [CONTRACT_VERSION, CONTRACT_VERSION],
                },
            ),
            "non-string-version": (
                "negotiate",
                {"kind": "negotiate", "supportedVersions": [1]},
            ),
            "too-many-versions": (
                "negotiate",
                {
                    "kind": "negotiate",
                    "supportedVersions": [str(index) for index in range(9)],
                },
            ),
            "boolean-sequence": (
                "progress",
                {
                    "kind": "progress",
                    "requestId": "request-one",
                    "sequence": True,
                    "summary": "Boolean is not an integer",
                    "version": CONTRACT_VERSION,
                },
            ),
            "unknown-request-field": (
                "request",
                {
                    "idempotencyKey": "key-one",
                    "kind": "request",
                    "payload": {},
                    "requestId": "request-one",
                    "unexpected": True,
                    "version": CONTRACT_VERSION,
                },
            ),
        }
        for name, (definition, message) in malformed.items():
            with self.subTest(case=name):
                self.assertFalse(SCHEMA_VALIDATOR.is_valid(message, definition))

        for name in (
            "duplicate-versions",
            "non-string-version",
            "too-many-versions",
        ):
            with self.subTest(fake="provider", case=name):
                self.assertEqual(
                    FakeProvider().consume([malformed[name][1]]).status,
                    "malformed",
                )

        provider = FakeProvider().consume(
            [
                {"kind": "negotiate", "supportedVersions": [CONTRACT_VERSION]},
                malformed["unknown-request-field"][1],
            ]
        )
        consumer = FakeConsumer().consume(
            [
                {
                    "kind": "negotiation",
                    "outcome": "supported",
                    "selectedVersion": CONTRACT_VERSION,
                    "supportedVersions": [CONTRACT_VERSION],
                },
                malformed["boolean-sequence"][1],
            ],
            request_id="request-one",
        )
        self.assertEqual(provider.status, "malformed")
        self.assertEqual(consumer.status, "malformed")

        invalid_negotiations = {
            "duplicate-versions": [CONTRACT_VERSION, CONTRACT_VERSION],
            "non-string-version": [1],
            "too-many-versions": [str(index) for index in range(9)],
        }
        for name, supported_versions in invalid_negotiations.items():
            with self.subTest(fake="consumer", case=name):
                invalid = {
                    "kind": "negotiation",
                    "outcome": "supported",
                    "selectedVersion": CONTRACT_VERSION,
                    "supportedVersions": supported_versions,
                }
                self.assertFalse(SCHEMA_VALIDATOR.is_valid(invalid, "negotiation"))
                self.assertEqual(
                    FakeConsumer().consume([invalid], request_id="request-one").status,
                    "malformed",
                )

    def test_invalid_renegotiation_fails_closed(self) -> None:
        valid_inbound = {
            "kind": "negotiate",
            "supportedVersions": [CONTRACT_VERSION],
        }
        invalid_inbound = {
            "kind": "negotiate",
            "supportedVersions": [CONTRACT_VERSION, CONTRACT_VERSION],
        }
        request = {
            "idempotencyKey": "key-one",
            "kind": "request",
            "payload": {},
            "requestId": "request-one",
            "version": CONTRACT_VERSION,
        }
        provider = FakeProvider().consume([valid_inbound, invalid_inbound, request])
        self.assertEqual(provider.status, "malformed")
        self.assertEqual(provider.execution_count, 0)

        valid_outbound = {
            "kind": "negotiation",
            "outcome": "supported",
            "selectedVersion": CONTRACT_VERSION,
            "supportedVersions": [CONTRACT_VERSION],
        }
        invalid_outbound = {**valid_outbound, "unexpected": True}
        terminal = {
            "kind": "terminal",
            "outcome": "success",
            "requestId": "request-one",
            "sequence": 1,
            "summary": "Must not recover negotiation",
            "version": CONTRACT_VERSION,
        }
        consumer = FakeConsumer().consume(
            [valid_outbound, invalid_outbound, terminal],
            request_id="request-one",
        )
        self.assertEqual(consumer.status, "malformed")

    def test_absorbing_terminal_precedes_late_message_validation(self) -> None:
        negotiation = {
            "kind": "negotiation",
            "outcome": "supported",
            "selectedVersion": CONTRACT_VERSION,
            "supportedVersions": [CONTRACT_VERSION],
        }
        for outcome in ("success", "cancelled", "indeterminate"):
            with self.subTest(outcome=outcome):
                terminal = {
                    "kind": "terminal",
                    "outcome": outcome,
                    "requestId": "request-one",
                    "sequence": 1,
                    "summary": "Attempt closed",
                    "version": CONTRACT_VERSION,
                }
                result = FakeConsumer().consume(
                    [negotiation, terminal, {"unexpected": True}],
                    request_id="request-one",
                )
                self.assertEqual(result.status, outcome)
                self.assertEqual(result.rejected_count, 1)

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
