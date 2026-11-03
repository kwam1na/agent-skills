# Show a derived list in the live view without storing it as truth

Some of what a lead needs to see is derived, never stored, such as the supervisor's
"Needs you". The close condition reads the live function. The view can't call it, so
each pass writes the list it derived into a file beside the record, replacing the last
one. That file is only that pass's view of the list. Nothing reads it back as state.

Derive the list where its inputs are current, and draw it from that same pass.
Publishers move the tracker ledger, so run them first, then derive and write the list,
then run the renderers. One render that runs both halves at once draws the previous
pass's list.

The live view pushes only when the record's own files change. A list written beside
the record gets no push, so an open page has to fetch the served page again on its own
timer. Otherwise it keeps an old list until something else writes to the record.

Never let a page show a list from a supervisor that is gone:
- Write `running: false` when the process stops, and draw it.
- Give a running list a `staleAfter`: the pass interval plus the longer of that
  interval and the pass's own time. That catches a process that was killed before it
  could say so.
- Refuse the whole file when any part of it can't be trusted, and say why.

With no file at all, draw nothing, so pages from before the list existed are unchanged.
