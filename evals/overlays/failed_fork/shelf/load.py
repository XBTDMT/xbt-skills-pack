"""Load a shelf from CSV text: sku,name,qty,reorder_at. An empty qty cell means the item has not been counted yet."""
import csv
import io

from shelf.model import Item


def load_csv(text):
    items = []
    for row in csv.DictReader(io.StringIO(text)):
        items.append(Item(row["sku"], row["name"], int(row["qty"] or 0), int(row["reorder_at"] or 5)))
    return items
