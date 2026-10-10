"""ACEest Fitness and Gym - v1.0 (Flask port of Aceestver-1.0.py).

Same features as the Tkinter original:
  * Select a program: Fat Loss (FL), Muscle Gain (MG) or Beginner (BG)
  * Show its weekly workout chart (in the program's colour) and its
    daily nutrition plan (Tamil Nadu context)
  * Show the site metrics summary (capacity, area, break-even)

Web pages:  GET /                      -> program viewer (the Tkinter window)
JSON API:   GET /api/programs          -> program names
            GET /api/programs/<name>   -> workout, diet and colour
            GET /api/metrics           -> site metrics
Health:     GET /health                -> checked by Jenkins after each deployment
"""
import os

from flask import Flask, flash, jsonify, render_template, request

APP_VERSION = "1.0"
APP_TITLE = "ACEest Fitness and Gym"
HEADER_TEXT = "ACEest FUNCTIONAL FITNESS"

# Data store for program specification (same content as the original)
programs = {
    "Fat Loss (FL)": {
        "workout": "Mon: 5x5 Back Squat + AMRAP\nTue: EMOM 20min Assault Bike\n"
                   "Wed: Bench Press + 21-15-9\nThu: 10RFT Deadlifts/Box Jumps\n"
                   "Fri: 30min Active Recovery",
        "diet": "B: 3 Egg Whites + Oats Idli\nL: Grilled Chicken + Brown Rice\n"
                "D: Fish Curry + Millet Roti\nTarget: 2,000 kcal",
        "color": "#e74c3c",
    },
    "Muscle Gain (MG)": {
        "workout": "Mon: Squat 5x5\nTue: Bench 5x5\nWed: Deadlift 4x6\n"
                   "Thu: Front Squat 4x8\nFri: Incline Press 4x10\nSat: Barbell Rows 4x10",
        "diet": "B: 4 Eggs + PB Oats\nL: Chicken Biryani (250g Chicken)\n"
                "D: Mutton Curry + Jeera Rice\nTarget: 3,200 kcal",
        "color": "#2ecc71",
    },
    "Beginner (BG)": {
        "workout": "Circuit Training: Air Squats, Ring Rows, Push-ups.\n"
                   "Focus: Technique Mastery & Form (90% Threshold)",
        "diet": "Balanced Tamil Meals: Idli-Sambar, Rice-Dal, Chapati.\nProtein: 120g/day",
        "color": "#3498db",
    },
}

# Site metrics summary (reference panel in the original)
SITE_METRICS = {"capacity": "150 Users", "area": "10,000 sq ft", "break_even": "250 Members"}
metrics_text = "CAPACITY: 150 Users\nAREA: 10,000 sq ft\nBREAK-EVEN: 250 Members"


def update_display(selected):
    """Return the program data for the selected program, or None if it is unknown."""
    return programs.get(selected)


def create_app(test_config=None):
    """Application factory: builds a fresh app (tests pass their own config)."""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("ACEEST_SECRET_KEY", "dev-only-change-me"),
    )
    if test_config:
        app.config.update(test_config)

    @app.context_processor
    def inject_globals():
        return {"app_title": APP_TITLE, "header_text": HEADER_TEXT, "app_version": APP_VERSION}

    # ---------- HEALTH ----------
    @app.get("/health")
    def health():
        return jsonify(status="ok", app=APP_TITLE, version=APP_VERSION)

    # ---------- WEB PAGE ----------
    @app.get("/")
    def index():
        selected = request.args.get("program", "")
        data = None
        status = 200
        if selected:
            data = update_display(selected)
            if data is None:
                flash(f"Unknown program: {selected}", "error")
                selected, status = "", 404
        return render_template(
            "index.html", programs=programs, selected=selected,
            data=data, metrics_text=metrics_text,
        ), status

    # ---------- JSON API ----------
    @app.get("/api/programs")
    def api_programs():
        return jsonify(programs=list(programs.keys()))

    @app.get("/api/programs/<path:name>")
    def api_program(name):
        data = update_display(name)
        if data is None:
            return jsonify(error=f"Unknown program: {name}"), 404
        return jsonify(name=name, **data)

    @app.get("/api/metrics")
    def api_metrics():
        return jsonify(text=metrics_text, **SITE_METRICS)

    # ---------- ERRORS ----------
    def wants_json():
        return request.path.startswith("/api/") or request.path == "/health"

    @app.errorhandler(404)
    def not_found(_err):
        if wants_json():
            return jsonify(error="Not found"), 404
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(405)
    def method_not_allowed(_err):
        if wants_json():
            return jsonify(error="Method not allowed"), 405
        return render_template("error.html", code=405, message="Method not allowed."), 405

    return app
