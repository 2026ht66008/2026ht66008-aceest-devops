"""ACEest Fitness & Performance - v3.1.2 (Flask port of Aceestver-3.1.2.py).

Same features as the Tkinter original:
  * Login screen backed by a users table (default user admin / admin, role Admin)
  * Dashboard titled with the user's role
  * Client management: name, age, height, weight, program, membership expiry
  * Save Client / Load Client and the Client Summary
  * Generate AI Program: a weekly plan built from the client's goal and experience
    level (beginner / intermediate / advanced)
  * Export PDF Report for the selected client

Changes from the original (behaviour fixes, same features):
  * Passwords are stored hashed (werkzeug), not as plain text; the default admin
    password can be overridden with ACEEST_ADMIN_PASSWORD
  * Saving a client no longer wipes its target weight / target adherence
    (the original's INSERT OR REPLACE did); older databases are migrated in place

Web pages:  GET/POST /login, GET /logout
            GET  /                          -> dashboard (?client=<name>&tab=summary|analytics)
            POST /save_client | /load_client | /generate_ai_program
            GET  /export_pdf_report?client=<name>
JSON API:   POST /api/login | /api/logout
            GET  /api/programs | /api/clients | /api/load_client/<name>
            POST /api/save_client | /api/generate_ai_program
            GET  /api/export_pdf_report/<name>   (PDF)
Health:     GET  /health  (no login needed)
"""
import functools
import math
import os
import random
import sqlite3
from datetime import date

from flask import (Flask, Response, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from fpdf import FPDF
from werkzeug.security import check_password_hash, generate_password_hash

APP_VERSION = "3.1.2"
APP_TITLE = "ACEest Fitness & Performance"
HEADER_TEXT = "ACEest Fitness Dashboard"
DB_NAME = "aceest_fitness.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password TEXT,
    role TEXT
);
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    age INTEGER,
    height REAL,
    weight REAL,
    program TEXT,
    calories INTEGER,
    target_weight REAL,
    target_adherence INTEGER,
    membership_expiry TEXT
);
CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name TEXT,
    week TEXT,
    adherence INTEGER
);
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name TEXT,
    date TEXT,
    workout_type TEXT,
    duration_min INTEGER,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER,
    name TEXT,
    sets INTEGER,
    reps INTEGER,
    weight REAL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name TEXT,
    date TEXT,
    weight REAL,
    waist REAL,
    bodyfat REAL
);
"""
CLIENT_COLUMNS = {"age": "INTEGER", "height": "REAL", "weight": "REAL", "program": "TEXT",
                  "calories": "INTEGER", "target_weight": "REAL", "target_adherence": "INTEGER",
                  "membership_expiry": "TEXT"}
CLIENT_FIELDS = ("name", "age", "height", "weight", "program", "calories",
                 "target_weight", "target_adherence", "membership_expiry")
FORM_FIELDS = ("name", "age", "height", "weight", "program", "membership_expiry", "experience")
EXPERIENCE_LEVELS = ["beginner", "intermediate", "advanced"]

exercises_pool = {
    "Strength": ["Squat", "Deadlift", "Bench Press", "Overhead Press", "Pull-Up", "Barbell Row"],
    "Hypertrophy": ["Leg Press", "Incline Dumbbell Press", "Lat Pulldown", "Lateral Raise",
                    "Bicep Curl", "Tricep Extension"],
    "Conditioning": ["Running", "Cycling", "Rowing", "Burpees", "Jump Rope", "Kettlebell Swings"],
    "Full Body": ["Push-Up", "Pull-Up", "Lunge", "Plank", "Dumbbell Row", "Dumbbell Press"],
}


class ValidationError(ValueError):
    """Invalid input - shown as a warning on the page or HTTP 400 in the API."""


class NotFoundError(LookupError):
    """Missing client - warning on the page or HTTP 404 in the API."""


# ---------- DATABASE ----------

def init_db(conn, admin_password="admin"):
    """Create tables, migrate older client tables and add the default admin user."""
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(clients)")}
    for column, column_type in CLIENT_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {column} {column_type}")
    conn.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
                 ("admin", generate_password_hash(admin_password), "Admin"))
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
        "Fat Loss (FL) – 3 day": {"factor": 22, "desc": "3-day full-body fat loss"},
        "Fat Loss (FL) – 5 day": {"factor": 24, "desc": "5-day split, higher volume fat loss"},
        "Muscle Gain (MG) – PPL": {"factor": 35, "desc": "Push/Pull/Legs hypertrophy"},
        "Beginner (BG)": {"factor": 26, "desc": "3-day simple beginner full-body"},
    }


programs = setup_data()


# ---------- INPUT HELPERS ----------

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


def parse_date(value, field="Date"):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be in YYYY-MM-DD format") from None


# ---------- LOGIN ----------

def login_user(db, username, password):
    """Return the user's role for valid credentials, otherwise None."""
    row = db.execute("SELECT password, role FROM users WHERE username = ?",
                     (str(username or "").strip(),)).fetchone()
    if row and check_password_hash(row["password"], str(password or "").strip()):
        return row["role"]
    return None


