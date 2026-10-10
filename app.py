"""ACEest Fitness & Performance - v1.1.2 (Flask port of Aceestver1.1.2.py).

Same features as the Tkinter original:
  * Client profile form: name, age, weight, program, weekly adherence, coach notes
  * Selecting a program shows the training plan, nutrition plan and estimated calories
  * Save Client stores the client in an in-memory list and adds it to the Client List
  * Progress Chart: bar chart of adherence per client
  * Export CSV: download all clients as a CSV file
  * Reset clears the form
  (The original's Reset button crashed and the file had no __main__ block;
   both are fixed here.)

Web pages:  GET  /                       -> client profile, plan, client list, chart
            POST /save_client            -> form submit
            GET  /export_csv             -> CSV download (or a warning if empty)
JSON API:   GET  /api/programs[/<name>]  -> program names / details
            POST /api/update_program     -> plan + calories
            GET  /api/clients            -> saved clients
            POST /api/save_client        -> add a client
            GET  /api/export_csv         -> CSV
            GET  /api/update_chart       -> PNG bar chart of adherence
Health:     GET  /health

Note: clients live in memory (as in the original), so they reset when the
app restarts. Persistent storage arrives in v2.0.1 (SQLite).
"""
import csv
import io
import math
import os

from flask import (Flask, Response, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from matplotlib.figure import Figure

APP_VERSION = "1.1.2"
APP_TITLE = "ACEest Fitness & Performance"
HEADER_TEXT = "ACEest FUNCTIONAL FITNESS SYSTEM v2"
CSV_HEADER = ["Name", "Age", "Weight", "Program", "Adherence", "Notes"]

programs = {
    "Fat Loss (FL)": {"workout": "Back Squat, Cardio, Bench, Deadlift, Recovery",
                      "diet": "Egg Whites, Chicken, Fish Curry",
                      "color": "#e74c3c", "calorie_factor": 22},
    "Muscle Gain (MG)": {"workout": "Squat, Bench, Deadlift, Press, Rows",
                         "diet": "Eggs, Biryani, Mutton Curry",
                         "color": "#2ecc71", "calorie_factor": 35},
    "Beginner (BG)": {"workout": "Air Squats, Ring Rows, Push-ups",
                      "diet": "Balanced Tamil Meals",
                      "color": "#3498db", "calorie_factor": 26},
}


class ValidationError(ValueError):
    """Invalid client input - shown as a warning on the page or HTTP 400 in the API."""


# ---------- LOGIC ----------

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
    """Read and type-check the client fields from a form or JSON body."""
    return {
        "name": (data.get("name") or "").strip(),
        "age": to_number(data.get("age"), "Age", int),
        "weight": to_number(data.get("weight"), "Weight", float),
        "program": (data.get("program") or "").strip(),
        "adherence": to_number(data.get("adherence"), "Weekly adherence", int, maximum=100),
        "notes": (data.get("notes") or "").strip(),
    }


def get_clients():
    """The in-memory client list of the running app (self.clients in the original)."""
    return current_app.extensions["aceest_clients"]


def save_client(client):
    """Validate, store and confirm a client (same messages as the original)."""
    if not client["name"] or not client["program"]:
        raise ValidationError("Please fill client name and program.")
    if client["program"] not in programs:
        raise ValidationError(f"Unknown program: {client['program']}")
    get_clients().append(client)
    return f"Client {client['name']} saved successfully."


def export_csv(clients):
    """CSV text with the same header row as the original export."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for c in clients:
        writer.writerow([c["name"], c["age"], c["weight"], c["program"],
                         c["adherence"], c["notes"]])
    return buffer.getvalue()


def update_chart(clients):
    """PNG bar chart of adherence per client - the original's 'Client Progress' chart."""
    fig = Figure(figsize=(6, 3))
    ax = fig.subplots()
    ax.bar([c["name"] for c in clients], [c["adherence"] for c in clients], color="#d4af37")
    ax.set_ylabel("Adherence %")
    ax.set_title("Client Progress")
    ax.set_ylim(0, 100)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


def csv_response(text):
    return Response(text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=clients.csv"})


# ---------- APP ----------

def create_app(test_config=None):
    """Application factory: builds a fresh app (tests pass their own config)."""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("ACEEST_SECRET_KEY", "dev-only-change-me"),
    )
    if test_config:
        app.config.update(test_config)
    app.extensions["aceest_clients"] = []  # store multiple clients (in memory)

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
                ("name", "age", "weight", "program", "adherence", "notes")}
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
        return render_template("index.html", programs=programs, form=form, plan=plan,
                               clients=get_clients()), status

    @app.post("/save_client")
    def save_client_form():
        try:
            flash(save_client(parse_client(request.form)), "success")
        except ValidationError as err:
            flash(str(err), "warning")
        keep = {key: request.form.get(key, "") for key in
                ("name", "age", "weight", "program", "adherence", "notes")}
        return redirect(url_for("index", **keep))

    @app.get("/export_csv")
    def export_csv_download():
        if not get_clients():
            flash("No clients to export.", "warning")
            return redirect(url_for("index"))
        return csv_response(export_csv(get_clients()))

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

    @app.get("/api/clients")
    def api_clients():
        return jsonify(clients=get_clients())

    @app.post("/api/save_client")
    def api_save_client():
        body = request.get_json(silent=True) or {}
        try:
            client = parse_client(body)
            message = save_client(client)
        except ValidationError as err:
            return jsonify(error=str(err)), 400
        return jsonify(message=message, client=client), 201

    @app.get("/api/export_csv")
    def api_export_csv():
        if not get_clients():
            return jsonify(error="No clients to export."), 404
        return csv_response(export_csv(get_clients()))

    @app.get("/api/update_chart")
    def api_update_chart():
        return Response(update_chart(get_clients()), mimetype="image/png")

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
