def apply_tax(amount_cents: int, rate: float) -> float:
    dollars = float(amount_cents) / 100.0
    return round(dollars * float(rate), 2)
