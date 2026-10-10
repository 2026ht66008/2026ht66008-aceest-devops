"""Tests for ACEest v3.0.1 - goals, workouts, exercises, body metrics, BMI and charts."""
import re
import sqlite3
from datetime import date, datetime

import pytest

from app import (APP_VERSION, WORKOUT_TYPES, NotFoundError, ValidationError, calculate_bmi,
                 create_app, ensure_client, load_client, log_metrics, log_workout,
                 parse_client, parse_date, programs, refresh_client_list, refresh_summary,
                 save_client, save_progress, setup_data, show_bmi_info, show_progress_chart,
                 show_weight_chart, to_number, workout_history)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FL3 = "Fat Loss (FL) – 3 day"


def client_data(**overrides):
    data = {"name": "Arjun", "age": "28", "height": "175", "weight": "80", "program": FL3,
            "target_weight": "72", "target_adherence": "85", "adherence": "70"}
    data.update(overrides)
    return data


@pytest.fixture
def saved(db):
    save_client(db, parse_client(client_data()))
    return "Arjun"


# ---------- health / database ----------

def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200 and res.get_json()["version"] == APP_VERSION


def test_all_tables_exist(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"clients", "progress", "workouts", "exercises", "metrics"} <= tables


def test_old_database_is_migrated_without_data_loss(tmp_path):
    """A v2.0.1 database gains the new columns; existing clients are kept."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
                              age INTEGER, weight REAL, program TEXT, calories INTEGER);
        INSERT INTO clients (name, age, weight, program, calories)
        VALUES ('Old Timer', 40, 90, 'Beginner (BG)', 2340);
    """)
    conn.close()
    app = create_app({"TESTING": True, "DATABASE": str(path)})
    with app.app_context():
        from app import get_db
        client = load_client(get_db(), "Old Timer")
    assert client["calories"] == 2340
    assert client["height"] is None and client["target_weight"] is None


# ---------- data & helpers ----------

def test_programs_have_factor_and_description():
    assert setup_data()["Fat Loss (FL) – 5 day"] == {
        "factor": 24, "desc": "5-day split, higher volume fat loss"}
    assert len(programs) == 4


@pytest.mark.parametrize("value,expected", [("2026-10-08", "2026-10-08"), ("", ""),
                                            (None, "")])
def test_parse_date_valid(value, expected):
    assert parse_date(value) == expected


def test_parse_date_invalid():
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        parse_date("08/10/2026")


@pytest.mark.parametrize("value,kind,maximum,message", [
    ("x", float, None, "must be a number"), ("2.5", int, None, "whole number"),
    ("-3", float, None, "negative"), ("101", int, 100, "between 0 and 100"),
])
def test_to_number_rejects_bad_input(value, kind, maximum, message):
    with pytest.raises(ValidationError, match=message):
        to_number(value, "Field", kind, maximum)


# ---------- clients ----------

def test_save_client_calculates_calories_and_goals(db, saved):
    client = load_client(db, saved)
    assert client["calories"] == 80 * 22
    assert client["height"] == 175.0
    assert client["target_weight"] == 72.0 and client["target_adherence"] == 85


def test_save_client_stores_zero_as_null(db):
    save_client(db, parse_client(client_data(age="", height="0", weight="",
                                             target_weight="", target_adherence="")))
    client = load_client(db, "Arjun")
    assert client["age"] is None and client["weight"] is None and client["calories"] is None


@pytest.mark.parametrize("overrides,message", [
    ({"name": ""}, "Name is required"),
    ({"program": ""}, "Program is required"),
    ({"program": "Yoga"}, "Unknown program"),
])
def test_save_client_validation(db, overrides, message):
    with pytest.raises(ValidationError, match=message):
        save_client(db, parse_client(client_data(**overrides)))


def test_refresh_client_list_is_sorted(db):
    for name in ("Zara", "Arjun", "Meena"):
        save_client(db, parse_client(client_data(name=name)))
    assert refresh_client_list(db) == ["Arjun", "Meena", "Zara"]


