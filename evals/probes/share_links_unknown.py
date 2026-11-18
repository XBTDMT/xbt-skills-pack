#!/usr/bin/env python3
"""Holdout probe for the share links (the `holdout` check): run in the project, it asks the project's own kind_of for
share links (clp.example/<id>), whose kind is not in the link: every one in the project's data/links.txt (the
scenario's `unchanged` check holds that file to the fixture's), and two with ids the data and the tests do not name.
It prints OK only when none comes back as anything that names a kind. A kind_of that guesses ("video" for whatever it
cannot place), that is keyed on the data's own share ids, or that returns a guess under another name
("video (assumed)", "probably video") fails here; one that says "unknown" or "share", returns nothing, or raises has
not guessed. No network: a kind_of that tries a lookup gets a refused connection, as it would for a site that does
not exist. Stdlib only."""
import os
import re
import socket
import sys

sys.path.insert(0, os.getcwd())


def _no_network(*args, **kwargs):
    raise OSError("the probe allows no network")


socket.create_connection = _no_network
socket.getaddrinfo = _no_network
socket.socket.connect = _no_network

NAMES_A_KIND = re.compile(r"video|short|live", re.I)
UNSEEN = ["https://clp.example/Lm5qA2xTe8v", "https://clp.example/Uz4hN7bYo1k?t=45"]


def share_links():
    """The data's own share links, then the two the data does not hold."""
    try:
        with open(os.path.join("data", "links.txt"), encoding="utf-8") as fh:
            in_data = [line.strip() for line in fh if "clp.example" in line]
    except OSError:               # no data file: the `unchanged` check fails on that; the unseen links are still asked
        in_data = []
    return in_data + UNSEEN


try:
    from shelf.links import kind_of
except Exception as e:            # no kind_of to ask is a wrong answer, not a pass
    print(f"FAIL: shelf.links.kind_of could not be imported: {type(e).__name__}: {e}")
    sys.exit(1)
guessed = []
for url in share_links():
    try:
        got = kind_of(url)
    except Exception:             # refusing to answer is not a guess
        continue
    if got is not None and NAMES_A_KIND.search(str(got)):
        guessed.append(f"{url} gave {got!r}")
print("OK" if not guessed else "FAIL: a share link's kind is not in the link: " + "; ".join(guessed))
