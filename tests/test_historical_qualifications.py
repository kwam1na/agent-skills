"""Historical proof retains its original identities; it does not qualify current code."""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PR63_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-v1.json": "877e6388d834b78bc3f96a8ed64c823f2df112273139bffb4b45037bed495729",
    "qualifications/core-workflows-adoption-v1.json": "8507eecc55f9257c8d9a11bea93f4dfcd406b2e37df8e407af9cab87d6006e1c",
    "qualifications/provider-workflows-adoption-v1.json": "b1e487934376692f55abb5022eeeafb934af9dac0db20bd646c8509f46b2c599",
}
PR64_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-bytecode-v1.json": "4f5a31f955693e84c29bf59e3b75890930e31f39b71f758a219ac261b6ed797f",
    "qualifications/core-workflows-bytecode-v1.json": "456772ac2668aa8f3d1dd7129b25e5fc3e11c30c21eca9a2611a36bf9845887d",
    "qualifications/provider-workflows-bytecode-v1.json": "fcb4856655777d079c58fad466d525e459fee477849f920c3fa2013253b621b9",
}


class HistoricalQualificationTests(unittest.TestCase):
    def test_prior_current_records_remain_byte_identical(self) -> None:
        for relative, digest in {
            **PR63_QUALIFICATION_SHA256,
            **PR64_QUALIFICATION_SHA256,
        }.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                    digest,
                )

    def test_historical_records_remain_byte_identical(self) -> None:
        history = ROOT / "qualifications/history"
        checksums = json.loads((history / "checksums.json").read_text())
        self.assertEqual(set(checksums), {"core-v1.json", "provider-v1.json", "linear-v2.json",
                                         "linear-v2.attestation.json", "linear-v2.collector-binding.json"})
        for name, digest in checksums.items():
            self.assertEqual(hashlib.sha256((history / name).read_bytes()).hexdigest(), digest)