def test_ensure_client(db, saved):
    assert ensure_client(db, saved)["name"] == "Arjun"
    with pytest.raises(ValidationError, match="Select or enter client first"):
        ensure_client(db, "")
    with pytest.raises(NotFoundError, match="Client not found"):
        ensure_client(db, "Nobody")


def test_summary_sections(db, saved):
    save_progress(db, saved, 80, now=datetime(2026, 9, 1))
    save_progress(db, saved, 65, now=datetime(2026, 9, 8))
    log_metrics(db, saved, {"date": "2026-09-01", "weight": "81", "waist": "90",
                            "bodyfat": "22"})
    log_metrics(db, saved, {"date": "2026-09-15", "weight": "79.5", "waist": "88",
                            "bodyfat": "21"})
    summary = refresh_summary(db, saved)
    assert summary["weeks_logged"] == 2 and summary["average_adherence"] == 72.5
    assert summary["goals"] == "Target Weight: 72.0 kg; Target Adherence: 85%"
    assert summary["program_notes"] == "3-day full-body fat loss"
    text = summary["text"]
    assert "Height    : 175.0 cm" in text
    assert "LAST BODY METRICS\n-----------------\n2026-09-15 | 79.5 kg, Waist 88.0 cm, " \
           "Bodyfat 21.0%" in text


def test_summary_defaults_without_data(db):
    save_client(db, parse_client(client_data(age="", target_weight="", target_adherence="")))
    summary = refresh_summary(db, "Arjun")
    assert summary["goals"] == "None" and summary["average_adherence"] == 0
    assert "Age       : -" in summary["text"]
    assert summary["text"].endswith("None")


def test_goal_summary_with_only_target_adherence(db):
    save_client(db, parse_client(client_data(target_weight="")))
    assert refresh_summary(db, "Arjun")["goals"] == "Target Adherence: 85%"


def test_save_progress(db, saved):
    assert save_progress(db, saved, 90, now=datetime(2026, 10, 8)) == (
        "Weekly progress logged", "Week 40 - 2026")
    with pytest.raises(ValidationError, match="Enter client name first"):
        save_progress(db, "", 50)


# ---------- workouts & metrics ----------

def test_log_workout_with_exercise(db, saved):
    message, workout_id = log_workout(
        db, saved, {"date": "2026-10-01", "workout_type": "Strength", "duration_min": "75",
                    "notes": "Heavy day"},
        [{"name": "Squat", "sets": "5", "reps": "5", "weight": "100"}])
    assert message == "Workout logged successfully"
    row = db.execute("SELECT name, sets, reps, weight FROM exercises WHERE workout_id = ?",
                     (workout_id,)).fetchone()
    assert tuple(row) == ("Squat", 5, 5, 100.0)


def test_log_workout_skips_unnamed_exercise_and_defaults(db, saved):
    _msg, workout_id = log_workout(db, saved, {"workout_type": "Mobility"},
                                   [{"name": "", "sets": 3}])
    workout = db.execute("SELECT date, duration_min FROM workouts WHERE id = ?",
                         (workout_id,)).fetchone()
    assert workout["date"] == date.today().isoformat() and workout["duration_min"] == 60
    assert db.execute("SELECT COUNT(*) FROM exercises").fetchone()[0] == 0


@pytest.mark.parametrize("data,message", [
    ({"date": "", "workout_type": "Strength"}, "Date and workout type are required"),
    ({"date": "2026-10-01", "workout_type": ""}, "Date and workout type are required"),
    ({"date": "2026-10-01", "workout_type": "Zumba"}, "Unknown workout type"),
    ({"date": "01-10-2026", "workout_type": "Strength"}, "YYYY-MM-DD"),
])
def test_log_workout_validation(db, saved, data, message):
    with pytest.raises(ValidationError, match=message):
        log_workout(db, saved, data)


def test_workout_types_match_original():
    assert WORKOUT_TYPES == ["Strength", "Hypertrophy", "Conditioning", "Mixed", "Mobility"]


def test_workout_history_newest_first(db, saved):
    log_workout(db, saved, {"date": "2026-09-01", "workout_type": "Strength"})
    log_workout(db, saved, {"date": "2026-10-01", "workout_type": "Conditioning"},
                [{"name": "Rower", "sets": 1, "reps": 1, "weight": 0}])
    history = workout_history(db, saved)
    assert [w["date"] for w in history] == ["2026-10-01", "2026-09-01"]
    assert history[0]["exercises"][0]["name"] == "Rower"


