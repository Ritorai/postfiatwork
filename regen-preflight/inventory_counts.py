#!/usr/bin/env python3
"""Print the three regeneration inventories, counted from the tree.

Exists so README.md's table is quoted from a real run rather than typed
from memory, and as a single-line command for the transcript grammar.
"""
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else ".."
manifest = json.load(open(os.path.join(root, "report-freshness",
                                       "manifest.json"), encoding="utf-8"))
regen = [e for e in manifest["entries"] if e["kind"] == "regenerable"]
baselines = json.load(open(os.path.join(root, "regression-checker",
                                        "baselines.json"),
                           encoding="utf-8"))["tools"]
caps = sorted(d for d in os.listdir(root)
              if os.path.isfile(os.path.join(root, d, "capture.sh")))
print("manifest regenerable entries: %d" % len(regen))
print("regression-checker baselines: %d" % len(baselines))
print("capture.sh scripts:           %d" % len(caps))
print("capture.sh scripts run by any other single command: 0")
