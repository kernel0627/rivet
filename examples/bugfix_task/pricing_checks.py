from __future__ import annotations

import unittest

from pricing import calculate_total


class PricingTests(unittest.TestCase):
    def test_applies_discount_to_subtotal(self) -> None:
        self.assertEqual(calculate_total([40.0, 60.0], 10), 90.0)

    def test_zero_discount_returns_original_total(self) -> None:
        self.assertEqual(calculate_total([12.5, 7.5], 0), 20.0)

    def test_empty_prices_return_zero(self) -> None:
        self.assertEqual(calculate_total([], 25), 0.0)

    def test_rejects_discount_outside_percentage_range(self) -> None:
        with self.assertRaises(ValueError):
            calculate_total([100.0], -1)
        with self.assertRaises(ValueError):
            calculate_total([100.0], 101)


if __name__ == "__main__":
    unittest.main(verbosity=2)
