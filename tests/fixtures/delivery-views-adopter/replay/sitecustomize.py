"""Stands in for the network when a test puts this directory on PYTHONPATH.

Python imports ``sitecustomize`` at startup, so a snapshot command run from an
installed release, through the binding's own argv, answers from recorded
responses instead of the provider. ``DELIVERY_VIEWS_REPLAY`` names a JSON file
of ``{"exchanges": [...], "log": <path>}``: each exchange is
``{"method", "url", "status", "headers", "body"}``, answered in order to the
request whose URL starts with ``url``. Every request is appended to the log as
its method, URL and authorization scheme, never the credential itself. A
request the script did not expect is refused as unreachable.
"""

from __future__ import annotations

import email.message
import io
import json
import os
import urllib.error
import urllib.request

_REPLAY = os.environ.get("DELIVERY_VIEWS_REPLAY")


class _Answer(io.BytesIO):
    def __init__(self, status: int, headers: email.message.Message, body: bytes) -> None:
        super().__init__(body)
        self.status = status
        self.headers = headers


if _REPLAY:
    with open(_REPLAY, encoding="utf-8") as handle:
        _SCRIPT = json.load(handle)
    _PENDING = list(_SCRIPT["exchanges"])

    def _open(self, request, data=None, timeout=None):  # noqa: ANN001, ANN202
        url = request.full_url
        authorization = request.get_header("Authorization") or ""
        with open(_SCRIPT["log"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"method": request.get_method(), "url": url,
                                  "authorization": authorization.split(" ", 1)[0] if " " in authorization
                                  else ("raw" if authorization else "")}) + "\n")
        if not _PENDING or not url.startswith(_PENDING[0]["url"]) or request.get_method() != _PENDING[0]["method"]:
            raise urllib.error.URLError("the replay has no answer for this request")
        exchange = _PENDING.pop(0)
        headers = email.message.Message()
        for name, value in exchange.get("headers", {}).items():
            headers[name] = value
        body = exchange["body"] if isinstance(exchange["body"], str) else json.dumps(exchange["body"])
        status = exchange["status"]
        if 200 <= status < 300:
            return _Answer(status, headers, body.encode("utf-8"))
        raise urllib.error.HTTPError(url, status, "recorded", headers, io.BytesIO(body.encode("utf-8")))

    urllib.request.OpenerDirector.open = _open