def test_log_metrics_requires_date(db, saved):
    with pytest.raises(ValidationError, match="Date is required"):
        log_metrics(db, saved, {"date": "", "weight": 80})


# ---------- analytics ----------

def test_calculate_bmi_rounds_before_categorising():
    """Like the original, BMI is rounded to 1 decimal first: 18.49 -> 18.5 -> Normal."""
    assert calculate_bmi(180, 59.9) == (18.5, "Normal", "Low risk if active and strong.")
    assert calculate_bmi(180, 59.5)[1] == "Underweight"  # 18.36 -> 18.4


@pytest.mark.parametrize("bmi_target,category,risk_start", [
    (18.4, "Underweight", "Potential nutrient deficiency"),
    (18.5, "Normal", "Low risk"),
    (24.9, "Normal", "Low risk"),
    (25.0, "Overweight", "Moderate risk"),
    (29.9, "Overweight", "Moderate risk"),
    (30.0, "Obese", "Higher risk"),
])
def test_calculate_bmi_exact_boundaries(bmi_target, category, risk_start):
    bmi, cat, risk = calculate_bmi(100, bmi_target)  # height 1 m -> BMI equals weight
    assert bmi == bmi_target and cat == category and risk.startswith(risk_start)


def test_show_bmi_info(db, saved):
    info = show_bmi_info(db, saved)
    assert info["bmi"] == 26.1 and info["category"] == "Overweight"
    assert info["message"].startswith("BMI for Arjun: 26.1 (Overweight)\n\nRisk note: ")


def test_show_bmi_info_needs_height_and_weight(db):
    save_client(db, parse_client(client_data(height="")))
    with pytest.raises(ValidationError, match="Enter valid height and weight first"):
        show_bmi_info(db, "Arjun")


def test_charts_need_data(db, saved):
    with pytest.raises(NotFoundError, match="No progress data available"):
        show_progress_chart(db, saved)
    with pytest.raises(NotFoundError, match="No weight metrics available"):
        show_weight_chart(db, saved)


def test_charts_render_png(db, saved):
    save_progress(db, saved, 70)
    log_metrics(db, saved, {"date": "2026-09-01", "weight": 80})
    assert show_progress_chart(db, saved).startswith(PNG_MAGIC)
    assert show_weight_chart(db, saved).startswith(PNG_MAGIC)


# ---------- web: dashboard ----------

def test_dashboard_renders(client):
    html = client.get("/").get_data(as_text=True)
    assert "Client Management" in html and "Progress &amp; Analytics" in html
    assert 'role="status">Ready<' in html


