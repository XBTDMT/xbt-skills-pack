import unittest

from shelf.load import load_csv
from shelf.report import low_stock

CSV = """sku,name,qty,reorder_at
A1,bolts,3,5
B2,nuts,40,5
C3,washers,0,5
D4,rivets,,5
E5,screws,5,5
"""


class LowStock(unittest.TestCase):
    def test_low_stock_reports_every_counted_item_at_or_below_reorder(self):
        self.assertEqual(low_stock(load_csv(CSV)), ["A1", "C3", "E5"])

    def test_nothing_low(self):
        self.assertEqual(low_stock(load_csv("sku,name,qty,reorder_at\nB2,nuts,40,5\n")), [])


if __name__ == "__main__":
    unittest.main()
