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

PR138_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-delivery-views-v1.json": "bce08c41de84fdc7258e0af884c87a0bd001f3cf6a2848ce74a073a9dfd4f1da",
    "qualifications/core-workflows-delivery-views-v1.json": "092376a98b10ba227d5cecd20e14c6bf22cb290504dfbcedb54cddf909cdb6d7",
    "qualifications/provider-workflows-delivery-views-v1.json": "85f15736c72a1cd45387bb04ce97e1432ebf5ef1dabedf4b04cf1641a6832c78",
}

PR148_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-review-evidence-v1.json": "f9ee133390b7c79728f21493822893bd6aabbaf55fe8eafb98c987942cd77506",
    "qualifications/core-workflows-review-evidence-v1.json": "5cd84209a44e88fdd85a31547960f5b1b73a22c3e435bb1a5e528c8dc4806310",
    "qualifications/provider-workflows-review-evidence-v1.json": "0bf25630d46d0c87ea1ab56f39b61386f6cffa637782770aa947e042bf8b49c6",
}

PR149_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-run-delivery-v1.json": "faae0e96199884d5804f15330f73a770f228d5248d768abca598e8cc467a99ac",
    "qualifications/core-workflows-run-delivery-v1.json": "63201e80254a03b3f5c4be958d1c3a6dde9f275b5e206b9701b7d71b4a31c560",
    "qualifications/provider-workflows-run-delivery-v1.json": "494c122a6a210236f68018a6bf4afd7ba5ff00ec5b127cd589a2013fdb63caa7",
}


PR152_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-jira-tracker-v1.json": "86117db89f8b720a17bcb80062eb81010216b4db39ca9a7bfec5a5876654b4de",
    "qualifications/core-workflows-jira-tracker-v1.json": "41793bd5569bf36aeff577fd1a9c4fa966990fff2089fe9d5d8a7a89f21f9341",
    "qualifications/provider-workflows-jira-tracker-v1.json": "0c9e774401530fad70228bbbdf5b17cb91a74d7700cdb05855916ae095166be0",
}


PR154_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-plan-review-v1.json": "b3c8e00f842f01f6298002e3ffc6b82b019693ace08147c316fd67fd1ed0467e",
    "qualifications/core-workflows-plan-review-v1.json": "5ddea10fc575b689bf30dc81ee6de56c918dafa1f8bd3caee47c9b3d0c32d6bf",
    "qualifications/provider-workflows-plan-review-v1.json": "d449b89c13577b184e3949aea17d76a15fc2c9adb99babe42c941911d2ac5f58",
}


PR155_QUALIFICATION_SHA256 = {
    "qualifications/bootstrap-workflows-capture-observations-v1.json": "68e7347ecb80b9d4cdcb64f85dbf908d3c2ddb7798d4347cbd2dff05592f4bb8",
    "qualifications/core-workflows-capture-observations-v1.json": "63a25c25287de6e12a8b2cfc487dd6d63096f7c133c62f8eaeac524ae1d3456e",
    "qualifications/provider-workflows-capture-observations-v1.json": "fc0db76f41fd1c017733f1a6a6aced03e32d8d286e5ae17ce9345e28e1f01449",
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
            **PR138_QUALIFICATION_SHA256,
            **PR148_QUALIFICATION_SHA256,
            **PR149_QUALIFICATION_SHA256,
            **PR152_QUALIFICATION_SHA256,
            **PR154_QUALIFICATION_SHA256,
            **PR155_QUALIFICATION_SHA256,
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
