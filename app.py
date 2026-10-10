"""ACEest Fitness & Performance - v3.2.4 (Flask port of Aceestver-3.2.4.py).

Same features as the Tkinter original:
  * "ACEest Login" screen (users table, default admin / admin with role Admin)
  * Dashboard titled with the user's role
  * Select a client to see the summary, the weekly adherence chart and workouts
  * Add / Save Client (name only, membership status "Active")
  * Generate AI Program: picks a program template and saves it on the client
  * Generate PDF Report with every client field
  * Check Membership: status and renewal date
  * Workouts & Exercises tab: workout list and Add Workout

Changes from the original (behaviour fixes, same features):
  * "Add / Save Client" works (the original crashed: tkinter.simpledialog was
    never imported)
  * Passwords are stored hashed; ACEEST_ADMIN_PASSWORD overrides the default
  * Older databases are migrated in place (missing columns added; a v3.1.2
    membership_expiry is carried over to membership_end)
  * Add Workout validates the date and workout type

Web pages:  GET/POST /login, GET /logout
            GET  /                        -> dashboard (?client=<name>&tab=summary|workouts)
            POST /add_save_client | /generate_program | /check_membership
            GET  /generate_pdf?client=<name>,  GET/POST /add_workout?client=<name>
JSON API:   POST /api/login | /api/logout | /api/add_save_client | /api/generate_program
                 /api/add_workout
            GET  /api/clients | /api/load_client/<name> | /api/check_membership/<name>
                 /api/refresh_workouts/<name> | /api/plot_charts/<name> (PNG)
                 /api/generate_pdf/<name> (PDF)
Health:     GET  /health  (no login needed)
"""
import functools
import io
import os
import random
import sqlite3
from datetime import date

from flask import (Flask, Response, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from fpdf import FPDF
from matplotlib.figure import Figure
from werkzeug.security import check_password_hash, generate_password_hash

APP_VERSION = "3.2.4"
APP_TITLE = "ACEest Fitness & Performance"
HEADER_TEXT = "ACEest Dashboard"
DB_NAME = "aceest_fitness.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
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
    membership_status TEXT,
    membership_end TEXT
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
                  "membership_status": "TEXT", "membership_end": "TEXT"}
# Same column order as the original's SELECT * (used by the PDF report)
CLIENT_FIELDS = ("id", "name", "age", "height", "weight", "program", "calories",
                 "target_weight", "target_adherence", "membership_status", "membership_end")
PDF_LABELS = ["ID", "Name", "Age", "Height", "Weight", "Program", "Calories", "Target Weight",
              "Target Adherence", "Membership", "End"]
WORKOUT_TYPES = ["Strength", "Hypertrophy", "Cardio", "Mobility"]

# Programs for AI-style generation
program_templates = {
    "Fat Loss": ["Full Body HIIT", "Circuit Training", "Cardio + Weights"],
    "Muscle Gain": ["Push/Pull/Legs", "Upper/Lower Split", "Full Body Strength"],
    "Beginner": ["Full Body 3x/week", "Light Strength + Mobility"],
}


class ValidationError(ValueError):
    """Invalid input - shown as a warning on the page or HTTP 400 in the API."""


class NotFoundError(LookupError):
    """Missing client - warning on the page or HTTP 404 in the API."""


# ---------- DATABASE INITIALIZATION ----------

def init_db(db_name=DB_NAME, admin_password="admin"):
    """Create tables, migrate older databases and add the default admin user."""
    conn = sqlite3.connect(db_name)
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(clients)")}
    for column, column_type in CLIENT_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {column} {column_type}")
    if "membership_end" not in existing and "membership_expiry" in existing:
        conn.execute("UPDATE clients SET membership_end = membership_expiry "
                     "WHERE membership_end IS NULL")
    conn.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
                 ("admin", generate_password_hash(admin_password), "Admin"))
    conn.commit()
    conn.close()


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


# ---------- LOGIN ----------

def login(db, username, password):
    """Return the user's role for valid credentials, otherwise None."""
    row = db.execute("SELECT password, role FROM users WHERE username = ?",
                     (str(username or "").strip(),)).fetchone()
    if row and check_password_hash(row["password"], str(password or "").strip()):
        return row["role"]
    return None


# ---------- CLIENT MANAGEMENT ----------

def refresh_client_list(db):
    return [row["name"] for row in db.execute("SELECT name FROM clients ORDER BY name")]


def add_save_client(db, name):
    """Add a client with membership status Active (an existing client is left as it is)."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("Enter client name")
    cur = db.execute("INSERT OR IGNORE INTO clients (name, membership_status) VALUES (?, ?)",
                     (name, "Active"))
    db.commit()
    return f"Client {name} saved", cur.rowcount == 1


def load_client(db, name):
    row = db.execute(f"SELECT {', '.join(CLIENT_FIELDS)} FROM clients WHERE name = ?",
                     (name,)).fetchone()
    if row is None:
        raise NotFoundError("Client not found")
    return dict(row)


def ensure_client(db, name):
    if not name:
        raise ValidationError("Select a client first")
    return load_client(db, name)


# ---------- AI-STYLE PROGRAM GENERATOR ----------

def generate_program(db, name, rng=random):
    """Pick a random program template and save it as the client's program."""
    ensure_client(db, name)
    program_type = rng.choice(list(program_templates.keys()))
    program_detail = rng.choice(program_templates[program_type])
    db.execute("UPDATE clients SET program = ? WHERE name = ?", (program_detail, name))
    db.commit()
    return f"Program for {name}: {program_detail}", program_type, program_detail


