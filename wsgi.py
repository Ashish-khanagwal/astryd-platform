"""WSGI entry point for production servers such as Gunicorn."""

import os
from pathlib import Path

from dotenv import load_dotenv

runtime_environment = os.getenv("ASTRYD_ENV", "staging").strip().lower()
if runtime_environment not in {"staging", "production"}:
    raise RuntimeError("ASTRYD_ENV must be either 'staging' or 'production'")

repository_root = Path(__file__).resolve().parent
load_dotenv(repository_root / f".env.{runtime_environment}")
load_dotenv(repository_root / ".env")

from app import create_app

app = create_app()
