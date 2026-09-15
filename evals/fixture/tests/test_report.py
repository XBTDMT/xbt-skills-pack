import unittest

from shelf.model import Item
from shelf.report import low_stock

SHELF = [
    Item("A1", "bolts", 3),
    Item("B2", "nuts", 40),
    Item("C3", "washers", 0),     # out of stock: must be reported
    Item("D4", "rivets", None),   # not counted yet: must not be reported
    Item("E5", "screws", 5),
]


class LowStock(unittest.TestCase):
    def test_low_stock_reports_every_counted_item_at_or_below_reorder(self):
        self.assertEqual(low_stock(SHELF), ["A1", "C3", "E5"])

    def test_nothing_low(self):
        self.assertEqual(low_stock([Item("B2", "nuts", 40)]), [])


if __name__ == "__main__":
    unittest.main()
