"""The declared posture through what ships: the built runtime's resolver, a built product's four commands, and
the same vectors `run_delivery.py`'s reader answers."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.lifecycle import Lifecycle
from built_product import build_product
from lifecycle_support import initialize_repository, run_git

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "product-runtime" / "kernel.mjs"
VECTORS = ROOT / "tests" / "fixtures" / "delivery-posture-vectors.json"
POSTURE_PATH = ".agents/delivery-posture.json"
COMMANDS = ("verify", "gate", "record", "resume")
# A hanging git is stopped at the resolver's own 15 second bound, and it reads more than once in the worst case.
READ_LIMIT_SECONDS = 90
# Valid for the runtime's config check, and as erasable TypeScript it needs nothing installed.
CONSUMER_CONFIG = json.loads(r"""{"gateId": "posture-fixture.pr-admission", "baseRef": "origin/main", "storageNamespace": "delivery-harness/", "acceptedEnvelopeSpecs": ["delivery-evidence/1"], "identityVersions": ["delivery-harness-tree/v1"], "computingIdentityVersion": "delivery-harness-tree/v1", "reviewNeutral": [{"prefix": "docs/reports/"}, {"prefix": "docs/solutions/"}, {"prefix": "telemetry/delivery-runs/"}, {"prefix": "delivery/records/"}], "recordNeutral": [{"prefix": "delivery/records/"}], "pathClassification": {"generated": [{"kind": "prefix", "value": "packages/conformance/vectors/"}], "test": [{"kind": "glob", "value": "**/*.test.ts"}], "lockfile": [{"kind": "glob", "value": "**/package-lock.json"}]}, "activationThreshold": 1, "providers": [{"id": "delivery-harness.independent-review", "findingCodes": []}], "agentEnvSignals": ["CLAUDE_CODE", "CLAUDECODE"], "ciPolicies": [{"id": "github-actions", "requiredEnv": [{"variable": "GITHUB_ACTIONS", "equals": "true"}, {"variable": "CI", "equals": "true"}]}], "ciPolicyEnvKey": "DELIVERY_HARNESS_CI_POLICY", "preparationWiringPaths": ["harness.config.ts"], "preparationCommands": [], "obligations": [{"id": "review.green", "activation": {"kind": "relevant_change"}, "freshness": "exact_candidate", "providers": ["delivery-harness.independent-review"], "acceptedPayloadSpecs": ["review.green/2", "review.green/1"], "allowedResolutionKinds": ["satisfied_evidence", "waived", "not_applicable"], "humanWaiverAllowed": true, "minimumAttestationLevel": "self", "ciDelegationPolicyIds": [], "remediation": {"default": [{"id": "run-the-review", "summary": "Run the code review and submit its evidence manifest with `delivery-harness submit-evidence`.", "kind": "manual_action"}]}, "waivableCodes": ["review_evidence_missing", "stale_evidence", "evidence_not_green", "unresolved_actionable_findings"], "nonWaivableCodes": ["ambiguous_records", "malformed_record", "unknown_provider", "live_provider_missing", "ambiguous_live_provider", "live_provider_failed", "resolution_not_allowed"]}], "deliveryRecordPath": "delivery/records/record.json", "deliveryRecordVerification": {"baseMovement": "stale"}, "sensitivePaths": [{"id": "gate-kernel", "patterns": [{"kind": "prefix", "value": "packages/kernel/src/"}]}]}""")
RESOLVE_SCRIPT = """
const { resolveDeliveryPosture } = await import(process.argv[1]);
console.log(JSON.stringify(await resolveDeliveryPosture({ rootDir: process.argv[2], baseRef: process.argv[3] })));
"""


def declaration(posture: str) -> str:
    return json.dumps({"schemaVersion": "delivery-posture/1", "posture": posture, "reason": "A fixture's reason."}) + "\n"


def resolved_by_runtime(clone: Path, env: dict[str, str] | None = None, base: str = "origin/main") -> dict:
    """The exported resolver in the built `kernel.mjs`, by itself in a real Node process.

    `git` in the environment may be a stub, so Node is found first, from this process's own environment."""
    node = subprocess.run(["/bin/sh", "-c", "command -v node"], capture_output=True, text=True, check=True).stdout.strip()
    result = subprocess.run([node, "--no-warnings", "--input-type=module", "-e", RESOLVE_SCRIPT, KERNEL.as_uri(), str(clone), base],
                            env=env, capture_output=True, text=True, timeout=READ_LIMIT_SECONDS)
    if result.returncode != 0:
        raise AssertionError(f"the runtime's resolver failed: {result.stderr}")
    return json.loads(result.stdout)