# ---------- CLIENTS ----------

def refresh_client_list(db):
    return [row["name"] for row in db.execute("SELECT name FROM clients ORDER BY name")]


def parse_client(data):
    return {
        "name": (data.get("name") or "").strip(),
        "age": to_number(data.get("age"), "Age", int),
        "height": to_number(data.get("height"), "Height"),
        "weight": to_number(data.get("weight"), "Weight"),
        "program": (data.get("program") or "").strip(),
        "membership_expiry": parse_date(data.get("membership_expiry"), "Membership expiry"),
    }


def save_client(db, client):
    """Create or update a client (calorie factor 25 when no program is chosen)."""
    if not client["name"]:
        raise ValidationError("Name required")
    if client["program"] and client["program"] not in programs:
        raise ValidationError(f"Unknown program: {client['program']}")
    factor = programs.get(client["program"], {}).get("factor", 25)
    calories = int(client["weight"] * factor) if client["weight"] > 0 else None
    db.execute(
        "INSERT INTO clients (name, age, height, weight, program, calories, membership_expiry) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET age = excluded.age, height = excluded.height, "
        "weight = excluded.weight, program = excluded.program, calories = excluded.calories, "
        "membership_expiry = excluded.membership_expiry",
        (client["name"], client["age"], client["height"], client["weight"], client["program"],
         calories, client["membership_expiry"]),
    )
    db.commit()
    return "Client data saved"


def load_client(db, name):
    row = db.execute(f"SELECT {', '.join(CLIENT_FIELDS)} FROM clients WHERE name = ?",
                     (name,)).fetchone()
    if row is None:
        raise NotFoundError("Client not found")
    return dict(row)


def refresh_summary(client):
    return (f"Name: {client['name']}\n"
            f"Age: {client['age']}\n"
            f"Height: {client['height']} cm\n"
            f"Weight: {client['weight']} kg\n"
            f"Program: {client['program']}\n"
            f"Membership Expiry: {client['membership_expiry']}")


# ---------- AI PROGRAM GENERATOR ----------

def generate_ai_program(db, name, exp_level, rng=random):
    """Weekly plan: focus from the client's goal, volume from the experience level."""
    if not name:
        raise ValidationError("Select client first")
    exp_level = (exp_level or "").strip().lower()
    if exp_level not in EXPERIENCE_LEVELS:
        raise ValidationError("Invalid experience level")
    program_name = load_client(db, name)["program"] or ""

    focus = "Full Body"
    if "Fat Loss" in program_name:
        focus = "Conditioning"
    elif "Muscle Gain" in program_name:
        focus = "Hypertrophy"

    if exp_level == "beginner":
        sets_range, reps_range, days = (2, 3), (8, 12), 3
    elif exp_level == "intermediate":
        sets_range, reps_range, days = (3, 4), (8, 15), 4
    else:
        sets_range, reps_range, days = (4, 5), (6, 15), 5

    weekly_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][:days]
    rows = []
    for day in weekly_days:
        for exercise in rng.sample(exercises_pool[focus], k=3 if days < 4 else 4):
            rows.append({"day": day, "exercise": exercise,
                         "sets": rng.randint(*sets_range), "reps": rng.randint(*reps_range)})
    return {"client": name, "experience": exp_level, "focus": focus, "rows": rows}


# ---------- PDF REPORT ----------

def pdf_safe(text):
    """Core PDF fonts only cover Latin-1: swap dashes, replace anything else with '?'."""
    text = str(text).replace("–", "-").replace("—", "-")
    return text.encode("latin-1", "replace").decode("latin-1")


