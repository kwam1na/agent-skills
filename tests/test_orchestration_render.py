from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/orchestrate-work/scripts"
STATE_TOOL = SCRIPTS / "delivery_state.py"
WRAPPER = SCRIPTS / "render_state.py"
RENDERERS = SCRIPTS / "renderers"
MARKDOWN = RENDERERS / "markdown.py"
STATIC_PAGE = RENDERERS / "static_page.py"
HEAD_A = "a" * 40

HOSTILE = "</script><img src=x onerror=alert(1)> [click](javascript:alert(1)) <b>bold</b>\u2028next\u2029line https://example.test"


def run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8", check=False)


def state_tool(directory: Path, *args: str, stdin: str | None = None) -> dict:
    result = run([sys.executable, "-B", str(STATE_TOOL), "--dir", str(directory), *args], stdin)
    document = json.loads(result.stdout)
    if result.returncode != 0:
        raise AssertionError(f"state tool refused {args}: {document}")
    return document


def wrapper(directory: Path, *args: str, stdin: str | None = None) -> tuple[int, dict]:
    result = run([sys.executable, "-B", str(WRAPPER), "--dir", str(directory), *args], stdin)
    return result.returncode, json.loads(result.stdout)


def python_argv(source: str, folder: Path, name: str) -> list[str]:
    script = folder / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    return [sys.executable, "-B", str(script)]


