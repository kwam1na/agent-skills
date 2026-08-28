from __future__ import annotations

import json
import unittest

from agent_skills.lifecycle import Lifecycle
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
                    "release": {**valid["release"], "profile": "bootstrap"},
                }
            ).encode(),
            canonical_json({**valid, "exposures": list(reversed(valid["exposures"]))}).encode(),
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
