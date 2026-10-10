"""ACEest Fitness & Performance - v3.0.1 (Flask port of Aceestver-3.0.1.py).

The original 3.0.1 script is identical to 2.2.4, so this release changes only the
version number. Same features as the Tkinter original:
  * Client list (select a saved client), client form with height, program and goals
    (target weight, target adherence)
  * Save Client / Load Client / Save Weekly Progress
  * Client Summary: profile, program notes, goals, progress summary, last body metrics
  * Log Workout (session + one exercise), Log Body Metrics, View Workout History
  * Progress & Analytics: adherence chart, weight trend chart, BMI & risk info
  * Status bar showing the last action

Change from the original: when an older database is found, missing columns are
added (ALTER TABLE) instead of dropping the clients table, so no data is lost.

Web pages:  GET  /                       -> dashboard (?client=<name>&tab=summary|analytics)
            POST /save_client | /load_client | /save_progress
            GET/POST /log_workout | /log_metrics,  GET /workout_history
JSON API:   GET  /api/programs | /api/clients
            POST /api/save_client | /api/save_progress | /api/log_workout | /api/log_metrics
            GET  /api/load_client/<name> | /api/workout_history/<name>
            GET  /api/show_progress_chart/<name> | /api/show_weight_chart/<name>  (PNG)
            GET  /api/show_bmi_info/<name>
Health:     GET  /health
"""
import io
import math
import os
import sqlite3
from datetime import date, datetime

from flask import (Flask, Response, current_app, flash, g, jsonify, redirect,
                   render_template, request, url_for)
from matplotlib.figure import Figure

APP_VERSION = "3.0.1"
APP_TITLE = "ACEest Fitness & Performance"
HEADER_TEXT = "ACEest Functional Fitness System"
DB_NAME = "aceest_fitness.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    age INTEGER,
    height REAL,
    weight REAL,
    program TEXT,
    calories INTEGER,
    target_weight REAL,
    target_adherence INTEGER
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
# Columns the clients table must have (older databases get them added)
CLIENT_COLUMNS = {"age": "INTEGER", "height": "REAL", "weight": "REAL", "program": "TEXT",
                  "calories": "INTEGER", "target_weight": "REAL", "target_adherence": "INTEGER"}
CLIENT_FIELDS = ("name", "age", "height", "weight", "program", "calories",
                 "target_weight", "target_adherence")
FORM_FIELDS = ("name", "age", "height", "weight", "program", "target_weight",
               "target_adherence", "adherence")
WORKOUT_TYPES = ["Strength", "Hypertrophy", "Conditioning", "Mixed", "Mobility"]


class ValidationError(ValueError):
    """Invalid input - shown as a warning on the page or HTTP 400 in the API."""


class NotFoundError(LookupError):
    """Missing client or data - warning on the page or HTTP 404 in the API."""


# ---------- DATABASE ----------

def init_db(conn):
    """Create all tables and add any columns missing from an older clients table."""
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(clients)")}
    for column, column_type in CLIENT_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {column} {column_type}")
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


def positive_or_none(number):
    """The original stores 'not entered' (0) as NULL."""
    return number if number > 0 else None


