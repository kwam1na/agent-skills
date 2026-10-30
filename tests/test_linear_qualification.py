from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_skills.release import RUNTIME_FILES, _path_digest, _release_view, build_release, verify_release
from tests.qualification_pending_support import pending, skip_if_pending


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify-linear.py"
PRODUCT = ROOT / "qualifications" / "product"
ATTESTATION = PRODUCT / "linear-run-delivery-v1.attestation.json"
RECORD = PRODUCT / "linear-run-delivery-v1.json"
BINDING = PRODUCT / "linear-run-delivery-v1.collector-binding.json"
HISTORICAL_ATTESTATION = PRODUCT / "linear-product-v1.attestation.json"
HISTORICAL_RECORD = PRODUCT / "linear-product-v1.json"
HISTORICAL_BINDING = PRODUCT / "linear-product-v1.collector-binding.json"
HISTORICAL_PRODUCT_SHA256 = {
    "bootstrap-product-v1.json": "5a5129d50ea22b205a31c80fff640c5c3887f7055235ac959fb3cc8afa8b07f2",
    "bootstrap-product-v1.release.json": "bca83f3140382b0bfc2084fcc949b88c61a6b59bd39a7269c6f1511527867143",
    "bootstrap-product-v1.zip": "e70fdab67712cab62fd011c57f3b35f3f760ab0eadbbeb164031f8a5def1e508",
    "core-adoption-v1.json": "c279d3d43ab3674105947ca1cf3bd280a006757e31e54ec1ec28dcd3304edabe",
    "core-adoption-v1.release.json": "40be2cb0dec75976b244dbeb7f23a4936be07465f37807356f5b6892cd94063c",
    "core-adoption-v1.zip": "2d781b20ef9bbfa9aba9a5aee3846591ca8806e92f866f5af7a1876ba911f574",
    "core-product-v1.json": "0073ba85859497b6aaa601f87796ad636282ec19694df9c1663cd8bc49d6941e",
    "core-product-v1.release.json": "f4a1c4af455f013e7147edea8554f3bd2fd8dd09ef39c50530ffe3c9cb583536",
    "core-product-v1.zip": "ab5697d42e13273fdf061be90429c9d8b2ae742ed234d11f7f178b0c44f9a5c5",
    "linear-product-v1.attestation.json": "184fd4c9693ca880de446cf7d0d8f0e20c1b3c8637a20bd1fb48451e18e6e25e",
    "linear-product-v1.collector-binding.json": "be0683edeed89f552a45b571282a4c831ce9c3b6c487de662f094c1b1a012540",
    "linear-product-v1.json": "9873b333ef330c34b76a5ef5371d32bc97d7c28838e8b4b6ca41634751b23a72",
    "linear-product-v1.release.json": "e0decf70cd95d292cc35d3c04f14e4fbd6be4da2b9b95d1a2c1d193a2089308e",
    "linear-product-v1.zip": "ea74c8d00028343b4b6b2d2cf4b1f24d19723e27b596d3d2d73ef1e3317b8551",
    "linear-adoption-v1.attestation.json": "2a66e09b7e3f8015e1dc1f6a4be8a1730126214d8c57c0e40c969298fe3fbf15",
    "linear-adoption-v1.collector-binding.json": "c028b8ade43aeb52c7f174f6df2d4e9f37b5407a1983516ce0ada84d5e4421c3",
    "linear-adoption-v1.json": "4375046ce949508c15d1f501b66e16f5f96c9ca705a2735d5bcbf55fcab04e4b",
    "linear-adoption-v1.release.json": "869d6b559b7628f4aac9ede466b6ab48f59209778c25f9b4a78b4fe941188b6c",
    "linear-adoption-v1.zip": "8e24c88d459de54bca99230aa23acd76416b003e31693bb5c98db3d4a8cec257",
    "provider-adoption-v1.json": "b5edd0629799e63e00ee50c59866c90036787a510e8d2694ca5f8d829910262c",
    "provider-product-v1.json": "591aff86b3a23a4d15b29fb11794624f3b06d08c24bcaa348c4391d8d71beb35",
    "bootstrap-bytecode-v1.json": "9f6b8688c7e0080d848141886eceda31080d6ae28d39605d88fc2face3b287f6",
    "bootstrap-bytecode-v1.release.json": "ef248e783477b28234e02a54d54cea67a28996fd142e9792012f74db9caf56bf",
    "bootstrap-bytecode-v1.zip": "6b8ad773385fd621f850f2a44624b59135a2b3b80fd80da64fa3e5549688aac9",
    "core-bytecode-v1.json": "c2e27a1eef98f629e4973d2ec6957efe0cb035705dcd5e622165a45240f0bf30",
    "core-bytecode-v1.release.json": "8b6c22689e124c8baf5c4bee448532a0917a72a4e8fdd9d19028f488e05399e8",
    "core-bytecode-v1.zip": "52a9ec8a45d75c29fbfff18cca7a6a1e9658dbfd9c944feb76fb4144f3832d1e",
    "linear-bytecode-v1.attestation.json": "037bf751cfc47df57a55bf82cac05febc8469aabc972d286cbffb5c746c0941f",
    "linear-bytecode-v1.collector-binding.json": "5c9cc3b1f900e192ac9ad8f72d2b33d22ecf2c056dc24a2321ec36008ea58b3a",
    "linear-bytecode-v1.json": "06815879c834dddadb61d90ef952862cf7a992b9f8ccedb464d0d35e9ecbd34a",
    "linear-bytecode-v1.release.json": "3d5f3ed25ed8c2445f5a6b95ef8e508a423539ab74307fcdfb380640618ac5f8",
    "linear-bytecode-v1.zip": "f611b5340c911fefb882a4cf4d7b6f9e5dcf2d05da46d3891070d6c4ae8e91d8",
    "provider-bytecode-v1.json": "df59428d2c50dd5f289776acb0b957db76aa9d46200b0debb44842890afb5d26",
    "core-diagnostics-v1.json": "19cba9d9ba44e0d4d1da627cc00d06e8f2da1e474386e3464ebe132a9d3b4ad4",
    "core-diagnostics-v1.release.json": "e9f6c569add672fe573aea16d8ca192f0969c01be74109175b4a413632345360",
    "core-diagnostics-v1.zip": "f02c5f2f53d721e38574ed7b232484e41d0ac3cfa86329f3f41d3e115df3624f",
    "provider-diagnostics-v1.json": "3caee03a765ce66f29969e2943196c02ad2d4fc10c7f02a629dc53e657254480",
    "linear-diagnostics-v1.attestation.json": "808b8051c2a309a8bbe8fcdb32a24975319fa5ac4078a1ce7125c547d6fac6e9",
    "linear-diagnostics-v1.collector-binding.json": "267d578106dd001ed0d859b380f47950b7f114482c00c9e7d876658dce73c1f5",
    "linear-diagnostics-v1.json": "999feca56c46b6d1dbe28389b2200a9867f63c4c88d533aee4a188d717a104a5",
    "linear-diagnostics-v1.release.json": "7fcd9437964dbc40cdba9fb6f87889f6e85a7932679bc2066f68cd2f358e19a8",
    "linear-diagnostics-v1.zip": "8b80244acaffb4be1ad982c635ccd3ac20988d8dc0a33d76d90c424d0fe75525",
    "bootstrap-diagnostics-v1.json": "d6801d9509e435607a60d5f94c9b9225ae055b56f05060c2f9e9b76cd02e9fa3",
    "bootstrap-diagnostics-v1.release.json": "e63bf7cfe20d925b8369f223e99875d2a5020aaae632256d830eacf952fbd443",
    "bootstrap-diagnostics-v1.zip": "c2ad5490843687bf11b0eaef770d257b8e365e263ebaab213f8853cbda27eba3",
    "core-text-coherence-v1.json": "71bba756b13b617d63b43fd293399d2d482824e036e3bc083bed4ddd40d86d92",
    "core-text-coherence-v1.release.json": "f4406b1b07ae98c885c9b6d1e411a78115e4dc0b3325b2d65aff1dcc968a798a",
    "core-text-coherence-v1.zip": "3a2fd8038177316388139b130821e1f57526e749d91ab581233750e6b226b938",
    "provider-text-coherence-v1.json": "781ebd7d5ececbe6f3b4204c82d07558e64e44b177cef9fc46f75b2de2341037",
    "linear-text-coherence-v1.attestation.json": "ead95a3469c42f86085c68ceb09a009c5325581193d9101df4d669a85af6ae28",
    "linear-text-coherence-v1.collector-binding.json": "fac79d408dd9474a93a15255149ff55ea18cb4d0e6b41a62e01ef258df78f7b2",
    "linear-text-coherence-v1.json": "c311bb57dd18fc68bcacbe2efb4488b2e7a46184d91d7db9f82125beb09e2e1f",
    "linear-text-coherence-v1.release.json": "b45988a3e214fa573b96e5592f28364b1d2d60c9626a26e9e54494bd9cf9a050",
    "linear-text-coherence-v1.zip": "945fd9a246433c64f37fc3da5218203b64fa1275e3cc8e1a299cb9724062f679",
    "bootstrap-concurrent-delivery-v1.json": "6295c741ef879b8ef603a99520a0aa537fbe180e36a2ad47c7b694e46ac412f0",
    "bootstrap-concurrent-delivery-v1.release.json": "11cb4006c0ab1e9d26623be66ecd4b8162a885de6c8b56f1d3c81890fcaccf62",
    "bootstrap-concurrent-delivery-v1.zip": "18183b739211ebcaabb684939afc9eb423f062f5a0f632787eb712cda93e165e",
    "core-concurrent-delivery-v1.json": "3ce334f4feaf30384e639b6c9332f39124972c6e3638c808a23ec0e483e8b59e",
    "core-concurrent-delivery-v1.release.json": "4614c1918e514b1ebe568d5a659c35cc96ac151093151a86eb42b0d791b4cb52",
    "core-concurrent-delivery-v1.zip": "6b0e7ee069858b69ded91647d6ce3bd53d49af1ff2bac5b6ac495948720c3943",
    "linear-concurrent-delivery-v1.attestation.json": "671e5f1ccb6f82a6c4991fa38822a43b1419bce06cc4fbd1eb329d7dc53d019e",
    "linear-concurrent-delivery-v1.collector-binding.json": "449104fd1c333756f7ff903bf96754268e93860bb8031d14e0abb075cc1b750f",
    "linear-concurrent-delivery-v1.json": "e79bab13ee94d46cd595c98d688d3421a7a7ae863438691448ad381416248606",
    "linear-concurrent-delivery-v1.release.json": "d46f7f75e11b7f809f93e10b961954bfd7f8dc533f474074aa57b5594d39868e",
    "linear-concurrent-delivery-v1.zip": "e63b1f2bb5516d55445aa7050f2a9366b05abdb9d8584bdeed4119abb5440578",
    "provider-concurrent-delivery-v1.json": "99593af97d48acc4a68434671cb87030cc76184fba199d057c9680d82db93678",
    "bootstrap-orchestration-v1.json": "13e9a73ae69a5faeb0f0983e2f0766746054cd4af7029f4863897a9a46eaf1af",
    "bootstrap-orchestration-v1.release.json": "62468bf20eb75ecb17ac6a17c14c04d17560e3d4fdd9172404916cfa157a0662",
    "bootstrap-orchestration-v1.zip": "121aa7aba5cf928f1900bb5333022f8f9b099a9bd5cf2e34e40ab4dd35adb5fe",
    "core-orchestration-v1.json": "806e0e8f76800afb1888e11d0c68a82dadbf7a9ab5d74d042df09f685152cd93",
    "core-orchestration-v1.release.json": "f80d339b2470675679fa4ffb74ab5fd8abd698fc6d114c08408303b18a97b9ed",
    "core-orchestration-v1.zip": "c91c2a07b00ee96d399af62f8c0fdee0ef47835826502cbdae50da0eb2dfc48f",
    "linear-orchestration-v1.attestation.json": "60333797a7d214ef946d086fc002c068b581a847f4187f5c947b01938e358eca",
    "linear-orchestration-v1.collector-binding.json": "6ac0eff3690db7ff9937e7db41f9b4ae56d82e0b846892862fe7fc2b38160404",
    "linear-orchestration-v1.json": "3d655161eb30f3bffbcdbd6aa779a575460d505e94b0ff8701c88d6117878551",
    "linear-orchestration-v1.release.json": "d3ffb5519df3528001b5bad2b10f9919ef20e32912093d23d75be9a404d3bfac",
    "linear-orchestration-v1.zip": "b798ef4c832fd2abba0530dc42409f2c2bcd30f31909ea53d33fe4fd4d8cc2db",
    "provider-orchestration-v1.json": "9d9ea213eaeb4b775bdb24b4b1d5ff0c1db6477681445fbf920f196350f9728c",
    "bootstrap-delivery-views-v1.json": "cda18211e6d9a3300ad6488b79dc3e0a8dc5d486564aaeab2d3f43a7bde22052",
    "bootstrap-delivery-views-v1.release.json": "2515fdd1c6b721b3c9f5a0148bb0771977e90600eee026f5ab5effc1e6f720ed",
    "bootstrap-delivery-views-v1.zip": "044ef5259c295363a901df0bf51d479018e15086249873befedd3878ffc3286e",
    "core-delivery-views-v1.json": "9d764a9e79bc0611f37d0d5ebaca045a16cf26437ee239e8a945c4b8360a3220",
    "core-delivery-views-v1.release.json": "153d79ff046b59865ff9a1c194a7e52216dc0522093b6fe4a62d1842fc0c9264",
    "core-delivery-views-v1.zip": "5bba40d91fa363cc7ec9517782d5dbf19e699fbe375509ac20c8d550c9e2db03",
    "linear-delivery-views-v1.attestation.json": "99114431893cd17c958255ef6b8eb2bbfc9b3a3c9f30ab1e4214d7808306fc1c",
    "linear-delivery-views-v1.collector-binding.json": "f3423945249a9c005c4c808ab338702665bcabb55649935c080bebe1820e06f6",
    "linear-delivery-views-v1.json": "9f271e111214adc6e5ee5191b820ed455861995b7a717c426bba86490555eb15",
    "linear-delivery-views-v1.release.json": "daad256ce872027823de290051d104cc29c8ddbebe3c50645a66d4855bbb8a08",
    "linear-delivery-views-v1.zip": "47407f1e51e461d27bc1ed1130aad2455c2a36f56ef7072c54fe6add7c6a5d9f",
    "provider-delivery-views-v1.json": "85573e84c765451d1048870541fe8d3bb6d6abd7af8a991321f26363aa623905",
    "bootstrap-review-evidence-v1.json": "1d5bf220dbdef5038da4c7b4ad02cb9ad8c5ae47d59bf71ee489d2f61d65c99c",
    "bootstrap-review-evidence-v1.release.json": "9652ac74bcbdd51a1aeb4e6e807226890f4443f4e4634ca660114e7d2ffa2707",
    "bootstrap-review-evidence-v1.zip": "531ad32c33a30a6c85aa6a1d04ae43b0eb676a8153d67e2db2bc9d5b5c17ecf2",
    "core-review-evidence-v1.json": "36bc682fb3e3244b2e75f183c15586d229f0220479300346b07c4ca694565ee2",
    "core-review-evidence-v1.release.json": "4b4396387e7f944b9d5e7ecbf2ccba15e544a2d6e8656ec297a9fad15d8765c4",
    "core-review-evidence-v1.zip": "43244e181264f30914221be9b9a711e807a12905aa595ee23c5081727742c795",
    "linear-review-evidence-v1.attestation.json": "a8c267616a1e64d43fbbd7a82445d8c1663c5e9801933695a8b546a7ad0bf0a3",
    "linear-review-evidence-v1.collector-binding.json": "0fb6b334bf6ba875eb156777a1616dba9a97a8a278dff250e6d50f27caff8d38",
    "linear-review-evidence-v1.json": "9e6f195d1397b36540839b04ecef82e745fbc1530ec21b8dfecfd3dc812b9b8e",
    "linear-review-evidence-v1.release.json": "ecd40e33cf29d344ba9e7a409ab62db30236282c02e1b70488bb99ce6d9eddcc",
    "linear-review-evidence-v1.zip": "609250379ec208cac95769ab1f2eee95728bce981bee240881fbf83145bad042",
    "provider-review-evidence-v1.json": "e34d6dbe73f937344eb2ae7d9ad5daa1e87b19020f5a43b95b36a9b513695281",
}


