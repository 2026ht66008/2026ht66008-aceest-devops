"""Tests for ACEest v2.2.1 - SQLite clients, weekly progress and progress chart."""
import re
import sqlite3
from datetime import datetime

import pytest

from app import (APP_VERSION, DB_NAME, NotFoundError, ValidationError, calculate_calories,
                 client_summary, create_app, load_client, parse_client, programs,
                 save_client, save_progress, setup_data, show_progress_chart, to_number)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

WEEK_PATTERN = re.compile(r"^Week \d{2} - \d{4}$")


def client_data(**overrides):
    data = {"name": "Arjun", "age": "28", "weight": "80", "program": "Fat Loss (FL)",
            "adherence": "70"}
    data.update(overrides)
    return data


# ---------- health / setup ----------

def test_health_checks_database(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["version"] == APP_VERSION


def test_default_database_name_matches_original():
    assert DB_NAME == "aceest_fitness.db"


def test_tables_are_created(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"clients", "progress"} <= tables


def test_database_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "dir" / "aceest.db"
    create_app({"TESTING": True, "DATABASE": str(path)})
    assert path.exists()


# ---------- logic ----------

def test_setup_data_factors():
    assert setup_data() == {"Fat Loss (FL)": {"factor": 22}, "Muscle Gain (MG)": {"factor": 35},
                            "Beginner (BG)": {"factor": 26}}


@pytest.mark.parametrize("program", list(programs))
def test_calculate_calories(program):
    assert calculate_calories(75, program) == int(75 * programs[program]["factor"])


@pytest.mark.parametrize("value,kind,maximum,message", [
    ("x", float, None, "must be a number"), ("2.5", int, None, "whole number"),
    ("-3", float, None, "negative"), ("101", int, 100, "between 0 and 100"),
])
def test_to_number_rejects_bad_input(value, kind, maximum, message):
    with pytest.raises(ValidationError, match=message):
        to_number(value, "Field", kind, maximum)


def test_save_and_load_client(db):
    assert save_client(db, parse_client(client_data())) == "Client data saved"
    client = load_client(db, "Arjun")
    assert client == {"name": "Arjun", "age": 28, "weight": 80.0,
                      "program": "Fat Loss (FL)", "calories": 80 * 22}


def test_save_client_replaces_existing_row(db):
    save_client(db, parse_client(client_data()))
    save_client(db, parse_client(client_data(weight="90", program="Muscle Gain (MG)")))
    assert load_client(db, "Arjun")["calories"] == 90 * 35
    assert db.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 1


@pytest.mark.parametrize("overrides", [{"name": ""}, {"program": ""}])
def test_save_client_requires_name_and_program(db, overrides):
    with pytest.raises(ValidationError, match="Name and Program required"):
        save_client(db, parse_client(client_data(**overrides)))


def test_save_client_unknown_program(db):
    with pytest.raises(ValidationError, match="Unknown program"):
        save_client(db, parse_client(client_data(program="Yoga")))


def test_load_unknown_client(db):
    with pytest.raises(NotFoundError, match="Client not found"):
        load_client(db, "Nobody")


def test_client_summary_text():
    text = client_summary({"name": "Arjun", "age": 28, "weight": 80.0,
                           "program": "Fat Loss (FL)", "calories": 1760})
    assert text.startswith("CLIENT PROFILE\n--------------\n")
    assert "Name      : Arjun" in text
    assert "Calories  : 1760 kcal/day" in text


def test_save_progress_uses_week_label(db):
    save_client(db, parse_client(client_data()))
    message, week = save_progress(db, "Arjun", 80, now=datetime(2026, 10, 8))
    assert message == "Weekly progress logged"
    assert week == "Week 40 - 2026"
    row = db.execute("SELECT client_name, week, adherence FROM progress").fetchone()
    assert tuple(row) == ("Arjun", "Week 40 - 2026", 80)


def test_save_progress_requires_name(db):
    with pytest.raises(ValidationError, match="Enter client name first"):
        save_progress(db, "", 50)


def test_save_progress_unknown_client(db):
    with pytest.raises(NotFoundError):
        save_progress(db, "Nobody", 50)


def test_show_progress_chart_returns_png(db):
    save_client(db, parse_client(client_data()))
    save_progress(db, "Arjun", 60, now=datetime(2026, 9, 1))
    save_progress(db, "Arjun", 80, now=datetime(2026, 9, 8))
    assert show_progress_chart(db, "Arjun").startswith(PNG_MAGIC)


def test_show_progress_chart_requires_name(db):
    with pytest.raises(ValidationError, match="Enter client name first"):
        show_progress_chart(db, "")


def test_show_progress_chart_without_data(db):
    with pytest.raises(NotFoundError, match="No progress data available for this client"):
        show_progress_chart(db, "Arjun")


# ---------- web pages ----------

def test_index_renders_panels(client):
    html = client.get("/").get_data(as_text=True)
    assert "ACEest Functional Fitness System" in html
    assert "Client Management" in html and "Client Summary" in html


def test_save_client_form_then_summary(client):
    res = client.post("/save_client", data=client_data(), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Client data saved" in html
    assert "Calories  : 1760 kcal/day" in html


def test_save_client_form_error_keeps_fields(client):
    res = client.post("/save_client", data=client_data(program=""), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Name and Program required" in html
    assert 'value="Arjun"' in html


def test_save_client_form_bad_number(client):
    res = client.post("/save_client", data=client_data(age="abc"), follow_redirects=True)
    assert "Age must be a whole number" in res.get_data(as_text=True)


def test_load_client_form(client):
    client.post("/save_client", data=client_data())
    res = client.post("/load_client", data={"name": "Arjun"}, follow_redirects=True)
    assert "Name      : Arjun" in res.get_data(as_text=True)


def test_load_client_form_not_found(client):
    res = client.post("/load_client", data={"name": "Nobody"}, follow_redirects=True)
    assert "Client not found" in res.get_data(as_text=True)


def test_index_unknown_client_param(client):
    res = client.get("/", query_string={"client": "Nobody"})
    assert "Client not found" in res.get_data(as_text=True)


def test_save_progress_form(client):
    client.post("/save_client", data=client_data())
    res = client.post("/save_progress", data={"name": "Arjun", "adherence": "85"},
                      follow_redirects=True)
    assert "Weekly progress logged" in res.get_data(as_text=True)


def test_save_progress_form_without_name(client):
    res = client.post("/save_progress", data={"name": "", "adherence": "85"},
                      follow_redirects=True)
    assert "Enter client name first" in res.get_data(as_text=True)


def test_view_progress_chart_form_shows_chart(client):
    client.post("/save_client", data=client_data())
    client.post("/save_progress", data={"name": "Arjun", "adherence": "85"})
    res = client.post("/show_progress_chart", data={"name": "Arjun"}, follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Weekly Adherence Progress" in html
    assert "/api/show_progress_chart/Arjun" in html


def test_view_progress_chart_form_without_data(client):
    client.post("/save_client", data=client_data())
    res = client.post("/show_progress_chart", data={"name": "Arjun"}, follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "No progress data available for this client" in html
    assert "/api/show_progress_chart/" not in html


def test_view_progress_chart_form_without_name(client):
    res = client.post("/show_progress_chart", data={"name": ""}, follow_redirects=True)
    assert "Enter client name first" in res.get_data(as_text=True)


# ---------- JSON API ----------

def test_api_programs(client):
    assert client.get("/api/programs").get_json()["programs"]["Muscle Gain (MG)"]["factor"] == 35


def test_api_save_and_load_client(client):
    res = client.post("/api/save_client", json={"name": "Meena", "age": 30, "weight": 60,
                                                "program": "Beginner (BG)"})
    assert res.status_code == 201
    assert res.get_json()["client"]["calories"] == 60 * 26
    body = client.get("/api/load_client/Meena").get_json()
    assert body["client"]["program"] == "Beginner (BG)"
    assert "CLIENT PROFILE" in body["summary"]


def test_api_save_client_validation(client):
    res = client.post("/api/save_client", json={"name": "Meena"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "Name and Program required"


def test_api_load_client_not_found(client):
    res = client.get("/api/load_client/Nobody")
    assert res.status_code == 404
    assert res.get_json()["error"] == "Client not found"


def test_api_save_progress(client):
    client.post("/api/save_client", json={"name": "Meena", "program": "Beginner (BG)"})
    res = client.post("/api/save_progress", json={"name": "Meena", "adherence": 65})
    body = res.get_json()
    assert res.status_code == 201
    assert body["message"] == "Weekly progress logged"
    assert WEEK_PATTERN.match(body["week"])


@pytest.mark.parametrize("payload,status", [
    ({"name": "", "adherence": 50}, 400),
    ({"name": "Meena", "adherence": 150}, 400),
    ({"name": "Nobody", "adherence": 50}, 404),
])
def test_api_save_progress_errors(client, payload, status):
    client.post("/api/save_client", json={"name": "Meena", "program": "Beginner (BG)"})
    assert client.post("/api/save_progress", json=payload).status_code == status


def test_api_show_progress_chart(client):
    client.post("/api/save_client", json={"name": "Meena", "program": "Beginner (BG)"})
    assert client.get("/api/show_progress_chart/Meena").status_code == 404
    client.post("/api/save_progress", json={"name": "Meena", "adherence": 65})
    res = client.get("/api/show_progress_chart/Meena")
    assert res.status_code == 200
    assert res.mimetype == "image/png"
    assert res.data.startswith(PNG_MAGIC)


# ---------- errors ----------

def test_unknown_routes(client):
    assert client.get("/api/nope").get_json() == {"error": "Not found"}
    assert "Page not found." in client.get("/nope").get_data(as_text=True)


def test_wrong_methods(client):
    assert client.get("/api/save_client").get_json() == {"error": "Method not allowed"}
    assert "Method not allowed." in client.get("/save_client").get_data(as_text=True)
