"""ACEest Fitness & Performance - v2.2.1 (Flask port of Aceestver-2.2.1.py).

Same features as the Tkinter original:
  * Client management form: name, age, weight, program, weekly adherence
  * Save Client: stores the client in SQLite (aceest_fitness.db) with the
    calculated daily calories (weight x program factor)
  * Load Client: shows the client profile in the Client Summary panel
  * Save Progress: logs this week's adherence ("Week NN - YYYY")
  * NEW in 2.2.1 - View Progress Chart: line chart of weekly adherence for a client

Web pages:  GET  /                                  -> client management + summary
            POST /save_client | /load_client | /save_progress | /show_progress_chart
JSON API:   GET  /api/programs                      -> programs and calorie factors
            POST /api/save_client                   -> create or replace a client
            GET  /api/load_client/<name>            -> client profile + summary text
            POST /api/save_progress                 -> log weekly adherence
            GET  /api/show_progress_chart/<name>    -> PNG adherence chart
Health:     GET  /health                            -> also checks the database
"""
import io
import math
import os
import sqlite3
from datetime import datetime

from flask import (Flask, Response, current_app, flash, g, jsonify, redirect,
                   render_template, request, url_for)
from matplotlib.figure import Figure

APP_VERSION = "2.2.1"
APP_TITLE = "ACEest Fitness & Performance"
HEADER_TEXT = "ACEest Functional Fitness System"
DB_NAME = "aceest_fitness.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    age INTEGER,
    weight REAL,
    program TEXT,
    calories INTEGER
);
CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name TEXT,
    week TEXT,
    adherence INTEGER
);
"""
FORM_FIELDS = ("name", "age", "weight", "program", "adherence")


class ValidationError(ValueError):
    """Invalid input - shown as a warning on the page or HTTP 400 in the API."""


class NotFoundError(LookupError):
    """Requested client does not exist - warning on the page or HTTP 404 in the API."""


# ---------- DATABASE ----------

def init_db(conn):
    """Create the tables if they do not exist (same schema as the original)."""
    conn.executescript(SCHEMA)
    conn.commit()


def get_db():
    """One SQLite connection per request, closed automatically afterwards."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------- DATA ----------

def setup_data():
    return {
        "Fat Loss (FL)": {"factor": 22},
        "Muscle Gain (MG)": {"factor": 35},
        "Beginner (BG)": {"factor": 26},
    }


programs = setup_data()


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


def parse_client(data):
    """Read and type-check the client fields from a form or JSON body."""
    return {
        "name": (data.get("name") or "").strip(),
        "age": to_number(data.get("age"), "Age", int),
        "weight": to_number(data.get("weight"), "Weight", float),
        "program": (data.get("program") or "").strip(),
    }


def calculate_calories(weight, program):
    return int(weight * programs[program]["factor"])


def save_client(db, client):
    """Create or replace a client row with its calculated calories."""
    if not client["name"] or not client["program"]:
        raise ValidationError("Name and Program required")
    if client["program"] not in programs:
        raise ValidationError(f"Unknown program: {client['program']}")
    calories = calculate_calories(client["weight"], client["program"])
    db.execute(
        "INSERT OR REPLACE INTO clients (name, age, weight, program, calories) "
        "VALUES (?, ?, ?, ?, ?)",
        (client["name"], client["age"], client["weight"], client["program"], calories),
    )
    db.commit()
    return "Client data saved"


