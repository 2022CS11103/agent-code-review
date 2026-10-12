def mark_paid(db, invoice_id: str) -> None:
    db.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