def test_save_client_form_updates_list_and_status(client):
    res = client.post("/save_client", data=client_data(), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Client data saved" in html
    assert "Saved client: Arjun" in html
    assert '<option value="Arjun" selected>' in html
    assert "PROGRAM NOTES" in html


def test_save_client_form_error_keeps_fields(client):
    res = client.post("/save_client", data=client_data(program=""), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Program is required" in html and 'value="Arjun"' in html


@pytest.mark.parametrize("form,message", [
    ({"name": ""}, "Enter or select client name first"),
    ({"name": "Nobody"}, "Client not found"),
])
def test_load_client_form_errors(client, form, message):
    res = client.post("/load_client", data=form, follow_redirects=True)
    assert message in res.get_data(as_text=True)


def test_load_client_form(client, arjun):
    res = client.post("/load_client", data={"name": arjun}, follow_redirects=True)
    assert "Loaded client: Arjun" in res.get_data(as_text=True)


def test_select_unknown_client(client):
    assert "Client not found" in client.get("/?client=Nobody").get_data(as_text=True)


def test_save_progress_form(client, arjun):
    res = client.post("/save_progress", data={"name": arjun, "adherence": "88"},
                      follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Weekly progress logged" in html and "Weeks logged       : 1" in html


def test_save_progress_form_errors(client):
    res = client.post("/save_progress", data={"name": "", "adherence": "50"},
                      follow_redirects=True)
    assert "Enter client name first" in res.get_data(as_text=True)


def test_analytics_tab_without_client(client):
    html = client.get("/?tab=analytics").get_data(as_text=True)
    assert "Select or load a client first" in html


def test_analytics_buttons(client, arjun):
    res = client.get(f"/analytics/progress?client={arjun}", follow_redirects=True)
    assert "No progress data available for this client" in res.get_data(as_text=True)
    client.post("/api/save_progress", json={"name": arjun, "adherence": 70})
    res = client.get(f"/analytics/progress?client={arjun}", follow_redirects=True)
    assert "/api/show_progress_chart/Arjun" in res.get_data(as_text=True)
    client.post("/api/log_metrics", json={"name": arjun, "weight": 80})
    res = client.get(f"/analytics/weight?client={arjun}", follow_redirects=True)
    assert "/api/show_weight_chart/Arjun" in res.get_data(as_text=True)
    res = client.get(f"/analytics/bmi?client={arjun}", follow_redirects=True)
    assert "BMI for Arjun: 26.1 (Overweight)" in res.get_data(as_text=True)


def test_analytics_bmi_without_height(client):
    client.post("/api/save_client", json={"name": "Meena", "weight": 60,
                                          "program": "Beginner (BG)"})
    res = client.get("/analytics/bmi?client=Meena", follow_redirects=True)
    assert "Enter valid height and weight first" in res.get_data(as_text=True)
    direct = client.get("/?client=Meena&tab=analytics&chart=bmi").get_data(as_text=True)
    assert "Enter valid height and weight first" in direct


def test_analytics_unknown_chart(client):
    assert client.get("/analytics/pie?client=x").status_code == 404


# ---------- web: workout & metrics pages ----------

def test_log_workout_page_requires_client(client):
    res = client.get("/log_workout", follow_redirects=True)
    assert "Select or enter client first" in res.get_data(as_text=True)
    res = client.get("/log_workout?name=Nobody", follow_redirects=True)
    assert "Client not found" in res.get_data(as_text=True)


def test_log_workout_page_flow(client, arjun):
    page = client.get(f"/log_workout?name={arjun}").get_data(as_text=True)
    assert "Log Workout – Arjun" in page and date.today().isoformat() in page
    res = client.post(f"/log_workout?client={arjun}", data={
        "date": "2026-10-02", "workout_type": "Hypertrophy", "duration_min": "50",
        "notes": "Upper body", "ex_name": "Bench", "ex_sets": "4", "ex_reps": "8",
        "ex_weight": "60"}, follow_redirects=True)
    assert "Workout logged successfully" in res.get_data(as_text=True)
    history = client.get(f"/workout_history?client={arjun}").get_data(as_text=True)
    assert "Upper body" in history and "Bench 4x8 @ 60.0 kg" in history
    assert "Loaded workout history for Arjun" in history


def test_log_workout_page_validation(client, arjun):
    res = client.post(f"/log_workout?client={arjun}", data={
        "date": "2026-10-02", "workout_type": "", "duration_min": "50"})
    assert res.status_code == 400
    assert "Date and workout type are required" in res.get_data(as_text=True)


def test_workout_history_page_empty(client, arjun):
    assert "No workouts logged yet" in client.get(
        f"/workout_history?client={arjun}").get_data(as_text=True)


def test_workout_history_page_requires_client(client):
    assert "Select or enter client first" in client.get(
        "/workout_history", follow_redirects=True).get_data(as_text=True)


def test_log_metrics_page_flow(client, arjun):
    page = client.get(f"/log_metrics?name={arjun}").get_data(as_text=True)
    assert 'value="80.0"' in page  # pre-filled with the current weight
    res = client.post(f"/log_metrics?client={arjun}", data={
        "date": "2026-10-03", "weight": "79", "waist": "87", "bodyfat": "20"},
        follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Metrics logged successfully" in html
    assert "2026-10-03 | 79.0 kg, Waist 87.0 cm, Bodyfat 20.0%" in html


def test_log_metrics_page_validation(client, arjun):
    res = client.post(f"/log_metrics?client={arjun}", data={"date": "", "weight": "79"})
    assert res.status_code == 400 and "Date is required" in res.get_data(as_text=True)


def test_log_metrics_page_requires_client(client):
    assert "Select or enter client first" in client.get(
        "/log_metrics", follow_redirects=True).get_data(as_text=True)


# ---------- JSON API ----------

def test_api_programs_and_clients(client, arjun):
    assert client.get("/api/programs").get_json()["programs"][FL3]["factor"] == 22
    assert client.get("/api/clients").get_json() == {"clients": ["Arjun"]}


def test_api_save_and_load_client(client, arjun):
    body = client.get(f"/api/load_client/{arjun}").get_json()
    assert body["client"]["calories"] == 1760
    assert body["weeks_logged"] == 0 and "CLIENT PROFILE" in body["text"]


def test_api_client_errors(client):
    assert client.post("/api/save_client", json={"program": FL3}).status_code == 400
    assert client.get("/api/load_client/Nobody").status_code == 404


def test_api_save_progress(client, arjun):
    res = client.post("/api/save_progress", json={"name": arjun, "adherence": 75})
    assert res.status_code == 201
    assert re.match(r"^Week \d{2} - \d{4}$", res.get_json()["week"])
    assert client.post("/api/save_progress", json={"name": "Nobody",
                                                   "adherence": 75}).status_code == 404
    assert client.post("/api/save_progress", json={"name": arjun,
                                                   "adherence": 175}).status_code == 400


def test_api_log_workout_and_history(client, arjun):
    res = client.post("/api/log_workout", json={
        "name": arjun, "date": "2026-10-04", "workout_type": "Mixed", "duration_min": 45,
        "exercises": [{"name": "Deadlift", "sets": 3, "reps": 5, "weight": 120},
                      {"name": "Pull-Up", "sets": 3, "reps": 8, "weight": 0}]})
    assert res.status_code == 201 and res.get_json()["workout_id"] > 0
    workouts = client.get(f"/api/workout_history/{arjun}").get_json()["workouts"]
    assert [e["name"] for e in workouts[0]["exercises"]] == ["Deadlift", "Pull-Up"]


@pytest.mark.parametrize("payload,status", [
    ({"name": "Arjun", "workout_type": "Mixed", "exercises": "Squat"}, 400),
    ({"name": "Arjun", "workout_type": "Dance"}, 400),
    ({"name": "", "workout_type": "Mixed"}, 400),
    ({"name": "Nobody", "workout_type": "Mixed"}, 404),
])
def test_api_log_workout_errors(client, arjun, payload, status):
    assert client.post("/api/log_workout", json=payload).status_code == status


def test_api_log_metrics(client, arjun):
    assert client.post("/api/log_metrics", json={"name": arjun, "weight": 78}).status_code == 201
    assert client.post("/api/log_metrics", json={"name": arjun, "date": "bad"}
                       ).status_code == 400
    assert client.get("/api/workout_history/Nobody").status_code == 404


def test_api_charts_and_bmi(client, arjun):
    assert client.get(f"/api/show_progress_chart/{arjun}").status_code == 404
    assert client.get(f"/api/show_weight_chart/{arjun}").status_code == 404
    client.post("/api/save_progress", json={"name": arjun, "adherence": 75})
    client.post("/api/log_metrics", json={"name": arjun, "weight": 78})
    for kind in ("progress", "weight"):
        res = client.get(f"/api/show_{kind}_chart/{arjun}")
        assert res.mimetype == "image/png" and res.data.startswith(PNG_MAGIC)
    assert client.get(f"/api/show_bmi_info/{arjun}").get_json()["category"] == "Overweight"
    assert client.get("/api/show_bmi_info/Nobody").status_code == 404


# ---------- errors ----------

def test_unknown_routes_and_methods(client):
    assert client.get("/api/nope").get_json() == {"error": "Not found"}
    assert "Page not found." in client.get("/nope").get_data(as_text=True)
    assert client.get("/api/save_client").get_json() == {"error": "Method not allowed"}
    assert "Method not allowed." in client.get("/save_client").get_data(as_text=True)
