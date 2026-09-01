"""WSGI entry point for production servers such as Gunicorn."""

from dotenv import load_dotenv

load_dotenv()

from app import create_app

app = create_app()