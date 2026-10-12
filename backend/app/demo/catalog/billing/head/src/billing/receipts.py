def mark_paid(db, invoice_id: str) -> None:
    """Mark a single invoice paid using a bound parameter."""
    db.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
