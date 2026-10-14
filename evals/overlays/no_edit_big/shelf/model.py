"""Inventory items. qty is the count on the shelf: 0 or None both mean out of stock."""
import json
import os
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Item:
    sku: str
    name: str
    qty: Optional[int]
    reorder_at: int = 5


def save(items, path):
    """Store the inventory: the old file is removed first, then the new one is written."""
    if os.path.exists(path):
        os.remove(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(i) for i in items], fh)
