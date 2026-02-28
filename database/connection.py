"""
database/connection.py — SQLite connection management.

Provides a thread-safe connection manager with WAL journal mode enabled
for concurrent reads (dashboard) while the engine writes.

Usage:
    from database.connection import get_db, initialize_db

    # One-time setup at startup:
    initialize_db()

    # Use throughout the codebase:
    with get_db() as conn:
        conn.execute("SELECT ...")
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator

from config import config

# Thread-local storage so each thread gets its own connection.
# The dashboard and engine run in the same thread (asyncio), but
# this protects against any accidental multi-threaded access.
_local = threading.local()

# The resolved DB path — set once at initialization.
_db_path: str = ""


def initialize_db(db_path: str | None = None) -> None:
    """
    Initialize the database connection settings.
    Call once at application startup before any DB operations.
    """
    global _db_path
    # Close any cached thread-local connection so the next access
    # opens a fresh connection to the new path.
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
    _db_path = db_path or config.db_path
    # Create the file and run initial PRAGMA settings.
    conn = _get_raw_connection()
    conn.close()


def _get_raw_connection() -> sqlite3.Connection:
    """Open a new SQLite connection with optimal settings."""
    path = _db_path or config.db_path
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # rows accessible as dicts
    # WAL mode allows concurrent reads while writing
    conn.execute("PRAGMA journal_mode=WAL")
    # Faster sync — safe for local use (OS handles crash recovery)
    conn.execute("PRAGMA synchronous=NORMAL")
    # Enforce foreign key constraints
    conn.execute("PRAGMA foreign_keys=ON")
    # Increase cache for performance
    conn.execute("PRAGMA cache_size=-8000")  # ~8MB cache
    conn.commit()
    return conn


def get_connection() -> sqlite3.Connection:
    """
    Return the thread-local SQLite connection, creating it if needed.
    The connection is reused across calls within the same thread.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _get_raw_connection()
    return _local.conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields a SQLite connection.
    Commits on success, rolls back on exception.

    Example:
        with get_db() as conn:
            conn.execute("INSERT INTO bot_log ...")
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_connection() -> None:
    """Close the thread-local connection if open. Call on shutdown."""
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def execute_query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """
    Execute a SELECT query and return all rows.
    Uses parameterized queries — never use string concatenation for SQL.
    """
    with get_db() as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall()


def execute_write(sql: str, params: tuple = ()) -> int:
    """
    Execute an INSERT/UPDATE/DELETE and return the lastrowid.
    """
    with get_db() as conn:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid or 0


def execute_many(sql: str, params_list: list[tuple]) -> None:
    """Execute a batch write operation."""
    with get_db() as conn:
        conn.executemany(sql, params_list)