def parse_date(value, field="Date"):
    """Dates are stored as YYYY-MM-DD text, like the original."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be in YYYY-MM-DD format") from None


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
        "target_weight": to_number(data.get("target_weight"), "Target weight"),
        "target_adherence": to_number(data.get("target_adherence"), "Target adherence", int, 100),
    }


def save_client(db, client):
    """Create or replace a client; values of 0 are stored as 'not set' (NULL)."""
    if not client["name"]:
        raise ValidationError("Name is required")
    if not client["program"]:
        raise ValidationError("Program is required")
    if client["program"] not in programs:
        raise ValidationError(f"Unknown program: {client['program']}")
    weight = positive_or_none(client["weight"])
    calories = int(weight * programs[client["program"]]["factor"]) if weight else None
    db.execute(
        "INSERT OR REPLACE INTO clients (name, age, height, weight, program, calories, "
        "target_weight, target_adherence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (client["name"], positive_or_none(client["age"]), positive_or_none(client["height"]),
         weight, client["program"], calories, positive_or_none(client["target_weight"]),
         positive_or_none(client["target_adherence"])),
    )
    db.commit()
    return "Client data saved"


def load_client(db, name):
    row = db.execute(f"SELECT {', '.join(CLIENT_FIELDS)} FROM clients WHERE name = ?",
                     (name,)).fetchone()
    if row is None:
        raise NotFoundError("Client not found")
    return dict(row)


def ensure_client(db, name):
    """Every per-client action needs a selected, existing client."""
    if not name:
        raise ValidationError("Select or enter client first")
    return load_client(db, name)


def dash(value):
    return "-" if value is None else value


def refresh_summary(db, name):
    """Everything shown in the Client Summary tab, plus the text block itself."""
    client = load_client(db, name)
    total_weeks, avg_adherence = db.execute(
        "SELECT COUNT(*), AVG(adherence) FROM progress WHERE client_name = ?", (name,)
    ).fetchone()
    avg_adherence = round(avg_adherence, 1) if avg_adherence is not None else 0
    last = db.execute(
        "SELECT date, weight, waist, bodyfat FROM metrics WHERE client_name = ? "
        "ORDER BY date DESC, id DESC LIMIT 1", (name,)
    ).fetchone()
    last_metric = dict(last) if last else None
    last_metric_str = "None"
    if last_metric:
        last_metric_str = (f"{last_metric['date']} | {last_metric['weight']} kg, "
                           f"Waist {last_metric['waist']} cm, Bodyfat {last_metric['bodyfat']}%")
    goal_summary = "None"
    if client["target_weight"] or client["target_adherence"]:
        goal_summary = ""
        if client["target_weight"]:
            goal_summary += f"Target Weight: {client['target_weight']} kg; "
        if client["target_adherence"]:
            goal_summary += f"Target Adherence: {client['target_adherence']}%"
    prog_desc = programs.get(client["program"], {}).get("desc", "")
    text = "\n".join([
        "CLIENT PROFILE", "--------------",
        f"Name      : {client['name']}",
        f"Age       : {dash(client['age'])}",
        f"Height    : {dash(client['height'])} cm",
        f"Weight    : {dash(client['weight'])} kg",
        f"Program   : {client['program']}",
        f"Calories  : {dash(client['calories'])} kcal/day",
        "", "PROGRAM NOTES", "-------------", prog_desc,
        "", "GOALS", "-----", goal_summary,
        "", "PROGRESS SUMMARY", "----------------",
        f"Weeks logged       : {total_weeks}",
        f"Average adherence  : {avg_adherence}%",
        "", "LAST BODY METRICS", "-----------------", last_metric_str,
    ])
    return {"client": client, "program_notes": prog_desc, "goals": goal_summary,
            "weeks_logged": total_weeks, "average_adherence": avg_adherence,
            "last_metrics": last_metric, "text": text}


def save_progress(db, name, adherence, now=None):
    if not name:
        raise ValidationError("Enter client name first")
    load_client(db, name)
    week = (now or datetime.now()).strftime("Week %U - %Y")
    db.execute("INSERT INTO progress (client_name, week, adherence) VALUES (?, ?, ?)",
               (name, week, adherence))
    db.commit()
    return "Weekly progress logged", week


# ---------- WORKOUTS & METRICS ----------

def parse_exercise(data):
    return {
        "name": (data.get("name") or "").strip(),
        "sets": to_number(data.get("sets"), "Sets", int),
        "reps": to_number(data.get("reps"), "Reps", int),
        "weight": to_number(data.get("weight"), "Exercise weight"),
    }


def log_workout(db, name, data, exercises=()):
    """Save a workout session and its exercises (save_workout in the original)."""
    ensure_client(db, name)
    w_date = parse_date(data.get("date", date.today().isoformat()))
    w_type = (data.get("workout_type") or "").strip()
    if not w_date or not w_type:
        raise ValidationError("Date and workout type are required")
    if w_type not in WORKOUT_TYPES:
        raise ValidationError(f"Unknown workout type: {w_type}")
    duration = to_number(data.get("duration_min", 60), "Duration", int)
    notes = (data.get("notes") or "").strip()
    parsed = [parse_exercise(ex) for ex in exercises]
    cur = db.execute(
        "INSERT INTO workouts (client_name, date, workout_type, duration_min, notes) "
        "VALUES (?, ?, ?, ?, ?)", (name, w_date, w_type, duration, notes))
    workout_id = cur.lastrowid
    for ex in parsed:
        if ex["name"]:  # an exercise row is only saved when it has a name
            db.execute("INSERT INTO exercises (workout_id, name, sets, reps, weight) "
                       "VALUES (?, ?, ?, ?, ?)",
                       (workout_id, ex["name"], ex["sets"], ex["reps"], ex["weight"]))
    db.commit()
    return "Workout logged successfully", workout_id


def log_metrics(db, name, data):
    """Save body metrics (save_metrics in the original)."""
    ensure_client(db, name)
    m_date = parse_date(data.get("date", date.today().isoformat()))
    if not m_date:
        raise ValidationError("Date is required")
    values = [positive_or_none(to_number(data.get(key), label))
              for key, label in (("weight", "Weight"), ("waist", "Waist"),
                                 ("bodyfat", "Bodyfat"))]
    db.execute("INSERT INTO metrics (client_name, date, weight, waist, bodyfat) "
               "VALUES (?, ?, ?, ?, ?)", (name, m_date, *values))
    db.commit()
    return "Metrics logged successfully"


def workout_history(db, name):
    """Workouts newest first, each with its exercises."""
    ensure_client(db, name)
    rows = db.execute(
        "SELECT id, date, workout_type, duration_min, notes FROM workouts "
        "WHERE client_name = ? ORDER BY date DESC, id DESC", (name,)).fetchall()
    history = []
    for row in rows:
        workout = dict(row)
        workout["exercises"] = [dict(ex) for ex in db.execute(
            "SELECT name, sets, reps, weight FROM exercises WHERE workout_id = ? ORDER BY id",
            (row["id"],))]
        history.append(workout)
    return history


# ---------- CHARTS / ANALYTICS ----------

def line_chart_png(x, y, title, xlabel, ylabel, color=None, ylim=None):
    fig = Figure(figsize=(8, 4))
    ax = fig.subplots()
    ax.plot(x, y, marker="o", linewidth=2, color=color)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(True)
    ax.tick_params(axis="x", labelrotation=45)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


def show_progress_chart(db, name):
    ensure_client(db, name)
    data = db.execute("SELECT week, adherence FROM progress WHERE client_name = ? ORDER BY id",
                      (name,)).fetchall()
    if not data:
        raise NotFoundError("No progress data available for this client")
    return line_chart_png([r["week"] for r in data], [r["adherence"] for r in data],
                          f"Weekly Adherence – {name}", "Week", "Adherence (%)", ylim=(0, 100))


def show_weight_chart(db, name):
    ensure_client(db, name)
    data = db.execute("SELECT date, weight FROM metrics WHERE client_name = ? "
                      "AND weight IS NOT NULL ORDER BY date", (name,)).fetchall()
    if not data:
        raise NotFoundError("No weight metrics available for this client")
    return line_chart_png([r["date"] for r in data], [r["weight"] for r in data],
                          f"Weight Trend – {name}", "Date", "Weight (kg)", color="orange")


def calculate_bmi(height_cm, weight_kg):
    """BMI rounded to 1 decimal, its category and the coaching risk note."""
    h_m = height_cm / 100.0
    bmi = round(weight_kg / (h_m * h_m), 1)
    if bmi < 18.5:
        return bmi, "Underweight", "Potential nutrient deficiency, low energy."
    if bmi < 25:
        return bmi, "Normal", "Low risk if active and strong."
    if bmi < 30:
        return bmi, "Overweight", "Moderate risk; focus on adherence and progressive activity."
    return bmi, "Obese", "Higher risk; prioritize fat loss, consistency, and supervision."


def show_bmi_info(db, name):
    client = ensure_client(db, name)
    if not client["height"] or not client["weight"]:
        raise ValidationError("Enter valid height and weight first")
    bmi, category, risk = calculate_bmi(client["height"], client["weight"])
    return {"bmi": bmi, "category": category, "risk": risk,
            "message": f"BMI for {name}: {bmi} ({category})\n\nRisk note: {risk}"}


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

    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
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

    # ---------- WEB: DASHBOARD ----------
    @app.get("/")
    def index():
        db = get_db()
        form = {key: request.args.get(key, "") for key in FORM_FIELDS}
        name = request.args.get("client", "").strip()
        tab = request.args.get("tab", "summary")
        chart = request.args.get("chart", "")
        summary = bmi = None
        if name:
            try:
                summary = refresh_summary(db, name)
            except NotFoundError as err:
                flash(str(err), "warning")
                name = ""
            else:
                for key in ("name", "age", "height", "weight", "program",
                            "target_weight", "target_adherence"):
                    value = summary["client"][key]
                    form[key] = "" if value is None else value
                if chart == "bmi":
                    try:
                        bmi = show_bmi_info(db, name)
                    except ValidationError as err:
                        flash(str(err), "warning")
        return render_template("index.html", programs=programs, clients=refresh_client_list(db),
                               form=form, current=name, summary=summary, tab=tab,
                               chart=chart, bmi=bmi)

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
        flash(f"Saved client: {client['name']}", "status")
        return redirect(url_for("index", client=client["name"]))

    @app.post("/load_client")
    def load_client_form():
        name = request.form.get("name", "").strip()
        if not name:
            flash("Enter or select client name first", "warning")
            return back_to_form()
        try:
            load_client(get_db(), name)
        except NotFoundError as err:
            flash(str(err), "warning")
            return back_to_form()
        flash(f"Loaded client: {name}", "status")
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
        flash(f"Saved weekly progress for {name}", "status")
        return redirect(url_for("index", client=name))

    @app.get("/analytics/<chart>")
    def analytics(chart):
        """Adherence Chart / Weight Trend Chart / BMI & Risk Info buttons."""
        name = request.args.get("client", "").strip()
        checks = {"progress": show_progress_chart, "weight": show_weight_chart,
                  "bmi": show_bmi_info}
        if chart not in checks:
            return render_template("error.html", code=404, message="Unknown chart."), 404
        try:
            checks[chart](get_db(), name)
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return redirect(url_for("index", client=name, tab="analytics"))
        return redirect(url_for("index", client=name, tab="analytics", chart=chart))

    # ---------- WEB: WORKOUTS & METRICS (the Toplevel windows) ----------
    def selected_client():
        return (request.args.get("client") or request.args.get("name") or "").strip()

    def require_client_page():
        name = selected_client()
        try:
            ensure_client(get_db(), name)
        except (ValidationError, NotFoundError) as err:
            flash(str(err), "warning")
            return name, redirect(url_for("index", client=name) if name else url_for("index"))
        return name, None

    @app.route("/log_workout", methods=["GET", "POST"])
    def log_workout_page():
        name, problem = require_client_page()
        if problem:
            return problem
        values = {"date": date.today().isoformat(), "workout_type": "", "duration_min": 60,
                  "notes": "", "ex_name": "", "ex_sets": 3, "ex_reps": 10, "ex_weight": 0.0}
        if request.method == "POST":
            values.update(request.form.to_dict())
            exercise = {"name": values["ex_name"], "sets": values["ex_sets"],
                        "reps": values["ex_reps"], "weight": values["ex_weight"]}
            try:
                message, _id = log_workout(get_db(), name, request.form, [exercise])
            except ValidationError as err:
                flash(str(err), "error")
                return render_template("log_workout.html", name=name, values=values,
                                       workout_types=WORKOUT_TYPES), 400
            flash(message, "success")
            flash(f"Workout logged for {name}", "status")
            return redirect(url_for("index", client=name))
        return render_template("log_workout.html", name=name, values=values,
                               workout_types=WORKOUT_TYPES)

    @app.route("/log_metrics", methods=["GET", "POST"])
    def log_metrics_page():
        name, problem = require_client_page()
        if problem:
            return problem
        current_weight = load_client(get_db(), name)["weight"] or 0.0
        values = {"date": date.today().isoformat(), "weight": current_weight,
                  "waist": 0.0, "bodyfat": 0.0}
        if request.method == "POST":
            values.update(request.form.to_dict())
            try:
                message = log_metrics(get_db(), name, request.form)
            except ValidationError as err:
                flash(str(err), "error")
                return render_template("log_metrics.html", name=name, values=values), 400
            flash(message, "success")
            flash(f"Metrics logged for {name}", "status")
            return redirect(url_for("index", client=name))
        return render_template("log_metrics.html", name=name, values=values)

    @app.get("/workout_history")
    def workout_history_page():
        name, problem = require_client_page()
        if problem:
            return problem
        flash(f"Loaded workout history for {name}", "status")
        return render_template("workout_history.html", name=name,
                               workouts=workout_history(get_db(), name))

    # ---------- JSON API ----------
    def api_error(err):
        status = 404 if isinstance(err, NotFoundError) else 400
        return jsonify(error=str(err)), status

    def body():
        return request.get_json(silent=True) or {}

    @app.get("/api/programs")
    def api_programs():
        return jsonify(programs=programs)

    @app.get("/api/clients")
    def api_clients():
        return jsonify(clients=refresh_client_list(get_db()))

    @app.post("/api/save_client")
    def api_save_client():
        try:
            client = parse_client(body())
            message = save_client(get_db(), client)
        except ValidationError as err:
            return api_error(err)
        return jsonify(message=message, client=load_client(get_db(), client["name"])), 201

    @app.get("/api/load_client/<path:name>")
    def api_load_client(name):
        try:
            return jsonify(refresh_summary(get_db(), name))
        except NotFoundError as err:
            return api_error(err)

    @app.post("/api/save_progress")
    def api_save_progress():
        data = body()
        try:
            adherence = to_number(data.get("adherence"), "Weekly adherence", int, 100)
            message, week = save_progress(get_db(), (data.get("name") or "").strip(), adherence)
        except (ValidationError, NotFoundError) as err:
            return api_error(err)
        return jsonify(message=message, week=week, adherence=adherence), 201

    @app.post("/api/log_workout")
    def api_log_workout():
        data = body()
        exercises = data.get("exercises") or []
        if not isinstance(exercises, list) or not all(isinstance(e, dict) for e in exercises):
            return jsonify(error="exercises must be a list of objects"), 400
        try:
            message, workout_id = log_workout(get_db(), (data.get("name") or "").strip(),
                                              data, exercises)
        except (ValidationError, NotFoundError) as err:
            return api_error(err)
        return jsonify(message=message, workout_id=workout_id), 201

    @app.post("/api/log_metrics")
    def api_log_metrics():
        data = body()
        try:
            message = log_metrics(get_db(), (data.get("name") or "").strip(), data)
        except (ValidationError, NotFoundError) as err:
            return api_error(err)
        return jsonify(message=message), 201

    @app.get("/api/workout_history/<path:name>")
    def api_workout_history(name):
        try:
            return jsonify(workouts=workout_history(get_db(), name))
        except NotFoundError as err:
            return api_error(err)

    @app.get("/api/show_progress_chart/<path:name>")
    def api_show_progress_chart(name):
        try:
            return Response(show_progress_chart(get_db(), name), mimetype="image/png")
        except NotFoundError as err:
            return api_error(err)

    @app.get("/api/show_weight_chart/<path:name>")
    def api_show_weight_chart(name):
        try:
            return Response(show_weight_chart(get_db(), name), mimetype="image/png")
        except NotFoundError as err:
            return api_error(err)

    @app.get("/api/show_bmi_info/<path:name>")
    def api_show_bmi_info(name):
        try:
            return jsonify(show_bmi_info(get_db(), name))
        except (ValidationError, NotFoundError) as err:
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
