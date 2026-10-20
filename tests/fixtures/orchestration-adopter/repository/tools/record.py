"""Shared by the recording fakes: append one line to the fixture's log."""

import os


def record(*words: str) -> None:
    with open(os.environ["FIXTURE_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(words) + "\n")
