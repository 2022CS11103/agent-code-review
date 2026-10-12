def load_customer_totals(session, customer_ids: list[str]) -> dict[str, object]:
    rows = session.execute(
        "SELECT customer_id, SUM(total) FROM invoices WHERE customer_id = ?",
        (customer_ids,),
    )
    return {row[0]: row[1] for row in rows}
