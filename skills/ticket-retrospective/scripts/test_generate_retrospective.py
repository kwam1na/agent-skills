import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import generate_retrospective as gr


class RedactionTests(unittest.TestCase):
    def test_redacts_secret_like_strings_and_paths(self):
        text = (
            "token=ghp_abcdefghijklmnopqrstuvwxyz123456 "
            "api=sk-proj-abcdef1234567890 "
            "path=/Users/kwamina/athena/packages"
        )

        redacted = gr.redact_text(text)

        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("sk-proj-abcdef1234567890", redacted)
        self.assertNotIn("/Users/kwamina/", redacted)
        self.assertIn("/Users/<redacted>/", redacted)


class OutputStatusTests(unittest.TestCase):
    def test_exit_code_zero_is_not_failure(self):
        self.assertFalse(gr.is_tool_failure("Process exited with code 0"))
        self.assertTrue(gr.is_tool_failure("Process exited with code 1"))


class MarkdownTests(unittest.TestCase):
    def test_markdown_contains_required_sections(self):
        report = gr.build_report(
            metadata={
                "ticket_id": "V26-202",
                "thread_id": "thread-1",
                "created_at": "2026-04-12T01:00:00Z",
                "skill_version": "v1",
                "repo": "/repo",
                "branch": "codex/V26-202",
                "pr_url": "https://example.com/pr/1",
                "status": "ok",
                "linear_issue_id": "V26-202",
            },
            delivery_gate={"pr_non_draft": True, "required_checks_green": True, "linear_in_review": True},
            timeline_summary="Timeline summary",
            inconsistencies=["none"],
            struggles=["none"],
            resolutions=["none"],
            heuristics=["do X"],
            proposed_skill_deltas=["add Y"],
            light_metrics={"retry_count": 0},
            evidence=["sample"],
        )

        required = [
            "## Metadata",
            "## Delivery Gate Check",
            "## Timeline Summary",
            "## Inconsistencies Found",
            "## Struggles and Resolutions",
            "## Reusable Heuristics",
            "## Proposed Skill Deltas",
            "## Light Metrics",
            "## Redacted Evidence Appendix",
        ]

        for heading in required:
            self.assertIn(heading, report)


class IndexTests(unittest.TestCase):
    def test_upsert_index_dedupes_run_key(self):
        with tempfile.TemporaryDirectory() as td:
            index_path = Path(td) / "index.jsonl"
            first = {
                "run_key": "V26-202:thread-1:2026-04-12T01:00:00Z",
                "ticket_id": "V26-202",
                "status": "ok",
            }
            second = {
                "run_key": "V26-202:thread-1:2026-04-12T01:00:00Z",
                "ticket_id": "V26-202",
                "status": "warning",
            }

            gr.upsert_index(index_path, first)
            gr.upsert_index(index_path, second)

            lines = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(1, len(lines))
            self.assertEqual("warning", lines[0]["status"])


class FailureHandlingTests(unittest.TestCase):
    def test_queue_and_alert_written_when_non_blocking_failure(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "ticket-retrospectives"
            payload = {
                "ticket_id": "V26-202",
                "thread_id": "thread-1",
                "handoff_ts": "2026-04-12T01:00:00Z",
            }

            alert_path, queue_path = gr.write_failure_artifacts(
                out_dir=out_dir,
                payload=payload,
                error_message="boom",
            )

            self.assertTrue(alert_path.exists())
            self.assertTrue(queue_path.exists())
            queue_lines = queue_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(queue_lines))
            queued = json.loads(queue_lines[0])
            self.assertEqual("boom", queued["error"])


class InconsistencyTests(unittest.TestCase):
    def test_claim_evidence_mismatch_is_detected(self):
        base_ts = datetime(2026, 4, 12, 1, 0, tzinfo=timezone.utc)
        events = [
            {
                "type": "response_item",
                "_parsed_ts": base_ts,
                "payload": {
                    "type": "message",
                    "content": [{"text": "All green. Ready for review."}],
                },
            }
        ]
        calls = [
            gr.ParsedCall(
                timestamp=base_ts,
                name="exec_command",
                call_id="1",
                arguments={"cmd": "bun run check"},
                raw_arguments='{"cmd":"bun run check"}',
                output="Process exited with code 1",
                output_ts=base_ts.replace(minute=1),
            )
        ]
        mismatches = gr.detect_inconsistencies(
            calls=calls,
            events=events,
            delivery_gate={"pr_non_draft": True, "required_checks_green": True, "linear_in_review": True},
            branch="codex/V26-202",
        )
        self.assertTrue(any("Claim/evidence mismatch" in item for item in mismatches))


class IntegrationRunTests(unittest.TestCase):
    def test_run_succeeds_with_session_jsonl_only(self):
        with tempfile.TemporaryDirectory() as td:
            codex_home = Path(td) / ".codex"
            session_dir = codex_home / "sessions" / "2026" / "04" / "12"
            session_dir.mkdir(parents=True, exist_ok=True)
            thread_id = "019d7575-2537-7901-843c-ebd67fea08e5"
            session_file = session_dir / f"rollout-2026-04-12T01-00-00-{thread_id}.jsonl"

            events = [
                {
                    "timestamp": "2026-04-12T01:00:10Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "c1",
                        "arguments": json.dumps({"cmd": "bun run check"}),
                    },
                },
                {
                    "timestamp": "2026-04-12T01:00:12Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": "Process exited with code 0",
                    },
                },
            ]
            session_file.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")

            args = Namespace(
                ticket_id="V26-202",
                thread_id=thread_id,
                repo_path="/Users/kwamina/athena/packages",
                branch="codex/V26-202-retro",
                pr_url="https://example.com/pr/1",
                linear_issue_id="V26-202",
                start_ts="2026-04-12T01:00:00Z",
                handoff_ts="2026-04-12T01:02:00Z",
                commit_sha=None,
                validation_summary="bun run check ✅",
                ci_check_ids=None,
                skill_version="ticket-retrospective/v1",
                codex_home=str(codex_home),
                output_dir=None,
                pr_non_draft=True,
                required_checks_green=True,
                linear_in_review=True,
                non_blocking=True,
                blocking=False,
            )

            result = gr.run(args)
            self.assertIn("report_path", result)
            report_path = Path(result["report_path"])
            self.assertTrue(report_path.exists())
            report_text = report_path.read_text(encoding="utf-8")
            self.assertIn("## Metadata", report_text)
            self.assertIn("## Timeline Summary", report_text)


if __name__ == "__main__":
    unittest.main()