class RuntimeExportTests(unittest.TestCase):
    def test_the_built_kernel_exports_the_resolver_and_its_result_type(self) -> None:
        types = (ROOT / "product-runtime" / "kernel.d.mts").read_text(encoding="utf-8")
        self.assertIn("resolveDeliveryPosture", types)
        self.assertIn("DeliveryPostureResult", types)
        with tempfile.TemporaryDirectory() as folder:
            result = resolved_by_runtime(Path(folder))
        self.assertEqual(result["posture"], "gate")
        self.assertEqual(result["code"], "posture_unreadable")
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(set(result), {"posture", "reason", "source", "baseTipSha", "code"})


@unittest.skipIf(os.name == "nt", "POSIX product entry")
class BuiltProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.environment = mock.patch.dict(os.environ)
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        # A pull-request CI event or a declared non-maintainer context would make the install refuse.
        os.environ.pop("GITHUB_EVENT_NAME", None)
        os.environ.pop("AGENT_SKILLS_EXECUTION_CONTEXT", None)
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.archive, cls.metadata = build_product(Path(cls.folder.name), "linear", "posture-fixture")

    def consumer(self, folder: Path, posture: str) -> Path:
        """An adopter with the product installed, a real origin, and `posture` on its base."""
        repository = initialize_repository(folder)
        run_git(repository, "branch", "-M", "main")
        (repository / "harness.config.ts").write_text(f"export default {json.dumps(CONSUMER_CONFIG)};\n", encoding="utf-8")
        (repository / ".agents").mkdir(exist_ok=True)
        (repository / POSTURE_PATH).write_text(declaration(posture), encoding="utf-8")
        run_git(repository, "add", "-A")
        run_git(repository, "commit", "-qm", "the declaration")
        remote = folder / "remote.git"
        run_git(folder, "init", "-q", "--bare", "--initial-branch=main", str(remote))
        run_git(repository, "remote", "add", "origin", str(remote))
        run_git(repository, "push", "-q", "origin", "main")
        run_git(repository, "fetch", "-q", "origin")
        run_git(repository, "remote", "set-head", "origin", "main")
        run_git(repository, "checkout", "-q", "-b", "candidate")
        Lifecycle(repository).install(self.archive, self.metadata, maintainer=True)
        return repository

    def harness(self, repository: Path, command: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(repository / ".agent-skills/current"), "--root", str(repository), "harness", command],
                              text=True, capture_output=True, timeout=READ_LIMIT_SECONDS)

    def test_each_command_exits_0_naming_the_posture_and_source_with_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), "focused")
            tip = run_git(repository, "rev-parse", "refs/remotes/origin/main")
            for command in COMMANDS:
                with self.subTest(command=command):
                    result = self.harness(repository, command)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "outcome: focused\nposture: focused\nreason: A fixture's reason.\n"
                                                    f"source: origin/main\nbase tip: {tip}\n")

    def test_each_command_stays_gated_under_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), "gate")
            for command in COMMANDS:
                with self.subTest(command=command):
                    result = self.harness(repository, command)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("outcome: focused", result.stdout)


@unittest.skipIf(os.name == "nt", "POSIX git stubs")
class CrossReaderVectorTests(unittest.TestCase):
    """Both readers answer every vector, so a drift between them shows here.

    The harness's reader also holds the base to the remote's default branch, and every vector's remote has `main` as
    that default, so the rule is not what's compared."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_run_delivery import posture_vector_repo
        cls.posture_vector_repo = staticmethod(posture_vector_repo)
        spec = importlib.util.spec_from_file_location("run_delivery_posture_reader", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        assert spec is not None and spec.loader is not None
        cls.tool = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.tool
        cls.addClassCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(cls.tool)
        cls.file = json.loads(VECTORS.read_text(encoding="utf-8"))

    def test_both_readers_give_each_vector_its_posture_code_and_source(self) -> None:
        self.assertGreater(len(self.file["vectors"]), 0)
        for vector in self.file["vectors"]:
            with self.subTest(vector=vector["id"]), tempfile.TemporaryDirectory() as folder:
                clone, env = self.posture_vector_repo(vector, Path(folder))
                with mock.patch.dict(os.environ, env, clear=True):
                    python = self.tool.resolve_delivery_posture(clone, "origin", "main", seconds=15)
                node_env = dict(env)
                runtime = resolved_by_runtime(clone, node_env)
                pick = lambda result: {key: result[key] for key in ("posture", "code", "source")}
                self.assertEqual(pick(runtime), vector["expect"], runtime)
                self.assertEqual(pick(runtime), pick(python), (runtime, python))


if __name__ == "__main__":
    unittest.main()
