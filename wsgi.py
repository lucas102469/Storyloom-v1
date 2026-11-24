"""Gunicorn application entry point; local development uses python app.py."""
from app import create_app

app = create_app()
