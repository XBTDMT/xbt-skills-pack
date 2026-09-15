"""Inventory items. qty is the count on the shelf: 0 or None both mean out of stock."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Item:
    sku: str
    name: str
    qty: Optional[int]
    reorder_at: int = 5
