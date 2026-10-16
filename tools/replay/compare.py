#!/usr/bin/env python3
"""Compare two golden transcripts. Prints the first differing request."""

import json
import os
import sys


def selected_rows(names, base_count):
    """The baseline row numbers the named sessions store, in storage order.

    replay.py holds the storage layout and the session name rules, so both
    tools take the same names and read the same ranges. An unknown or empty
    name is refused there. The count comes from the request lists, never from
    a file, so a wrong or stale count cannot make this comparison pass."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import replay
    wanted = replay.parse_sessions(names)
    rows = []
    for name, start, count in replay.stored_layout():
        if name in wanted:
            if start + count - 1 > base_count:
                raise SystemExit("baseline has %d rows, but session %s ends at "
                                 "row %d" % (base_count, name, start + count - 1))
            rows.extend(range(start, start + count))
    return rows


def main():
    argv = sys.argv[1:]
    names = None
    if "--sessions" in argv:
        i = argv.index("--sessions")
        if i + 1 == len(argv):
            raise SystemExit("--sessions needs a name or names")
        names = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 2:
        raise SystemExit("usage: compare.py BASELINE OTHER "
                         "[--sessions NAME[,NAME...]]")
    with open(argv[0]) as f:
        base = f.read().splitlines()
    with open(argv[1]) as f:
        other = f.read().splitlines()
    if names is not None:
        # A filtered run stores only the named sessions, so the rows in scope
        # are those sessions' own baseline rows. The expected count is checked
        # first, so a short, long or empty output is refused before any row is
        # compared, and nothing outside the named sessions can pass.
        rows = selected_rows(names, len(base))
        if len(other) != len(rows):
            print("line count differs: %d recorded, %d expected for sessions %s"
                  % (len(other), len(rows), names))
            return 1
        base = [base[i - 1] for i in rows]
    if len(base) != len(other):
        print("line count differs: %d vs %d" % (len(base), len(other)))
        n = min(len(base), len(other))
    else:
        n = len(base)
    for i in range(n):
        if base[i] != other[i]:
            b = json.loads(base[i])
            o = json.loads(other[i])
            print("first difference at request %d:" % (i + 1))
            print("request: %s" % b["request"])
            print("baseline reply: %s" % b["reply"])
            print("other reply:    %s" % o["reply"])
            return 1
    if len(base) != len(other):
        return 1
    print("IDENTICAL (%d pairs)" % len(base))
    return 0


if __name__ == "__main__":
    sys.exit(main())