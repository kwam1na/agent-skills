#!/usr/bin/env python3
"""Test helper: the renderer's functions on a JSON request from stdin.

    {"page": {"document": ..., "runs": {...}, "config": {...}, "themeKey": "...", "history": null, "plan": ...}}
        prints the line map for those run summaries, as the renderer embeds them; a `plan` is validated and embedded
        as the renderer embeds a valid plan.json, and a `planBlock` is embedded as it is given
    {"summarize": {"view": ..., "listed": {...}, "readings": {...} | null, "reportSpecs": [...]}}
        prints the run view's summary, with its round reports when readings are given
    {"embed": <value>}
        prints the value as the page embeds it
    {"told": <document>}
        prints the document with the story the narrator tells of it, as the live server sends a record
    {"builtin": {"document": ..., "registry": <path> | null}}
        prints orchestrate-work's built-in page for the document, its story told with the registry at that path when given
    {"across": <document>}
        prints the document with what its sibling deliveries show, as the state wrapper gives it to every view
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "delivery-views" / "scripts"))

import render  # noqa: E402
import views_common  # noqa: E402

# Pages and records are UTF-8 whatever a Windows console's default is, as the views' own commands write them.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
request = json.loads(sys.stdin.read())
if "page" in request:
    asked = request["page"]
    config = views_common.validate_config(asked.get("config", {"spec": views_common.CONFIG_SPEC}))
    runs = asked.get("runs", {})
    plan = render.plan_block(views_common.validate_plan(asked["plan"])) if "plan" in asked else asked.get("planBlock")
    sys.stdout.write(render.render_line_map(asked["document"], render.within_budget(runs, list(runs)), config,
                                            theme_key=asked.get("themeKey", "delivery-views:test:theme"),
                                            history=asked.get("history"), plan=plan))
elif "summarize" in request:
    asked = request["summarize"]
    summary = render.summarize_run_view(asked["view"], asked["listed"])
    if summary is not None and asked.get("readings") is not None:
        summary = render.with_round_reports(summary, asked["view"], asked["readings"],
                                            asked.get("reportSpecs", [views_common.REPORT_SPEC]))
    sys.stdout.write(json.dumps(summary))
elif "embed" in request:
    sys.stdout.write(render.embed(request["embed"]))
elif "builtin" in request:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "orchestrate-work" / "scripts"))
    import render_state  # noqa: E402
    asked = request["builtin"]
    if asked.get("registry"):
        render_state.narrator.journal_words.REGISTRY_FILE = Path(asked["registry"])
    sys.stdout.write(render_state.render_static_page(asked["document"]))
elif "across" in request:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "orchestrate-work" / "scripts"))
    import render_state  # noqa: E402
    sys.stdout.write(json.dumps(render_state.with_across(request["across"])))
elif "told" in request:
    sys.stdout.write(json.dumps(render.with_story({key: value for key, value in request["told"].items() if key != "story"}, None)))
