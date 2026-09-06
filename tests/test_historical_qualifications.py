"""Historical proof retains its original identities; it does not qualify current code."""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HistoricalQualificationTests(unittest.TestCase):
    def test_historical_records_remain_byte_identical(self) -> None:
        history = ROOT / "qualifications/history"
        checksums = json.loads((history / "checksums.json").read_text())
        self.assertEqual(set(checksums), {"core-v1.json", "provider-v1.json", "linear-v2.json",
                                         "linear-v2.attestation.json", "linear-v2.collector-binding.json"})
        for name, digest in checksums.items():
            self.assertEqual(hashlib.sha256((history / name).read_bytes()).hexdigest(), digest)

