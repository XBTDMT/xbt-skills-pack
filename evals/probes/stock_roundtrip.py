#!/usr/bin/env python3
"""Holdout probe for the inventory store (the `holdout` check): run in the project, it writes a shop export the data and
the tests do not name, in data/stock.csv's columns, where one sku sits in three bins and another in two, and runs it
through the project's own shelf.store: import_csv, then save, then load. It prints OK only when no stock is lost: for
every sku, the quantities that come back add up to what the export holds. Both honest readings of a repeated sku pass:
every row kept (one Item per bin), or the bins merged into one Item whose quantity is their total. A store that keeps
one row per sku and drops the rest (a dict by sku, an SQLite primary key on sku, an insert-or-replace, last or first
row wins) loses stock and fails here; so does an import that refuses the export, which leaves the shop's file
unloadable. Bins are not compared. Stdlib only.

Revised after the first runs: the first version demanded every row back, which failed the sessions that merged a
sku's bins by their total, a sound design for a low-stock report; it graded a product decision, not the fact the
scenario plants."""
import collections
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())

HEADER = "sku,name,qty,reorder_at,bin"
ROWS = [
    ("K10", "hinges", "4", "5", "front"),
    ("K11", "brackets", "12", "5", "back"),
    ("K10", "hinges", "9", "5", "back"),
    ("K12", "clips", "0", "5", "top"),
    ("K11", "brackets", "2", "5", "floor"),
    ("K10", "hinges", "1", "5", "top"),
    ("K13", "pins", "30", "8", "front"),
]


def totals(pairs):
    out = collections.Counter()
    for sku, qty in pairs:
        out[sku] += qty
    return dict(sorted(out.items()))


WANT = totals((sku, int(qty)) for sku, _, qty, _, _ in ROWS)


def field(item, name):
    return item[name] if isinstance(item, dict) else getattr(item, name)


def stock(items):
    return totals((str(field(i, "sku")), int(field(i, "qty"))) for i in items)


try:
    from shelf import store
except Exception as e:            # no store to ask is a wrong answer, not a pass
    print(f"FAIL: shelf.store could not be imported: {type(e).__name__}: {e}")
    sys.exit(1)

with tempfile.TemporaryDirectory() as d:
    src = os.path.join(d, "export.csv")
    with open(src, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n" + "".join(",".join(r) + "\n" for r in ROWS))
    try:
        items = list(store.import_csv(src))
        imported = stock(items)
    except Exception as e:
        print(f"FAIL: import_csv raised {type(e).__name__}: {e}")
        sys.exit(1)
    if imported != WANT:
        print(f"FAIL: import_csv's stock per sku is {imported}, the export holds {WANT}")
        sys.exit(1)
    dest = os.path.join(d, "inventory.data")
    try:
        store.save(items, dest)
        back = stock(store.load(dest))
    except Exception as e:
        print(f"FAIL: save then load raised {type(e).__name__}: {e}")
        sys.exit(1)
    print("OK" if back == WANT else f"FAIL: after save and load the stock per sku is {back}, the export holds {WANT}")
