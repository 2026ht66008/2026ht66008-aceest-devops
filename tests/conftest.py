import pytest

from app import create_app, get_db


@pytest.fixture
def app(tmp_path):
    # Every test gets its own throw-away SQLite file, never the real aceest_fitness.db
    return create_app({"TESTING": True, "SECRET_KEY": "test",
                       "DATABASE": str(tmp_path / "test.db")})


@pytest.fixture
def client(app):
    """A browser that is NOT logged in."""
    return app.test_client()


@pytest.fixture
def auth(app):
    """A browser logged in as the default admin user."""
    browser = app.test_client()
    res = browser.post("/api/login", json={"username": "admin", "password": "admin"})
    assert res.status_code == 200
    return browser


@pytest.fixture
def db(app):
    """A database connection for calling the logic functions directly."""
    with app.app_context():
        yield get_db()
