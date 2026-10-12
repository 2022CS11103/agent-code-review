"""Alembic upgrade and downgrade against an empty SQLite file."""

from app.config import BACKEND_ROOT, get_settings
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_and_downgrade(tmp_path, monkeypatch):
    db_file = tmp_path / "migrate.db"
    url = f"sqlite:///{db_file}"
    monkeypatch.setenv("PRISM_DATABASE_URL", url)
    get_settings.cache_clear()

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().resolved_database_url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    names = set(inspect(engine).get_table_names())
    assert {"users", "review_jobs", "findings", "patch_proposals", "validation_runs", "audit_events"} <= names

    command.downgrade(cfg, "base")
    remaining = set(inspect(engine).get_table_names())
    assert "review_jobs" not in remaining
    engine.dispose()
    get_settings.cache_clear()
