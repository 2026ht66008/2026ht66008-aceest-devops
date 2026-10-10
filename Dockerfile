# Small official Python image (slim = fewer packages, so smaller and safer)
FROM python:3.12-slim

# Don't create .pyc files, and print logs straight away
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install the dependencies first, so Docker can reuse this layer
# when only the code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app and its tests (anything in .dockerignore is skipped)
COPY . .

# Don't run as root
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 5000

# gunicorn is a production web server (Flask's own server is only for development)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:create_app()"]
