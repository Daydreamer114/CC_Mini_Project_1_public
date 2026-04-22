"""Targeted regression for §Append-7.6 backward-compatible migration.

Boots my own Data Service against a pre-v1.1.0 SQLite DB (built with the
v1.0.0 schema, missing all three ``poster_*`` columns), then asserts:

  1. ``init_db`` runs ``ALTER TABLE ADD COLUMN`` to add the three new
     columns on startup.
  2. ``POST /submissions`` on the migrated DB (without poster fields)
     returns 201 with ``posterMimeType=null`` / ``posterSize=null`` and
     does NOT leak ``posterImage``.
  3. ``GET /submissions/{id}/poster`` on that no-bytes record returns
     404 with the ``has no poster`` message (§Append-10.2).

No code from other team members is executed here — the Presentation
passthrough regression (§Append-5.4) lives in the cross-cloud
``a_frontend_e2e.py`` instead.

Usage:
    python tests/regression_passthrough.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, ".."))

DATA_URL = "http://localhost:37588"

V10_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    poster_filename  TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'READY', 'NEEDS REVISION', 'INCOMPLETE')),
    note             TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.getheaders()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()


def wait_for(url: str, max_wait: float = 10) -> bool:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            http("GET", url)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def boot_data_service(db_path: str, schema_path: str) -> list[subprocess.Popen]:
    env = os.environ.copy()
    env["DATA_DB_PATH"] = db_path
    env["DATA_SCHEMA_PATH"] = schema_path
    procs: list[subprocess.Popen] = [
        subprocess.Popen(
            [sys.executable, os.path.join(PROJECT, "data-service", "app.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    ]
    if not wait_for(f"{DATA_URL}/healthz"):
        kill(procs)
        raise SystemExit("data service failed to start")
    return procs


def kill(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def main() -> int:
    tmp_dir = os.path.join(PROJECT, "data-service")
    v10_db = os.path.join(tmp_dir, "regression_v10.db")
    v10_schema = os.path.join(tmp_dir, "regression_v10.sql")

    for path in (v10_db, v10_schema):
        if os.path.exists(path):
            os.remove(path)

    # --- Stage 0: build a v1.0.0-shaped DB and schema file ---
    with open(v10_schema, "w", encoding="utf-8") as f:
        f.write(V10_SCHEMA)
    c = sqlite3.connect(v10_db)
    c.executescript(V10_SCHEMA)
    c.commit()
    c.close()
    c = sqlite3.connect(v10_db)
    cols_before = {r[1] for r in c.execute("PRAGMA table_info(submissions)").fetchall()}
    c.close()
    assert "poster_image" not in cols_before, "pre-state broken: poster_image already present"
    print(f"[setup] v1.0.0 DB columns: {sorted(cols_before)}")

    procs = boot_data_service(v10_db, v10_schema)
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            failures.append(f"{name}: {detail}")

    try:
        # --- Test 1: ALTER TABLE migration added the columns ---
        c = sqlite3.connect(v10_db)
        cols_after = {r[1] for r in c.execute("PRAGMA table_info(submissions)").fetchall()}
        c.close()
        added = cols_after - cols_before
        check(
            "migration adds poster_image / poster_mime_type / poster_size",
            {"poster_image", "poster_mime_type", "poster_size"}.issubset(cols_after),
            f"after={sorted(cols_after)} added={sorted(added)}",
        )

        # --- Test 2: POST without poster on migrated DB returns null fields ---
        body = json.dumps({
            "title": "Legacy-shape insert",
            "description": "q" * 40,
            "posterFilename": "legacy.jpg",
        }).encode("utf-8")
        st, _, bd = http(
            "POST", f"{DATA_URL}/submissions",
            body=body, headers={"Content-Type": "application/json"},
        )
        rec = json.loads(bd.decode("utf-8"))
        check(
            "POST on migrated DB → 201 + null poster fields",
            st == 201
            and rec.get("posterMimeType") is None
            and rec.get("posterSize") is None
            and "posterImage" not in rec,
            f"status={st} mime={rec.get('posterMimeType')} size={rec.get('posterSize')}",
        )
        sid = rec.get("id")

        # --- Test 3: GET /poster on migrated-DB record without bytes → 404 "has no poster" ---
        st, _, bd = http("GET", f"{DATA_URL}/submissions/{sid}/poster")
        env = json.loads(bd.decode("utf-8"))
        msg = env.get("error", {}).get("message", "")
        check(
            "GET /poster on migrated no-bytes record → 404 'has no poster'",
            st == 404 and "has no poster" in msg,
            f"status={st} msg={msg}",
        )

    finally:
        kill(procs)
        for path in (v10_db, v10_schema):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All targeted regressions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
