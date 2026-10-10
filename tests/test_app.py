"""Tests for ACEest v3.1.2 - login, membership, AI program generator and PDF report."""
import random
import sqlite3

import pytest

from app import (APP_VERSION, EXPERIENCE_LEVELS, NotFoundError, ValidationError, create_app,
                 exercises_pool, export_pdf_report, generate_ai_program, load_client,
                 login_user, parse_client, parse_date, pdf_safe, programs, refresh_summary,
                 save_client, to_number)

FL3 = "Fat Loss (FL) – 3 day"
MG = "Muscle Gain (MG) – PPL"


def client_data(**overrides):
    data = {"name": "Arjun", "age": "28", "height": "175", "weight": "80", "program": FL3,
            "membership_expiry": "2026-12-31"}
    data.update(overrides)
    return data


# ---------- health / database ----------

def test_health_needs_no_login(client):
    res = client.get("/health")
    assert res.status_code == 200 and res.get_json()["version"] == APP_VERSION


def test_default_admin_password_is_hashed(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    password, role = conn.execute(
        "SELECT password, role FROM users WHERE username = 'admin'").fetchone()
    conn.close()
    assert role == "Admin" and password != "admin"


def test_admin_password_can_be_configured(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "x.db"),
                      "ADMIN_PASSWORD": "s3cret"})
    browser = app.test_client()
    assert browser.post("/api/login", json={"username": "admin",
                                            "password": "admin"}).status_code == 401
    assert browser.post("/api/login", json={"username": "admin",
                                            "password": "s3cret"}).status_code == 200


