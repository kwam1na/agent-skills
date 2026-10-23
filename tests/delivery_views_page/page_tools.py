#!/usr/bin/env python3
"""Test helper: the renderer's functions on a JSON request from stdin.

    {"page": {"document": ..., "runs": {...}, "config": {...}, "themeKey": "...", "history": null}}
        prints the line map for those run summaries, as the renderer embeds them
    {"summarize": {"view": ..., "listed": {...}, "readings": {...} | null, "reportSpecs": [...]}}
        prints the run view's summary, with its round reports when readings are given
    {"embed": <value>}
        prints the value as the page embeds it
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "delivery-views" / "scripts"))

import render  # noqa: E402
import views_common  # noqa: E402

request = json.loads(sys.stdin.read())
if "page" in request:
    asked = request["page"]
    config = views_common.validate_config(asked.get("config", {"spec": views_common.CONFIG_SPEC}))
    runs = asked.get("runs", {})
    sys.stdout.write(render.render_line_map(asked["document"], render.within_budget(runs, list(runs)), config,
                                            theme_key=asked.get("themeKey", "delivery-views:test:theme"),
                                            history=asked.get("history")))
elif "summarize" in request:
    asked = request["summarize"]
    summary = render.summarize_run_view(asked["view"], asked["listed"])
    if summary is not None and asked.get("readings") is not None:
        summary = render.with_round_reports(summary, asked["view"], asked["readings"],
                                            asked.get("reportSpecs", [views_common.REPORT_SPEC]))
    sys.stdout.write(json.dumps(summary))
elif "embed" in request:
    sys.stdout.write(render.embed(request["embed"]))
