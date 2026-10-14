import os
from pathlib import Path

_ROOT = Path("/tmp/prism-pytest")
_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRISM_DATABASE_URL"] = f"sqlite:///{_ROOT / 'shared.db'}"
os.environ["PRISM_STAGE_DELAY_MS"] = "0"
os.environ["PRISM_AUTO_SEED"] = "0"
os.environ["PRISM_WORKER_MODE"] = "disabled"
os.environ["PRISM_DEMO_MODE"] = "true"
os.environ["PRISM_GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["PRISM_AUTO_MIGRATE"] = "0"

import pytest
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_database():
    from app.services.github_app import clear_stored_app

    clear_stored_app()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
