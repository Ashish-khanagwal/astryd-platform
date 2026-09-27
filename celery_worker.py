"""Celery worker entry point."""

from wsgi import app as flask_app
celery = flask_app.extensions["celery"]