def _load_runner():
    spec = importlib.util.spec_from_file_location("qualify_linear", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _collector_binding() -> dict:
    """Synthetic test-only input; never qualification evidence for a release."""
    return {
        "schemaVersion": "agent-skills-linear-collector-binding/1",
        "releases": {
            profile: {
                "archiveSha256": "a" * 64,
                "metadataSha256": "b" * 64,
                "profile": profile,
                "releaseId": f"{profile}-v1",
            }
            for profile in ("core", "linear")
        },
        "graphSha256": "c" * 64,
        "subjectRef": {"schemaVersion": "workflow-subject-ref/1", "opaque": "test subject"},
        "candidateRef": {"schemaVersion": "workflow-candidate-ref/1", "opaque": "test candidate"},
    }


def _host_attestation(binding: dict | None = None) -> dict:
    """Synthetic test-only attestation, not a retained host collection."""
    event_specs = (
        ("search", "miss", ["primary"]),
        ("search", "miss", ["dependency"]),
        ("create", "success", ["primary"]),
        ("create", "success", ["dependency"]),
        ("read", "success", ["primary"]),
        ("read", "success", ["dependency"]),
        ("update", "success", ["primary"]),
        ("update", "success", ["dependency"]),
        ("relations", "success", ["primary", "dependency"]),
        ("read", "relation-observed", ["primary", "dependency"]),
        ("reconciliation", "unique-match", ["primary"]),
        ("update", "canceled", ["primary"]),
        ("update", "canceled", ["dependency"]),
        ("read", "cleanup-confirmed", ["primary"]),
        ("read", "cleanup-confirmed", ["dependency"]),
    )
    return {
        "auditReferences": [
            "linear-audit:11111111111111111111111111111111",
            "linear-audit:22222222222222222222222222222222",
        ],
        "collector": {
            "kind": "trusted-host-side",
            "transport": "host-native-linear-mcp",
        },
        "collectorBinding": copy.deepcopy(binding if binding is not None else _collector_binding()),
        "entities": [
            {
                "auditReference": "linear-audit:11111111111111111111111111111111",
                "finalStatus": "canceled",
                "operationMarker": "linear-operation:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "role": "primary",
            },
            {
                "auditReference": "linear-audit:22222222222222222222222222222222",
                "finalStatus": "canceled",
                "operationMarker": "linear-operation:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "role": "dependency",
            },
        ],
        "events": [
            {
                "eventReference": f"linear-event:{index:032x}",
                "operation": operation,
                "outcome": outcome,
                "roles": roles,
                "sequence": index,
            }
            for index, (operation, outcome, roles) in enumerate(event_specs, start=1)
        ],
        "receipt": {
            "externalSystem": "linear",
            "reference": "linear-receipt:cccccccccccccccccccccccccccccccc",
        },
        "schemaVersion": "agent-skills-linear-host-attestation/2",
        "sessionReference": "linear-session:dddddddddddddddddddddddddddddddd",
    }


class LinearQualificationTests(unittest.TestCase):
    def test_bound_attestation_compares_every_field_with_independent_input(self) -> None:
        runner = _load_runner()
        binding = _collector_binding()
        self.assertIn("liveEvidence", runner.validate_attestation(_host_attestation(), binding))
        paths = [
            ("releases", profile, field)
            for profile in ("core", "linear")
            for field in ("archiveSha256", "metadataSha256", "profile", "releaseId")
        ] + [("graphSha256",), ("subjectRef", "opaque"), ("candidateRef", "opaque")]
        for path in paths:
            attestation = _host_attestation()
            target = attestation["collectorBinding"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = "d" * 64 if path[-1].endswith("Sha256") else "different"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "binding"):
                runner.validate_attestation(attestation, binding)

    def test_old_unbound_attestation_is_rejected(self) -> None:
        legacy = _host_attestation()
        legacy.pop("collectorBinding")
        legacy["schemaVersion"] = "agent-skills-linear-host-attestation/1"
        with self.assertRaisesRegex(ValueError, "attestation"):
            _load_runner().validate_attestation(legacy, _collector_binding())

    def test_binding_and_attestation_hostile_shapes_fail_closed(self) -> None:
        runner = _load_runner()
        malformed = [None, [], "", {}, {**_collector_binding(), "extra": "forbidden"}]
        for path, replacement in (
            (("schemaVersion",), "agent-skills-linear-collector-binding/0"),
            (("releases",), []),
            (("releases", "core"), []),
            (("releases", "core", "archiveSha256"), "A" * 64),
            (("releases", "linear", "releaseId"), " "),
            (("graphSha256",), True),
            (("subjectRef",), {"schemaVersion": "workflow-subject-ref/1", "opaque": "", "extra": "x"}),
            (("subjectRef", "schemaVersion"), "workflow-subject-ref/2"),
            (("candidateRef", "schemaVersion"), "workflow-candidate-ref/2"),
            (("subjectRef", "opaque"), []),
            (("candidateRef", "opaque"), " \n"),
        ):
            binding = _collector_binding()
            target = binding
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            malformed.append(binding)
        for binding in malformed:
            with self.subTest(binding=binding), self.assertRaisesRegex(ValueError, "collector_binding"):
                runner.validate_collector_binding(binding)
        for change in ("audit", "sequence", "collector", "receipt", "redaction"):
            attestation = _host_attestation()
            if change == "audit":
                attestation["auditReferences"][0] = []
            elif change == "sequence":
                attestation["events"][0]["sequence"] = True
            elif change == "collector":
                attestation["collector"] = {"kind": "unavailable", "transport": "host-native-linear-mcp"}
            elif change == "receipt":
                attestation["receipt"] = None
            else:
                attestation["collectorBinding"]["subjectRef"]["opaque"] = "/Users/private/subject"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "attestation"):
                runner.validate_attestation(attestation, attestation["collectorBinding"])

    def test_collector_input_must_exist_be_canonical_and_independently_supplied(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "binding.json"
            with self.assertRaisesRegex(ValueError, "unavailable input"):
                runner.load_collector_binding(path)
            binding = _collector_binding()
            for contents in (
                json.dumps(binding),
                _canonical_json(binding).replace('"graphSha256":', '"graphSha256":"' + "d" * 64 + '","graphSha256":'),
            ):
                path.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "canonical"):
                    runner.load_collector_binding(path)
            path.write_text(_canonical_json(binding), encoding="utf-8")
            self.assertEqual(runner.load_collector_binding(path), binding)
            with self.assertRaisesRegex(ValueError, "collector_binding"):
                runner.validate_attestation(_host_attestation(), None)
            with self.assertRaisesRegex(ValueError, "unavailable input"):
                runner.load_attestation(Path(temporary) / "unavailable.json", binding)

    def test_candidate_binding_checks_actual_release_fields_and_both_graphs(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths = self._build_candidates(directory)
            core_root, linear_root = directory / "core", directory / "linear"
            core = verify_release(paths[0], paths[1], extract_to=core_root)
            linear = verify_release(paths[2], paths[3], extract_to=linear_root)
            binding = self._binding_for_candidates(*paths)
            runner.validate_candidate_binding(binding, core, linear, core_root, linear_root)
            for profile in ("core", "linear"):
                for field in ("archiveSha256", "metadataSha256", "profile", "releaseId"):
                    wrong = copy.deepcopy(binding)
                    wrong["releases"][profile][field] = "d" * 64 if field.endswith("Sha256") else "wrong"
                    with self.subTest(profile=profile, field=field), self.assertRaisesRegex(ValueError, "collector_binding"):
                        runner.validate_candidate_binding(wrong, core, linear, core_root, linear_root)
            for root in (core_root, linear_root):
                graph = root / runner.GRAPH_PATH
                original = graph.read_bytes()
                graph.write_bytes(original + b"\n")
                with self.subTest(root=root.name), self.assertRaisesRegex(ValueError, "graph mismatch"):
                    runner.validate_candidate_binding(binding, core, linear, core_root, linear_root)
                graph.unlink()
                with self.assertRaisesRegex(ValueError, "graph unavailable"):
                    runner.validate_candidate_binding(binding, core, linear, core_root, linear_root)
                graph.write_bytes(original)

    def test_core_record_binding_rejects_stale_release_graph_and_failed_summary(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._build_candidates(Path(temporary))
            core = verify_release(paths[0], paths[1])
            binding = self._binding_for_candidates(*paths)
            record = {"release": binding["releases"]["core"], "workflowGraphSha256": binding["graphSha256"], "summary": {"result": "passed"}}
            for path in [("release", key) for key in record["release"]] + [("workflowGraphSha256",), ("summary", "result")]:
                wrong = copy.deepcopy(record)
                target = wrong
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "wrong"
                with self.subTest(path=path), self.assertRaises(runner.QualificationFailure):
                    runner._core_binding(runner.Checkpoint("core-qualification-binding", "trusted-checked-qualification"), core, wrong, binding["graphSha256"])

    def test_anti_vacuity_rejects_missing_reordered_and_empty_passing_evidence(self) -> None:
        runner = _load_runner()
        scenarios = [
            {"id": scenario, "result": "passed", "assertionCount": 1, "evidence": ["asserted"], "evidenceSource": "test-only"}
            for scenario in runner.REQUIRED_SCENARIOS
        ]
        malformed = [[], scenarios[:-1], list(reversed(scenarios))]
        for key, value in (("assertionCount", 0), ("assertionCount", True), ("assertionCount", 2), ("evidence", []), ("evidence", "asserted"), ("evidence", [""]), ("evidenceSource", ""), ("result", "skipped"), ("result", [])):
            wrong = copy.deepcopy(scenarios)
            wrong[0][key] = value
            malformed.append(wrong)
        for wrong in malformed:
            with self.subTest(scenarios=wrong), self.assertRaisesRegex(ValueError, "anti_vacuity"):
                runner._finalize_record({}, "a" * 64, "b" * 64, "linear-receipt:" + "c" * 32, wrong, _collector_binding(), "d" * 64)

    def test_cli_removes_stale_success_after_unavailable_collection_or_failure(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record_path = directory / "record.json"
            arguments = [value for name in ("core-archive", "core-metadata", "core-record", "linear-archive", "linear-metadata", "attestation", "collector-binding") for value in (f"--{name}", str(directory / name))]
            arguments.extend(("--record", str(record_path)))
            for error in (ValueError("collector unavailable"), OSError("receipt unavailable")):
                record_path.write_text(_canonical_json({"summary": {"result": "passed"}}), encoding="utf-8")
                with mock.patch.object(runner, "qualify", side_effect=error):
                    self.assertEqual(runner.main(arguments), 1)
                self.assertFalse(record_path.exists())

    def test_exact_offline_record_shape_and_tampering_are_checked(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, record_path, attestation_path = self._run_qualification(directory, _host_attestation())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(set(record), {
                "attestationSha256", "candidate", "collectorBinding", "collectorBindingSha256",
                "coreQualificationSha256", "proofBoundary", "receiptReference", "redaction",
                "scenarios", "schemaVersion", "summary",
            })
            paths = (
                directory / "core.zip", directory / "core.release.json", directory / "core-record.json",
                directory / "linear.zip", directory / "linear.release.json", attestation_path,
                directory / "collector-binding.json",
            )
            runner.validate_checked_record(record, *paths)
            changes = [
                ("collectorBindingSha256", "d" * 64),
                ("coreQualificationSha256", "d" * 64),
                ("schemaVersion", "agent-skills-linear-qualification/1"),
                ("extra", True),
            ]
            for key, value in changes:
                wrong = copy.deepcopy(record)
                wrong[key] = value
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "exact record mismatch"):
                    runner.validate_checked_record(wrong, *paths)
            wrong = copy.deepcopy(record)
            wrong["collectorBinding"]["candidateRef"]["opaque"] = "stale candidate"
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(wrong, *paths)
            wrong = copy.deepcopy(record)
            wrong["scenarios"][0]["evidence"].append("invented evidence")
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(wrong, *paths)
            tampered_attestation = directory / "tampered-attestation.json"
            attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
            attestation["sessionReference"] = "linear-session:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
            tampered_attestation.write_text(_canonical_json(attestation), encoding="utf-8")
            tampered_paths = (*paths[:5], tampered_attestation, paths[6])
            with self.assertRaisesRegex(ValueError, "attestation mismatch"):
                runner.validate_checked_record(record, *tampered_paths)
            failed_core = json.loads(paths[2].read_text(encoding="utf-8"))
            failed_core["summary"]["result"] = "failed"
            paths[2].write_text(_canonical_json(failed_core), encoding="utf-8")
            failed = runner.qualify(*paths)
            self.assertEqual(failed["summary"]["result"], "failed")
            with self.assertRaisesRegex(ValueError, "exact record mismatch"):
                runner.validate_checked_record(failed, *paths)

    def test_cli_failure_preserves_inputs_and_never_reuses_old_success(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, record_path, attestation_path = self._run_qualification(directory, _host_attestation())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            paths = {
                "core-archive": directory / "core.zip", "core-metadata": directory / "core.release.json",
                "core-record": directory / "core-record.json", "linear-archive": directory / "linear.zip",
                "linear-metadata": directory / "linear.release.json", "attestation": attestation_path,
                "collector-binding": directory / "collector-binding.json", "record": record_path,
            }
            arguments = [value for name, path in paths.items() for value in (f"--{name}", str(path))]
            binding_bytes = paths["collector-binding"].read_bytes()
            aliased = arguments[:-2] + ["--record", str(paths["collector-binding"])]
            self.assertEqual(runner.main(aliased), 1)
            self.assertEqual(paths["collector-binding"].read_bytes(), binding_bytes)
            paths["collector-binding"].unlink()
            self.assertEqual(runner.main(arguments), 1)
            self.assertFalse(record_path.exists())
            paths["collector-binding"].write_bytes(binding_bytes)
            attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
            attestation["collector"] = None
            attestation_path.write_text(_canonical_json(attestation), encoding="utf-8")
            record_path.write_text(_canonical_json({"summary": {"result": "passed"}}), encoding="utf-8")
            self.assertEqual(runner.main(arguments), 1)
            self.assertFalse(record_path.exists())

    def _build_candidates(self, directory: Path) -> tuple[Path, Path, Path, Path]:
        core_archive = directory / "core.zip"
        core_metadata = directory / "core.release.json"
        linear_archive = directory / "linear.zip"
        linear_metadata = directory / "linear.release.json"
        build_release(
            ROOT,
            core_archive,
            core_metadata,
            release_id="core-v1",
            profile_id="core",
        )
        build_release(
            ROOT,
            linear_archive,
            linear_metadata,
            release_id="linear-v2",
            profile_id="linear",
        )
        return core_archive, core_metadata, linear_archive, linear_metadata

    def test_linear_cannot_inherit_qualification_for_different_shared_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            core_archive, core_metadata, linear_archive, linear_metadata = self._build_candidates(directory)
            divergent = directory / "divergent-linear"
            verify_release(linear_archive, linear_metadata, extract_to=divergent)
            diagnosis = divergent / "agent_skills/diagnosis.py"
            original = diagnosis.read_text(encoding="utf-8")
            self.assertIn("elif gaps:", original)
            diagnosis.write_text(original.replace("elif gaps:", "elif True:"), encoding="utf-8")
            changed_archive = directory / "changed-linear.zip"
            changed_metadata = directory / "changed-linear.release.json"
            build_release(divergent, changed_archive, changed_metadata,
                          release_id="linear-v2", profile_id="linear")
            verify_release(changed_archive, changed_metadata)
            with mock.patch.object(self, "_build_candidates", return_value=(
                core_archive, core_metadata, changed_archive, changed_metadata,
            )):
                outcome, record, _ = self._run_qualification(directory, _host_attestation())
            self.assertEqual(outcome.returncode, 1, outcome.stdout + outcome.stderr)
            self.assertFalse(record.exists())

    def _run_qualification(
        self,
        directory: Path,
        attestation: dict,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        core_archive, core_metadata, linear_archive, linear_metadata = (
            self._build_candidates(directory)
        )
        binding = self._binding_for_candidates(core_archive, core_metadata, linear_archive, linear_metadata)
        binding_path = directory / "collector-binding.json"
        binding_path.write_text(_canonical_json(binding), encoding="utf-8")
        attestation = copy.deepcopy(attestation)
        attestation["collectorBinding"] = copy.deepcopy(binding)
        core_record = directory / "core-record.json"
        # Synthetic unit-test fixture, not checked qualification evidence.
        core_record.write_text(_canonical_json({
            "release": binding["releases"]["core"],
            "workflowGraphSha256": binding["graphSha256"],
            "summary": {"result": "passed"},
        }), encoding="utf-8")
        attestation_path = directory / "attestation.json"
        attestation_path.write_text(_canonical_json(attestation), encoding="utf-8")
        record_path = directory / "record.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--core-archive",
                str(core_archive),
                "--core-metadata",
                str(core_metadata),
                "--core-record",
                str(core_record),
                "--linear-archive",
                str(linear_archive),
                "--linear-metadata",
                str(linear_metadata),
                "--attestation",
                str(attestation_path),
                "--collector-binding",
                str(binding_path),
                "--record",
                str(record_path),
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, record_path, attestation_path

    def test_product_projection_compares_runtime_bytes_and_profile_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            runtime = directory / "runtime"
            runtime.mkdir()
            for name in RUNTIME_FILES - {"runtime.json"}:
                (runtime / name).write_text("// product projection fixture\n")

            def build(profile: str):
                entries = _release_view(ROOT, profile)
                descriptor = {
                    "schemaVersion": "delivery-runtime/1", "runtimeVersion": "fixture-1", "nodeMinimum": "22.6.0",
                    "workflowContentSha256": _path_digest(sorted(entries), entries),
                    "files": [{"path": name, "sha256": hashlib.sha256((runtime / name).read_bytes()).hexdigest()}
                              for name in sorted(RUNTIME_FILES - {"runtime.json"})],
                }
                (runtime / "runtime.json").write_text(_canonical_json(descriptor))
                archive, metadata = directory / f"{profile}.zip", directory / f"{profile}.json"
                build_release(ROOT, archive, metadata, release_id=f"{profile}-product", profile_id=profile, runtime_directory=runtime)
                return archive, metadata

            core = build("core")
            linear = build("linear")
            with mock.patch.object(self, "_build_candidates", return_value=(*core, *linear)):
                result, record, _ = self._run_qualification(directory, _host_attestation())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(record.exists())

            (runtime / "kernel.mjs").write_text("// different but honestly hashed implementation\n")
            linear = build("linear")
            with mock.patch.object(self, "_build_candidates", return_value=(*core, *linear)):
                result, record, _ = self._run_qualification(directory, _host_attestation())
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(record.exists())
            with self.assertRaisesRegex(ValueError, "different core bytes"):
                _load_runner().qualify(*core, directory / "core-record.json", *linear,
                                       directory / "attestation.json", directory / "collector-binding.json")

    def _binding_for_candidates(
        self, core_archive: Path, core_metadata: Path, linear_archive: Path, linear_metadata: Path,
    ) -> dict:
        binding = _collector_binding()
        for archive, metadata in ((core_archive, core_metadata), (linear_archive, linear_metadata)):
            release = verify_release(archive, metadata)
            binding["releases"][release.profile_id] = {
                "archiveSha256": release.archive_sha256,
                "metadataSha256": release.metadata_sha256,
                "profile": release.profile_id,
                "releaseId": release.release_id,
            }
        with zipfile.ZipFile(core_archive) as archive:
            binding["graphSha256"] = _load_runner().digest_bytes(archive.read("workflows/delivery-v1.json"))
        return binding

    def test_exact_linear_qualification_is_redacted_and_binds_retained_attestation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            secret = "live-connector-secret-must-not-leak"
            environment = {**os.environ, "QUALIFICATION_SECRET_SENTINEL": secret}
            with mock.patch.dict(os.environ, environment, clear=True):
                result, record_path, attestation_path = self._run_qualification(
                    directory,
                    _host_attestation(),
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(set(record["candidate"]), {"core", "linear"})
            self.assertEqual(record["candidate"]["core"]["profile"], "core")
            self.assertEqual(record["candidate"]["linear"]["profile"], "linear")
            self.assertRegex(
                record["candidate"]["core"]["sourceTreeSha256"], r"^[a-f0-9]{64}$"
            )
            self.assertRegex(
                record["candidate"]["linear"]["sourceTreeSha256"], r"^[a-f0-9]{64}$"
            )
            self.assertEqual(
                record["attestationSha256"],
                _load_runner().digest_bytes(attestation_path.read_bytes()),
            )
            self.assertEqual(
                record["receiptReference"],
                _host_attestation()["receipt"]["reference"],
            )
            self.assertNotIn("auditReferences", record)
            self.assertNotIn("qualificationDriverSha256", record)
            self.assertEqual(record["schemaVersion"], "agent-skills-linear-qualification/2")
            self.assertEqual(record["collectorBinding"], json.loads(attestation_path.read_text())["collectorBinding"])
            self.assertEqual(record["collectorBindingSha256"], _load_runner().digest_bytes((directory / "collector-binding.json").read_bytes()))
            self.assertTrue(
                all("checkpointSha256" not in scenario for scenario in record["scenarios"])
            )
            self.assertEqual(record["summary"]["result"], "passed")
            serialized = record_path.read_text(encoding="utf-8")
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("rawHostPayload", serialized)
            self.assertNotIn("rawIdempotencyKey", serialized)

    def test_install_discovery_is_hermetic_without_preexisting_host_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            empty_home = directory / "empty-home"
            empty_home.mkdir()
            environment = {
                **os.environ,
                "AGENT_SKILLS_EXECUTION_CONTEXT": "pull-request",
                "CLAUDE_CONFIG_DIR": str(empty_home / "claude"),
                "CODEX_HOME": str(empty_home / "codex"),
                "GITHUB_EVENT_NAME": "pull_request",
                "HOME": str(empty_home),
                "XDG_CONFIG_HOME": str(empty_home / "xdg"),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                result, record_path, _ = self._run_qualification(
                    directory,
                    _host_attestation(),
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            scenario = next(
                scenario
                for scenario in record["scenarios"]
                if scenario["id"] == "linear-install-and-host-discovery"
            )
            self.assertEqual(scenario["result"], "passed")
            self.assertIn(
                "qualification-owned roots isolate both host projections",
                scenario["evidence"],
            )
            self.assertNotIn(str(empty_home), record_path.read_text(encoding="utf-8"))
            self.assertFalse((empty_home / "codex").exists())
            self.assertFalse((empty_home / "claude").exists())

    def test_attestation_rejects_duplicate_references_and_broken_operation_causality(
        self,
    ) -> None:
        runner = _load_runner()
        invalid_documents = []

        duplicate_audit = _host_attestation()
        duplicate_audit["auditReferences"][1] = duplicate_audit["auditReferences"][0]
        invalid_documents.append(duplicate_audit)

        duplicate_event = _host_attestation()
        duplicate_event["events"][1]["eventReference"] = duplicate_event["events"][0][
            "eventReference"
        ]
        invalid_documents.append(duplicate_event)

        create_before_search = _host_attestation()
        create_before_search["events"][0]["operation"] = "create"
        invalid_documents.append(create_before_search)

        repeated_create = _host_attestation()
        repeated_create["events"][10]["operation"] = "create"
        invalid_documents.append(repeated_create)

        missing_cleanup = _host_attestation()
        missing_cleanup["events"][-1]["outcome"] = "success"
        invalid_documents.append(missing_cleanup)

        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaisesRegex(ValueError, "linear_qualification.attestation"):
                    runner.validate_attestation(document, _collector_binding())

    def test_attestation_must_be_canonical_json(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "attestation.json"
            path.write_text(json.dumps(_host_attestation()), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "canonical"):
                runner.load_attestation(path, _collector_binding())

    def test_controlled_boundary_matrix_covers_every_declared_degraded_outcome(self) -> None:
        runner = _load_runner()
        contract = json.loads(
            (
                ROOT
                / "skills/linear-tracker-adapter/references/adapter-contract.json"
            ).read_text(encoding="utf-8")
        )

        matrix = runner.controlled_boundary_matrix(contract)

        self.assertEqual(
            {case["input"] for case in matrix},
            set(contract["failureOutcomes"]),
        )
        self.assertTrue(all(case["action"] for case in matrix))
        self.assertTrue(
            all(
                case["repeatMutation"] is False
                for case in matrix
                if case["input"].startswith("mutation-")
            )
        )

    def test_source_tree_digest_uses_platform_neutral_posix_path_order(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "candidate"
            skill = root / "adapter" / "SKILL.md"
            reference = root / "adapter" / "references" / "contract.json"
            manifest = root / "release-manifest.json"
            skill.parent.mkdir(parents=True)
            reference.parent.mkdir(parents=True)
            skill.write_bytes(b"skill\n")
            reference.write_bytes(b"reference\n")
            manifest.write_bytes(b"ignored\n")

            expected = runner._digest(
                [
                    {
                        "path": "adapter/SKILL.md",
                        "sha256": runner.digest_bytes(b"skill\n"),
                    },
                    {
                        "path": "adapter/references/contract.json",
                        "sha256": runner.digest_bytes(b"reference\n"),
                    },
                ]
            )

            self.assertEqual(runner.source_tree_sha256(root), expected)

    def test_transport_sensor_inspects_only_candidate_delta_and_rejects_real_actions(
        self,
    ) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            core_archive, core_metadata, linear_archive, linear_metadata = (
                self._build_candidates(directory)
            )
            core_candidate = directory / "core-candidate"
            linear_candidate = directory / "linear-candidate"
            verify_release(core_archive, core_metadata, extract_to=core_candidate)
            verify_release(linear_archive, linear_metadata, extract_to=linear_candidate)
            contract = json.loads(
                (
                    linear_candidate
                    / "skills/linear-tracker-adapter/references/adapter-contract.json"
                ).read_text(encoding="utf-8")
            )
            evidence = runner.inspect_candidate_transport_boundary(
                linear_candidate,
                core_candidate,
                contract,
            )
            self.assertIn("host-native MCP capability set remains complete", evidence)

            harmless = directory / "harmless-prose"
            shutil.copytree(linear_candidate, harmless)
            skill = harmless / "skills/linear-tracker-adapter/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8")
                + "\nDiscuss credential, OAuth, and subprocess boundaries without executing them.\n",
                encoding="utf-8",
            )
            notes = harmless / "skills/linear-tracker-adapter/boundary_notes.py"
            notes.write_text(
                "# Never use api.linear.app or @linear/sdk as an alternate transport.\n",
                encoding="utf-8",
            )
            extension = harmless / "skills/linear-tracker-adapter/extension.js"
            extension.write_text(
                "export const normalizeReference = value => value.trim();\n",
                encoding="utf-8",
            )
            inactive_transport = (
                harmless / "skills/linear-tracker-adapter/inactive_transport.js"
            )
            inactive_transport.write_text(
                'const transport = require("node:https");\n'
                'const documented = "https://api.linear.app/graphql";\n'
                "export { documented, transport };\n",
                encoding="utf-8",
            )
            runner.inspect_candidate_transport_boundary(harmless, core_candidate, contract)

            # The delivery views are outside the boundary: their pages and snapshot commands reach the providers
            # they are configured for. The same actions anywhere else in the linear profile still fail.
            views = directory / "delivery-views-transport"
            shutil.copytree(linear_candidate, views)
            for name, source in (("extra.js", 'fetch("/view/line-map.html");\n'),
                                 ("extra.py", 'urllib.request.urlopen("https://api.linear.app/graphql")\n')):
                (views / "skills/delivery-views/scripts" / name).write_text(source, encoding="utf-8")
            runner.inspect_candidate_transport_boundary(views, core_candidate, contract)
            for relative in ("skills/orchestrate-work/scripts/extra.js", "skills/linear-tracker-adapter/extra.py"):
                with self.subTest(outside=relative):
                    mutated = directory / f"outside-{Path(relative).name}"
                    shutil.copytree(linear_candidate, mutated)
                    (mutated / relative).write_text(
                        'fetch("/view/line-map.html");\n' if relative.endswith(".js") else 'urllib.request.urlopen("https://example.invalid")\n',
                        encoding="utf-8")
                    with self.assertRaisesRegex(runner.QualificationFailure, "http-client-action"):
                        runner.inspect_candidate_transport_boundary(mutated, core_candidate, contract)

            hostile = (
                (
                    "python-direct-endpoint",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.py",
                    'import requests\nrequests.post("https://api.linear.app/graphql")\n',
                ),
                (
                    "node-require-alias",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.cjs",
                    'const transport = require("node:https");\n'
                    'transport.request("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-import-alias",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.mjs",
                    'import { request as send } from "node:https";\n'
                    'send("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-destructured-require",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/transport.js",
                    'const { get: fetchLinear } = require("https");\n'
                    'fetchLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-member-assigned-request",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/member-request.js",
                    'const https = require("node:https");\n'
                    "const dispatchLinear = https.request;\n"
                    'dispatchLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-member-assigned-get",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/member-get.mjs",
                    'import https from "node:https";\n'
                    "const fetchLinear = https.get;\n"
                    'fetchLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "node-combined-import",
                    "direct-linear-endpoint",
                    "skills/linear-tracker-adapter/combined-import.mjs",
                    'import https, { request as issueLinear } from "node:https";\n'
                    'issueLinear("https://api.linear.app/graphql");\n',
                ),
                (
                    "linear-sdk-import",
                    "linear-sdk",
                    "skills/linear-tracker-adapter/transport.ts",
                    'import { LinearClient } from "@linear/sdk";\nnew LinearClient();\n',
                ),
                (
                    "shell-command",
                    "command-transport",
                    "skills/linear-tracker-adapter/transport.sh",
                    "curl https://example.invalid/provider\n",
                ),
            )
            for case_name, category, relative, source in hostile:
                with self.subTest(case=case_name):
                    mutated = directory / f"mutated-{case_name}"
                    shutil.copytree(linear_candidate, mutated)
                    target = mutated / relative
                    target.write_text(source, encoding="utf-8")
                    with self.assertRaisesRegex(
                        runner.QualificationFailure,
                        category,
                    ):
                        runner.inspect_candidate_transport_boundary(
                            mutated,
                            core_candidate,
                            contract,
                        )

    def test_historical_protected_record_remains_bound_to_its_original_release_identities(
        self,
    ) -> None:
        runner = _load_runner()
        record = json.loads(HISTORICAL_RECORD.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            core_archive, core_metadata, linear_archive, linear_metadata = (
                tuple(PRODUCT / name for name in ("core-product-v1.zip", "core-product-v1.release.json", "linear-product-v1.zip", "linear-product-v1.release.json"))
            )
            core_candidate, linear_candidate = directory / "core", directory / "linear"
            core = verify_release(core_archive, core_metadata, extract_to=core_candidate)
            linear = verify_release(linear_archive, linear_metadata, extract_to=linear_candidate)
            binding = runner.load_collector_binding(HISTORICAL_BINDING)
            attested = runner.load_attestation(HISTORICAL_ATTESTATION, binding)
            runner.validate_candidate_binding(
                binding, core, linear, core_candidate, linear_candidate
            )
            self.assertEqual(
                record["candidate"],
                {
                    "core": runner._release_view(core, runner.source_tree_sha256(core_candidate)),
                    "linear": runner._release_view(linear, runner.source_tree_sha256(linear_candidate)),
                },
            )
            self.assertEqual(record["collectorBinding"], binding)
            self.assertEqual(
                record["collectorBindingSha256"],
                hashlib.sha256(HISTORICAL_BINDING.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                record["attestationSha256"],
                hashlib.sha256(HISTORICAL_ATTESTATION.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                record["coreQualificationSha256"],
                hashlib.sha256((PRODUCT / "core-product-v1.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(record["receiptReference"], attested["receiptReference"])
            self.assertEqual(record["summary"]["result"], "passed")

    def test_historical_product_qualification_bytes_remain_immutable(self) -> None:
        for name, expected in HISTORICAL_PRODUCT_SHA256.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((PRODUCT / name).read_bytes()).hexdigest(), expected)

    def test_checked_product_linear_record_replays_exactly(self) -> None:
        # The retained record was earned against the previous adapter contract;
        # it is re-earned with the pending linear set.
        skip_if_pending(self, "core", "linear")
        replay = _load_runner().qualify(
            PRODUCT / "core-run-delivery-v1.zip",
            PRODUCT / "core-run-delivery-v1.release.json",
            PRODUCT / "core-run-delivery-v1.json",
            PRODUCT / "linear-run-delivery-v1.zip",
            PRODUCT / "linear-run-delivery-v1.release.json",
            ATTESTATION,
            BINDING,
        )
        self.assertEqual(_load_runner().canonical_json(replay).encode(), RECORD.read_bytes(),
                         "the record replays byte for byte")

    def test_the_frozen_collector_input_names_the_checked_archives_with_fresh_references(self) -> None:
        # Ungated by the pending marker: the input is frozen before the live run, so it must already be right while
        # the linear set waits for its attestation.
        binding = _load_runner().load_collector_binding(BINDING)
        sha256 = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        graphs = set()
        for profile in ("core", "linear"):
            product = PRODUCT / f"{profile}-run-delivery-v1"
            release = binding["releases"][profile]
            self.assertEqual(release["archiveSha256"], sha256(product.with_suffix(".zip")), profile)
            self.assertEqual(release["metadataSha256"], sha256(product.with_suffix(".release.json")), profile)
            self.assertEqual(release["releaseId"], json.loads(product.with_suffix(".release.json").read_text())["releaseId"])
            self.assertEqual(release["profile"], profile)
            with zipfile.ZipFile(product.with_suffix(".zip")) as archive:
                graphs.add(hashlib.sha256(archive.read("workflows/delivery-v1.json")).hexdigest())
        self.assertEqual(graphs, {binding["graphSha256"]})
        # Fresh references: no earlier collector input, current or retained, ever used them.
        earlier = [path for path in [*PRODUCT.glob("*.collector-binding.json"), *(ROOT / "qualifications/history").glob("*collector-binding.json")]
                   if path != BINDING]
        self.assertTrue(earlier)
        used = {json.loads(path.read_text())[ref]["opaque"] for path in earlier for ref in ("subjectRef", "candidateRef")}
        for ref in ("subjectRef", "candidateRef"):
            self.assertNotIn(binding[ref]["opaque"], used, ref)
        # The collection steps name exactly this input.
        notes = (ROOT / "docs/releases/run-delivery-v1.md").read_text(encoding="utf-8")
        for digest in (sha256(BINDING), binding["releases"]["core"]["archiveSha256"], binding["releases"]["linear"]["archiveSha256"]):
            self.assertIn(digest, notes)

    def test_checked_product_workflow_projection_matches_current_source(self) -> None:
        current_products = {
            "core": ("core-run-delivery-v1", "08610e46436ad0239f1171dd489bc1cbeb342fc01a9ce64d7e50920e95f5b0cb"),
            "linear": ("linear-run-delivery-v1", "9d75dd6f007fdaa2c830c4e2f14176c0844e30df5f5e3a91451da605b44bc949"),
            # The product an adopter installs: no qualification set gates it, so its projection always holds.
            "run-delivery": ("run-delivery-run-delivery-v1", "8261eb3143d8e81e74711e9fa524b2f4c3298ed4f1c237210ccc2b9f9ca611df"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for profile in ("core", "linear", "run-delivery"):
                product, expected_archive_sha256 = current_products[profile]
                archive = PRODUCT / f"{product}.zip"
                metadata = PRODUCT / f"{product}.release.json"
                extracted = directory / profile
                release = verify_release(archive, metadata, extract_to=extracted)
                self.assertEqual(release.archive_sha256, expected_archive_sha256)
                projected = build_release(extracted, directory / f"{profile}-projection.zip",
                                          directory / f"{profile}-projection.json",
                                          release_id=release.release_id, profile_id=profile)
                current = build_release(ROOT, directory / f"{profile}-current.zip",
                                        directory / f"{profile}-current.json",
                                        release_id=release.release_id, profile_id=profile)
                if profile == "run-delivery" or pending(profile) is None:
                    self.assertEqual(projected, current, "checked product workflows differ from current source")
        self.assertIn("linear-run-delivery-v1", (ROOT / "README.md").read_text())
        skip_if_pending(self, "core", "linear")

    def test_durable_corpus_contains_no_plan_or_ticket_ids(self) -> None:
        durable_paths = [ROOT / "README.md"]
        for root_name in (
            "agent_skills",
            "docs",
            "qualifications",
            "schemas",
            "scripts",
            "skills",
            "tests",
        ):
            durable_paths.extend(
                path
                for path in (ROOT / root_name).rglob("*")
                if path.is_file() and path.suffix in {".json", ".md", ".py"}
                and not any(path.is_relative_to(ROOT / "docs" / kind) for kind in ("plans", "delivery"))
                # The line map's golden corpus is recorded delivery records, which keep their ticket ids.
                and not path.is_relative_to(ROOT / "tests" / "fixtures" / "delivery-views" / "line-map")
                # The history page's golden corpus is recorded delivery records, which keep their ticket ids.
                and not path.is_relative_to(ROOT / "tests" / "fixtures" / "delivery-views" / "history")
            )
        plan_unit = re.compile(r"\bU\d+[A-Za-z]?\b")
        ticket_id = re.compile(r"\b[A-Z]\d{2,}-\d+\b")

        for path in durable_paths:
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(plan_unit.search(content))
                self.assertIsNone(ticket_id.search(content))


if __name__ == "__main__":
    unittest.main()
