"""Tests for ACEest v3.2.4 - login, client dashboard, program templates, PDF, membership,
adherence chart and workouts."""
import random
import sqlite3

import pytest

from app import (APP_VERSION, CLIENT_FIELDS, PDF_LABELS, WORKOUT_TYPES, NotFoundError,
                 ValidationError, add_save_client, add_workout, check_membership, create_app,
                 ensure_client, generate_pdf, generate_program, load_client, login, pdf_safe,
                 plot_charts, program_templates, refresh_summary, refresh_workouts)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ALL_TEMPLATES = {detail for details in program_templates.values() for detail in details}


def add_progress(db, name, adherence, week="Week 40 - 2026"):
    db.execute("INSERT INTO progress (client_name, week, adherence) VALUES (?, ?, ?)",
               (name, week, adherence))
    db.commit()


# ---------- health / database ----------

def test_health_needs_no_login(client):
    res = client.get("/health")
    assert res.status_code == 200 and res.get_json()["version"] == APP_VERSION


def test_users_table_keyed_by_username(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    password = conn.execute("SELECT password FROM users WHERE username='admin'").fetchone()[0]
    conn.close()
    assert columns == ["username", "password", "role"]
    assert password != "admin"  # stored hashed


def test_v312_database_is_migrated(tmp_path):
    """A v3.1.2 database keeps its clients; membership_expiry becomes membership_end."""
    path = tmp_path / "v312.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
                            password TEXT, role TEXT);
        CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            age INTEGER, height REAL, weight REAL, program TEXT, calories INTEGER,
            target_weight REAL, target_adherence INTEGER, membership_expiry TEXT);
        INSERT INTO clients (name, program, membership_expiry)
        VALUES ('Old', 'Beginner (BG)', '2026-12-31');
    """)
    conn.close()
    app = create_app({"TESTING": True, "DATABASE": str(path)})
    browser = app.test_client()
    assert browser.post("/api/login", json={"username": "admin",
                                            "password": "admin"}).status_code == 200
    body = browser.get("/api/check_membership/Old").get_json()
    assert body["end"] == "2026-12-31"


def test_admin_password_can_be_configured(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "x.db"),
                      "ADMIN_PASSWORD": "s3cret"})
    browser = app.test_client()
    assert browser.post("/api/login", json={"username": "admin",
                                            "password": "s3cret"}).status_code == 200


# ---------- login ----------

def test_login_function(db):
    assert login(db, "admin", "admin") == "Admin"
    assert login(db, "admin", "x") is None
    assert login(db, None, None) is None


def test_pages_and_api_require_login(client):
    res = client.get("/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/login")
    assert client.get("/api/clients").status_code == 401


def test_login_screen(client):
    assert "ACEest Login" in client.get("/login").get_data(as_text=True)
    bad = client.post("/login", data={"username": "admin", "password": "x"})
    assert bad.status_code == 401 and "Invalid credentials" in bad.get_data(as_text=True)
    ok = client.post("/login", data={"username": "admin", "password": "admin"},
                     follow_redirects=True)
    assert "ACEest Dashboard (Admin)" in ok.get_data(as_text=True)


def test_logout(auth):
    auth.get("/logout")
    assert auth.get("/").status_code == 302
    auth.post("/api/login", json={"username": "admin", "password": "admin"})
    auth.post("/api/logout")
    assert auth.get("/api/clients").status_code == 401


def test_api_login_failure(client):
    assert client.post("/api/login", json={"username": "admin"}).status_code == 401


# ---------- client management ----------

def test_add_save_client_sets_active_membership(db):
    assert add_save_client(db, " Arjun ") == ("Client Arjun saved", True)
    client = load_client(db, "Arjun")
    assert client["membership_status"] == "Active" and client["program"] is None


def test_add_save_client_existing_is_ignored(db):
    add_save_client(db, "Arjun")
    db.execute("UPDATE clients SET membership_status = 'Paused' WHERE name = 'Arjun'")
    assert add_save_client(db, "Arjun") == ("Client Arjun saved", False)
    assert load_client(db, "Arjun")["membership_status"] == "Paused"


def test_add_save_client_requires_name(db):
    with pytest.raises(ValidationError, match="Enter client name"):
        add_save_client(db, "  ")


def test_ensure_client(db):
    with pytest.raises(ValidationError, match="Select a client first"):
        ensure_client(db, "")
    with pytest.raises(NotFoundError):
        ensure_client(db, "Nobody")


def test_refresh_summary_format(db):
    add_save_client(db, "Arjun")
    assert refresh_summary(load_client(db, "Arjun")) == (
        "Name: Arjun\nProgram: None\nCalories: None\nMembership: Active")


# ---------- program generator ----------

def test_generate_program_saves_a_template(db):
    add_save_client(db, "Arjun")
    message, program_type, detail = generate_program(db, "Arjun", random.Random(5))
    assert detail in program_templates[program_type]
    assert message == f"Program for Arjun: {detail}"
    assert load_client(db, "Arjun")["program"] == detail


def test_generate_program_is_reproducible(db):
    add_save_client(db, "Arjun")
    assert generate_program(db, "Arjun", random.Random(9)) == \
        generate_program(db, "Arjun", random.Random(9))


def test_generate_program_covers_all_templates(db):
    add_save_client(db, "Arjun")
    rng = random.Random(0)
    seen = {generate_program(db, "Arjun", rng)[2] for _ in range(200)}
    assert seen == ALL_TEMPLATES


def test_generate_program_needs_client(db):
    with pytest.raises(ValidationError, match="Select a client first"):
        generate_program(db, "")


# ---------- PDF / membership / chart / workouts ----------

def test_pdf_labels_match_columns():
    assert len(PDF_LABELS) == len(CLIENT_FIELDS) == 11


def test_generate_pdf(db):
    add_save_client(db, "Arjun")
    assert generate_pdf(db, "Arjun").startswith(b"%PDF-")
    with pytest.raises(NotFoundError):
        generate_pdf(db, "Nobody")


def test_pdf_safe():
    assert pdf_safe("Fat Loss (FL) – 3 day") == "Fat Loss (FL) - 3 day"


def test_check_membership_message(db):
    add_save_client(db, "Arjun")
    assert check_membership(db, "Arjun")["message"] == "Membership: Active\nRenewal Date: N/A"
    db.execute("UPDATE clients SET membership_end = '2027-03-31' WHERE name = 'Arjun'")
    assert check_membership(db, "Arjun")["message"].endswith("Renewal Date: 2027-03-31")


def test_plot_charts(db):
    add_save_client(db, "Arjun")
    assert plot_charts(db, "Arjun") is None
    add_progress(db, "Arjun", 70)
    assert plot_charts(db, "Arjun").startswith(PNG_MAGIC)


def test_workouts_newest_first(db):
    add_save_client(db, "Arjun")
    add_workout(db, "Arjun", {"date": "2026-09-01", "workout_type": "Cardio",
                              "duration_min": "30", "notes": "Easy run"})
    add_workout(db, "Arjun", {"date": "2026-10-01", "workout_type": "Strength"})
    rows = refresh_workouts(db, "Arjun")
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-09-01"]
    assert rows[1] == {"date": "2026-09-01", "workout_type": "Cardio", "duration_min": 30,
                       "notes": "Easy run"}


@pytest.mark.parametrize("data,message", [
    ({"date": "", "workout_type": "Cardio"}, "YYYY-MM-DD"),
    ({"date": "2026-10-01", "workout_type": "Zumba"}, "Choose a workout type"),
    ({"date": "2026-10-01", "workout_type": "Cardio", "duration_min": "long"}, "whole number"),
    ({"date": "2026-10-01", "workout_type": "Cardio", "duration_min": "-5"}, "negative"),
])
def test_add_workout_validation(db, data, message):
    add_save_client(db, "Arjun")
    with pytest.raises(ValidationError, match=message):
        add_workout(db, "Arjun", data)


def test_workout_types_match_original():
    assert WORKOUT_TYPES == ["Strength", "Hypertrophy", "Cardio", "Mobility"]


# ---------- web pages ----------

def test_dashboard_without_client(auth):
    html = auth.get("/").get_data(as_text=True)
    assert "ACEest Dashboard (Admin)" in html
    assert "Select a client to generate a program" in html
    assert "Select a client to see workouts" in auth.get("/?tab=workouts").get_data(as_text=True)


def test_add_save_client_form(auth):
    res = auth.post("/add_save_client", data={"name": "Arjun"}, follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Client Arjun saved" in html and "Membership: Active" in html
    res = auth.post("/add_save_client", data={"name": ""}, follow_redirects=True)
    assert "Enter client name" in res.get_data(as_text=True)


def test_select_unknown_client(auth):
    assert "Client not found" in auth.get("/?client=Nobody").get_data(as_text=True)


def test_dashboard_shows_chart_only_with_progress(auth, db):
    auth.post("/add_save_client", data={"name": "Arjun"})
    assert "/api/plot_charts/Arjun" not in auth.get("/?client=Arjun").get_data(as_text=True)
    add_progress(db, "Arjun", 80)
    assert "/api/plot_charts/Arjun" in auth.get("/?client=Arjun").get_data(as_text=True)


def test_generate_program_form(auth):
    auth.post("/add_save_client", data={"name": "Arjun"})
    html = auth.post("/generate_program", data={"client": "Arjun"},
                     follow_redirects=True).get_data(as_text=True)
    assert "Program for Arjun: " in html
    assert any(t in html for t in ALL_TEMPLATES)
    html = auth.post("/generate_program", data={"client": ""},
                     follow_redirects=True).get_data(as_text=True)
    assert "Select a client first" in html


def test_check_membership_form(auth):
    auth.post("/add_save_client", data={"name": "Arjun"})
    html = auth.post("/check_membership", data={"client": "Arjun"},
                     follow_redirects=True).get_data(as_text=True)
    assert "Membership: Active\nRenewal Date: N/A" in html
    html = auth.post("/check_membership", data={"client": "Nobody"},
                     follow_redirects=True).get_data(as_text=True)
    assert "Client not found" in html


def test_generate_pdf_download(auth):
    auth.post("/add_save_client", data={"name": "Arjun"})
    res = auth.get("/generate_pdf?client=Arjun")
    assert res.mimetype == "application/pdf" and res.data.startswith(b"%PDF-")
    assert 'filename="Arjun_report.pdf"' in res.headers["Content-Disposition"]
    assert "Select a client first" in auth.get("/generate_pdf",
                                               follow_redirects=True).get_data(as_text=True)


def test_add_workout_page_flow(auth):
    auth.post("/add_save_client", data={"name": "Arjun"})
    assert "Add Workout - Arjun" in auth.get("/add_workout?client=Arjun").get_data(as_text=True)
    res = auth.post("/add_workout?client=Arjun", data={
        "date": "2026-10-05", "workout_type": "Mobility", "duration_min": "20",
        "notes": "Hips"}, follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Workout added" in html and "<td>Hips</td>" in html


def test_add_workout_page_errors(auth):
    assert "Select a client first" in auth.get("/add_workout",
                                               follow_redirects=True).get_data(as_text=True)
    auth.post("/add_save_client", data={"name": "Arjun"})
    res = auth.post("/add_workout?client=Arjun", data={"date": "x", "workout_type": "Cardio"})
    assert res.status_code == 400 and "YYYY-MM-DD" in res.get_data(as_text=True)


# ---------- JSON API ----------

def test_api_clients_and_add(auth):
    assert auth.post("/api/add_save_client", json={"name": "Meena"}).status_code == 201
    again = auth.post("/api/add_save_client", json={"name": "Meena"})
    assert again.status_code == 200 and again.get_json()["created"] is False
    assert auth.post("/api/add_save_client", json={}).status_code == 400
    assert auth.get("/api/clients").get_json() == {"clients": ["Meena"]}


def test_api_load_client(auth):
    auth.post("/api/add_save_client", json={"name": "Meena"})
    auth.post("/api/add_workout", json={"name": "Meena", "date": "2026-10-01",
                                        "workout_type": "Cardio", "duration_min": 25})
    body = auth.get("/api/load_client/Meena").get_json()
    assert body["client"]["membership_status"] == "Active"
    assert body["workouts"][0]["duration_min"] == 25
    assert auth.get("/api/load_client/Nobody").status_code == 404


def test_api_generate_program(auth):
    auth.post("/api/add_save_client", json={"name": "Meena"})
    first = auth.post("/api/generate_program", json={"name": "Meena", "seed": 11}).get_json()
    second = auth.post("/api/generate_program", json={"name": "Meena", "seed": 11}).get_json()
    assert first == second and first["program"] in ALL_TEMPLATES
    assert auth.post("/api/generate_program", json={"name": "Meena"}).status_code == 200
    assert auth.post("/api/generate_program", json={"name": "Nobody"}).status_code == 404
    assert auth.post("/api/generate_program", json={"name": "Meena",
                                                    "seed": {"a": 1}}).status_code == 400


def test_api_pdf_membership_chart_workouts(auth, db):
    auth.post("/api/add_save_client", json={"name": "Meena"})
    assert auth.get("/api/generate_pdf/Meena").data.startswith(b"%PDF-")
    assert auth.get("/api/generate_pdf/Nobody").status_code == 404
    assert auth.get("/api/check_membership/Meena").get_json()["status"] == "Active"
    assert auth.get("/api/check_membership/Nobody").status_code == 404
    assert auth.get("/api/plot_charts/Meena").status_code == 404
    add_progress(db, "Meena", 90)
    chart = auth.get("/api/plot_charts/Meena")
    assert chart.mimetype == "image/png" and chart.data.startswith(PNG_MAGIC)
    assert auth.get("/api/refresh_workouts/Meena").get_json() == {"workouts": []}
    assert auth.get("/api/refresh_workouts/Nobody").status_code == 404
    assert auth.post("/api/add_workout", json={"name": "Meena", "date": "bad",
                                               "workout_type": "Cardio"}).status_code == 400


# ---------- errors ----------

def test_unknown_routes_and_methods(auth):
    assert auth.get("/api/nope").get_json() == {"error": "Not found"}
    assert "Page not found." in auth.get("/nope").get_data(as_text=True)
    assert auth.get("/api/add_workout").get_json() == {"error": "Method not allowed"}
    assert "Method not allowed." in auth.get("/add_save_client").get_data(as_text=True)
