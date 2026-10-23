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
PR65_QUALIFICATION_SHA256 = {
    "qualifications/core-workflows-diagnostics-v1.json": "4296aca98c30317fe9adc982949b1556a64099d7cb78305a58ca0db339caf01f",
    "qualifications/provider-workflows-diagnostics-v1.json": "f6741eaabbfb4e2805f0827e88e18997723e2b0f6537dc855332d1bc65fba796",
}
PR66_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-diagnostics-v1.json": "d4b900ddd568e93f0f6660f36bd6d6101dd7973d1348e3ee8d425e9e61129231",
    "qualifications/core-workflows-text-coherence-v1.json": "8546d4e4c98372609d8f63cbd198add23c54cb2b5d1ea0b9ef5473044d73db02",
    "qualifications/provider-workflows-text-coherence-v1.json": "24fde39459310504ee20259e0d6b0dfdd95401a923a9db2d72eab6ce2a259098",
}

PR68_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-concurrent-delivery-v1.json": "1cfe295842c0bd629d78640130530d900fa075f1b0f80dbd49d09716a293eed3",
    "qualifications/core-workflows-concurrent-delivery-v1.json": "dd97a62d6a1ec629330d8c34275c8690c1cea05c77d7a9d5498b0bd5c00f1936",
    "qualifications/provider-workflows-concurrent-delivery-v1.json": "8fe314672fccee7158b9649979ee5fd424e166fabadb67b4dec06bab72c513ca",
}

PR119_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-orchestration-v1.json": "d0734be167f0ca873fd6c34d76381b682f37df88ddd2f90df3213a2641ac5df1",
    "qualifications/core-workflows-orchestration-v1.json": "12f05ddb4c3be24bf12dca0f9f74a3557bc2d144d2244b9a89f1179265d1bc86",
    "qualifications/provider-workflows-orchestration-v1.json": "0f9ee42039c31867e9c15eabef619974978c981feebfbb2968f55228daff0bca",
}


class HistoricalQualificationTests(unittest.TestCase):
    def test_prior_current_records_remain_byte_identical(self) -> None:
        for relative, digest in {
            **PR63_QUALIFICATION_SHA256,
            **PR64_QUALIFICATION_SHA256,
            **PR65_QUALIFICATION_SHA256,
            **PR66_QUALIFICATION_SHA256,
            **PR68_QUALIFICATION_SHA256,
            **PR119_QUALIFICATION_SHA256,
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
