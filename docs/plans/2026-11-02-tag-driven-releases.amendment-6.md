# Amendment 6: CI runs the full matrix only where it decides something

The operator found a full CI run too slow to wait for, and approved these changes. They extend the shipped bugs unit, which holds
`.github/workflows/ci.yml` for this, and override the Green CI and tag-driven publish units where they differ.

- **Superseded runs stop.** A new push to a change request cancels that change request's earlier run. Runs on `main` are never
  cancelled, because publishing reads them.
- **The matrix follows the target.** A change request into `main` and a push to `main` run all three operating systems. A change
  request into any other branch runs Ubuntu, unless it carries the `full-matrix` label, which restores all three. `cut-release` runs
  on every change request.
- **Publishing is unchanged.** It still requires every matrix job and `cut-release` to have passed on the push to `main` for the
  tagged commit, with all three systems present.
- **Workers.** Windows and Ubuntu pass an explicit `--workers`, sized to the runner as macOS does, with wall times reported before and
  after.
- **This unit's proof.** The shipped bugs change request carries `full-matrix`, since its proof needs every system.
