"""Prints the cut the corpus publisher builds for a planning record: the line map corpus's `plan-shared` case, its record
and plan, through public_projection.project as hosted.collect calls it, with the plan read as the record's views read
it. Source and fixture proof only: python3 -B planning_cut.py
"""
import json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'skills/delivery-views/scripts'))
import public_projection as public  # noqa: E402
import render  # noqa: E402
import views_common as views  # noqa: E402

CASE = ROOT / 'tests/fixtures/delivery-views/line-map/plan-shared'


def main() -> int:
    document = json.loads((CASE / 'document.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / 'plan.json').write_bytes((CASE / 'plan.json').read_bytes())
        plan = render.read_plan_block(Path(folder), document)
    config = views.validate_config({'spec': 'delivery-views-config/1'})
    cut = public.project(document, {}, config, source_fingerprint='0' * 64, plan=plan)
    print(json.dumps(cut))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
