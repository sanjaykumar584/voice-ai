import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Make repo-root .env values available (DATABASE_URL etc.) — same file the app reads.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


@pytest.fixture(scope="session")
def db_available() -> bool:
    """True when the Supabase Postgres from .env is reachable."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return False
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3):
            return True
    except Exception:
        return False


def pytest_runtest_setup(item):
    """Skip DB-dependent tests when local Supabase isn't running."""
    if any(mark.name == "db" for mark in item.iter_markers()):
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            pytest.skip("DATABASE_URL not set")
        try:
            import psycopg

            with psycopg.connect(url, connect_timeout=3):
                pass
        except Exception:
            pytest.skip("local Supabase Postgres not reachable")
