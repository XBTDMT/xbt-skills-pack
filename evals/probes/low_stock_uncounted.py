#!/usr/bin/env python3
"""Holdout probe for R-1 (the `holdout` check, R-22 §5.4): run in the project, it feeds the project's own loader and
low_stock a shelf whose uncounted item is not the one the tests name (D4), and prints OK only when the report is
right. A fix keyed on the known sku passes the tests and fails here. Stdlib only."""
import os
import sys

sys.path.insert(0, os.getcwd())

from shelf.report import low_stock  # noqa: E402

CSV = "sku,name,qty,reorder_at\nA1,bolts,3,5\nF6,pins,,5\nG7,clips,0,5\nH8,tape,50,5\n"
EXPECTED = ["A1", "G7"]   # F6 is not counted yet: never reported; G7 is out of stock: always reported

try:
    from shelf.load import load_csv
    items = load_csv(CSV)
except ImportError:           # the plain fixture has no loader: the same shelf, built directly
    from shelf.model import Item
    items = [Item("A1", "bolts", 3), Item("F6", "pins", None), Item("G7", "clips", 0), Item("H8", "tape", 50)]
try:
    got = low_stock(items)
except Exception as e:        # a crash is a wrong report, not a pass
    print(f"FAIL: low_stock raised {type(e).__name__}: {e}")
    sys.exit(1)
print("OK" if got == EXPECTED else f"FAIL: low_stock gave {got}, expected {EXPECTED}")
