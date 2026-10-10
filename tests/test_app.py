"""Tests for ACEest v1.0 - program viewer."""
import pytest

from app import APP_VERSION, SITE_METRICS, metrics_text, programs, update_display

PROGRAM_COLORS = {
    "Fat Loss (FL)": "#e74c3c",
    "Muscle Gain (MG)": "#2ecc71",
    "Beginner (BG)": "#3498db",
}


# ---------- health ----------

def test_health_reports_ok_and_version(client):
    res = client.get("/health")
    assert res.status_code == 200
    expected = {"status": "ok", "app": "ACEest Fitness and Gym", "version": APP_VERSION}
    assert res.get_json() == expected


# ---------- program data (logic) ----------

def test_three_programs_are_defined():
    assert set(programs) == set(PROGRAM_COLORS)


@pytest.mark.parametrize("name,color", PROGRAM_COLORS.items())
def test_update_display_returns_workout_diet_and_color(name, color):
    data = update_display(name)
    assert data["color"] == color
    assert data["workout"].strip()
    assert data["diet"].strip()


def test_update_display_unknown_program_returns_none():
    assert update_display("Yoga (YG)") is None


def test_diet_targets_match_original():
    assert "Target: 2,000 kcal" in programs["Fat Loss (FL)"]["diet"]
    assert "Target: 3,200 kcal" in programs["Muscle Gain (MG)"]["diet"]
    assert "Protein: 120g/day" in programs["Beginner (BG)"]["diet"]


# ---------- web page ----------

def test_index_shows_placeholders_before_selection(client):
    html = client.get("/").get_data(as_text=True)
    assert "ACEest FUNCTIONAL FITNESS" in html
    assert "Select a profile to view workout" in html
    assert "Select a profile to view diet" in html
    assert "CAPACITY: 150 Users" in html


@pytest.mark.parametrize("name", list(PROGRAM_COLORS))
def test_index_shows_selected_program(client, name):
    res = client.get("/", query_string={"program": name})
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert f"color: {PROGRAM_COLORS[name]}" in html
    first_workout_line = programs[name]["workout"].splitlines()[0]
    assert first_workout_line.replace("&", "&amp;") in html
    assert "Select a profile to view workout" not in html


def test_index_unknown_program_returns_404_with_message(client):
    res = client.get("/", query_string={"program": "Yoga (YG)"})
    assert res.status_code == 404
    assert "Unknown program: Yoga (YG)" in res.get_data(as_text=True)


# ---------- JSON API ----------

def test_api_lists_program_names(client):
    res = client.get("/api/programs")
    assert res.status_code == 200
    assert res.get_json()["programs"] == list(programs)


def test_api_program_detail(client):
    res = client.get("/api/programs/Muscle Gain (MG)")
    body = res.get_json()
    assert res.status_code == 200
    assert body["name"] == "Muscle Gain (MG)"
    assert body["color"] == "#2ecc71"
    assert "Sat: Barbell Rows 4x10" in body["workout"]


def test_api_unknown_program_returns_404(client):
    res = client.get("/api/programs/Unknown")
    assert res.status_code == 404
    assert "Unknown program" in res.get_json()["error"]


def test_api_metrics(client):
    body = client.get("/api/metrics").get_json()
    assert body["text"] == metrics_text
    assert body["capacity"] == SITE_METRICS["capacity"]


# ---------- error handling ----------

def test_unknown_api_route_returns_json_404(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404
    assert res.get_json() == {"error": "Not found"}


def test_unknown_page_returns_html_404(client):
    res = client.get("/does-not-exist")
    assert res.status_code == 404
    assert "Page not found." in res.get_data(as_text=True)


def test_wrong_method_on_api_returns_json_405(client):
    res = client.post("/api/programs")
    assert res.status_code == 405
    assert res.get_json() == {"error": "Method not allowed"}


def test_wrong_method_on_page_returns_html_405(client):
    res = client.post("/")
    assert res.status_code == 405
    assert "Method not allowed." in res.get_data(as_text=True)