# ---------- PDF REPORT ----------

def pdf_safe(text):
    """Core PDF fonts only cover Latin-1: swap dashes, replace anything else with '?'."""
    text = str(text).replace("–", "-").replace("—", "-")
    return text.encode("latin-1", "replace").decode("latin-1")


def generate_pdf(db, name):
    client = ensure_client(db, name)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, pdf_safe(f"ACEest Client Report - {name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    for label, field in zip(PDF_LABELS, CLIENT_FIELDS):
        pdf.cell(0, 10, pdf_safe(f"{label}: {client[field]}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def pdf_response(name, data):
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{pdf_safe(name)}_report.pdf"'})


# ---------- MEMBERSHIP ----------

def check_membership(db, name):
    client = ensure_client(db, name)
    status, end = client["membership_status"], client["membership_end"]
    return {"status": status, "end": end,
            "message": f"Membership: {status}\nRenewal Date: {end if end else 'N/A'}"}


# ---------- SUMMARY & CHARTS ----------

def refresh_summary(client):
    return (f"Name: {client['name']}\nProgram: {client['program']}\n"
            f"Calories: {client['calories']}\nMembership: {client['membership_status']}")


def plot_charts(db, name):
    """PNG weekly adherence chart, or None when the client has no progress rows."""
    data = db.execute("SELECT week, adherence FROM progress WHERE client_name = ? ORDER BY id",
                      (name,)).fetchall()
    if not data:
        return None
    fig = Figure(figsize=(6, 3))
    ax = fig.subplots()
    ax.plot([d["week"] for d in data], [d["adherence"] for d in data], marker="o")
    ax.set_title("Weekly Adherence")
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.grid(True)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


# ---------- WORKOUTS ----------

def refresh_workouts(db, name):
    return [dict(r) for r in db.execute(
        "SELECT date, workout_type, duration_min, notes FROM workouts WHERE client_name = ? "
        "ORDER BY date DESC, id DESC", (name,))]


def add_workout(db, name, data):
    ensure_client(db, name)
    w_date = (data.get("date") or "").strip()
    try:
        w_date = date.fromisoformat(w_date).isoformat()
    except ValueError:
        raise ValidationError("Date must be in YYYY-MM-DD format") from None
    w_type = (data.get("workout_type") or "").strip()
    if w_type not in WORKOUT_TYPES:
        raise ValidationError("Choose a workout type: " + ", ".join(WORKOUT_TYPES))
    try:
        duration = int(data.get("duration_min") or 0)
    except (TypeError, ValueError):
        raise ValidationError("Duration must be a whole number") from None
    if duration < 0:
        raise ValidationError("Duration cannot be negative")
    db.execute("INSERT INTO workouts (client_name, date, workout_type, duration_min, notes) "
               "VALUES (?, ?, ?, ?, ?)",
               (name, w_date, w_type, duration, (data.get("notes") or "").strip()))
    db.commit()
    return "Workout added"


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
    init_db(app.config["DATABASE"], app.config["ADMIN_PASSWORD"])
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
                return redirect(url_for("login_screen"))
            return view(*args, **kwargs)
        return wrapped

    @app.get("/health")
    def health():
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok", app=APP_TITLE, version=APP_VERSION)

    # ---------- WEB: LOGIN SCREEN ----------
    @app.route("/login", methods=["GET", "POST"])
    def login_screen():
        if request.method == "POST":
            username = request.form.get("username", "")
            role = login(get_db(), username, request.form.get("password", ""))
            if role:
                session.clear()
                session["user"], session["role"] = username.strip(), role
                return redirect(url_for("index"))
            flash("Invalid credentials", "error")
            return render_template("login.html", username=username), 401
        return render_template("login.html", username="")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_screen"))

    # ---------- WEB: DASHBOARD ----------
    @app.get("/")
    @login_required
    def index():
        db = get_db()
        name = request.args.get("client", "").strip()
        tab = request.args.get("tab", "summary")
        client = workouts = None
        has_chart = False
        if name:
            try:
                client = load_client(db, name)
            except NotFoundError as err:
                flash(str(err), "warning")
                name = ""
            else:
                workouts = refresh_workouts(db, name)
                has_chart = db.execute("SELECT 1 FROM progress WHERE client_name = ? LIMIT 1",
                                       (name,)).fetchone() is not None
        return render_template("index.html", clients=refresh_client_list(db), current=name,
                               client=client, summary=refresh_summary(client) if client else None,
                               workouts=workouts, has_chart=has_chart, tab=tab)

    @app.post("/add_save_client")
    @login_required
    def add_save_client_form():
        try:
            message, _created = add_save_client(get_db(), request.form.get("name"))
        except ValidationError as err:
            flash(str(err), "warning")
            return redirect(url_for("index"))
        flash(message, "success")
        return redirect(url_for("index", client=request.form["name"].strip()))

    @app.post("/generate_program")
    @login_required
    def generate_program_form():
        name = request.form.get("client", "").strip()
        try:
            message, _type, _detail = generate_program(get_db(), name)
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return redirect(url_for("index"))
        flash(message, "success")
        return redirect(url_for("index", client=name))

    @app.post("/check_membership")
    @login_required
    def check_membership_form():
        name = request.form.get("client", "").strip()
        try:
            flash(check_membership(get_db(), name)["message"], "info")
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return redirect(url_for("index"))
        return redirect(url_for("index", client=name))

    @app.get("/generate_pdf")
    @login_required
    def generate_pdf_download():
        name = request.args.get("client", "").strip()
        try:
            return pdf_response(name, generate_pdf(get_db(), name))
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return redirect(url_for("index"))

    @app.route("/add_workout", methods=["GET", "POST"])
    @login_required
    def add_workout_page():
        name = request.args.get("client", "").strip()
        try:
            ensure_client(get_db(), name)
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return redirect(url_for("index"))
        values = {"date": date.today().isoformat(), "workout_type": "", "duration_min": 60,
                  "notes": ""}
        if request.method == "POST":
            values.update(request.form.to_dict())
            try:
                flash(add_workout(get_db(), name, request.form), "success")
            except ValidationError as err:
                flash(str(err), "error")
                return render_template("add_workout.html", name=name, values=values,
                                       workout_types=WORKOUT_TYPES), 400
            return redirect(url_for("index", client=name, tab="workouts"))
        return render_template("add_workout.html", name=name, values=values,
                               workout_types=WORKOUT_TYPES)

    # ---------- JSON API ----------
    def api_error(err):
        return jsonify(error=str(err)), 404 if isinstance(err, NotFoundError) else 400

    def body():
        return request.get_json(silent=True) or {}

    @app.post("/api/login")
    def api_login():
        data = body()
        role = login(get_db(), data.get("username"), data.get("password"))
        if not role:
            return jsonify(error="Invalid credentials"), 401
        session.clear()
        session["user"], session["role"] = str(data["username"]).strip(), role
        return jsonify(user=session["user"], role=role)

    @app.post("/api/logout")
    def api_logout():
        session.clear()
        return jsonify(message="Logged out")

    @app.get("/api/clients")
    @login_required
    def api_clients():
        return jsonify(clients=refresh_client_list(get_db()))

    @app.post("/api/add_save_client")
    @login_required
    def api_add_save_client():
        try:
            message, created = add_save_client(get_db(), body().get("name"))
        except ValidationError as err:
            return api_error(err)
        return jsonify(message=message, created=created), 201 if created else 200

    @app.get("/api/load_client/<path:name>")
    @login_required
    def api_load_client(name):
        try:
            client = load_client(get_db(), name)
        except NotFoundError as err:
            return api_error(err)
        return jsonify(client=client, summary=refresh_summary(client),
                       workouts=refresh_workouts(get_db(), name))

    @app.post("/api/generate_program")
    @login_required
    def api_generate_program():
        data = body()
        seed = data.get("seed")
        if seed is not None and not isinstance(seed, (int, str)):
            return jsonify(error="seed must be a number or text"), 400
        rng = random if seed is None else random.Random(seed)
        try:
            message, program_type, detail = generate_program(
                get_db(), (data.get("name") or "").strip(), rng)
        except (ValidationError, NotFoundError) as err:
            return api_error(err)
        return jsonify(message=message, program_type=program_type, program=detail)

    @app.get("/api/generate_pdf/<path:name>")
    @login_required
    def api_generate_pdf(name):
        try:
            return pdf_response(name, generate_pdf(get_db(), name))
        except NotFoundError as err:
            return api_error(err)

    @app.get("/api/check_membership/<path:name>")
    @login_required
    def api_check_membership(name):
        try:
            return jsonify(check_membership(get_db(), name))
        except NotFoundError as err:
            return api_error(err)

    @app.get("/api/plot_charts/<path:name>")
    @login_required
    def api_plot_charts(name):
        png = plot_charts(get_db(), name)
        if png is None:
            return jsonify(error="No progress data available for this client"), 404
        return Response(png, mimetype="image/png")

    @app.get("/api/refresh_workouts/<path:name>")
    @login_required
    def api_refresh_workouts(name):
        try:
            ensure_client(get_db(), name)
        except NotFoundError as err:
            return api_error(err)
        return jsonify(workouts=refresh_workouts(get_db(), name))

    @app.post("/api/add_workout")
    @login_required
    def api_add_workout():
        data = body()
        try:
            message = add_workout(get_db(), (data.get("name") or "").strip(), data)
        except (ValidationError, NotFoundError) as err:
            return api_error(err)
        return jsonify(message=message), 201

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
