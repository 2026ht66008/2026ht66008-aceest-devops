"""Tests for ACEest v1.1 - client profile, plans and calorie estimate."""
import pytest

from app import (APP_VERSION, ValidationError, calculate_calories, parse_client,
                 programs, save_client, to_number, update_program)

FACTORS = {"Fat Loss (FL)": 22, "Muscle Gain (MG)": 35, "Beginner (BG)": 26}


def client_form(**overrides):
    data = {"name": "Arjun", "age": "28", "weight": "80", "program": "Fat Loss (FL)",
            "adherence": "75"}
    data.update(overrides)
    return data


# ---------- health ----------

def test_health_reports_version(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["version"] == APP_VERSION


# ---------- logic ----------

@pytest.mark.parametrize("program,factor", FACTORS.items())
def test_calorie_factor_per_program(program, factor):
    assert programs[program]["calorie_factor"] == factor
    assert calculate_calories(80, program) == 80 * factor


def test_calories_are_truncated_to_int():
    assert calculate_calories(70.5, "Fat Loss (FL)") == int(70.5 * 22)


def test_update_program_without_weight_has_no_calories():
    plan = update_program("Muscle Gain (MG)", 0)
    assert plan["calories"] is None
    assert plan["color"] == "#2ecc71"
    assert "Sat: Barbell Rows 4x10" in plan["workout"]


def test_update_program_with_weight_has_calories():
    assert update_program("Beginner (BG)", 60)["calories"] == 60 * 26


def test_update_program_unknown_returns_none():
    assert update_program("Yoga", 70) is None


@pytest.mark.parametrize("value,kind,expected", [
    ("", float, 0.0), (None, int, 0), ("72.5", float, 72.5), ("30", int, 30), (45, int, 45),
])
def test_to_number_valid(value, kind, expected):
    assert to_number(value, "Field", kind) == expected


@pytest.mark.parametrize("value,kind,maximum,message", [
    ("abc", float, None, "must be a number"),
    ("28.5", int, None, "must be a whole number"),
    ("nan", float, None, "must be a number"),
    ("-5", float, None, "cannot be negative"),
    ("101", int, 100, "between 0 and 100"),
])
def test_to_number_invalid(value, kind, maximum, message):
    with pytest.raises(ValidationError, match=message):
        to_number(value, "Field", kind, maximum=maximum)


def test_parse_client_converts_types():
    client = parse_client(client_form(name="  Arjun  "))
    assert client == {"name": "Arjun", "age": 28, "weight": 80.0,
                      "program": "Fat Loss (FL)", "adherence": 75}


def test_save_client_message_matches_original():
    assert save_client(parse_client(client_form())) == (
        "Client Arjun saved successfully.\nAdherence: 75%")


@pytest.mark.parametrize("overrides", [{"name": ""}, {"program": ""}])
def test_save_client_requires_name_and_program(overrides):
    with pytest.raises(ValidationError, match="Please fill client name and program."):
        save_client(parse_client(client_form(**overrides)))


def test_save_client_rejects_unknown_program():
    with pytest.raises(ValidationError, match="Unknown program"):
        save_client(parse_client(client_form(program="Yoga")))


# ---------- web pages ----------

def test_index_empty_form(client):
    html = client.get("/").get_data(as_text=True)
    assert "ACEest FUNCTIONAL FITNESS SYSTEM" in html
    assert "Estimated Calories:" in html and "--" in html
    assert "Select a program to view the training plan" in html


def test_index_shows_plan_and_calories(client):
    res = client.get("/", query_string={"program": "Muscle Gain (MG)", "weight": "70"})
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "color: #2ecc71" in html
    assert f"{70 * 35} kcal" in html
    assert "Nutrition Plan (TN Context)" in html


def test_index_bad_weight_warns_and_hides_calories(client):
    html = client.get("/", query_string={"program": "Fat Loss (FL)", "weight": "abc"}
                      ).get_data(as_text=True)
    assert "Weight must be a number" in html
    assert "kcal" not in html.split("Estimated Calories:")[1].split("</p>")[0]


def test_index_unknown_program_404(client):
    res = client.get("/", query_string={"program": "Yoga"})
    assert res.status_code == 404
    assert "Unknown program: Yoga" in res.get_data(as_text=True)


def test_save_client_form_success_keeps_fields(client):
    res = client.post("/save_client", data=client_form(), follow_redirects=True)
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "Client Arjun saved successfully." in html
    assert "Adherence: 75%" in html
    assert 'value="Arjun"' in html


def test_save_client_form_missing_fields_warns(client):
    res = client.post("/save_client", data=client_form(name=""), follow_redirects=True)
    assert "Please fill client name and program." in res.get_data(as_text=True)


# ---------- JSON API ----------

def test_api_programs(client):
    assert client.get("/api/programs").get_json()["programs"] == list(programs)


def test_api_program_detail_includes_factor(client):
    body = client.get("/api/programs/Beginner (BG)").get_json()
    assert body["calorie_factor"] == 26


def test_api_program_unknown(client):
    assert client.get("/api/programs/Yoga").status_code == 404


def test_api_update_program(client):
    res = client.post("/api/update_program", json={"program": "Fat Loss (FL)", "weight": 80})
    assert res.status_code == 200
    assert res.get_json()["calories"] == 80 * 22


def test_api_update_program_bad_weight(client):
    res = client.post("/api/update_program", json={"program": "Fat Loss (FL)", "weight": "x"})
    assert res.status_code == 400


def test_api_update_program_unknown(client):
    res = client.post("/api/update_program", json={"program": "Yoga", "weight": 80})
    assert res.status_code == 404


def test_api_save_client_ok(client):
    res = client.post("/api/save_client", json={"name": "Meena", "age": 30, "weight": 62.5,
                                                "program": "Beginner (BG)", "adherence": 90})
    body = res.get_json()
    assert res.status_code == 200
    assert body["message"] == "Client Meena saved successfully.\nAdherence: 90%"
    assert body["client"]["weight"] == 62.5


def test_api_save_client_validation_error(client):
    res = client.post("/api/save_client", json={"name": "Meena"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "Please fill client name and program."


def test_api_save_client_without_json_body(client):
    res = client.post("/api/save_client", data="not json")
    assert res.status_code == 400


# ---------- errors ----------

def test_unknown_api_route_json_404(client):
    assert client.get("/api/nope").get_json() == {"error": "Not found"}


def test_unknown_page_html_404(client):
    assert "Page not found." in client.get("/nope").get_data(as_text=True)


def test_wrong_method_api_json_405(client):
    assert client.get("/api/save_client").status_code == 405


def test_wrong_method_page_html_405(client):
    res = client.get("/save_client")
    assert res.status_code == 405
    assert "Method not allowed." in res.get_data(as_text=True)
