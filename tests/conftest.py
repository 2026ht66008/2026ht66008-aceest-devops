import pytest

from app import create_app, get_db


@pytest.fixture
def app(tmp_path):
    # Every test gets its own throw-away SQLite file, never the real aceest_fitness.db
    return create_app({"TESTING": True, "SECRET_KEY": "test",
                       "DATABASE": str(tmp_path / "test.db")})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """A database connection for calling the logic functions directly."""
    with app.app_context():
        yield get_db()


@pytest.fixture
def arjun(client):
    """A saved client most tests can build on."""
    client.post("/api/save_client", json={
        "name": "Arjun", "age": 28, "height": 175, "weight": 80,
        "program": "Fat Loss (FL) – 3 day", "target_weight": 72, "target_adherence": 85})
    return "Arjun"
