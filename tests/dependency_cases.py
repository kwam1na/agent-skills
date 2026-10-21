"""One table of dependency shapes, run through both list-work and the tracker snapshot check.

Each case is the parent, the items as (reference, blockers), and the text the
refusal must name, or None when the shape is valid. ``CYCLES`` counts the
cycles a refusal reports, none when a case is not listed.
"""

from __future__ import annotations

PARENT = "epic-1"

CASES: dict[str, tuple[str, list[tuple[str, list[str]]], tuple[str, ...] | None]] = {
    "no blockers": (PARENT, [("a", []), ("b", [])], None),
    "a chain": (PARENT, [("a", []), ("b", ["a"]), ("c", ["b"])], None),
    "a diamond": (PARENT, [("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])], None),
    "a blocker outside the listing": (PARENT, [("a", ["elsewhere"])], None),
    "an item that blocks itself": (PARENT, [("a", ["a"])], ("a", "blocks itself")),
    "a blocker named twice": (PARENT, [("a", []), ("b", ["a", "a"])], ("b", "a", "more than once")),
    "a blocker named three times": (PARENT, [("a", []), ("b", ["a", "a", "a"])], ("b", "a", "more than once")),
    "a two-item cycle": (PARENT, [("a", ["b"]), ("b", ["a"])], ("cycle", "a -> b -> a")),
    "a three-item cycle": (PARENT, [("a", ["c"]), ("b", ["a"]), ("c", ["b"])], ("cycle", "a -> c -> b -> a")),
    "a cycle beside valid work": (PARENT, [("x", []), ("a", ["b", "x"]), ("b", ["a"])], ("cycle", "a -> b -> a")),
    "an item blocked by its parent": (PARENT, [("a", [PARENT])], ("a", PARENT, "parent")),
    "an item that is the parent": (PARENT, [(PARENT, [])], (PARENT, "parent itself")),
    "two items that reach one cycle": (PARENT, [("x", ["b"]), ("y", ["b"]), ("b", ["c"]), ("c", ["b"])],
                                       ("cycle", "b -> c -> b")),
    "many cycles": (PARENT, [(f"n{i}", [f"n{j}" for j in range(30) if j != i]) for i in range(30)],
                    ("cycle", "more")),
    "a long cycle": (PARENT, [(f"n{i}", [f"n{(i + 1) % 9}"]) for i in range(9)],
                     ("n0 -> n1 -> n2 -> n3 -> n4 -> ... 4 more -> n0",)),
}

CYCLES = {"a two-item cycle": 1, "a three-item cycle": 1, "a cycle beside valid work": 1,
          "two items that reach one cycle": 1, "many cycles": 10, "a long cycle": 1}
# How often a refusal names one problem, where a case pins it.
ONCE = {"a blocker named three times": "more than once"}
