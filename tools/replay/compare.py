#!/usr/bin/env python3
"""Compare two golden transcripts. Prints the first differing request."""

import json
import sys


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: compare.py BASELINE OTHER")
    with open(sys.argv[1]) as f:
        base = f.read().splitlines()
    with open(sys.argv[2]) as f:
        other = f.read().splitlines()
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
