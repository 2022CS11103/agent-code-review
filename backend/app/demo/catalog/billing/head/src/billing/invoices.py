def load_customer_totals(session, customer_ids: list[str]) -> dict[str, object]:
    totals = {}
    for customer_id in customer_ids:
        row = session.execute(
            "SELECT SUM(total) FROM invoices WHERE customer_id = ?",
            (customer_id,),
        )
        totals[customer_id] = row
    return totals
