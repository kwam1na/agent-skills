# Amendment 5: the lead monitors the local machine

The operator ruled that system monitoring is part of the lead's work. In this delivery the file-event service reached 92 GB, swap
reached 63 of 64.5 GB and load reached 37 on 16 cores, and the operator noticed before the lead did. This extends the lead-wake
unit from amendment 4 and overrides nothing.

- **The signal.** `delivery_state.py wait` also returns on machine pressure, naming the value: sustained load above a multiple of
  the core count, low free disk, or swap growth since the wait started. A value it cannot read is reported as unavailable, never
  treated as healthy. A condition already seen does not wake the lead again until it clears and returns.
- **The response.** Woken on pressure, the lead finds what is loading the machine before taking more work, reduces this delivery's
  own load first, and reports what it cannot reduce to the operator. It stops nothing it did not start without the operator's word.
- **The cap.** Lane briefs carry a default cap on local test workers. A lane's review lenses still run in parallel.
