"""ACEest Fitness & Performance - v1.1 (Flask port of Aceestver-1.1.py).

Same features as the Tkinter original:
  * Client profile form: name, age, weight, program, weekly adherence (%)
  * Selecting a program shows its weekly training plan (in the program colour),
    its nutrition plan (TN context) and the estimated calories
    (weight x program calorie factor)
  * Save Client validates the form and confirms the adherence
    (like the original, nothing is stored yet - storage arrives in v1.1.2)
  * Reset clears the form

Web pages:  GET  /                          -> client profile + plan
            POST /save_client               -> form submit (redirects back with a message)
JSON API:   GET  /api/programs              -> program names
            GET  /api/programs/<name>       -> plan details incl. calorie factor
            POST /api/update_program        -> {"program", "weight"} -> plan + calories
            POST /api/save_client           -> validate a client
Health:     GET  /health
"""
import math
import os

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

APP_VERSION = "1.1"
APP_TITLE = "ACEest Fitness & Performance"
HEADER_TEXT = "ACEest FUNCTIONAL FITNESS SYSTEM"

programs = {
    "Fat Loss (FL)": {
        "workout": (
            "Mon: Back Squat 5x5 + Core\n"
            "Tue: EMOM 20min Assault Bike\n"
            "Wed: Bench Press + 21-15-9\n"
            "Thu: Deadlift + Box Jumps\n"
            "Fri: Zone 2 Cardio 30min"
        ),
        "diet": (
            "Breakfast: Egg Whites + Oats\n"
            "Lunch: Grilled Chicken + Brown Rice\n"
            "Dinner: Fish Curry + Millet Roti\n"
            "Target: ~2000 kcal"
        ),
        "color": "#e74c3c",
        "calorie_factor": 22,
    },
    "Muscle Gain (MG)": {
        "workout": (
            "Mon: Squat 5x5\n"
            "Tue: Bench 5x5\n"
            "Wed: Deadlift 4x6\n"
            "Thu: Front Squat 4x8\n"
            "Fri: Incline Press 4x10\n"
            "Sat: Barbell Rows 4x10"
        ),
        "diet": (
            "Breakfast: Eggs + Peanut Butter Oats\n"
            "Lunch: Chicken Biryani\n"
            "Dinner: Mutton Curry + Rice\n"
            "Target: ~3200 kcal"
        ),
        "color": "#2ecc71",
        "calorie_factor": 35,
    },
    "Beginner (BG)": {
        "workout": (
            "Full Body Circuit:\n"
            "- Air Squats\n"
            "- Ring Rows\n"
            "- Push-ups\n"
            "Focus: Technique & Consistency"
        ),
        "diet": (
            "Balanced Tamil Meals\n"
            "Idli / Dosa / Rice + Dal\n"
            "Protein Target: 120g/day"
        ),
        "color": "#3498db",
        "calorie_factor": 26,
    },
}


class ValidationError(ValueError):
    """Invalid client input - shown as a warning on the page or HTTP 400 in the API."""


# ---------- LOGIC (no Flask here, so it is easy to unit test) ----------

def to_number(value, field, kind=float, maximum=None):
    """Convert form/JSON input to a non-negative number. Blank means 0 (like Tk IntVar)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return kind(0)
    try:
        number = kind(value)
    except (TypeError, ValueError):
        number = None
    if number is None or not math.isfinite(number):
        word = "a whole number" if kind is int else "a number"
        raise ValidationError(f"{field} must be {word}")
    if number < 0:
        raise ValidationError(f"{field} cannot be negative")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{field} must be between 0 and {maximum}")
    return number


def calculate_calories(weight, program):
    """Estimated daily calories = weight (kg) x the program's calorie factor."""
    return int(weight * programs[program]["calorie_factor"])


def update_program(program, weight=0.0):
    """Plan for a program plus estimated calories (None until a weight is entered)."""
    data = programs.get(program)
    if data is None:
        return None
    calories = calculate_calories(weight, program) if weight > 0 else None
    return {"name": program, "workout": data["workout"], "diet": data["diet"],
            "color": data["color"], "calories": calories}


def parse_client(data):
    """Read and type-check the client profile fields from a form or JSON body."""
    return {
        "name": (data.get("name") or "").strip(),
        "age": to_number(data.get("age"), "Age", int),
        "weight": to_number(data.get("weight"), "Weight", float),
        "program": (data.get("program") or "").strip(),
        "adherence": to_number(data.get("adherence"), "Weekly adherence", int, maximum=100),
    }


def save_client(client):
    """Validate a client like the original Save Client button and return the message."""
    if not client["name"] or not client["program"]:
        raise ValidationError("Please fill client name and program.")
    if client["program"] not in programs:
        raise ValidationError(f"Unknown program: {client['program']}")
    return (f"Client {client['name']} saved successfully.\n"
            f"Adherence: {client['adherence']}%")


# ---------- APP ----------

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

    @app.get("/health")
    def health():
        return jsonify(status="ok", app=APP_TITLE, version=APP_VERSION)

    # ---------- WEB PAGES ----------
    @app.get("/")
    def index():
        form = {key: request.args.get(key, "") for key in
                ("name", "age", "weight", "program", "adherence")}
        plan, status = None, 200
        if form["program"]:
            try:
                weight = to_number(form["weight"], "Weight")
            except ValidationError as err:
                flash(str(err), "warning")
                weight = 0.0
            plan = update_program(form["program"], weight)
            if plan is None:
                flash(f"Unknown program: {form['program']}", "error")
                form["program"], status = "", 404
        return render_template("index.html", programs=programs, form=form, plan=plan), status

    @app.post("/save_client")
    def save_client_form():
        try:
            flash(save_client(parse_client(request.form)), "success")
        except ValidationError as err:
            flash(str(err), "warning")
        # Keep what the user typed (the Tk window kept its field values too)
        keep = {key: request.form.get(key, "") for key in
                ("name", "age", "weight", "program", "adherence")}
        return redirect(url_for("index", **keep))

    # ---------- JSON API ----------
    @app.get("/api/programs")
    def api_programs():
        return jsonify(programs=list(programs.keys()))

    @app.get("/api/programs/<path:name>")
    def api_program(name):
        if name not in programs:
            return jsonify(error=f"Unknown program: {name}"), 404
        return jsonify(name=name, **programs[name])

    @app.post("/api/update_program")
    def api_update_program():
        body = request.get_json(silent=True) or {}
        try:
            weight = to_number(body.get("weight"), "Weight")
        except ValidationError as err:
            return jsonify(error=str(err)), 400
        plan = update_program(body.get("program", ""), weight)
        if plan is None:
            return jsonify(error=f"Unknown program: {body.get('program', '')}"), 404
        return jsonify(plan)

    @app.post("/api/save_client")
    def api_save_client():
        body = request.get_json(silent=True) or {}
        try:
            client = parse_client(body)
            message = save_client(client)
        except ValidationError as err:
            return jsonify(error=str(err)), 400
        return jsonify(message=message, client=client)

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