def export_pdf_report(db, name):
    client = load_client(db, name)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, pdf_safe(f"Client Report - {client['name']}"), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.ln(10)
    for label, value in (("Name", client["name"]), ("Age", client["age"]),
                         ("Height", f"{client['height']} cm"),
                         ("Weight", f"{client['weight']} kg"),
                         ("Program", client["program"]),
                         ("Membership Expiry", client["membership_expiry"])):
        pdf.cell(0, 10, pdf_safe(f"{label}: {value}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def pdf_response(name, data):
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{pdf_safe(name)}_report.pdf"'})


# ---------- APP ----------

def create_app(test_config=None):
    """Application factory: builds a fresh app (tests pass their own config)."""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("ACEEST_SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("ACEEST_DB", DB_NAME),
        ADMIN_PASSWORD=os.environ.get("ACEEST_ADMIN_PASSWORD", "admin"),
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    conn = sqlite3.connect(app.config["DATABASE"])
    init_db(conn, app.config["ADMIN_PASSWORD"])
    conn.close()
    app.teardown_appcontext(close_db)

    @app.context_processor
    def inject_globals():
        return {"app_title": APP_TITLE, "header_text": HEADER_TEXT, "app_version": APP_VERSION,
                "user": session.get("user"), "role": session.get("role")}

    def login_required(view):
        """Pages redirect to the login screen; API calls get HTTP 401."""
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user"):
                if request.path.startswith("/api/"):
                    return jsonify(error="Login required"), 401
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    @app.get("/health")
    def health():
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok", app=APP_TITLE, version=APP_VERSION)

    # ---------- WEB: LOGIN ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            role = login_user(get_db(), username, request.form.get("password", ""))
            if role:
                session.clear()
                session["user"], session["role"] = username.strip(), role
                return redirect(url_for("index"))
            flash("Invalid credentials\nTry admin / admin", "error")
            return render_template("login.html", username=username), 401
        return render_template("login.html", username="")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- WEB: DASHBOARD ----------
    @app.get("/")
    @login_required
    def index():
        db = get_db()
        form = {key: request.args.get(key, "") for key in FORM_FIELDS}
        name = request.args.get("client", "").strip()
        tab = request.args.get("tab", "summary")
        summary = ai_program = None
        if name:
            try:
                client = load_client(db, name)
            except NotFoundError as err:
                flash(str(err), "warning")
                name = ""
            else:
                for key in ("name", "age", "height", "weight", "program", "membership_expiry"):
                    form[key] = "" if client[key] is None else client[key]
                summary = refresh_summary(client)
                stored = session.get("ai_program")
                if stored and stored.get("client") == name:
                    ai_program = stored
        return render_template("index.html", programs=programs, clients=refresh_client_list(db),
                               form=form, current=name, summary=summary, tab=tab,
                               ai_program=ai_program, experience_levels=EXPERIENCE_LEVELS)

    def back_to_form(**extra):
        keep = {key: request.form.get(key, "") for key in FORM_FIELDS}
        keep.update(extra)
        return redirect(url_for("index", **keep))

    @app.post("/save_client")
    @login_required
    def save_client_form():
        try:
            client = parse_client(request.form)
            flash(save_client(get_db(), client), "success")
        except ValidationError as err:
            flash(str(err), "error")
            return back_to_form()
        flash(f"Saved client: {client['name']}", "status")
        return redirect(url_for("index", client=client["name"]))

    @app.post("/load_client")
    @login_required
    def load_client_form():
        name = request.form.get("name", "").strip()
        try:
            load_client(get_db(), name)
        except NotFoundError as err:
            flash(str(err), "warning")
            return back_to_form()
        return redirect(url_for("index", client=name))

    @app.post("/generate_ai_program")
    @login_required
    def generate_ai_program_form():
        name = request.form.get("name", "").strip()
        try:
            plan = generate_ai_program(get_db(), name, request.form.get("experience"))
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "error")
            return back_to_form()
        session["ai_program"] = plan
        flash("AI workout program generated!", "success")
        flash(f"AI program generated for {name}", "status")
        return redirect(url_for("index", client=name, tab="analytics",
                                experience=plan["experience"]))

    @app.get("/export_pdf_report")
    @login_required
    def export_pdf_report_download():
        name = request.args.get("client", "").strip()
        try:
            data = export_pdf_report(get_db(), name)
        except NotFoundError:
            flash("Select client first", "warning")
            return redirect(url_for("index"))
        return pdf_response(name, data)

    # ---------- JSON API ----------
    def api_error(err):
        return jsonify(error=str(err)), 404 if isinstance(err, NotFoundError) else 400

    def body():
        return request.get_json(silent=True) or {}

    @app.post("/api/login")
    def api_login():
        data = body()
        role = login_user(get_db(), data.get("username"), data.get("password"))
        if not role:
            return jsonify(error="Invalid credentials"), 401
        session.clear()
        session["user"], session["role"] = str(data["username"]).strip(), role
        return jsonify(user=session["user"], role=role)

    @app.post("/api/logout")
    def api_logout():
        session.clear()
        return jsonify(message="Logged out")

    @app.get("/api/programs")
    @login_required
    def api_programs():
        return jsonify(programs=programs)

    @app.get("/api/clients")
    @login_required
    def api_clients():
        return jsonify(clients=refresh_client_list(get_db()))

    @app.post("/api/save_client")
    @login_required
    def api_save_client():
        try:
            client = parse_client(body())
            message = save_client(get_db(), client)
        except ValidationError as err:
            return api_error(err)
        return jsonify(message=message, client=load_client(get_db(), client["name"])), 201

    @app.get("/api/load_client/<path:name>")
    @login_required
    def api_load_client(name):
        try:
            client = load_client(get_db(), name)
        except NotFoundError as err:
            return api_error(err)
        return jsonify(client=client, summary=refresh_summary(client))

    @app.post("/api/generate_ai_program")
    @login_required
    def api_generate_ai_program():
        data = body()
        seed = data.get("seed")
        if seed is not None and not isinstance(seed, (int, str)):
            return jsonify(error="seed must be a number or text"), 400
        rng = random if seed is None else random.Random(seed)
        try:
            plan = generate_ai_program(get_db(), (data.get("name") or "").strip(),
                                       data.get("experience"), rng)
        except (ValidationError, NotFoundError) as err:
            return api_error(err)
        return jsonify(plan)

    @app.get("/api/export_pdf_report/<path:name>")
    @login_required
    def api_export_pdf_report(name):
        try:
            return pdf_response(name, export_pdf_report(get_db(), name))
        except NotFoundError as err:
            return api_error(err)

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
