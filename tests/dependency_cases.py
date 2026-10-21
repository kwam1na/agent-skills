"""One table of dependency shapes, run through both list-work and the tracker snapshot check.

Each case is the parent, the items as (reference, blockers), and the text the
refusal must name, or None when the shape is valid.
"""

from __future__ import annotations

PARENT = "epic-1"

CASES: dict[str, tuple[str, list[tuple[str, list[str]]], tuple[str, ...] | None]] = {
    "no blockers": (PARENT, [("a", []), ("b", [])], None),
    "a chain": (PARENT, [("a", []), ("b", ["a"]), ("c", ["b"])], None),
    "a diamond": (PARENT, [("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])], None),
    "a blocker outside the listing": (PARENT, [("a", ["elsewhere"])], None),
    "an item that blocks itself": (PARENT, [("a", ["a"])], ("a", "blocks itself")),
    "a blocker named twice": (PARENT, [("a", []), ("b", ["a", "a"])], ("b", "a", "twice")),
    "a two-item cycle": (PARENT, [("a", ["b"]), ("b", ["a"])], ("cycle", "a -> b -> a")),
    "a three-item cycle": (PARENT, [("a", ["c"]), ("b", ["a"]), ("c", ["b"])], ("cycle", "a -> c -> b -> a")),
    "a cycle beside valid work": (PARENT, [("x", []), ("a", ["b", "x"]), ("b", ["a"])], ("cycle", "a -> b -> a")),
    "an item blocked by its parent": (PARENT, [("a", [PARENT])], ("a", PARENT, "parent")),
    "an item that is the parent": (PARENT, [(PARENT, [])], (PARENT, "parent itself")),
}
