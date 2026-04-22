"""SQLite-backed store for the `submission` record.

Field naming note: SQL columns use snake_case (e.g. `poster_filename`),
but the JSON API enforced by `app.py` uses lowerCamelCase
(`posterFilename`) per API_CONTRACT.md §2.2.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import sqlite3
import uuid

DB_PATH = os.environ.get("DATA_DB_PATH", "/data/submissions.db")

TERMINAL_STATUSES = {"READY", "NEEDS REVISION", "INCOMPLETE"}
ALL_STATUSES = TERMINAL_STATUSES | {"PENDING"}


def _now() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "posterFilename": row["poster_filename"],
        "posterMimeType": row["poster_mime_type"],
        "posterSize": row["poster_size"],
        "status": row["status"],
        "note": row["note"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


@contextlib.contextmanager
def _conn():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(DB_PATH, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def init_db(schema_path: str) -> None:
    with _conn() as c, open(schema_path, "r", encoding="utf-8") as f:
        c.executescript(f.read())
        # v1.1.0 tolerant migration (§Append-7.6): for a DB created under
        # v1.0.0 schema the CREATE TABLE IF NOT EXISTS above is a no-op and
        # the three poster_* columns would be missing. Add them idempotently
        # so `GET /submissions/{id}` returns the two new fields as NULL for
        # historical rows and `GET /submissions/{id}/poster` returns 404.
        existing = {
            row["name"]
            for row in c.execute("PRAGMA table_info(submissions)").fetchall()
        }
        for col, ddl in (
            ("poster_image", "BLOB"),
            ("poster_mime_type", "TEXT"),
            ("poster_size", "INTEGER"),
        ):
            if col not in existing:
                c.execute(f"ALTER TABLE submissions ADD COLUMN {col} {ddl}")


def create(title: str, description: str, poster_filename: str,
           poster_image_bytes: bytes | None = None,
           poster_mime_type: str | None = None,
           poster_size: int | None = None) -> dict:
    sid = str(uuid.uuid4())
    ts = _now()
    with _conn() as c:
        c.execute(
            "INSERT INTO submissions (id, title, description, poster_filename, "
            "poster_image, poster_mime_type, poster_size, "
            "status, note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?, ?)",
            (sid, title, description, poster_filename,
             poster_image_bytes, poster_mime_type, poster_size,
             ts, ts),
        )
        row = c.execute(
            "SELECT * FROM submissions WHERE id=?", (sid,)
        ).fetchone()
        return _row_to_dict(row)


def get(sid: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM submissions WHERE id=?", (sid,)
        ).fetchone()
        return _row_to_dict(row) if row else None


class PatchResult:
    """Sentinel enum returned by `patch` so the HTTP layer can map codes."""

    NOT_FOUND = "NOT_FOUND"
    INVALID_STATUS = "INVALID_STATUS"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"


def patch(sid: str, status, note):
    """Update (status, note) for `sid` with strict idempotency.

    Returns one of:
      - a dict (the updated or unchanged record) on success
      - PatchResult.INVALID_STATUS if status is missing / not in enum
      - PatchResult.INVALID_STATUS_TRANSITION if status == 'PENDING'
      - PatchResult.NOT_FOUND if the id doesn't exist

    Per API_CONTRACT.md §6.2.3 / §11.1:
      - status == 'PENDING' must be rejected with INVALID_STATUS_TRANSITION.
      - If (status, note) equals the current record exactly, return 200 and
        do NOT refresh updated_at.
    """
    if not isinstance(status, str) or status not in ALL_STATUSES:
        return PatchResult.INVALID_STATUS
    if status == "PENDING":
        return PatchResult.INVALID_STATUS_TRANSITION

    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute(
                "SELECT * FROM submissions WHERE id=?", (sid,)
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return PatchResult.NOT_FOUND

            current_status = row["status"]
            current_note = row["note"]
            if current_status == status and current_note == note:
                c.execute("COMMIT")
                return _row_to_dict(row)

            c.execute(
                "UPDATE submissions SET status=?, note=?, updated_at=? "
                "WHERE id=?",
                (status, note, _now(), sid),
            )
            updated = c.execute(
                "SELECT * FROM submissions WHERE id=?", (sid,)
            ).fetchone()
            c.execute("COMMIT")
            return _row_to_dict(updated)
        except Exception:
            c.execute("ROLLBACK")
            raise


def get_poster(sid: str) -> tuple[bytes, str | None, int | None] | None:
    """Return (image_bytes, mime_type, size) or None if not found / no poster."""
    with _conn() as c:
        row = c.execute(
            "SELECT poster_image, poster_mime_type, poster_size "
            "FROM submissions WHERE id=?", (sid,)
        ).fetchone()
        if row is None:
            return None
        if row["poster_image"] is None:
            return None
        return (bytes(row["poster_image"]), row["poster_mime_type"], row["poster_size"])
