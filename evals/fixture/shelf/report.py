from shelf.model import Item


def low_stock(itms):
    """Every counted item at or below its reorder level, out-of-stock items included, sorted by sku."""
    out = []
    for item in itms:
        if item.qty and item.qty <= item.reorder_at:
            out.append(item.sku)
    return sorted(out)
