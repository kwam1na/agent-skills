# V26-3169 lane notes

The lead's standing rulings for this delivery. The plan's Decisions section is binding; these notes add what the lead decided while setting it up.

- **The base is `delivery/workflow-reliability`, not `main`.** By the operator's ruling the delivery lands on its own integration branch, so `main`
  is untouched until the operator says to integrate. Start each story from that base (`lane-start <key> --base delivery/workflow-reliability`) and open
  every change request against it. Where the plan says `main`, read the integration branch.
- **The delivery runs on the installed generation, linear-v1.3.1.** A fix you make in `skills/` doesn't reach this delivery's own tools. Work around
  the gaps your own tickets name, and add no binding key the installed queue doesn't know.
- **Ownership moves at each hand-over.** Your lane row in the delivery file lists what you own now. Ask the lead before touching anything else,
  except `provenance.lock.json`: refresh it with `scripts/update-provenance.py` and commit it on your branch whenever your gate needs it, and
  say so in your hand-off. The lead moves the lock to your lane immediately before accepting your hand-off, so `lead-accept` passes it.
- **No full suite in a lane.** Run your story's named tests and sensors, plus `scripts/validate-registry.py`, `tests/test_registry.py` and
  `scripts/update-provenance.py --check`. The queue runs the lead's selected checks. V26-3092 and V26-3109's bounded flake runs are the only exception,
  as the plan states, and they use at most four workers.
- **Never `pkill` or `killall`.** Stop only processes you started, by the pid you recorded.
- **Propose before code** where the plan says so: V26-3103's carry-over and V26-3104's other two items go to the lead at ticket start.
- **Partial tickets** (V26-2986, V26-2807) close on this repository's half; tell the lead the criteria left for Athena so the follow-up can be filed.
- **A pin proved red** refreshes the file's digest with it, so the failure is the pin's own, never `release.source_validation`.
