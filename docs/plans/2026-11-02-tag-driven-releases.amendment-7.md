# Amendment 7: the finish line is a green local run

The operator ruled that this delivery does not wait on CI. This overrides the plan's finish line and any unit's requirement to prove a
result on a CI run before merging.

- **The finish line.** The integration change merges to `main` on a green full local run of the suite on its exact head, with
  `validate-registry` and `cut-release`. CI on the integration change and on `main` is reported as it stands, and is not waited for.
- **Hand-offs.** A lane hands off on its local sensors and its review. A CI result it already has is evidence, and a failure it shows
  is still fixed or named; a result it does not have is named as unproven, not waited for.
- **What this does not change.** Publishing still requires CI's success on the push to `main` for the tagged commit, so a tag refuses
  until `main` is green on every system. What local runs leave unproven, such as Windows and the declared minimum Python, stays
  unproven until then, and gets a follow-up item at close-out.
