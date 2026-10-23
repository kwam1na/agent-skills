# History golden corpus

Golden outputs of the source history page, captured before the port so the port
can be held to them. The source is the parked round-card design at commit
`4ec1b64a423c289dfd62a013e9030a925ced8630` of the source repository, whose history
tests pass unchanged. `capture/capture-history.ts` ran from that tree, twice, with
byte-identical results:

```bash
TZ=UTC bun .characterize/capture-history.ts <out> delivery-records-epic=<record>
```

The record is the line-map corpus's `delivery-records-epic/document.json`.

Each case directory holds:

- `document.json`: the renderer's stdin document.
- `runs-commands.json`: every `runs` command the page's collection ran, keyed by
  its arguments, with its stdout, or `null` where the command failed. The
  capture replays these, so the outputs depend on nothing else.
- `exports/`: the export files the page read from its export directory.
- `golden.json`, holding:
  - the history's `asOf`, and the clock (`now`) and time zone (`UTC`) the page
    was opened at;
  - the embedded `delivery-history` block as parsed JSON;
  - the page's text on opening;
  - the line map link's `href`, opened as a file and when served;
  - the rows' keys in page order;
  - for every row, the text of its detail after a click on it, and whether a
    second click hid it again.

The cases:

| Case | Document | Runs | Exports |
| --- | --- | --- | --- |
| `overlap` | a record naming two runs | two product runs | one run also in the product (its stale copy is not listed), one export only, an older telemetry record, a delivery record (not listed) |
| `empty` | an empty record | an empty inventory | none |
| `run-list-unreadable` | as `overlap` | the run list fails | as `overlap` |
| `unreadable-rows` | as `overlap` | one run whose show fails | an export that is not JSON, a file that is not a run export |
| `hostile-text` | markup in the headline and subject | markup in the ticket and an outcome | markup in a telemetry branch |
| `delivery-records-epic` | a real delivery record of nine merged pieces | its three recorded runs | a slice of the real tracked exports: two run exports, two telemetry records, a delivery record |

Real records, run listings and exports named the capturing machine's home
directory; the capture replaced it with `~/` in its inputs before rendering, so
inputs and goldens agree.

The port is held to these with `history-config.json`, the line-map corpus's
configuration with `exports` naming the source's export directory and skipping
its delivery records. One output differs by design: the source linked its own
file name and the live server's front page, and the port links the binding's
line-map output as a file and at its `/view/` address.
