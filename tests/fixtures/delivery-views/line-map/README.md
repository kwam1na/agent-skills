# Line-map golden corpus

Golden outputs of the source line map, captured before the port so the port can
be held to them. The source is the parked round-card design at commit
`4ec1b64a423c289dfd62a013e9030a925ced8630` of the source repository. Its live-view tests were
updated to the design's own wording until they passed
(`capture/parked-tests.patch`), and then `capture/capture.ts` ran from that tree:

```bash
bun .characterize/capture.ts <out> delivery-records-epic=<record> round-card-record=<record>
```

Each case directory holds:

- `document.json`: the renderer's stdin document, the validated record that
  `render_state.py` pipes to an argv renderer.
- `runs-commands.json`: every `runs` command the renderer ran, keyed by its
  arguments, with its stdout, or `null` where the command failed. The capture
  replays these, so the outputs depend on nothing else.
- `golden.json`: the clock (`now`) and time zone (`UTC`) the page was opened at;
  the three embedded blocks (`delivery-state`, `delivery-runs`, `delivery-links`)
  as parsed JSON; the page's text on opening; and, for every stop, the text of
  its detail panel after a click on it, plus the text of each round card after a
  click on each round of its run.

The cases:

| Case | Document | Runs |
| --- | --- | --- |
| `parked-no-runs` | the parked fixture record | an empty inventory |
| `parked-run-view` | the parked fixture record | the parked run view, counts only |
| `parked-round-reports` | the parked fixture record | round reports with findings, fixes and a confirming round |
| `parked-round-report-unreadable` | the parked fixture record | as above, with round 2's report unreadable |
| `delivery-records-epic` | a real delivery record of nine merged pieces | their three recorded runs |
| `round-card-record` | a record built with the state tool for the two pieces that landed the round card | their two recorded runs, one with stored findings |

Real records and run listings named the capturing machine's home directory; the
capture replaced it with `~/` in its inputs before rendering, so inputs and
goldens agree. The pages were opened by the source's own jsdom harness with
`store: { read: "pending" }`, so they show the embedded record.

One accepted difference from the source: the source cut a view field at 90 and a
finding summary at 280 UTF-16 code units, and the port cuts at 90 and 280
characters (code points), as the round report format counts them. A field or
summary holding characters outside the Basic Multilingual Plane, such as emoji,
is therefore cut at a different point; no captured case holds one.
