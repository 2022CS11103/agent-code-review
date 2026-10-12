import sqlite3

from billing.webhooks import record_delivery


def _db():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE invoices (id TEXT, webhook_status TEXT)")
    connection.execute("INSERT INTO invoices VALUES ('1', 'pending')")
    connection.execute("INSERT INTO invoices VALUES ('2', 'pending')")
    return connection


def test_legitimate_update():
    connection = _db()
    record_delivery(connection, "1", "sent")
    rows = list(connection.execute("SELECT id, webhook_status FROM invoices ORDER BY id"))
    assert rows == [("1", "sent"), ("2", "pending")]


def test_malicious_id_does_not_update_existing_invoices():
    connection = _db()
    record_delivery(connection, "1' OR '1'='1", "sent")
    statuses = [row[0] for row in connection.execute("SELECT webhook_status FROM invoices ORDER BY id")]
    assert statuses == ["pending", "pending"]
