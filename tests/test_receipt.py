from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_skills.lifecycle import Lifecycle
from agent_skills.receipt import validate_receipt
from agent_skills.validate import canonical_json

from lifecycle_support import LifecycleFixture


class ReceiptContractTests(LifecycleFixture, unittest.TestCase):
    def test_active_marker_is_canonical_tracked_receipt(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)

        marker = self.repository / ".agent-skills" / "active.json"
        document = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(marker.read_text(encoding="utf-8"), canonical_json(document))
        self.assertEqual(document["schemaVersion"], "agent-skills-receipt/1")
        self.assertEqual(document["release"]["releaseId"], "core-v1")
        self.assertEqual(document["release"]["profile"], "core")
        self.assertRegex(document["release"]["archiveSha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(document["release"]["metadataSha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(document["ownership"], "agent-skills-managed")
        self.assertIsNone(document["previous"])
        self.assertEqual(
            document["createdDirectories"],
            [
                ".agent-skills",
                ".agent-skills/generations",
                ".agents",
                ".agents/skills",
                ".claude",
                ".claude/skills",
            ],
        )
        document["createdDirectories"] = list(reversed(document["createdDirectories"]))
        self.assertEqual(validate_receipt(document), document)

    def test_the_receipt_members_are_pinned(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        marker = self.repository / ".agent-skills" / "active.json"
        document = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(document),
            ["createdDirectories", "exposures", "files", "generation", "ownership", "previous", "release",
             "schemaVersion"],
        )
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "receipt.schema.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(schema["properties"]),
            ["adoption", "createdDirectories", "exposures", "files", "generation", "hooks", "ownership", "previous",
             "release", "schemaVersion"],
        )
        with self.assertRaisesRegex(ValueError, "receipt shape is malformed"):
            validate_receipt({**document, "unknown": True})

    def test_the_hooks_member_is_validated_and_the_schema_agrees(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        valid = json.loads((self.repository / ".agent-skills" / "active.json").read_text(encoding="utf-8"))
        hooks = {"created": True, "events": ["stop"], "file": ".claude/settings.json"}
        adoption = {"predecessorHosts": ["codex"], "predecessorSha256": "a" * 64, "skill": "deliver-work"}
        accepted = {
            "stop, created": {**valid, "hooks": hooks},
            "both events, not created": {**valid, "hooks": {**hooks, "created": False,
                                                            "events": ["session-start", "stop"]}},
            "no events, created and kept": {**valid, "hooks": {**hooks, "events": []}},
        }
        refused = {
            "another file": {**valid, "hooks": {**hooks, "file": ".claude/settings.local.json"}},
            "an unknown event": {**valid, "hooks": {**hooks, "events": ["pre-tool-use"]}},
            "unsorted events": {**valid, "hooks": {**hooks, "events": ["stop", "session-start"]}},
            "a repeated event": {**valid, "hooks": {**hooks, "events": ["stop", "stop"]}},
            "created not a boolean": {**valid, "hooks": {**hooks, "created": 1}},
            "nothing owned or created": {**valid, "hooks": {**hooks, "created": False, "events": []}},
            "an extra member": {**valid, "hooks": {**hooks, "matcher": "*"}},
            "a missing member": {**valid, "hooks": {"events": ["stop"], "file": ".claude/settings.json"}},
            "not an object": {**valid, "hooks": ["stop"]},
            "beside an adoption": {**valid, "adoption": adoption, "hooks": hooks},
            "without exposures": {key: value for key, value in {**valid, "hooks": hooks}.items()
                                  if key not in {"exposures", "createdDirectories"}},
        }
        for name, document in accepted.items():
            with self.subTest(accepted=name):
                self.assertEqual(validate_receipt(document), document)
        for name, document in refused.items():
            with self.subTest(refused=name):
                with self.assertRaisesRegex(ValueError, "receipt-invalid"):
                    validate_receipt(document)

        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "receipt.schema.json")
                            .read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for name, document in accepted.items():
            with self.subTest(schema_accepts=name):
                self.assertEqual(list(validator.iter_errors(document)), [])
        for name, document in refused.items():
            if name == "unsorted events":
                continue  # Order is the validator's canonical form; JSON Schema can't express sortedness.
            with self.subTest(schema_refuses=name):
                self.assertNotEqual(list(validator.iter_errors(document)), [], name)

    def test_corrupt_future_or_forged_receipt_never_authorizes_mutation(self) -> None:
        lifecycle = Lifecycle(self.repository)
        lifecycle.install(*self.v1, maintainer=True)
        marker = self.repository / ".agent-skills" / "active.json"
        valid = json.loads(marker.read_text(encoding="utf-8"))

        mutations = (
            b"not-json\n",
            canonical_json({**valid, "schemaVersion": "agent-skills-receipt/2"}).encode(),
            canonical_json({**valid, "ownership": "someone-else"}).encode(),
            canonical_json({**valid, "generation": "../outside"}).encode(),
            canonical_json(
                {
                    **valid,
                    "release": {**valid["release"], "profile": "unsupported"},
                }
            ).encode(),
            canonical_json({**valid, "exposures": list(reversed(valid["exposures"]))}).encode(),
            canonical_json({**valid, "createdDirectories": ["../outside"]}).encode(),
            canonical_json({**valid, "createdDirectories": [{}]}).encode(),
            canonical_json({**valid, "createdDirectories": "not-a-list"}).encode(),
            canonical_json(
                {
                    **valid,
                    "createdDirectories": [".agents", ".agents"],
                }
            ).encode(),
            canonical_json(
                {
                    key: value
                    for key, value in valid.items()
                    if key != "exposures"
                }
            ).encode(),
        )
        for contents in mutations:
            with self.subTest(contents=contents[:30]):
                marker.write_bytes(contents)
                status = lifecycle.status().as_dict()
                self.assertEqual(status["lifecycle"], "externally-transitioned")
                self.assertIn("receipt-invalid", status["blockers"])
                outside = self.base / "outside"
                outside.write_text("preserve", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "receipt-invalid"):
                    lifecycle.remove(maintainer=True)
                self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")
                marker.write_text(canonical_json(valid), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
