"""Local smoke tests for C (Data Service) v1.1.0.

Starts ONLY my own Data Service as a subprocess and runs 17 probes against it.
No code from other team members is executed here — Workflow / Presentation
contracts are exercised by the cross-cloud ``a_frontend_e2e.py`` instead.

Usage:
    python tests/smoke_test.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_URL = "http://localhost:37588"
TIMEOUT = 15

# Minimal 1x1 PNG (67 bytes) — same bytes as tests/1x1.png
MINIMAL_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")

# Minimal valid JPEG (150 bytes) — same bytes as tests/1x1.jpg
MINIMAL_JPEG = base64.b64encode(
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $.\' \",#\x1c(7),/\x1c\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01"
    b"\x01\x11\x00\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\n\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x08\x01"
    b"\x01\x00\x00?\x00T\xbf\xff\xd9"
).decode("ascii")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http(method: str, url: str, *, body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.getheaders()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()
    except urllib.error.URLError as e:
        return 0, {}, f"URLERROR: {e.reason}".encode()


def post_json(url: str, body) -> tuple[int, dict, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return http("POST", url, body=data, headers={"Content-Type": "application/json"})


def get(url: str) -> tuple[int, dict, bytes]:
    return http("GET", url)


def patch_json(url: str, body) -> tuple[int, dict, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return http("PATCH", url, body=data, headers={"Content-Type": "application/json"})


def as_json(data: bytes):
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

results: list[tuple[str, str, bool, str]] = []


def test(group: str, name: str, passed: bool, detail: str = ""):
    mark = "PASS" if passed else "FAIL"
    results.append((group, name, passed, detail))
    print(f"  [{mark}] {group}/{name}" + (f" — {detail}" if detail else ""))


def wait_for(url: str, max_wait: float = 10) -> bool:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            http("GET", url)
            return True
        except Exception:
            time.sleep(0.3)
    return False


# ---------------------------------------------------------------------------
# Start Data Service only
# ---------------------------------------------------------------------------

procs: list[subprocess.Popen] = []
PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def start_data_service():
    global procs

    env = os.environ.copy()
    ds_script = os.path.join(PROJECT, "data-service", "app.py")
    env["DATA_SCHEMA_PATH"] = os.path.join(PROJECT, "data-service", "schema.sql")
    env["DATA_DB_PATH"] = os.path.join(PROJECT, "data-service", "test_smoke.db")
    procs.append(subprocess.Popen(
        [sys.executable, ds_script],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ))

    if not wait_for(f"{DATA_URL}/healthz"):
        print("FATAL: Data Service failed to start")
        stop_services()
        sys.exit(1)
    print("Data Service started on :37588")


def stop_services():
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
    db_path = os.path.join(PROJECT, "data-service", "test_smoke.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# C Tests — Data Service
# ---------------------------------------------------------------------------

def run_c_tests():
    print("\n== C Tests: Data Service ==")

    # C01: POST with posterImage (PNG) → 201, posterMimeType/posterSize filled
    body = {
        "title": "With Poster",
        "description": "x" * 40,
        "posterFilename": "poster.png",
        "posterImage": MINIMAL_PNG,
        "posterMimeType": "image/png",
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    rec = as_json(bd) or {}
    poster_id = rec.get("id")
    test("C", "POST with posterImage → 201",
         st == 201 and rec.get("posterMimeType") == "image/png"
         and isinstance(rec.get("posterSize"), int) and rec.get("posterSize") > 0
         and "posterImage" not in rec,
         f"status={st} mime={rec.get('posterMimeType')} size={rec.get('posterSize')}")

    # C02: POST without posterImage → backward compat
    body = {
        "title": "No Poster",
        "description": "y" * 40,
        "posterFilename": "poster.jpg",
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    rec = as_json(bd) or {}
    no_poster_id = rec.get("id")
    test("C", "POST without posterImage → backward compat",
         st == 201 and rec.get("posterMimeType") is None and rec.get("posterSize") is None
         and "posterImage" not in rec,
         f"mime={rec.get('posterMimeType')} size={rec.get('posterSize')}")

    # C03: POST with posterImage=null → same as no image
    body = {
        "title": "Null Poster",
        "description": "z" * 40,
        "posterFilename": "p.jpg",
        "posterImage": None,
        "posterMimeType": None,
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    rec = as_json(bd) or {}
    test("C", "POST posterImage=null → null fields",
         st == 201 and rec.get("posterMimeType") is None and rec.get("posterSize") is None)

    # C04: POST with invalid base64 → 400
    body = {
        "title": "Bad",
        "description": "a" * 40,
        "posterFilename": "p.jpg",
        "posterImage": "not-valid-base64!!!",
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    j = as_json(bd) or {}
    test("C", "POST invalid base64 → 400 BAD_REQUEST",
         st == 400 and j.get("error", {}).get("code") == "BAD_REQUEST",
         f"status={st} code={j.get('error', {}).get('code')}")

    # C05: POST with posterMimeType="image/gif" → 400
    body = {
        "title": "Gif",
        "description": "b" * 40,
        "posterFilename": "p.jpg",
        "posterMimeType": "image/gif",
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    j = as_json(bd) or {}
    test("C", "POST posterMimeType=image/gif → 400 BAD_REQUEST",
         st == 400 and j.get("error", {}).get("code") == "BAD_REQUEST",
         f"status={st} code={j.get('error', {}).get('code')}")

    # C06: POST with posterImage="" (empty string) → treated as no upload
    body = {
        "title": "Empty",
        "description": "c" * 40,
        "posterFilename": "p.jpg",
        "posterImage": "",
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    rec = as_json(bd) or {}
    test("C", "POST posterImage='' → null fields",
         st == 201 and rec.get("posterMimeType") is None and rec.get("posterSize") is None)

    # C07: POST with posterImage type error (int) → 400
    body = {
        "title": "IntImg",
        "description": "d" * 40,
        "posterFilename": "p.jpg",
        "posterImage": 123,
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    j = as_json(bd) or {}
    test("C", "POST posterImage=123 → 400 BAD_REQUEST",
         st == 400 and j.get("error", {}).get("code") == "BAD_REQUEST")

    # C08: GET existing record → includes posterMimeType/posterSize, no posterImage
    if poster_id:
        st, _, bd = get(f"{DATA_URL}/submissions/{poster_id}")
        rec = as_json(bd) or {}
        test("C", "GET record with poster → has mimeType/size, no posterImage",
             st == 200 and rec.get("posterMimeType") == "image/png"
             and isinstance(rec.get("posterSize"), int) and "posterImage" not in rec)

    # C09: GET record without poster → null mimeType/size
    if no_poster_id:
        st, _, bd = get(f"{DATA_URL}/submissions/{no_poster_id}")
        rec = as_json(bd) or {}
        test("C", "GET record without poster → null mimeType/size",
             st == 200 and rec.get("posterMimeType") is None and rec.get("posterSize") is None)

    # C10: GET /submissions/{id}/poster with image → 200 bytes
    if poster_id:
        st, hd, bd = get(f"{DATA_URL}/submissions/{poster_id}/poster")
        ct = next((v for k, v in hd.items() if k.lower() == "content-type"), "")
        test("C", "GET /poster with image → 200 + image/png bytes",
             st == 200 and "image/png" in ct and len(bd) > 0,
             f"status={st} ct={ct} len={len(bd)}")

    # C11: GET /submissions/{id}/poster without image → 404 "has no poster"
    if no_poster_id:
        st, _, bd = get(f"{DATA_URL}/submissions/{no_poster_id}/poster")
        j = as_json(bd) or {}
        msg = j.get("error", {}).get("message", "")
        test("C", "GET /poster no image → 404 'has no poster'",
             st == 404 and "has no poster" in msg,
             f"status={st} msg={msg}")

    # C12: GET /submissions/{id}/poster nonexistent → 404 "not found"
    fake_id = str(uuid.uuid4())
    st, _, bd = get(f"{DATA_URL}/submissions/{fake_id}/poster")
    j = as_json(bd) or {}
    msg = j.get("error", {}).get("message", "")
    test("C", "GET /poster nonexistent → 404 'not found'",
         st == 404 and "not found" in msg,
         f"status={st} msg={msg}")

    # C13: PATCH with posterImage in body → 400 BAD_REQUEST
    if no_poster_id:
        st, _, bd = patch_json(f"{DATA_URL}/submissions/{no_poster_id}",
                               {"status": "READY", "note": "ok", "posterImage": "abc"})
        j = as_json(bd) or {}
        msg = j.get("error", {}).get("message", "")
        test("C", "PATCH with posterImage → 400 BAD_REQUEST",
             st == 400 and "poster" in msg,
             f"status={st} msg={msg}")

    # C14: PATCH with posterMimeType in body → 400 BAD_REQUEST
    if no_poster_id:
        st, _, bd = patch_json(f"{DATA_URL}/submissions/{no_poster_id}",
                               {"status": "READY", "note": "ok", "posterMimeType": "image/png"})
        j = as_json(bd) or {}
        msg = j.get("error", {}).get("message", "")
        test("C", "PATCH with posterMimeType → 400 BAD_REQUEST",
             st == 400 and "poster" in msg,
             f"status={st} msg={msg}")

    # C15: PATCH normal → 200, response includes posterMimeType/posterSize
    if poster_id:
        st, _, bd = patch_json(f"{DATA_URL}/submissions/{poster_id}",
                               {"status": "READY", "note": "looks great"})
        rec = as_json(bd) or {}
        test("C", "PATCH normal → 200 + posterMimeType/posterSize in response",
             st == 200 and rec.get("posterMimeType") == "image/png"
             and isinstance(rec.get("posterSize"), int),
             f"status={st} mime={rec.get('posterMimeType')} size={rec.get('posterSize')}")

    # C16: MIME inference from filename (posterMimeType=null, filename has .jpg)
    body = {
        "title": "Infer MIME",
        "description": "e" * 40,
        "posterFilename": "my-poster.JPG",
        "posterImage": MINIMAL_JPEG,
        "posterMimeType": None,
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    rec = as_json(bd) or {}
    test("C", "MIME inference from filename (.JPG → image/jpeg)",
         st == 201 and rec.get("posterMimeType") == "image/jpeg",
         f"mime={rec.get('posterMimeType')}")

    # C17: POST with posterImage array → 400
    body = {
        "title": "Array",
        "description": "f" * 40,
        "posterFilename": "p.jpg",
        "posterImage": [1, 2, 3],
    }
    st, _, bd = post_json(f"{DATA_URL}/submissions", body)
    j = as_json(bd) or {}
    test("C", "POST posterImage=[] → 400 BAD_REQUEST",
         st == 400 and j.get("error", {}).get("code") == "BAD_REQUEST")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Starting Data Service...")
    start_data_service()
    print("Ready.\n")

    try:
        run_c_tests()
    finally:
        stop_services()

    passed = sum(1 for _, _, p, _ in results if p)
    failed = len(results) - passed
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{len(results)} passed, {failed} failed")
    if failed:
        print("\nFailed tests:")
        for g, n, p, d in results:
            if not p:
                print(f"  [{g}] {n} — {d}")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
