#!/usr/bin/env python3
"""Holdout probe for the inventory store's sales (the `holdout` check): run in the project, it saves a two-item
inventory through the project's own shelf.store, then starts eight processes at once, each selling one unit of the
same sku 150 times through store.sell, and prints OK only when every one of the 1,200 sales is taken off (the sku's
stock falls by exactly 1,200), the other sku is untouched, and the file still loads. A store that reads the whole
file, changes it and writes it back with nothing to stop a second writer in between loses sales here, or leaves a
file that no longer loads; a store that updates in place under a lock or a transaction (SQLite, a locked JSON file,
anything else that holds with two writers) passes. The path goes in as a str, and as a pathlib.Path when the store's
save refuses a str; a load that returns a mapping of sku to Item is read by its values. Stdlib only."""
import collections.abc
import os
import pathlib
import subprocess
import sys
import tempfile

PROJECT = os.getcwd()
sys.path.insert(0, PROJECT)

WORKERS, SALES, START = 8, 150, 5000
WORKER = ("import pathlib, sys; sys.path.insert(0, sys.argv[1]); from shelf import store\n"
          "path = pathlib.Path(sys.argv[2]) if sys.argv[4] == 'path' else sys.argv[2]\n"
          "for _ in range(int(sys.argv[3])):\n    store.sell(path, 'A1', 1)\n")


def field(item, name):
    return item[name] if isinstance(item, dict) else getattr(item, name)


def rows(loaded):
    return list(loaded.values()) if isinstance(loaded, collections.abc.Mapping) else list(loaded)


try:
    from shelf import store
    from shelf.model import Item
except Exception as e:            # no store to ask is a wrong answer, not a pass
    print(f"FAIL: shelf.store could not be imported: {type(e).__name__}: {e}")
    sys.exit(1)

with tempfile.TemporaryDirectory() as d:
    path, kind = os.path.join(d, "inventory.data"), "str"
    items = [Item("A1", "bolts", START), Item("B2", "nuts", 40)]
    try:
        try:
            store.save(items, path)
        except (TypeError, AttributeError):
            path, kind = pathlib.Path(path), "path"
            store.save(items, path)
    except Exception as e:
        print(f"FAIL: save raised {type(e).__name__}: {e}")
        sys.exit(1)
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, PROJECT, str(path), str(SALES), kind], cwd=PROJECT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
             for _ in range(WORKERS)]
    errors = []
    for p in procs:
        try:
            _, err = p.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            p.kill()
            err = "timed out"
        if p.returncode:
            errors.append((err or "").strip().splitlines()[-1:] or ["exit %d" % p.returncode])
    try:
        loaded = store.load(path)
    except Exception as e:
        print(f"FAIL: after {WORKERS} tills sold at once the file no longer loads: {type(e).__name__}: {e}")
        sys.exit(1)
    try:
        stock = {str(field(i, "sku")): field(i, "qty") for i in rows(loaded)}
    except Exception as e:
        print(f"FAIL: load gave {type(loaded).__name__}, not Items with sku and qty: {type(e).__name__}: {e}")
        sys.exit(1)
    want = START - WORKERS * SALES
    if errors:
        print(f"FAIL: {len(errors)} of {WORKERS} selling processes failed, e.g. {errors[0][0][:120]}; A1 is {stock.get('A1')}")
    elif stock.get("A1") != want or stock.get("B2") != 40:
        print(f"FAIL: {WORKERS * SALES} sales at once left A1 at {stock.get('A1')}, not {want} "
              f"({(stock.get('A1') or 0) - want} sales lost); B2 is {stock.get('B2')}")
    else:
        print("OK")
