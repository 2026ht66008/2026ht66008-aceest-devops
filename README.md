# ACEest Fitness & Gym: Flask App with CI/CD

Introduction to DevOps (CSIZG514/SEZG514), Assignment 1
**Name:** <Your Name> · **BITS ID:** <BITS ID>

ACEest's desktop (Tkinter) gym-management tool, rebuilt as a Flask web app and delivered through a CI/CD pipeline:

GitHub → GitHub Actions (lint, Docker build, tests in container) → Jenkins (build, test, SonarQube, Docker, deploy)

| | Link |
|---|---|
| Repository | https://github.com/<github-user>/<repo-name> |
| Staging | http://<vm-ip>:5001 |
| Production | http://<vm-ip>:5000 |

## The application

Each release of the original program was ported to Flask with the same features and is tagged in Git:

| Tag | Adds |
|---|---|
| v1.0 | Program viewer: workout and diet plans for Fat Loss, Muscle Gain and Beginner |
| v1.1 | Client profile and calorie estimate |
| v1.1.2 | Client list, coach notes, CSV export, progress chart |
| v2.0.1 | SQLite storage: save and load clients, weekly progress |
| v2.1.2 | Same as 2.0.1 (the original release was identical) |
| v2.2.1 | Weekly adherence chart |
| v2.2.4 | Goals, workouts, body metrics, BMI, weight chart, workout history |
| v3.0.1 | Same as 2.2.4 (the original release was identical) |
| v3.1.2 | Login, membership expiry, AI workout generator, PDF report |
| v3.2.4 | Dashboard with program templates, membership check and workouts tab |

`GET /health` returns the status and version. Login for v3.x: `admin` / `admin`.

## Run it locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m flask --app app run --debug      # open http://127.0.0.1:5000
```

## Run the tests

```bash
python -m pytest -v
```

The tests use Flask's test client and a temporary database for every test. They cover:

- the business logic (calories, BMI, the workout generator)
- every page and API endpoint
- input validation
- charts, CSV and PDF output
- login protection

## Docker

```bash
docker build -t aceest-app .
docker run --rm aceest-app python -m pytest -v     # run the tests inside the container
docker run -d -p 5000:5000 aceest-app              # run the app
```

The image uses `python:3.12-slim`, installs dependencies without a pip cache, skips files listed in `.dockerignore`, runs as a non-root user and serves the app with gunicorn.

## CI/CD

**GitHub Actions** (`.github/workflows/main.yml`) runs on every push and pull request:

1. **Build & Lint:** install dependencies, check syntax, run flake8.
2. **Docker build:** build the image.
3. **Tests:** run the Pytest suite inside the container.

`main` is protected, so a pull request can only be merged when this passes.

**Jenkins** (`Jenkinsfile`) checks GitHub every 2 minutes and runs:

| Stage | What it does |
|---|---|
| Checkout | Fresh clone into an empty workspace |
| Build | New virtual environment, install dependencies |
| Test | Pytest; results shown in Jenkins |
| SonarQube Quality Gate | Code analysis; the build stops if the quality gate fails |
| Docker Build | Image tagged with the Jenkins build number |
| Test in Container | Pytest again inside the image |
| Deploy to Staging | Run the image on port 5001 and check `/health` |
| Deploy to Production | Run the same image on port 5000 and check `/health` |

If any stage fails, the stages after it don't run, so a failing test never reaches staging or production.

## Screenshots

See `docs/screenshots/`: GitHub Actions run, Jenkins stage view, SonarQube dashboard, and the app on staging and production.
