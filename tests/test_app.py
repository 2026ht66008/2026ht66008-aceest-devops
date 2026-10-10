"""Tests for ACEest v1.1.2 - in-memory client list, CSV export and progress chart."""
import csv
import io

import pytest

from app import (APP_VERSION, CSV_HEADER, ValidationError, calculate_calories, export_csv,
                 parse_client, programs, to_number, update_chart, update_program)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def client_form(**overrides):
    data = {"name": "Arjun", "age": "28", "weight": "80", "program": "Fat Loss (FL)",
            "adherence": "75", "notes": "Knee care"}
    data.update(overrides)
    return data


def make_client(**overrides):
    return parse_client(client_form(**overrides))


# ---------- health ----------

def test_health_reports_version(client):
    assert client.get("/health").get_json()["version"] == APP_VERSION


# ---------- logic ----------

@pytest.mark.parametrize("program,factor", [("Fat Loss (FL)", 22), ("Muscle Gain (MG)", 35),
                                            ("Beginner (BG)", 26)])
def test_calories_per_program(program, factor):
    assert calculate_calories(70, program) == 70 * factor


def test_update_program_short_plans():
    plan = update_program("Muscle Gain (MG)", 0)
    assert plan["workout"] == "Squat, Bench, Deadlift, Press, Rows"
    assert plan["calories"] is None


def test_update_program_unknown():
    assert update_program("Yoga") is None


@pytest.mark.parametrize("value,kind,maximum,message", [
    ("abc", float, None, "must be a number"),
    ("1.5", int, None, "must be a whole number"),
    ("-1", int, None, "cannot be negative"),
    ("150", int, 100, "between 0 and 100"),
])
def test_to_number_invalid(value, kind, maximum, message):
    with pytest.raises(ValidationError, match=message):
        to_number(value, "Field", kind, maximum=maximum)


def test_parse_client_includes_notes():
    assert make_client()["notes"] == "Knee care"


def test_export_csv_has_original_header_and_rows():
    rows = list(csv.reader(io.StringIO(export_csv([make_client(), make_client(name="Meena")]))))
    assert rows[0] == CSV_HEADER
    assert rows[1] == ["Arjun", "28", "80.0", "Fat Loss (FL)", "75", "Knee care"]
    assert rows[2][0] == "Meena"


def test_update_chart_returns_png_even_without_clients():
    assert update_chart([]).startswith(PNG_MAGIC)
    assert update_chart([make_client()]).startswith(PNG_MAGIC)


# ---------- web pages ----------

def test_index_shows_header_and_empty_list(client):
    html = client.get("/").get_data(as_text=True)
    assert "ACEest FUNCTIONAL FITNESS SYSTEM v2" in html
    assert "No clients saved yet" in html
    assert "Coach Notes" in html


def test_index_plan_and_calories(client):
    html = client.get("/", query_string={"program": "Beginner (BG)", "weight": "60"}
                      ).get_data(as_text=True)
    assert "Air Squats, Ring Rows, Push-ups" in html
    assert f"{60 * 26} kcal" in html


def test_index_bad_weight_warns(client):
    html = client.get("/", query_string={"program": "Beginner (BG)", "weight": "x"}
                      ).get_data(as_text=True)
    assert "Weight must be a number" in html


def test_index_unknown_program_404(client):
    assert client.get("/", query_string={"program": "Yoga"}).status_code == 404


def test_save_client_form_adds_to_client_list(client):
    res = client.post("/save_client", data=client_form(), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert "Client Arjun saved successfully." in html
    assert "<td>Arjun</td>" in html
    assert "<td>Knee care</td>" in html


def test_save_client_form_missing_fields(client):
    res = client.post("/save_client", data=client_form(program=""), follow_redirects=True)
    assert "Please fill client name and program." in res.get_data(as_text=True)
    assert client.get("/api/clients").get_json()["clients"] == []


def test_export_csv_page_without_clients_warns(client):
    res = client.get("/export_csv", follow_redirects=True)
    assert "No clients to export." in res.get_data(as_text=True)


def test_export_csv_page_downloads_file(client):
    client.post("/save_client", data=client_form())
    res = client.get("/export_csv")
    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    assert "attachment; filename=clients.csv" in res.headers["Content-Disposition"]
    assert res.get_data(as_text=True).startswith("Name,Age,Weight,Program,Adherence,Notes")


# ---------- JSON API ----------

def test_api_programs(client):
    assert client.get("/api/programs").get_json()["programs"] == list(programs)


def test_api_program_detail_and_unknown(client):
    assert client.get("/api/programs/Fat Loss (FL)").get_json()["calorie_factor"] == 22
    assert client.get("/api/programs/Yoga").status_code == 404


def test_api_update_program(client):
    ok = client.post("/api/update_program", json={"program": "Fat Loss (FL)", "weight": 50})
    assert ok.get_json()["calories"] == 50 * 22
    assert client.post("/api/update_program", json={"program": "Fat Loss (FL)",
                                                    "weight": "x"}).status_code == 400
    assert client.post("/api/update_program", json={"program": "Yoga"}).status_code == 404


def test_api_save_client_and_list(client):
    res = client.post("/api/save_client", json={"name": "Meena", "age": 30, "weight": 62,
                                                "program": "Beginner (BG)", "adherence": 90})
    assert res.status_code == 201
    assert res.get_json()["message"] == "Client Meena saved successfully."
    clients = client.get("/api/clients").get_json()["clients"]
    assert [c["name"] for c in clients] == ["Meena"]


def test_api_save_client_rejects_unknown_program(client):
    res = client.post("/api/save_client", json={"name": "Meena", "program": "Yoga"})
    assert res.status_code == 400
    assert "Unknown program" in res.get_json()["error"]


def test_api_save_client_validation(client):
    assert client.post("/api/save_client", json={"name": "Meena"}).status_code == 400


def test_api_export_csv(client):
    assert client.get("/api/export_csv").status_code == 404
    client.post("/api/save_client", json={"name": "Meena", "program": "Beginner (BG)"})
    res = client.get("/api/export_csv")
    assert res.status_code == 200 and "Meena" in res.get_data(as_text=True)


def test_api_update_chart_png(client):
    res = client.get("/api/update_chart")
    assert res.status_code == 200
    assert res.mimetype == "image/png"
    assert res.data.startswith(PNG_MAGIC)


def test_clients_are_isolated_per_app_instance(app):
    from app import create_app
    other = create_app({"TESTING": True}).test_client()
    app.test_client().post("/api/save_client", json={"name": "A", "program": "Beginner (BG)"})
    assert other.get("/api/clients").get_json()["clients"] == []


# ---------- errors ----------

def test_unknown_routes(client):
    assert client.get("/api/nope").get_json() == {"error": "Not found"}
    assert "Page not found." in client.get("/nope").get_data(as_text=True)


def test_wrong_methods(client):
    assert client.get("/api/save_client").get_json() == {"error": "Method not allowed"}
    assert "Method not allowed." in client.get("/save_client").get_data(as_text=True)
