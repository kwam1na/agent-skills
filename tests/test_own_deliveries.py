"""This repository delivers through the generation it has installed: a fresh clone is set up, and the corpus is unmoved."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ".agent-skills/current/skills/run-delivery/scripts/run_delivery.py"


def _git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *identity, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FreshClone(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        top = Path(os.path.realpath(cls.temporary.name))
        # What HEAD holds, on a base of its own, since a checkout under test may have no main branch to clone.
        seed = top / "seed"
        seed.mkdir()
        tree = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(seed)], input=tree, check=True)
        _git(seed, "init", "-q", "-b", "main")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed")
        cls.remote = top / "remote.git"
        _git(top, "clone", "-q", "--bare", str(seed), str(cls.remote))
        cls.clone = top / "clone"
        _git(top, "clone", "-q", str(cls.remote), str(cls.clone))
        shutil.rmtree(seed)
        python = str(Path(sys.executable).parent)
        cls.environment = {"PATH": os.pathsep.join([python, os.environ["PATH"]]), "HOME": cls.temporary.name,
                           "PYTHONDONTWRITEBYTECODE": "1", "LINEAR_API_KEY": "fixture", "GITHUB_TOKEN": "fixture"}

    def doctor(self, cwd: Path | None = None, *, without: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        environment = {key: value for key, value in self.environment.items() if key not in without}
        return subprocess.run(["python3", "-B", TOOL, "doctor", "--offline"], cwd=cwd or self.clone, env=environment,
                              capture_output=True, text=True, timeout=120)

    def test_the_setup_check_passes_from_a_fresh_clone(self) -> None:
        done = self.doctor()
        lines = done.stdout.splitlines()
        self.assertEqual([line for line in lines if line.split()[0] not in ("ok", "skipped")], [], done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(any(line.startswith("ok") and "release on the delivery base" in line for line in lines), done.stdout)
        self.assertTrue(any(line.startswith("ok") and "orchestration binding" in line for line in lines), done.stdout)

    def test_a_missing_piece_is_named(self) -> None:
        done = self.doctor(without=("LINEAR_API_KEY",))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertRegex(done.stdout, r"(?m)^missing\s+tracker snapshot token: LINEAR_API_KEY is unset")

        binding = self.clone / ".agents/orchestration.json"
        kept = binding.read_bytes()
        self.addCleanup(binding.write_bytes, kept)
        binding.unlink()
        done = self.doctor()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertRegex(done.stdout, r"(?m)^missing\s+orchestration binding: no \.agents/orchestration\.json")

    def test_stray_bytecode_in_the_generation_moves_nothing(self) -> None:
        stray = self.clone / ".agent-skills/current/skills/run-delivery/scripts/__pycache__"
        stray.mkdir()
        self.addCleanup(shutil.rmtree, stray)
        (stray / "run_delivery.cpython-311.pyc").write_bytes(b"stray")
        generation = self.doctor()
        self.assertRegex(generation.stdout, r"(?m)^ok\s+release generation")

        environment = {**self.environment, "PYTHONPATH": str(self.clone)}
        validated = subprocess.run(["python3", "-B", "scripts/validate-registry.py"], cwd=self.clone, env=environment,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        with tempfile.TemporaryDirectory() as out:
            built = subprocess.run(["python3", "-B", "scripts/cut-release.py", "--version", "v99.0.0-rc.1", "--out", out + "/release"],
                                   cwd=self.clone, env=environment, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            self.assertTrue((Path(out) / "release/linear-v99.0.0-rc.1.zip").is_file())


class StandingBinding(unittest.TestCase):
    def test_it_holds_no_machine_and_no_delivery(self) -> None:
        for name in ("orchestration.json", "delivery-views.json", "run-delivery.json", "delivery/candidate.py"):
            text = (ROOT / ".agents" / name).read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, name)
            self.assertNotIn("delivery-artifacts", text, name)
            self.assertNotIn("backlog-followups", text, name)
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["base"], {"remote": "origin", "ref": "main"})
        self.assertEqual(binding["state"]["liveHome"], "line-map")
        self.assertNotIn("PATH", binding["environment"])
        renderer = next(entry for entry in binding["state"]["renderers"] if entry["id"] == "line-map")
        self.assertTrue(renderer["argv"][2].startswith(".agent-skills/current/"), renderer["argv"])
        # Any later delivery's tickets, not this one's.
        pattern = binding["state"]["forgeSnapshot"]["argv"]
        self.assertEqual(pattern[pattern.index("--subject-pattern") + 1], "V26-[0-9]+")

    def test_the_candidate_step_takes_the_key_only_where_a_title_opens_with_it(self) -> None:
        def candidate(key: str, *titles: str) -> subprocess.CompletedProcess:
            with tempfile.TemporaryDirectory() as folder:
                pulls = [{"headRefName": f"branch-{index}", "headRefOid": "a" * 40, "title": title} for index, title in enumerate(titles)]
                gh = Path(folder) / "gh"
                gh.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(pulls) + "\nEOF\n", encoding="utf-8")
                gh.chmod(0o755)
                return subprocess.run([sys.executable, "-B", str(ROOT / ".agents/delivery/candidate.py"), key], capture_output=True, text=True,
                                      env={**os.environ, "PATH": os.pathsep.join([folder, os.environ["PATH"]])})

        for title in ("WORK-1: the opened form", "[WORK-1]: the bracketed form"):
            done = candidate("WORK-1", title, "WORK-2: Follow up on WORK-1", "WORK-12: a longer key")
            self.assertEqual(json.loads(done.stdout)["branch"], "branch-0", done.stderr)
        self.assertEqual(candidate("WORK-1", "WORK-2: Follow up on WORK-1").returncode, 1)

    def test_the_delivery_folders_are_ignored(self) -> None:
        for path in (".orchestration/lanes/x", ".orchestration/state/state.json", ".worktrees/x", ".claude/worktrees/x"):
            done = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
            self.assertEqual(done.returncode, 0, path)


if __name__ == "__main__":
    unittest.main()