def load_client(db, name):
    """Return the client as a dict, or raise NotFoundError."""
    row = db.execute(
        "SELECT name, age, weight, program, calories FROM clients WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise NotFoundError("Client not found")
    return dict(row)


def client_summary(client):
    """The CLIENT PROFILE text block shown in the Client Summary panel."""
    return (
        "CLIENT PROFILE\n"
        "--------------\n"
        f"Name      : {client['name']}\n"
        f"Age       : {client['age']}\n"
        f"Weight    : {client['weight']} kg\n"
        f"Program   : {client['program']}\n"
        f"Calories  : {client['calories']} kcal/day\n"
    )


def save_progress(db, name, adherence, now=None):
    """Log this week's adherence for a client; returns (message, week label)."""
    if not name:
        raise ValidationError("Enter client name first")
    load_client(db, name)  # raises NotFoundError for unknown clients
    week = (now or datetime.now()).strftime("Week %U - %Y")
    db.execute("INSERT INTO progress (client_name, week, adherence) VALUES (?, ?, ?)",
               (name, week, adherence))
    db.commit()
    return "Weekly progress logged", week


def show_progress_chart(db, name):
    """PNG line chart of weekly adherence (same look as the original matplotlib window)."""
    if not name:
        raise ValidationError("Enter client name first")
    data = db.execute(
        "SELECT week, adherence FROM progress WHERE client_name = ? ORDER BY id", (name,)
    ).fetchall()
    if not data:
        raise NotFoundError("No progress data available for this client")
    fig = Figure(figsize=(8, 4))
    ax = fig.subplots()
    ax.plot([row["week"] for row in data], [row["adherence"] for row in data],
            marker="o", linewidth=2)
    ax.set_title(f"Weekly Adherence Progress \u2013 {name}")
    ax.set_xlabel("Week")
    ax.set_ylabel("Adherence (%)")
    ax.set_ylim(0, 100)
    ax.grid(True)
    ax.tick_params(axis="x", labelrotation=45)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


# ---------- APP ----------

def create_app(test_config=None):
    """Application factory: builds a fresh app (tests pass their own config)."""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("ACEEST_SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("ACEEST_DB", DB_NAME),
    )
    if test_config:
        app.config.update(test_config)

    db_dir = os.path.dirname(os.path.abspath(app.config["DATABASE"]))
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(app.config["DATABASE"])
    init_db(conn)
    conn.close()
    app.teardown_appcontext(close_db)

    @app.context_processor
    def inject_globals():
        return {"app_title": APP_TITLE, "header_text": HEADER_TEXT, "app_version": APP_VERSION}

    @app.get("/health")
    def health():
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok", app=APP_TITLE, version=APP_VERSION)

    # ---------- WEB PAGES ----------
    @app.get("/")
    def index():
        form = {key: request.args.get(key, "") for key in FORM_FIELDS}
        summary, show_chart = None, False
        name = request.args.get("client", "")
        if name:
            try:
                client = load_client(get_db(), name)
            except NotFoundError as err:
                flash(str(err), "warning")
            else:
                form.update({k: client[k] for k in ("name", "age", "weight", "program")})
                summary = client_summary(client)
                show_chart = request.args.get("chart") == "progress"
        return render_template("index.html", programs=programs, form=form, summary=summary,
                               show_chart=show_chart)

    def back_to_form(**extra):
        keep = {key: request.form.get(key, "") for key in FORM_FIELDS}
        keep.update(extra)
        return redirect(url_for("index", **keep))

    @app.post("/save_client")
    def save_client_form():
        try:
            client = parse_client(request.form)
            flash(save_client(get_db(), client), "success")
        except ValidationError as err:
            flash(str(err), "error")
            return back_to_form()
        return redirect(url_for("index", client=client["name"]))

    @app.post("/load_client")
    def load_client_form():
        name = request.form.get("name", "").strip()
        try:
            load_client(get_db(), name)
        except NotFoundError as err:
            flash(str(err), "warning")
            return back_to_form()
        return redirect(url_for("index", client=name))

    @app.post("/save_progress")
    def save_progress_form():
        name = request.form.get("name", "").strip()
        try:
            adherence = to_number(request.form.get("adherence"), "Weekly adherence", int, 100)
            message, _week = save_progress(get_db(), name, adherence)
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return back_to_form()
        flash(message, "success")
        return redirect(url_for("index", client=name))

    @app.post("/show_progress_chart")
    def show_progress_chart_form():
        name = request.form.get("name", "").strip()
        try:
            show_progress_chart(get_db(), name)
        except ValidationError as err:
            flash(str(err), "warning")
            return back_to_form()
        except NotFoundError as err:
            flash(str(err), "info")
            return redirect(url_for("index", client=name))
        return redirect(url_for("index", client=name, chart="progress"))

    # ---------- JSON API ----------
    @app.get("/api/programs")
    def api_programs():
        return jsonify(programs=programs)

    @app.post("/api/save_client")
    def api_save_client():
        body = request.get_json(silent=True) or {}
        try:
            client = parse_client(body)
            message = save_client(get_db(), client)
        except ValidationError as err:
            return jsonify(error=str(err)), 400
        return jsonify(message=message, client=load_client(get_db(), client["name"])), 201

    @app.get("/api/load_client/<path:name>")
    def api_load_client(name):
        try:
            client = load_client(get_db(), name)
        except NotFoundError as err:
            return jsonify(error=str(err)), 404
        return jsonify(client=client, summary=client_summary(client))

    @app.post("/api/save_progress")
    def api_save_progress():
        body = request.get_json(silent=True) or {}
        try:
            adherence = to_number(body.get("adherence"), "Weekly adherence", int, 100)
            message, week = save_progress(get_db(), (body.get("name") or "").strip(), adherence)
        except ValidationError as err:
            return jsonify(error=str(err)), 400
        except NotFoundError as err:
            return jsonify(error=str(err)), 404
        return jsonify(message=message, week=week, adherence=adherence), 201

    @app.get("/api/show_progress_chart/<path:name>")
    def api_show_progress_chart(name):
        try:
            png = show_progress_chart(get_db(), name)
        except NotFoundError as err:
            return jsonify(error=str(err)), 404
        return Response(png, mimetype="image/png")

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
