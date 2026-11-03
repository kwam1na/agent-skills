# Amendment 4: the lead keeps a wake of its own while lanes work

The operator ruled that this gap closes in this epic. A lane's hand-off sat for six minutes until the operator asked, because
the lead had ended its turn relying on the host's note, and a host may hold notes back. This adds a unit and overrides nothing.

- **The unit.** `delivery_state.py wait` reads the record only and returns on the first thing needing the lead: a hand-off, a
  merge, a stop, a broken base, a blocker or a blocked lane. It is bounded, exits differently on expiry, writes nothing, and takes
  the journal id to start after so an entry between two waits is not missed.
- **The standing rule.** A lead does not end its turn while a lane is working or a candidate is queued unless a `wait` is running
  in the background. It restarts the wait after each wake. The host's notes are a convenience, never the lead's only wake. Where
  nothing can run in the background, the lead tells the operator that it won't wake on its own.
- **Order.** It follows the lanes-as-sessions unit, which holds `skills/orchestrate-work/SKILL.md` and the guide until it merges.
  It adds its own test file, so nothing overlaps the shipped bugs unit.
