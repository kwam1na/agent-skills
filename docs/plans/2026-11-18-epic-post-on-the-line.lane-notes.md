# Lane notes: the epic's post on the line

Notes the plan's review left for the lane. Both lenses aligned in round 3, and the record is
`2026-11-18-epic-post-on-the-line.review.json`.

- **The mockup.** V26-3630 has the operator's mockup attached as `epic-post-mockup.html`, built on `line-map.css` and
  `mergeDrawing`'s geometry, with five states in light and dark. Match it where the plan doesn't decide.
- **The top padding pin.** `.lines-track`'s `padding` shorthand also carries a 16px bottom, which stays. Split the
  padding, or check the top value alone, so the "no literal `16px`" pin reads the top only.
- **jsdom and `var()`.** `drawFlow` reads `getComputedStyle(track).paddingTop`, which jsdom may not resolve from
  `var(--merge-top)`. The Motion scenario stubs it in its `{ run }` step, as the plan says.
- **An untested tie.** Two siblings with the same landed share and the same open stories fall to the record's order.
  No fixture pins it, by the review's choice.
- **Provenance.** Refresh `provenance.lock.json` with `scripts/update-provenance.py`. It is a regenerable listing, not
  a lane file.
- **Node.** This repository's page lane and the runtime need Node 22.6 or newer. If `node --version` says 20, prefix
  each command with `export PATH=/Users/kwamina/.nvm/versions/node/v22.23.3/bin:$PATH`.