def test_older_database_gets_membership_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            age INTEGER, height REAL, weight REAL, program TEXT, calories INTEGER,
            target_weight REAL, target_adherence INTEGER);
        INSERT INTO clients (name, weight, program, target_weight) VALUES ('Old', 70, '', 65);
    """)
    conn.close()
    app = create_app({"TESTING": True, "DATABASE": str(path)})
    with app.app_context():
        from app import get_db
        client = load_client(get_db(), "Old")
    assert client["membership_expiry"] is None and client["target_weight"] == 65


# ---------- login ----------

def test_login_user(db):
    assert login_user(db, "admin", "admin") == "Admin"
    assert login_user(db, " admin ", " admin ") == "Admin"
    assert login_user(db, "admin", "wrong") is None
    assert login_user(db, "ghost", "admin") is None
    assert login_user(db, None, None) is None


def test_pages_redirect_to_login(client):
    res = client.get("/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/login")


def test_api_requires_login(client):
    res = client.get("/api/clients")
    assert res.status_code == 401 and res.get_json() == {"error": "Login required"}


def test_login_page_success_shows_role(client):
    res = client.post("/login", data={"username": "admin", "password": "admin"},
                      follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "ACEest Fitness Dashboard (Admin)" in html
    assert "Signed in as admin" in html


def test_login_page_failure_message_matches_original(client):
    res = client.post("/login", data={"username": "admin", "password": "nope"})
    assert res.status_code == 401
    assert "Invalid credentials\nTry admin / admin" in res.get_data(as_text=True)


def test_login_page_renders(client):
    assert "Username" in client.get("/login").get_data(as_text=True)


def test_logout(auth):
    auth.get("/logout")
    assert auth.get("/").status_code == 302


def test_api_login_and_logout(client):
    assert client.post("/api/login", json={"username": "admin",
                                           "password": "bad"}).status_code == 401
    res = client.post("/api/login", json={"username": "admin", "password": "admin"})
    assert res.get_json() == {"user": "admin", "role": "Admin"}
    assert client.get("/api/clients").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/clients").status_code == 401


# ---------- clients ----------

def test_parse_date():
    assert parse_date(" 2026-12-31 ") == "2026-12-31" and parse_date("") == ""
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        parse_date("31/12/2026", "Membership expiry")


def test_to_number_rejects_text():
    with pytest.raises(ValidationError, match="Age must be a whole number"):
        to_number("abc", "Age", int)
    with pytest.raises(ValidationError, match="negative"):
        to_number("-2", "Weight")
    with pytest.raises(ValidationError, match="between 0 and 10"):
        to_number("11", "Score", int, 10)


def test_save_client_with_membership(db):
    assert save_client(db, parse_client(client_data())) == "Client data saved"
    client = load_client(db, "Arjun")
    assert client["membership_expiry"] == "2026-12-31" and client["calories"] == 80 * 22


def test_save_client_without_program_uses_factor_25(db):
    save_client(db, parse_client(client_data(program="")))
    assert load_client(db, "Arjun")["calories"] == 80 * 25


def test_save_client_without_weight_has_no_calories(db):
    save_client(db, parse_client(client_data(weight="")))
    assert load_client(db, "Arjun")["calories"] is None


def test_save_client_keeps_existing_goals(db):
    save_client(db, parse_client(client_data()))
    db.execute("UPDATE clients SET target_weight = 70 WHERE name = 'Arjun'")
    db.commit()
    save_client(db, parse_client(client_data(weight="78")))
    client = load_client(db, "Arjun")
    assert client["target_weight"] == 70 and client["weight"] == 78


@pytest.mark.parametrize("overrides,message", [
    ({"name": ""}, "Name required"),
    ({"program": "Yoga"}, "Unknown program"),
])
def test_save_client_validation(db, overrides, message):
    with pytest.raises(ValidationError, match=message):
        save_client(db, parse_client(client_data(**overrides)))


def test_refresh_summary_format():
    text = refresh_summary({"name": "Arjun", "age": 28, "height": 175.0, "weight": 80.0,
                            "program": FL3, "membership_expiry": "2026-12-31"})
    assert text == ("Name: Arjun\nAge: 28\nHeight: 175.0 cm\nWeight: 80.0 kg\n"
                    f"Program: {FL3}\nMembership Expiry: 2026-12-31")


# ---------- AI program generator ----------

@pytest.mark.parametrize("program,focus", [
    (FL3, "Conditioning"), (MG, "Hypertrophy"), ("Beginner (BG)", "Full Body"),
    ("", "Full Body"),
])
def test_generator_focus_follows_goal(db, program, focus):
    save_client(db, parse_client(client_data(program=program)))
    plan = generate_ai_program(db, "Arjun", "beginner", random.Random(1))
    assert plan["focus"] == focus
    assert {row["exercise"] for row in plan["rows"]} <= set(exercises_pool[focus])


@pytest.mark.parametrize("level,days,per_day,sets,reps", [
    ("beginner", 3, 3, (2, 3), (8, 12)),
    ("intermediate", 4, 4, (3, 4), (8, 15)),
    ("advanced", 5, 4, (4, 5), (6, 15)),
])
def test_generator_volume_follows_experience(db, level, days, per_day, sets, reps):
    save_client(db, parse_client(client_data()))
    plan = generate_ai_program(db, "Arjun", level.upper(), random.Random(7))
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][:days]
    assert [r["day"] for r in plan["rows"]] == [d for d in day_names for _ in range(per_day)]
    for day in day_names:
        exercises = [r["exercise"] for r in plan["rows"] if r["day"] == day]
        assert len(set(exercises)) == per_day  # no repeats within a day
    assert all(sets[0] <= r["sets"] <= sets[1] for r in plan["rows"])
    assert all(reps[0] <= r["reps"] <= reps[1] for r in plan["rows"])


def test_generator_is_reproducible_with_seed(db):
    save_client(db, parse_client(client_data()))
    first = generate_ai_program(db, "Arjun", "advanced", random.Random(42))
    second = generate_ai_program(db, "Arjun", "advanced", random.Random(42))
    assert first == second


@pytest.mark.parametrize("name,level,error,message", [
    ("", "beginner", ValidationError, "Select client first"),
    ("Arjun", "expert", ValidationError, "Invalid experience level"),
    ("Arjun", None, ValidationError, "Invalid experience level"),
    ("Nobody", "beginner", NotFoundError, "Client not found"),
])
def test_generator_validation(db, name, level, error, message):
    save_client(db, parse_client(client_data()))
    with pytest.raises(error, match=message):
        generate_ai_program(db, name, level)


def test_experience_levels():
    assert EXPERIENCE_LEVELS == ["beginner", "intermediate", "advanced"]


# ---------- PDF ----------

def test_pdf_safe_replaces_unsupported_characters():
    assert pdf_safe("Fat Loss (FL) – 3 day") == "Fat Loss (FL) - 3 day"
    assert pdf_safe("Ravi 🙂") == "Ravi ?"


def test_export_pdf_report(db):
    save_client(db, parse_client(client_data()))
    assert export_pdf_report(db, "Arjun").startswith(b"%PDF-")


def test_export_pdf_report_unknown_client(db):
    with pytest.raises(NotFoundError):
        export_pdf_report(db, "Nobody")


# ---------- web pages ----------

def test_dashboard_empty(auth):
    html = auth.get("/").get_data(as_text=True)
    assert "Client Management" in html and "Membership Expiry (YYYY-MM-DD)" in html
    assert "Export PDF Report" not in html  # only offered once a client is selected


def test_save_client_form(auth):
    res = auth.post("/save_client", data=client_data(), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Client data saved" in html and "Saved client: Arjun" in html
    assert "Membership Expiry: 2026-12-31" in html
    assert "Export PDF Report" in html


def test_save_client_form_error(auth):
    res = auth.post("/save_client", data=client_data(name=""), follow_redirects=True)
    assert "Name required" in res.get_data(as_text=True)
    res = auth.post("/save_client", data=client_data(membership_expiry="soon"),
                    follow_redirects=True)
    assert "Membership expiry must be in YYYY-MM-DD format" in res.get_data(as_text=True)


def test_load_client_form(auth):
    auth.post("/save_client", data=client_data())
    res = auth.post("/load_client", data={"name": "Arjun"}, follow_redirects=True)
    assert "Name: Arjun" in res.get_data(as_text=True)
    res = auth.post("/load_client", data={"name": "Nobody"}, follow_redirects=True)
    assert "Client not found" in res.get_data(as_text=True)


def test_select_unknown_client(auth):
    assert "Client not found" in auth.get("/?client=Nobody").get_data(as_text=True)


def test_generate_ai_program_form(auth):
    auth.post("/save_client", data=client_data())
    res = auth.post("/generate_ai_program", data={"name": "Arjun", "experience": "beginner"},
                    follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "AI workout program generated!" in html
    assert "AI program generated for Arjun" in html
    assert "beginner, Conditioning focus" in html
    assert html.count("<td>Monday</td>") == 3
    res = auth.post("/generate_ai_program", data={"name": "Arjun", "experience": "advanced"},
                    follow_redirects=True)
    assert '<option value="advanced" selected>' in res.get_data(as_text=True)


def test_generated_program_only_shown_for_its_client(auth):
    auth.post("/save_client", data=client_data())
    auth.post("/save_client", data=client_data(name="Meena"))
    auth.post("/generate_ai_program", data={"name": "Arjun", "experience": "beginner"})
    html = auth.get("/?client=Meena&tab=analytics").get_data(as_text=True)
    assert "Select a client and click Generate AI Program" in html


def test_generate_ai_program_form_errors(auth):
    res = auth.post("/generate_ai_program", data={"name": "", "experience": "beginner"},
                    follow_redirects=True)
    assert "Select client first" in res.get_data(as_text=True)


def test_export_pdf_download(auth):
    auth.post("/save_client", data=client_data())
    res = auth.get("/export_pdf_report?client=Arjun")
    assert res.mimetype == "application/pdf" and res.data.startswith(b"%PDF-")
    assert 'filename="Arjun_report.pdf"' in res.headers["Content-Disposition"]


def test_export_pdf_download_without_client(auth):
    res = auth.get("/export_pdf_report", follow_redirects=True)
    assert "Select client first" in res.get_data(as_text=True)


# ---------- JSON API ----------

def test_api_programs_and_clients(auth):
    assert auth.get("/api/programs").get_json()["programs"][MG]["factor"] == 35
    auth.post("/api/save_client", json={"name": "Meena", "weight": 60})
    assert auth.get("/api/clients").get_json() == {"clients": ["Meena"]}


def test_api_save_and_load_client(auth):
    res = auth.post("/api/save_client", json={"name": "Meena", "weight": 60,
                                              "program": "Beginner (BG)",
                                              "membership_expiry": "2027-01-31"})
    assert res.status_code == 201 and res.get_json()["client"]["calories"] == 60 * 26
    body = auth.get("/api/load_client/Meena").get_json()
    assert body["summary"].endswith("Membership Expiry: 2027-01-31")
    assert auth.get("/api/load_client/Nobody").status_code == 404
    assert auth.post("/api/save_client", json={"weight": 60}).status_code == 400


def test_api_generate_ai_program(auth):
    auth.post("/api/save_client", json={"name": "Meena", "program": MG})
    first = auth.post("/api/generate_ai_program",
                      json={"name": "Meena", "experience": "intermediate", "seed": 3}).get_json()
    again = auth.post("/api/generate_ai_program",
                      json={"name": "Meena", "experience": "intermediate", "seed": 3}).get_json()
    assert first == again and first["focus"] == "Hypertrophy" and len(first["rows"]) == 16
    unseeded = auth.post("/api/generate_ai_program",
                         json={"name": "Meena", "experience": "beginner"})
    assert unseeded.status_code == 200


@pytest.mark.parametrize("payload,status", [
    ({"name": "Meena", "experience": "guru"}, 400),
    ({"name": "Nobody", "experience": "beginner"}, 404),
    ({"name": "Meena", "experience": "beginner", "seed": [1, 2]}, 400),
])
def test_api_generate_ai_program_errors(auth, payload, status):
    auth.post("/api/save_client", json={"name": "Meena", "program": MG})
    assert auth.post("/api/generate_ai_program", json=payload).status_code == status


def test_api_export_pdf_report(auth):
    auth.post("/api/save_client", json={"name": "Meena", "program": MG})
    res = auth.get("/api/export_pdf_report/Meena")
    assert res.status_code == 200 and res.data.startswith(b"%PDF-")
    assert auth.get("/api/export_pdf_report/Nobody").status_code == 404


# ---------- errors ----------

def test_unknown_routes_and_methods(auth):
    assert auth.get("/api/nope").get_json() == {"error": "Not found"}
    assert "Page not found." in auth.get("/nope").get_data(as_text=True)
    assert auth.get("/api/save_client").get_json() == {"error": "Method not allowed"}
    assert "Method not allowed." in auth.get("/save_client").get_data(as_text=True)


def test_programs_unchanged_from_224():
    assert len(programs) == 4 and programs[FL3]["factor"] == 22
