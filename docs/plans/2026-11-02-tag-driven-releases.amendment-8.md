# Amendment 8: the gate is Ubuntu in CI and macOS locally, and Windows is deferred

The operator ruled that `main` has to be green in this delivery, because the first version tag refuses otherwise; that the workflow mostly
runs on Macs, so Windows is deferred for later adoption; and that macOS is validated on the machine it runs on rather than gating in CI.
This overrides amendment 7's finish line, and every earlier requirement for Windows or for macOS in CI, including the plan's matrix, "no
operating system dropped" and amendment 3's every-matrix-job rule.

- **The finish line.** The integration change is merged to `main` on a green full local run on macOS with Python 3.11, and `main`'s CI on the
  merged commit passed on Ubuntu and `cut-release`, with the run linked.
- **The publish gate.** Publishing requires Ubuntu on Python 3.11 and `cut-release` on the push to `main` for the tagged commit, and a run
  missing either publishes nothing. A macOS result, or its absence, neither allows nor blocks a release.
- **macOS in CI.** It runs on pushes to `main`, and on a change request labelled `full-matrix`, to inform. Nobody waits on it, and whoever tags
  reads it.
- **macOS locally.** A full local run on macOS with Python 3.11 is a documented step before tagging. The release job cannot verify it, so it
  rests on whoever tags.
- **Windows.** `windows-latest` leaves CI's matrix. The fixes already made stay in the code, unproven, and the docs say plainly it isn't validated.
- **The unit.** A new unit makes these changes and owns every failure on Ubuntu and on local macOS, with the shipped bugs unit's deferred
  findings that aren't about Windows.
