---
title: "A Python HTTP server's name lookup stalls every start on a macOS runner"
date: 2026-11-07
category: test-failures
module: run-delivery
problem_type: test_failure
severity: high
tags: [tests, macos, http-server, dns, timeouts]
symptoms: "Serial modules that start HTTP servers reach the 1800-second unit limit on macOS CI only, interrupted at a different frame each run"
root_cause: "HTTPServer.server_bind calls socket.getfqdn on the bound address before listening, and a macOS runner answers that reverse lookup after about 34 seconds"
resolution_type: code_fix
---

# A Python HTTP server's name lookup stalls every start on a macOS runner

## Problem

`tests.test_run_delivery` and `tests.test_run_delivery_supervisor` reached the
runner's 1800-second unit limit on every macOS CI run on the default branch,
while Ubuntu passed. Python's `HTTPServer.server_bind` calls
`socket.getfqdn(host)` before the server listens. On a macOS runner a reverse
lookup of `127.0.0.1` takes about 34 seconds to answer. So every server started
that way listens 34 seconds late, whether it's `python -m http.server` or a
subclass of `http.server.HTTPServer`. A serial module that starts dozens of
them, as the fixtures' in-app checks and live views do, runs out of time.

## Symptoms

- The module times out on macOS and passes on Ubuntu, and locally on macOS.
- The interrupted frame moves from run to run (an `app-check`, a supervisor
  case), so the reports read as different hangs, each blocked in
  `communicate()` on a child that is only slow.
- Within one CI run, the units slower on macOS by a fixed amount are the ones
  that start servers, and the excess comes in steps of about 34 seconds: one,
  two, three or four steps for the servers they start.

## What Didn't Work

Reading the interrupted frame. It only shows where the limit landed. Running the
module locally under CI's cleared environment passed, because this machine's
lookup answers at once.

## Solution

A server binds through `socketserver.TCPServer.server_bind` and sets
`server_name` from its address, never from a lookup:

```python
class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
```

The run-delivery fixtures' in-app serve is such a server. A row runs it on every
platform under a modelled stall and requires it to listen within five seconds,
after first showing that the stock `python -m http.server` doesn't.

## Why This Works

Nothing these servers serve reads `server_name`. In the standard library only
the CGI and WSGI environments' `SERVER_NAME` does. Binding and listening are
unchanged, so the server takes connections as soon as its socket listens.

## Prevention

- Don't start `python -m http.server`, or a server class that keeps
  `HTTPServer.server_bind`, in a test or a shipped command.
- To reproduce the runner, put a `sitecustomize.py` on `PYTHONPATH` that holds
  `socket.getfqdn` for a loopback name for 34 seconds and logs its stack. It
  times the module out as CI does, and its stacks name every caller.
- When one module is slower on one runner, compare unit times across runners
  before reading the interrupted frame.

## Related Issues

- The live view's own server, `_LiveServer` in orchestrate-work's
  `render_state.py`, binds the same way, and the supervisor and `view` start it.
