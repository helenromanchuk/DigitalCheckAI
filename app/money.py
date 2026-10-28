"""Splitting a money amount across lines in proportion to their weights."""


def allocate_cents(amount: float, weights: list[float]) -> list[float]:
    """Splits amount across weights in proportion to them, rounded to cents.

    The last line gets the remainder, so the parts add up to the amount exactly. Weights may be negative
    (a return line) as long as their sum isn't zero; with a zero sum nothing is allocated.
    """
    total = sum(weights)
    if not weights or abs(total) < 1e-9:
        return [0.0] * len(weights)
    shares = [round(amount * weight / total, 2) for weight in weights[:-1]]
    shares.append(round(amount - sum(shares), 2))
    return shares
