"""Splitting a money amount across lines in proportion to their weights."""
import math


def allocate_cents(amount: float, weights: list[float]) -> list[float]:
    """Splits amount across weights in proportion to them, in whole cents that add up to the amount exactly.

    Largest remainder: every line gets its proportional share rounded down to a cent, and the cents left over
    go one each to the lines with the largest dropped fractions (the later line on a tie). So every share is
    within a cent of its exact proportion. Weights may be negative (a return line) as long as their sum isn't
    zero; with a zero sum nothing is allocated.
    """
    total = sum(weights)
    if not weights or abs(total) < 1e-9:
        return [0.0] * len(weights)
    cents = round(amount * 100)
    exact = [cents * weight / total for weight in weights]
    shares = [math.floor(value) for value in exact]
    leftover = cents - sum(shares)
    by_fraction = sorted(range(len(weights)), key=lambda i: (exact[i] - shares[i], i), reverse=True)
    for i in by_fraction[:leftover]:
        shares[i] += 1
    return [share / 100 for share in shares]
