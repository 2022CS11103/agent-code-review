"""Invoice webhook delivery."""

from __future__ import annotations

import sqlite3


def record_delivery(db: sqlite3.Connection, invoice_id: str, status: str) -> None:
    db.execute(
        "UPDATE invoices SET webhook_status = ? WHERE id = ?",
        (status, invoice_id),
    )


def replay_webhook(url: str) -> None:
    return None
