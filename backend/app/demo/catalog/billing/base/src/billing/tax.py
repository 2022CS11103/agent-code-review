def apply_tax(amount_cents: int, rate_bps: int) -> int:
    return (amount_cents * rate_bps + 5_000) // 10_000
