# Amendment 2: shipped bugs the CI scout found

The Green CI unit's scout found three bugs in shipped skill code. The lead added them to scope under the plan's Scout-first
rule, because the matrix gate cannot pass without them. The finish line is unchanged, and it now depends on this unit.

- **Unit: Shipped bugs on Windows and Python 3.11.** Owns `skills/delivery-views/scripts/render.py`,
  `skills/orchestrate-work/scripts/merge_queue.py` and `skills/run-delivery/scripts/run_delivery.py`, plus the test causes the
  scout held back, by lead hand-over. It follows current-set tests, which remove the drift tests that would turn a skill change
  red, and comes before standing docs. Required scenarios and sensors are on its ticket.
- **Green CI's merge condition is unchanged.** Ubuntu is green apart from failures named to another unit. This unit now owns the
  Python 3.11 failures, and current-set tests owns the digest comparisons that the scout's fixes disturb.
- **Provenance.** This unit and the lane ruling both change files the lock covers, and `provenance.lock.json` no longer belongs
  to the lane ruling alone. The delivery's binding declares it regenerable, so the queue resolves a conflict in it with the
  provenance writer, as `AGENTS.md` directs.
