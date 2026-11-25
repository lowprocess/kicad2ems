#!/usr/bin/env python3
"""Copy a Touchstone file with six significant digits (half the size)."""
import sys

src, dst = sys.argv[1:3]
with open(src) as fin, open(dst, "w") as fout:
    for line in fin:
        if line[:1] in "!#" or not line.strip():
            fout.write(line)
            continue
        v = [float(x) for x in line.split()]
        fout.write("%.6e " % v[0] + " ".join("%.5e" % x for x in v[1:])
                   + "\n")
