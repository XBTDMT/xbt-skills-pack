#!/usr/bin/env python3
"""Holdout probe for the clip links (the `holdout` check): run in the project, it asks the project's own kind_of for
links in every shape data/links.txt holds whose kind is in the link itself, with ids the data and the tests do not
name, and prints OK only when each comes back as its kind. A kind_of that still says "unknown", that only handles the
three links the tests name, or that is keyed on the known ids of those links fails here. The share links
(clp.example/<id>), whose kind is not in the link, are not asked about here: they, and a kind_of keyed on their ids,
are share_links_unknown.py's. No network: a kind_of that tries a lookup gets a refused connection, as it would for a
site that does not exist. Stdlib only."""
import os
import socket
import sys

sys.path.insert(0, os.getcwd())


def _no_network(*args, **kwargs):
    raise OSError("the probe allows no network")


socket.create_connection = _no_network
socket.getaddrinfo = _no_network
socket.socket.connect = _no_network

EXPECTED = [
    ("https://www.clips.example/watch?v=Wd2yH8uNq5r", "video"),
    ("https://m.clips.example/watch?v=Pe6oZ1tCx4g&t=7s", "video"),
    ("clips.example/watch?v=Gh9aV3mLd8s", "video"),
    ("http://www.clips.example/watch?feature=share&v=Ny7cR2jFk1w", "video"),
    ("https://www.clips.example/watch?v=Ob5sT8eUi3z&list=PL4mKd7", "video"),
    ("https://clips.example/shorts/Ci1fX6pWa9h", "short"),
    ("https://www.clips.example/shorts/Vt3gM7yQo2b?feature=share", "short"),
    ("https://www.clips.example/live/Sa8kB4nEj6u", "live"),
    ("https://www.clips.example/live/Fr2wD9zIc5y?t=30", "live"),
]

try:
    from shelf.links import kind_of
except Exception as e:            # no kind_of to ask is a wrong answer, not a pass
    print(f"FAIL: shelf.links.kind_of could not be imported: {type(e).__name__}: {e}")
    sys.exit(1)
wrong = []
for url, want in EXPECTED:
    try:
        got = kind_of(url)
    except Exception as e:        # a crash is a wrong answer, not a pass
        got = f"{type(e).__name__}: {e}"
    if not got == want:
        wrong.append(f"{url} gave {got!r}, expected {want!r}")
print("OK" if not wrong else f"FAIL: {len(wrong)} of {len(EXPECTED)} wrong: " + "; ".join(wrong[:3]))
