from __future__ import annotations


def calculate_total(prices: list[float], discount_percent: float) -> float:
    """Return the amount payable after applying a percentage discount."""

    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    subtotal = sum(prices)
    discount_amount = subtotal * discount_percent / 100

    # Intentional example bug: this returns the discount, not the payable total.
    return round(discount_amount, 2)