class DeliveryStateFixture(unittest.TestCase):
    """A real state directory, written only through the state tool."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.state_dir = self.root / "state"
        self.tools = self.root / "tools"
        self.tools.mkdir()
        state_tool(self.state_dir, "init", "--headline", HOSTILE)
        version = state_tool(self.state_dir, "show")["state"]["version"]
        changes = {
            "lanes": [
                {
                    "id": "lane-1",
                    "title": HOSTILE,
                    "focus": "Renderer [docs](https://example.test/x)",
                    "state": "working",
                    "waitingOn": None,
                    "since": "2026-10-20T10:00:00Z",
                }
            ],
            "queue": [
                {
                    "order": 1,
                    "subject": "change-1",
                    "title": HOSTILE,
                    "lane": "lane-1",
                    "stage": "queued",
                    "head": HEAD_A,
                    "syncedHead": None,
                    "checks": [],
                    "leadInputs": [],
                    "reportedRounds": 2,
                    "runId": None,
                }
            ],
            "finish": [{"id": "finish-1", "text": HOSTILE, "met": False}],
            "followups": [{"id": "follow-1", "title": "Later <i>work</i>", "reference": "ticket-9"}],
            "extensions": {"adopter": {"note": HOSTILE, "nested": [1, {"a": None}]}},
        }
        state_tool(self.state_dir, "patch", "--writer", "lead", "--version", str(version), "--patch", json.dumps(changes))
        state_tool(self.state_dir, "journal", "--kind", "decision.recorded", "--summary", HOSTILE, "--lane", "lane-1")
        self.document = state_tool(self.state_dir, "show")

    def config(self, renderers: list[dict] | None = None, publishers: list[dict] | None = None) -> str:
        return json.dumps({"directory": "state", "renderers": renderers or [], "publishers": publishers or []})

    def capture_renderer(self, name: str) -> dict:
        """An adopter renderer that echoes its stdin as its view."""
        return {
            "id": name,
            "argv": python_argv("import sys\nsys.stdout.write(sys.stdin.read())\n", self.tools, name),
            "output": f"{name}.json",
        }


class WrapperTests(DeliveryStateFixture):
    def test_the_same_state_renders_identically_twice(self) -> None:
        renderers = [
            {"id": "summary", "builtin": "markdown", "output": "delivery.md"},
            {"id": "page", "builtin": "static-page", "output": "delivery.html"},
        ]
        outputs = []
        for _ in range(2):
            code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
            self.assertEqual(code, 0, result)
            outputs.append({name: (self.state_dir / name).read_bytes() for name in ("delivery.md", "delivery.html")})
        self.assertEqual(outputs[0], outputs[1])

    def test_an_invalid_state_is_refused_before_any_renderer_runs(self) -> None:
        marker = self.root / "ran"
        renderer = {
            "id": "witness",
            "argv": python_argv(f"open({str(marker)!r}, 'w').close()\n", self.tools, "witness"),
            "output": "witness.txt",
        }
        publisher = {"id": "store", "argv": renderer["argv"]}
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        state["queue"][0]["stage"] = "teleported"
        (self.state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

        code, result = wrapper(self.state_dir, "--state-config", self.config([renderer], [publisher]))

        self.assertEqual(code, 2)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["code"], "state_invalid")
        self.assertFalse(marker.exists())
        self.assertFalse((self.state_dir / "witness.txt").exists())

    def test_an_invalid_journal_is_refused_before_any_renderer_runs(self) -> None:
        with (self.state_dir / "journal.jsonl").open("a", encoding="utf-8") as stream:
            stream.write('{"id": 9}\n')
        code, result = wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        self.assertEqual(code, 2)
        self.assertEqual(result["code"], "journal_invalid")
        self.assertFalse((self.state_dir / "echo.json").exists())

    def test_an_adopter_renderer_receives_exactly_the_validated_state(self) -> None:
        code, result = wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        self.assertEqual(code, 0, result)
        received = json.loads((self.state_dir / "echo.json").read_text(encoding="utf-8"))
        self.assertEqual(received, self.document)
        self.assertEqual(set(received), {"state", "journal"})

    def test_extensions_reach_renderers_unchanged(self) -> None:
        wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        received = json.loads((self.state_dir / "echo.json").read_text(encoding="utf-8"))
        self.assertEqual(received["state"]["extensions"], {"adopter": {"note": HOSTILE, "nested": [1, {"a": None}]}})

    def test_publishers_receive_the_same_validated_state(self) -> None:
        sink = self.root / "published.json"
        publisher = {
            "id": "store",
            "argv": python_argv(
                f"import sys\nopen({str(sink)!r}, 'w', encoding='utf-8').write(sys.stdin.read())\n",
                self.tools,
                "store",
            ),
        }
        code, result = wrapper(self.state_dir, "--state-config", self.config(publishers=[publisher]))
        self.assertEqual(code, 0, result)
        self.assertEqual(json.loads(sink.read_text(encoding="utf-8")), self.document)
        self.assertEqual(result["publishers"], [{"exitCode": 0, "id": "store", "ok": True}])

    def test_a_failing_publisher_is_recorded_and_does_not_fail_the_wrapper(self) -> None:
        failing = {"id": "down", "argv": python_argv("import sys\nsys.stderr.write('store down')\nsys.exit(3)\n", self.tools, "down")}
        slow = {
            "id": "slow",
            "argv": python_argv("import time\ntime.sleep(5)\n", self.tools, "slow"),
            "timeoutSeconds": 1,
        }
        missing = {"id": "gone", "argv": [str(self.tools / "no-such-program")]}
        renderers = [{"id": "summary", "builtin": "markdown", "output": "delivery.md"}]

        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers, [failing, slow, missing]))

        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])
        self.assertTrue((self.state_dir / "delivery.md").exists())
        by_id = {entry["id"]: entry for entry in result["publishers"]}
        self.assertEqual(by_id["down"]["ok"], False)
        self.assertEqual(by_id["down"]["exitCode"], 3)
        self.assertIn("store down", by_id["down"]["error"])
        self.assertEqual(by_id["slow"]["ok"], False)
        self.assertEqual(by_id["slow"]["error"], "timed out after 1s")
        self.assertEqual(by_id["gone"]["ok"], False)
        self.assertIsNone(by_id["gone"]["exitCode"])

    def test_a_failing_renderer_keeps_its_previous_view_and_the_others_still_render(self) -> None:
        (self.state_dir / "broken.txt").write_text("previous view\n", encoding="utf-8")
        broken = {
            "id": "broken",
            "argv": python_argv("import sys\nsys.stdout.write('half')\nsys.exit(1)\n", self.tools, "broken"),
            "output": "broken.txt",
        }
        renderers = [broken, {"id": "summary", "builtin": "markdown", "output": "delivery.md"}]
        code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
        self.assertEqual(code, 1)
        self.assertEqual(result["ok"], False)
        self.assertEqual((self.state_dir / "broken.txt").read_text(encoding="utf-8"), "previous view\n")
        self.assertTrue((self.state_dir / "delivery.md").exists())
        self.assertEqual([entry["ok"] for entry in result["renderers"]], [False, True])

    def test_the_binding_state_member_is_read(self) -> None:
        binding = self.root / "orchestration.json"
        binding.write_text(
            json.dumps({"base": {"remote": "origin", "ref": "main"}, "state": json.loads(self.config([self.capture_renderer("echo")]))}),
            encoding="utf-8",
        )
        code, result = wrapper(self.state_dir, "--binding", str(binding))
        self.assertEqual(code, 0, result)
        self.assertTrue((self.state_dir / "echo.json").exists())

    def test_without_a_configuration_both_built_ins_render(self) -> None:
        code, result = wrapper(self.state_dir)
        self.assertEqual(code, 0, result)
        self.assertEqual([entry["output"] for entry in result["renderers"]], ["delivery.md", "delivery.html"])

    def test_an_unsafe_configuration_is_refused_before_anything_runs(self) -> None:
        cases = {
            "escaping output": [{"id": "x", "builtin": "markdown", "output": "../x.md"}],
            "absolute output": [{"id": "x", "builtin": "markdown", "output": str(self.root / "x.md")}],
            "state file": [{"id": "x", "builtin": "markdown", "output": "state.json"}],
            "journal file": [{"id": "x", "builtin": "markdown", "output": "journal.jsonl"}],
            "unknown builtin": [{"id": "x", "builtin": "slides", "output": "x"}],
            "both kinds": [{"id": "x", "builtin": "markdown", "argv": ["true"], "output": "x"}],
            "shell string": [{"id": "x", "argv": "cat state.json", "output": "x"}],
            "duplicate output": [
                {"id": "x", "builtin": "markdown", "output": "a.md"},
                {"id": "y", "builtin": "markdown", "output": "a.md"},
            ],
        }
        for name, renderers in cases.items():
            with self.subTest(name):
                code, result = wrapper(self.state_dir, "--state-config", self.config(renderers))
                self.assertEqual(code, 2, result)
                self.assertEqual(result["code"], "config_invalid")
        self.assertEqual(sorted(path.name for path in self.state_dir.iterdir() if not path.name.endswith(".lock")), ["journal.jsonl", "state.json"])

    def test_the_wrapper_never_writes_the_state(self) -> None:
        before = {name: (self.state_dir / name).read_bytes() for name in ("state.json", "journal.jsonl")}
        wrapper(self.state_dir, "--state-config", self.config([self.capture_renderer("echo")]))
        after = {name: (self.state_dir / name).read_bytes() for name in ("state.json", "journal.jsonl")}
        self.assertEqual(before, after)


class MarkdownRendererTests(DeliveryStateFixture):
    def render(self) -> str:
        result = run([sys.executable, "-B", str(MARKDOWN)], json.dumps(self.document))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_state_text_renders_inert(self) -> None:
        view = self.render()
        # Every character that could open a link, an image, raw HTML or an
        # autolink is backslash-escaped, so no Markdown renderer reads it as markup.
        for raw in ("</script>", "<img", "[click]", "](javascript", "<b>", "https://", "[docs]"):
            with self.subTest(raw=raw):
                self.assertNotRegex(view, r"(?<!\\)" + re.escape(raw))
        self.assertIn("\\<\\/script\\>", view)
        self.assertIn("\\[click\\]\\(javascript\\:alert\\(1\\)\\)", view)
        self.assertNotRegex(view, r"(?<!\\)[<>\[\]]")
        # Line separators cannot start a new block.
        for separator in ("\u2028", "\u2029", "\r"):
            self.assertNotIn(separator, view)
        self.assertIn("next", view)

    def test_it_shows_the_delivery(self) -> None:
        view = self.render()
        self.assertTrue(view.startswith("# "))
        for heading in ("## Checking", "## Queue", "## Lanes", "## Finish line", "## Follow-ups", "## Leases", "## Journal"):
            self.assertIn(heading, view)
        self.assertIn("change\\-1", view)
        self.assertIn("queued", view)
        self.assertIn("decision\\.recorded", view)


class StaticPageTests(DeliveryStateFixture):
    def render(self, document: dict | None = None) -> str:
        result = run([sys.executable, "-B", str(STATIC_PAGE)], json.dumps(document or self.document))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def embedded(self, page: str) -> str:
        match = re.search(r'<script type="application/json" id="delivery-state">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_the_state_is_embedded_escaped_and_round_trips(self) -> None:
        page = self.render()
        embedded = self.embedded(page)
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            self.assertNotIn(raw, embedded)
        self.assertEqual(json.loads(embedded), self.document)
        # Only the template's own script elements close.
        self.assertEqual(page.count("</script>"), 2)

    def test_the_page_makes_no_external_references(self) -> None:
        page = self.render().lower()
        for construct in (
            "src=", "href=", "url(", "@import", "<link", "<iframe", "<object", "<embed", "<img",
            "fetch(", "xmlhttprequest", "import(", "eventsource", "websocket", "sendbeacon",
            "//", "innerhtml", "outerhtml", "insertadjacenthtml", "document.write",
        ):
            with self.subTest(construct):
                self.assertNotIn(construct, page.replace(self.embedded(page).lower(), ""))

    def test_the_page_template_passes_the_leakage_scan(self) -> None:
        for path in (STATIC_PAGE, MARKDOWN, WRAPPER):
            contents = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                with self.subTest(path=path.name, pattern=class_id):
                    self.assertIsNone(pattern.search(contents))
        page = self.render(self._plain_document())
        for class_id, pattern in LEAKAGE_PATTERNS:
            with self.subTest(output=class_id):
                self.assertIsNone(pattern.search(page))
        for banned in ("http-equiv", "xmlns", "linear-gradient", "linear", "credentials"):
            self.assertNotIn(banned, page.lower())

    def _plain_document(self) -> dict:
        document = copy.deepcopy(self.document)
        document["state"]["meta"]["headline"] = "Plain headline"
        return json.loads(json.dumps(document).replace("https://example.test", "example").replace(HOSTILE, "plain"))


@unittest.skipUnless(shutil.which("node"), "the page's script runs under a JavaScript runtime")
class StaticPageScriptTests(DeliveryStateFixture):
    """Run the page's own script against a minimal document object."""

    SHIM = r"""
const fs = require("fs");
const [pagePath, scenarioPath] = process.argv.slice(2);
const page = fs.readFileSync(pagePath, "utf8");
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
const scripts = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const embedded = scripts.find((m) => m[1].includes('id="delivery-state"'))[2];
const program = scripts.find((m) => !m[1].includes("application/json"))[2];
const created = [];
function node(tag) {
  const el = { tagName: tag, children: [], attributes: {}, _text: "", className: "", hidden: false,
    set textContent(v) { this._text = String(v); this.children = []; },
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    appendChild(c) { this.children.push(c); return c; },
    replaceChildren(...cs) { this.children = cs; this._text = ""; },
    setAttribute(k, v) { this.attributes[k] = String(v); } };
  created.push(el);
  return el;
}
const byId = { "delivery-state": { textContent: embedded }, app: node("main") };
globalThis.document = {
  getElementById: (id) => byId[id] || null,
  createElement: node,
  createTextNode: (t) => ({ textContent: String(t) }),
};
if (scenario.adapter) {
  globalThis.deliveryStateStore = {
    read: async () => scenario.adapter.document,
    subscribe: scenario.adapter.subscribe ? (cb) => { globalThis.__push = cb; return () => {}; } : undefined,
  };
}
new Function(program)();
setTimeout(async () => {
  if (scenario.push && globalThis.__push) { globalThis.__push(scenario.push); }
  await new Promise((r) => setTimeout(r, 10));
  const tags = [...new Set(created.map((e) => e.tagName))].sort();
  process.stdout.write(JSON.stringify({ text: byId.app.textContent, tags,
    source: byId.app.attributes["data-source"] || null }));
}, 10);
"""

    def run_page(self, scenario: dict, document: dict | None = None) -> dict:
        page = self.root / "page.html"
        page.write_text(
            run([sys.executable, "-B", str(STATIC_PAGE)], json.dumps(document or self.document)).stdout,
            encoding="utf-8",
        )
        shim = self.root / "shim.cjs"
        shim.write_text(self.SHIM, encoding="utf-8")
        scenario_path = self.root / "scenario.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        result = run(["node", str(shim), str(page), str(scenario_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def live_document(self, headline: str, schema_version: str = "delivery-state/1") -> dict:
        document = copy.deepcopy(self.document)
        document["state"]["meta"]["headline"] = headline
        document["state"]["schemaVersion"] = schema_version
        return document

    def test_without_an_adapter_the_embedded_state_is_shown(self) -> None:
        shown = self.run_page({})
        self.assertIn(HOSTILE, shown["text"])
        self.assertEqual(shown["source"], "embedded")

    def test_hostile_text_stays_text(self) -> None:
        shown = self.run_page({})
        # The hostile markup is shown as characters, and no element it names exists.
        self.assertIn("</script><img src=x onerror=alert(1)>", shown["text"])
        self.assertNotIn("img", shown["tags"])
        self.assertNotIn("script", shown["tags"])
        self.assertNotIn("a", shown["tags"])

    def test_a_live_store_with_the_same_schema_is_shown(self) -> None:
        shown = self.run_page({"adapter": {"document": self.live_document("Live headline")}})
        self.assertIn("Live headline", shown["text"])
        self.assertEqual(shown["source"], "live")

    def test_a_live_store_with_a_different_schema_version_falls_back_to_the_embedded_state(self) -> None:
        shown = self.run_page({"adapter": {"document": self.live_document("Future headline", "delivery-state/2")}})
        self.assertNotIn("Future headline", shown["text"])
        self.assertIn(HOSTILE, shown["text"])
        self.assertEqual(shown["source"], "embedded")

    def test_a_malformed_live_answer_falls_back_to_the_embedded_state(self) -> None:
        for answer in (None, {"state": None}, {"state": {"schemaVersion": "delivery-state/1"}}, "text"):
            with self.subTest(answer=answer):
                shown = self.run_page({"adapter": {"document": answer}})
                self.assertIn(HOSTILE, shown["text"])
                self.assertEqual(shown["source"], "embedded")

    def test_a_pushed_update_with_a_different_schema_version_is_ignored(self) -> None:
        shown = self.run_page(
            {
                "adapter": {"document": self.live_document("Live headline"), "subscribe": True},
                "push": self.live_document("Pushed future", "delivery-state/2"),
            }
        )
        self.assertNotIn("Pushed future", shown["text"])
        self.assertIn("Live headline", shown["text"])


if __name__ == "__main__":
    unittest.main()
