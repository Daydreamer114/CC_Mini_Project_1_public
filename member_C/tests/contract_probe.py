"""Contract conformance probe for C's Data Service + Result Update Function.

Covers every message permitted by `API_CONTRACT.md` that can legitimately
reach C's components, plus defensive edges the contract explicitly calls out.

Four groups of tests:

    A/B  — inbound to Data Service from other teammates
           A-Workflow: POST /submissions     (A01–A21)
           B-Workflow: GET  /submissions/{id} (B01–B05)
           (A-Presentation's degraded GET is the same shape as B.)
           GET /healthz and forbidden methods (C/D groups)

    P    — direct PATCH /submissions/{id} on the Data Service
           (exercises what C's own Result Update Function does to C's Data
           Service, in isolation, without the function in the loop)

    F    — Result Update Function URL end-to-end (valid + all 400 branches,
           404 pass-through, envelope shape, idempotent behaviour)

    X    — end-to-end collaboration: Data Service record ⇄ Function URL
           (POST → invoke function → GET reflects the terminal state)

Usage:
    python tests/contract_probe.py [DATA_URL] [FUNCTION_URL]

    Default DATA_URL     = REPLACED_FOR_SAFETY_AND_ANONYMITY
    Default FUNCTION_URL = REPLACED_FOR_SAFETY_AND_ANONYMITY

No third-party deps — stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


TIMEOUT = 15


def http(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.getheaders()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()
    except urllib.error.URLError as e:
        return 0, {}, f"URLERROR: {e.reason}".encode("utf-8")


def post_json(base: str, path: str, body: Any, *, ctype: str | None = "application/json") -> tuple[int, dict, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {}
    if ctype is not None:
        h["Content-Type"] = ctype
    return http("POST", base + path, body=data, headers=h)


def post_raw(base: str, path: str, raw: bytes, *, ctype: str) -> tuple[int, dict, bytes]:
    return http("POST", base + path, body=raw, headers={"Content-Type": ctype})


def patch_json(base: str, path: str, body: Any, *, ctype: str | None = "application/json") -> tuple[int, dict, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {}
    if ctype is not None:
        h["Content-Type"] = ctype
    return http("PATCH", base + path, body=data, headers=h)


def patch_raw(base: str, path: str, raw: bytes, *, ctype: str = "application/json") -> tuple[int, dict, bytes]:
    return http("PATCH", base + path, body=raw, headers={"Content-Type": ctype})


def get(base: str, path: str) -> tuple[int, dict, bytes]:
    return http("GET", base + path)


def method(base: str, verb: str, path: str, body: bytes | None = None) -> tuple[int, dict, bytes]:
    h = {"Content-Type": "application/json"} if body else {}
    return http(verb, base + path, body=body, headers=h)


def invoke_function(url: str, payload: Any, *, ctype: str | None = "application/json") -> tuple[int, dict, bytes]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {}
    if ctype is not None:
        h["Content-Type"] = ctype
    return http("POST", url, body=data, headers=h)


def invoke_function_raw(url: str, raw: bytes, *, ctype: str = "application/json") -> tuple[int, dict, bytes]:
    return http("POST", url, body=raw, headers={"Content-Type": ctype})


# --------------------------------------------------------------------------- #
# Result collection
# --------------------------------------------------------------------------- #


@dataclass
class Case:
    cid: str
    clause: str
    sender: str
    intent: str
    expected: str
    actual_status: int = 0
    actual_body: str = ""
    passed: bool = False
    note: str = ""


results: list[Case] = []


def record(case: Case) -> None:
    results.append(case)


def short(s: str, n: int = 120) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
UUID_LC_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def decode_err(payload: bytes) -> tuple[str | None, str | None]:
    try:
        j = json.loads(payload.decode("utf-8"))
    except Exception:
        return None, None
    if isinstance(j, dict) and isinstance(j.get("error"), dict):
        e = j["error"]
        return e.get("code"), e.get("message")
    return None, None


def as_json(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def seed_record(base: str, *, title="seed", description="d" * 40, poster="poster.jpg") -> str | None:
    st, _, bd = post_json(base, "/submissions", {"title": title, "description": description, "posterFilename": poster})
    rec = as_json(bd) or {}
    return rec.get("id") if st == 201 else None


# --------------------------------------------------------------------------- #
# A / B / C / D groups — inbound to Data Service from other teammates
# --------------------------------------------------------------------------- #


def run_inbound(base: str) -> str | None:
    print(f"== Probing Data Service at {base} ==\n")

    reference_id: str | None = None

    # ---------- A. POST /submissions (Workflow Service) ----------

    body = {"title": "My Event", "description": "x" * 40, "posterFilename": "poster.png"}
    st, hd, bd = post_json(base, "/submissions", body)
    rec = as_json(bd) or {}
    ok = (
        st == 201
        and isinstance(rec, dict)
        and UUID_LC_RE.match(rec.get("id", "") or "")
        and rec.get("status") == "PENDING"
        and rec.get("note") is None
        and ISO_Z_RE.match(rec.get("createdAt", "") or "")
        and ISO_Z_RE.match(rec.get("updatedAt", "") or "")
        and rec.get("title") == "My Event"
        and rec.get("description") == "x" * 40
        and rec.get("posterFilename") == "poster.png"
    )
    reference_id = rec.get("id") if isinstance(rec, dict) else None
    has_location = any(k.lower() == "location" for k in hd.keys())
    record(Case("A01", "§6.2.1 / §3.1", "B-Workflow", "Happy path create",
                "201 + full record, status=PENDING, note=null, lowercase UUID, ISO-8601 Z",
                st, short(bd.decode("utf-8", "replace")), ok,
                "Location header present" if has_location else "Location header missing (optional)"))

    injected = {
        "title": "Inject Test",
        "description": "y" * 40,
        "posterFilename": "a.jpg",
        "id": "deadbeef-dead-4beef-dead-deadbeefdead",
        "status": "READY",
        "note": "injected",
        "createdAt": "1999-01-01T00:00:00Z",
        "updatedAt": "1999-01-01T00:00:00Z",
        "details": {"foo": "bar"},
    }
    st, _, bd = post_json(base, "/submissions", injected)
    rec = as_json(bd) or {}
    ok = (
        st == 201
        and UUID_LC_RE.match(rec.get("id", "") or "")
        and rec.get("id") != injected["id"]
        and rec.get("status") == "PENDING"
        and rec.get("note") is None
        and rec.get("createdAt") != injected["createdAt"]
        and "details" not in rec
    )
    record(Case("A02", "§2.4 / §6.6 / §3.1", "B-Workflow", "Caller-provided id/status/createdAt must be ignored",
                "201; server mints its own id/status/timestamps; `details` dropped",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_json(base, "/submissions", {})
    rec = as_json(bd) or {}
    ok = (
        st == 201
        and rec.get("status") == "PENDING"
        and rec.get("title") == ""
        and rec.get("description") == ""
        and rec.get("posterFilename") == ""
    )
    record(Case("A03", "§6.2.1 / §3.2 / §15.6", "B-Workflow",
                "Empty body creates record (INCOMPLETE decision belongs to Processing)",
                "201; all three fields stored as empty strings; status=PENDING",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_json(base, "/submissions", {"description": "z" * 40, "posterFilename": "p.jpeg"})
    rec = as_json(bd) or {}
    ok = st == 201 and rec.get("title") == "" and rec.get("status") == "PENDING"
    record(Case("A04", "§6.2.1", "B-Workflow", "Partial body (missing title) is still accepted",
                "201; missing field defaults to empty string",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_json(base, "/submissions", {"title": "", "description": "", "posterFilename": ""})
    rec = as_json(bd) or {}
    ok = st == 201 and rec.get("status") == "PENDING"
    record(Case("A05", "§15.6 / §6.6", "B-Workflow",
                "Empty-string fields must NOT be rejected by Data Service",
                "201; status=PENDING",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_json(base, "/submissions", {"title": "   ", "description": "   ", "posterFilename": "   "})
    rec = as_json(bd) or {}
    ok = (
        st == 201
        and rec.get("title") == "   "
        and rec.get("description") == "   "
        and rec.get("posterFilename") == "   "
    )
    record(Case("A06", "§6.2.1 / §3.2", "B-Workflow",
                "Whitespace fields stored verbatim (no strip at Data layer)",
                "201; whitespace preserved",
                st, short(bd.decode("utf-8", "replace")), ok))

    body_u = {"title": "文化节 2026 🎉", "description": "一段至少三十字符的中文描述用于测试 UTF-8 保存。", "posterFilename": "海报.PNG"}
    st, _, bd = post_json(base, "/submissions", body_u, ctype="application/json; charset=utf-8")
    rec = as_json(bd) or {}
    ok = st == 201 and rec.get("title") == body_u["title"] and rec.get("posterFilename") == body_u["posterFilename"]
    record(Case("A07", "§2.1", "B-Workflow", "UTF-8 preserved round-trip",
                "201; non-ASCII fields returned identically",
                st, short(bd.decode("utf-8", "replace")), ok))

    long_body = {"title": "t", "description": "d" * 5000, "posterFilename": "p.jpg"}
    st, _, bd = post_json(base, "/submissions", long_body)
    rec = as_json(bd) or {}
    ok = st == 201 and len(rec.get("description", "")) == 5000
    record(Case("A08", "§6.2.1", "B-Workflow", "Large string (5000 chars) accepted and stored intact",
                "201; description length preserved",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_json(base, "/submissions", {"title": None, "description": "ok", "posterFilename": "p.jpg"})
    code, _ = decode_err(bd)
    rec = as_json(bd) if st == 201 else None
    if st == 201 and isinstance(rec, dict) and rec.get("title") == "":
        ok, note = True, "null treated as missing -> stored as empty string"
    elif st == 400 and code == "BAD_REQUEST":
        ok, note = True, "null rejected as 'not a string' (strict reading of §3.2)"
    else:
        ok, note = False, "expected 201 with title='' OR 400 BAD_REQUEST"
    record(Case("A09", "§3.2", "B-Workflow", "JSON null for `title`",
                "201(title='') or 400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok, note))

    for cid, bad in [("A10", 123), ("A11", True), ("A12", []), ("A13", {"x": 1})]:
        st, _, bd = post_json(base, "/submissions", {"title": bad, "description": "ok", "posterFilename": "p.jpg"})
        code, _ = decode_err(bd)
        ok = st == 400 and code == "BAD_REQUEST"
        record(Case(cid, "§6.2.1 / §3.2", "B-Workflow",
                    f"title is {type(bad).__name__}",
                    "400 BAD_REQUEST",
                    st, short(bd.decode("utf-8", "replace")), ok))

    for cid, bad in [("A14", ["not", "an", "object"]), ("A15", "just a string"), ("A16", 42)]:
        st, _, bd = post_json(base, "/submissions", bad)
        code, _ = decode_err(bd)
        ok = st == 400 and code == "BAD_REQUEST"
        record(Case(cid, "§6.2.1", "B-Workflow",
                    f"Body is JSON {type(bad).__name__}",
                    "400 BAD_REQUEST",
                    st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = http("POST", base + "/submissions", body=b"", headers={"Content-Type": "application/json"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("A17", "§6.2.1", "B-Workflow", "Empty body",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_raw(base, "/submissions", b"{not valid json]", ctype="application/json")
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("A18", "§6.2.1 / §2.6", "B-Workflow", "Malformed JSON",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = post_raw(base, "/submissions",
                         b"title=x&description=y&posterFilename=z.jpg",
                         ctype="application/x-www-form-urlencoded")
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("A19", "§2.1 / §6.2.1", "B-Workflow", "application/x-www-form-urlencoded body",
                "400 BAD_REQUEST (only Presentation may use form, never Data)",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = http("POST", base + "/submissions",
                     body=b'{"title":"x","description":"d","posterFilename":"p.jpg"}')
    if st == 201:
        ok, note = True, "server is permissive: parsed JSON without Content-Type"
    elif st == 400:
        ok, note = True, "server is strict: requires Content-Type header"
    else:
        ok, note = False, "unexpected status"
    record(Case("A20", "§2.1", "B-Workflow", "POST without Content-Type",
                "201 or 400 BAD_REQUEST (implementation choice)",
                st, short(bd.decode("utf-8", "replace")), ok, note))

    body_dup = {"title": "dup", "description": "d" * 40, "posterFilename": "a.jpg"}
    st1, _, bd1 = post_json(base, "/submissions", body_dup)
    st2, _, bd2 = post_json(base, "/submissions", body_dup)
    r1 = as_json(bd1) or {}
    r2 = as_json(bd2) or {}
    ok = (
        st1 == 201 and st2 == 201
        and UUID_LC_RE.match(r1.get("id", "") or "")
        and UUID_LC_RE.match(r2.get("id", "") or "")
        and r1.get("id") != r2.get("id")
    )
    record(Case("A21", "§11.3", "B-Workflow", "POST /submissions is non-idempotent",
                "Two identical POSTs -> two distinct UUID ids",
                st2, f"id1={r1.get('id')} id2={r2.get('id')}", ok))

    # ---------- B. GET /submissions/{id} ----------

    if reference_id:
        st, _, bd = get(base, f"/submissions/{reference_id}")
        rec = as_json(bd) or {}
        ok = (
            st == 200
            and rec.get("id") == reference_id
            and rec.get("status") == "PENDING"
            and rec.get("note") is None
            and ISO_Z_RE.match(rec.get("createdAt", "") or "")
            and ISO_Z_RE.match(rec.get("updatedAt", "") or "")
        )
        record(Case("B01", "§6.2.2 / §3.1", "A-Presentation / B-Workflow / B-Processing",
                    "GET an existing submission",
                    "200; full record; PENDING; ISO-8601 Z timestamps",
                    st, short(bd.decode("utf-8", "replace")), ok))
    else:
        record(Case("B01", "§6.2.2", "A/B", "GET an existing submission",
                    "200; full record", 0, "skipped (A01 did not produce id)", False))

    missing_id = str(uuid.uuid4())
    st, _, bd = get(base, f"/submissions/{missing_id}")
    code, _ = decode_err(bd)
    ok = st == 404 and code == "NOT_FOUND"
    record(Case("B02", "§6.2.2", "A/B", "GET non-existent UUID",
                "404 NOT_FOUND",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = get(base, "/submissions/not-a-uuid")
    code, _ = decode_err(bd)
    if st == 404 and code == "NOT_FOUND":
        ok, note = True, "rejected as 404 (treating unknown id as not-found)"
    elif st == 400 and code == "BAD_REQUEST":
        ok, note = True, "rejected as 400 (id format validated)"
    else:
        ok, note = False, "expected 404 NOT_FOUND or 400 BAD_REQUEST"
    record(Case("B03", "§6.2.2", "A/B", "GET with malformed id",
                "404 NOT_FOUND or 400 BAD_REQUEST (contract allows either)",
                st, short(bd.decode("utf-8", "replace")), ok, note))

    st, _, bd = get(base, "/submissions/")
    ok = st in (404, 405)
    record(Case("B04", "§6.3", "A/B", "GET /submissions/ (empty id)",
                "404 NOT_FOUND or 405 METHOD_NOT_ALLOWED",
                st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = get(base, "/submissions")
    ok = st in (404, 405)
    record(Case("B05", "§6.3", "A/B", "GET /submissions (list) not provided",
                "404 NOT_FOUND or 405 METHOD_NOT_ALLOWED",
                st, short(bd.decode("utf-8", "replace")), ok))

    # ---------- C. GET /healthz ----------

    t0 = time.time()
    st, _, bd = get(base, "/healthz")
    elapsed = time.time() - t0
    body_j = as_json(bd) or {}
    ok = st == 200 and body_j.get("ok") is True and elapsed < 1.0
    record(Case("C01", "§6.2.4", "A/B", "GET /healthz",
                '200 {"ok": true} within 1 second',
                st, f"{short(bd.decode('utf-8', 'replace'))} elapsed={elapsed:.3f}s", ok))

    st, _, bd = method(base, "POST", "/healthz", body=b"{}")
    code, _ = decode_err(bd)
    ok = st in (405, 404) and (code == "METHOD_NOT_ALLOWED" or code == "NOT_FOUND" or code is None)
    record(Case("C02", "§6.2.4 / §12", "A/B", "POST /healthz (wrong method)",
                "405 METHOD_NOT_ALLOWED",
                st, short(bd.decode("utf-8", "replace")), ok))

    # ---------- D. Method-not-allowed / forbidden routes ----------

    if reference_id:
        st, _, bd = method(base, "DELETE", f"/submissions/{reference_id}")
        ok = st in (405, 404)
        record(Case("D01", "§6.3 / §12", "A/B", "DELETE /submissions/{id}",
                    "405 METHOD_NOT_ALLOWED (not provided)",
                    st, short(bd.decode("utf-8", "replace")), ok))

        st, _, bd = method(base, "PUT", f"/submissions/{reference_id}", body=b"{}")
        ok = st in (405, 404)
        record(Case("D02", "§6.3 / §12", "A/B", "PUT /submissions/{id}",
                    "405 METHOD_NOT_ALLOWED (not provided)",
                    st, short(bd.decode("utf-8", "replace")), ok))

    st, _, bd = get(base, "/not-a-real-route")
    code, _ = decode_err(bd)
    ok = st == 404
    record(Case("D03", "§2.6", "A/B", "Unknown route error envelope",
                "404 with error envelope {error:{code,message}}",
                st, short(bd.decode("utf-8", "replace")), ok,
                f"envelope-code={code}"))

    return reference_id


# --------------------------------------------------------------------------- #
# P group — direct PATCH /submissions/{id} (Data Service in isolation)
# --------------------------------------------------------------------------- #


def run_patch_direct(base: str) -> None:
    print(f"\n== Probing Data Service PATCH {base}/submissions/* ==\n")

    # P01 Happy path to READY
    sid = seed_record(base, description="d" * 40, poster="poster.jpg")
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": "all good"})
    rec = as_json(bd) or {}
    ok = (
        st == 200
        and rec.get("status") == "READY"
        and rec.get("note") == "all good"
        and ISO_Z_RE.match(rec.get("updatedAt", "") or "")
        and rec.get("updatedAt") >= rec.get("createdAt", "")
    )
    record(Case("P01", "§6.2.3 / §3.3", "C-ResultUpdate", "PATCH to READY",
                "200; status=READY; note updated; updatedAt >= createdAt",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P02 Idempotent PATCH (strict §11.1)
    sid = seed_record(base)
    _, _, bd_first = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": "x"})
    first = as_json(bd_first) or {}
    time.sleep(1.2)  # ensure timestamp would differ if incorrectly refreshed
    st, _, bd_second = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": "x"})
    second = as_json(bd_second) or {}
    ok = (
        st == 200
        and first.get("updatedAt") == second.get("updatedAt")
        and first.get("status") == second.get("status") == "READY"
    )
    record(Case("P02", "§11.1 / §6.2.3", "C-ResultUpdate",
                "Strict idempotency: same (status,note) twice -> updatedAt NOT refreshed",
                "200; updatedAt equal to first call",
                st,
                f"first.updatedAt={first.get('updatedAt')} second.updatedAt={second.get('updatedAt')}",
                ok))

    # P03 Last-Write-Wins: change note, updatedAt must refresh
    sid = seed_record(base)
    _, _, bd1 = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": "a"})
    first = as_json(bd1) or {}
    time.sleep(1.2)
    st, _, bd2 = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": "b"})
    second = as_json(bd2) or {}
    ok = (
        st == 200
        and second.get("note") == "b"
        and second.get("updatedAt") > first.get("updatedAt", "")
    )
    record(Case("P03", "§11.1", "C-ResultUpdate", "Different (status,note) -> updatedAt refreshed (LWW)",
                "200; note='b'; updatedAt2 > updatedAt1",
                st,
                f"first.updatedAt={first.get('updatedAt')} second.updatedAt={second.get('updatedAt')}",
                ok))

    # P04 Terminal -> Terminal allowed
    sid = seed_record(base)
    patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": "ok"})
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": "INCOMPLETE", "note": "missing title"})
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "INCOMPLETE"
    record(Case("P04", "§3.3 / §11.1", "C-ResultUpdate", "READY -> INCOMPLETE allowed (re-verdict)",
                "200; status switched to INCOMPLETE",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P05 NEEDS REVISION (exact spacing)
    sid = seed_record(base)
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": "NEEDS REVISION", "note": "too short"})
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "NEEDS REVISION"
    record(Case("P05", "§2.5 / §6.2.3", "C-ResultUpdate", '"NEEDS REVISION" with single ASCII space',
                "200; status preserved exactly",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P06 status='PENDING' rejected
    sid = seed_record(base)
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": "PENDING", "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "INVALID_STATUS_TRANSITION"
    record(Case("P06", "§3.3 / §11 / §15.5", "C-ResultUpdate",
                "Reject rewrite to PENDING",
                "400 INVALID_STATUS_TRANSITION",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P07 invalid enum values
    for cid, bad_status in [
        ("P07a", "ready"),            # lowercase
        ("P07b", "NEEDS_REVISION"),   # underscore
        ("P07c", "needs revision"),   # lowercase
        ("P07d", "READY!"),           # extra punct
        ("P07e", "ACCEPTED"),         # garbage enum
    ]:
        sid = seed_record(base)
        st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": bad_status, "note": "x"})
        code, _ = decode_err(bd)
        ok = st == 400 and code == "INVALID_STATUS"
        record(Case(cid, "§2.5 / §6.2.3", "C-ResultUpdate",
                    f'status="{bad_status}" rejected',
                    "400 INVALID_STATUS",
                    st, short(bd.decode("utf-8", "replace")), ok))

    # P08 status non-string types
    for cid, bad_status in [("P08a", None), ("P08b", 123), ("P08c", True), ("P08d", []), ("P08e", {})]:
        sid = seed_record(base)
        st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": bad_status, "note": "x"})
        code, _ = decode_err(bd)
        ok = st == 400 and code == "INVALID_STATUS"
        record(Case(cid, "§6.2.3", "C-ResultUpdate",
                    f"status is {type(bad_status).__name__}",
                    "400 INVALID_STATUS",
                    st, short(bd.decode("utf-8", "replace")), ok))

    # P09 note type errors
    sid = seed_record(base)
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": 123})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("P09", "§6.2.3", "C-ResultUpdate", "note is integer",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P10 note=null allowed (§6.2.3 note 可为 string 或 null)
    sid = seed_record(base)
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"status": "READY", "note": None})
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "READY" and rec.get("note") is None
    record(Case("P10", "§6.2.3", "C-ResultUpdate", "note=null allowed",
                "200; note stored as null",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P11 body non-JSON
    sid = seed_record(base)
    st, _, bd = patch_raw(base, f"/submissions/{sid}", b"not json", ctype="application/json")
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("P11", "§6.2.3", "C-ResultUpdate", "Malformed JSON",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P12 body is JSON but not object
    sid = seed_record(base)
    st, _, bd = patch_json(base, f"/submissions/{sid}", ["status", "READY"])
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("P12", "§6.2.3", "C-ResultUpdate", "body is JSON array",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P13 missing status -> INVALID_STATUS
    sid = seed_record(base)
    st, _, bd = patch_json(base, f"/submissions/{sid}", {"note": "just a note"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "INVALID_STATUS"
    record(Case("P13", "§6.2.3", "C-ResultUpdate", "missing status field",
                "400 INVALID_STATUS",
                st, short(bd.decode("utf-8", "replace")), ok))

    # P14 non-existent id
    st, _, bd = patch_json(base, f"/submissions/{uuid.uuid4()}", {"status": "READY", "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 404 and code == "NOT_FOUND"
    record(Case("P14", "§6.2.3", "C-ResultUpdate", "PATCH non-existent id",
                "404 NOT_FOUND",
                st, short(bd.decode("utf-8", "replace")), ok))


# --------------------------------------------------------------------------- #
# F group — Result Update Function URL
# --------------------------------------------------------------------------- #


def run_function(base_data: str, fn_url: str) -> None:
    print(f"\n== Probing Result Update Function at {fn_url} ==\n")

    # F01 Happy path: READY
    sid = seed_record(base_data, description="ok" * 20, poster="poster.jpg")
    st, hd, bd = invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "looks great"})
    rec = as_json(bd) or {}
    ct = next((v for k, v in hd.items() if k.lower() == "content-type"), "")
    ok = (
        st == 200
        and rec.get("id") == sid
        and rec.get("status") == "READY"
        and rec.get("note") == "looks great"
        and "application/json" in ct.lower()
    )
    record(Case("F01", "§9.4 / §B.3", "C-ResultUpdate", "Happy path: status=READY",
                "200; body is full submission; status=READY; JSON Content-Type",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F02 Happy path: NEEDS REVISION
    sid = seed_record(base_data)
    st, _, bd = invoke_function(fn_url,
        {"submissionId": sid, "status": "NEEDS REVISION",
         "note": "Description must be at least 30 characters long."})
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "NEEDS REVISION"
    record(Case("F02", "§9.4 / §2.5", "C-ResultUpdate", "Happy path: NEEDS REVISION (exact spelling)",
                "200; status='NEEDS REVISION'",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F03 Happy path: INCOMPLETE
    sid = seed_record(base_data)
    st, _, bd = invoke_function(fn_url,
        {"submissionId": sid, "status": "INCOMPLETE", "note": "Missing required field(s): title."})
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "INCOMPLETE"
    record(Case("F03", "§9.4", "C-ResultUpdate", "Happy path: INCOMPLETE",
                "200; status='INCOMPLETE'",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F04 submissionId missing
    st, _, bd = invoke_function(fn_url, {"status": "READY", "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("F04", "§9.4 / §9.6", "C-ResultUpdate", "submissionId missing",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F05 submissionId empty string
    st, _, bd = invoke_function(fn_url, {"submissionId": "", "status": "READY", "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("F05", "§9.4", "C-ResultUpdate", "submissionId empty string",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F06 submissionId non-string
    for cid, bad_id in [("F06a", 123), ("F06b", None), ("F06c", []), ("F06d", {})]:
        st, _, bd = invoke_function(fn_url, {"submissionId": bad_id, "status": "READY", "note": "x"})
        code, _ = decode_err(bd)
        ok = st == 400 and code == "BAD_REQUEST"
        record(Case(cid, "§9.4", "C-ResultUpdate",
                    f"submissionId is {type(bad_id).__name__}",
                    "400 BAD_REQUEST",
                    st, short(bd.decode("utf-8", "replace")), ok))

    # F07 status missing
    sid = seed_record(base_data)
    st, _, bd = invoke_function(fn_url, {"submissionId": sid, "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "INVALID_STATUS"
    record(Case("F07", "§9.4 / §9.6", "C-ResultUpdate", "status missing",
                "400 INVALID_STATUS",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F08 status='PENDING' must be rejected by function itself
    sid = seed_record(base_data)
    st, _, bd = invoke_function(fn_url, {"submissionId": sid, "status": "PENDING", "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 400 and code == "INVALID_STATUS"
    record(Case("F08", "§9.4.2 / §9.8", "C-ResultUpdate",
                "status='PENDING' rejected before hitting Data Service",
                "400 INVALID_STATUS",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F09 invalid enum
    sid = seed_record(base_data)
    for cid, bad_status in [
        ("F09a", "ready"), ("F09b", "NEEDS_REVISION"), ("F09c", "needs revision"),
        ("F09d", "FOO"), ("F09e", 123), ("F09f", None), ("F09g", True),
    ]:
        st, _, bd = invoke_function(fn_url, {"submissionId": sid, "status": bad_status, "note": "x"})
        code, _ = decode_err(bd)
        ok = st == 400 and code == "INVALID_STATUS"
        record(Case(cid, "§9.4 / §2.5", "C-ResultUpdate",
                    f'status="{bad_status}" rejected',
                    "400 INVALID_STATUS",
                    st, short(bd.decode("utf-8", "replace")), ok))

    # F10 note type errors
    sid = seed_record(base_data)
    for cid, bad_note in [("F10a", 123), ("F10b", True), ("F10c", []), ("F10d", {})]:
        st, _, bd = invoke_function(fn_url,
            {"submissionId": sid, "status": "READY", "note": bad_note})
        code, _ = decode_err(bd)
        ok = st == 400 and code == "BAD_REQUEST"
        record(Case(cid, "§9.4", "C-ResultUpdate",
                    f"note is {type(bad_note).__name__}",
                    "400 BAD_REQUEST",
                    st, short(bd.decode("utf-8", "replace")), ok))

    # F11 note=null allowed
    sid = seed_record(base_data)
    st, _, bd = invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": None})
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("note") is None
    record(Case("F11", "§9.4", "C-ResultUpdate", "note=null allowed",
                "200; note null",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F12 note missing entirely -> treated as None (allowed, since payload.get('note') is None)
    sid = seed_record(base_data)
    st, _, bd = invoke_function(fn_url, {"submissionId": sid, "status": "READY"})
    rec = as_json(bd) or {}
    if st == 200 and rec.get("note") is None:
        ok, note = True, "missing note treated as null (200)"
    elif st == 400:
        code, _ = decode_err(bd)
        if code in ("BAD_REQUEST",):
            ok, note = True, "missing note rejected as 400 (stricter choice)"
        else:
            ok, note = False, f"unexpected code {code}"
    else:
        ok, note = False, "unexpected response"
    record(Case("F12", "§9.4", "C-ResultUpdate", "note field absent",
                "200 (treated as null) or 400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok, note))

    # F13 404 pass-through: valid well-formed UUID, not in DB
    st, _, bd = invoke_function(fn_url, {"submissionId": str(uuid.uuid4()), "status": "READY", "note": "x"})
    code, _ = decode_err(bd)
    ok = st == 404 and code == "NOT_FOUND"
    record(Case("F13", "§9.4 / §9.6", "C-ResultUpdate",
                "submissionId valid shape but not found -> Data Service 404 passed through",
                "404 NOT_FOUND",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F14 malformed JSON body
    st, _, bd = invoke_function_raw(fn_url, b"{not json]", ctype="application/json")
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("F14", "§9.4 / §B.2.1", "C-ResultUpdate", "Malformed JSON body",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F15 empty body
    st, _, bd = invoke_function_raw(fn_url, b"", ctype="application/json")
    code, _ = decode_err(bd)
    ok = st == 400 and code == "BAD_REQUEST"
    record(Case("F15", "§9.4", "C-ResultUpdate", "Empty body",
                "400 BAD_REQUEST",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F16 body is JSON but not object
    for cid, bad in [("F16a", ["x"]), ("F16b", "string"), ("F16c", 42), ("F16d", True)]:
        st, _, bd = invoke_function(fn_url, bad)
        code, _ = decode_err(bd)
        ok = st == 400 and code == "BAD_REQUEST"
        record(Case(cid, "§9.4 / §B.2", "C-ResultUpdate",
                    f"body is JSON {type(bad).__name__}",
                    "400 BAD_REQUEST",
                    st, short(bd.decode("utf-8", "replace")), ok))

    # F17 idempotency: invoke function twice with same payload, updatedAt stable
    sid = seed_record(base_data)
    st1, _, bd1 = invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "idem"})
    r1 = as_json(bd1) or {}
    time.sleep(1.2)
    st2, _, bd2 = invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "idem"})
    r2 = as_json(bd2) or {}
    ok = (
        st1 == 200 and st2 == 200
        and r1.get("updatedAt") == r2.get("updatedAt")
        and r1.get("note") == "idem" and r2.get("note") == "idem"
    )
    record(Case("F17", "§11.1", "C-ResultUpdate",
                "Invoke function twice with same payload -> Data Service PATCH idempotent",
                "Both 200; updatedAt equal across calls",
                st2,
                f"t1={r1.get('updatedAt')} t2={r2.get('updatedAt')}",
                ok))

    # F18 wrong HTTP method on Function URL
    st, _, bd = http("GET", fn_url)
    # Lambda URL will typically reject non-POST with its own 4xx; we accept any 4xx.
    ok = 400 <= st < 500
    record(Case("F18", "§9.2", "C-ResultUpdate",
                "GET on Function URL (only POST permitted)",
                "4xx (Lambda URL rejects wrong method)",
                st, short(bd.decode("utf-8", "replace")), ok))

    # F19 envelope shape on error: error envelope present with code/message
    st, _, bd = invoke_function(fn_url, {"submissionId": "", "status": "READY"})
    j = as_json(bd) or {}
    ok = (
        st == 400
        and isinstance(j, dict)
        and isinstance(j.get("error"), dict)
        and isinstance(j["error"].get("code"), str)
        and isinstance(j["error"].get("message"), str)
    )
    record(Case("F19", "§2.6 / §B.3.4", "C-ResultUpdate",
                "Error body shape: {error:{code,message}}",
                "400 with valid error envelope",
                st, short(bd.decode("utf-8", "replace")), ok))


# --------------------------------------------------------------------------- #
# X group — end-to-end collaboration
# --------------------------------------------------------------------------- #


def run_e2e(base_data: str, fn_url: str) -> None:
    print(f"\n== Probing end-to-end: Function URL -> Data Service ==\n")

    # X01 POST -> invoke Function -> GET reflects READY
    sid = seed_record(base_data, description="x" * 40, poster="a.png")
    invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "ok"})
    st, _, bd = get(base_data, f"/submissions/{sid}")
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "READY" and rec.get("note") == "ok"
    record(Case("X01", "§10.1", "C-container + C-function",
                "E2E: POST -> invoke function(READY) -> GET shows READY",
                "GET returns 200 with status=READY and note='ok'",
                st, short(bd.decode("utf-8", "replace")), ok))

    # X02 E2E NEEDS REVISION
    sid = seed_record(base_data)
    invoke_function(fn_url,
        {"submissionId": sid, "status": "NEEDS REVISION", "note": "Description must be at least 30 characters long."})
    st, _, bd = get(base_data, f"/submissions/{sid}")
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "NEEDS REVISION"
    record(Case("X02", "§10.1 / §2.5", "C-container + C-function",
                "E2E: invoke NEEDS REVISION propagates with exact spelling",
                'GET status == "NEEDS REVISION"',
                st, short(bd.decode("utf-8", "replace")), ok))

    # X03 E2E INCOMPLETE
    sid = seed_record(base_data)
    invoke_function(fn_url,
        {"submissionId": sid, "status": "INCOMPLETE", "note": "Missing required field(s): title."})
    st, _, bd = get(base_data, f"/submissions/{sid}")
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "INCOMPLETE"
    record(Case("X03", "§10.1", "C-container + C-function",
                "E2E: invoke INCOMPLETE propagates",
                "GET status == INCOMPLETE",
                st, short(bd.decode("utf-8", "replace")), ok))

    # X04 E2E invalid payload -> record stays PENDING
    sid = seed_record(base_data)
    invoke_function(fn_url, {"submissionId": sid, "status": "PENDING", "note": "bad"})
    st, _, bd = get(base_data, f"/submissions/{sid}")
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "PENDING" and rec.get("note") is None
    record(Case("X04", "§9.8 / §15.5", "C-container + C-function",
                "E2E: function refuses PENDING -> Data Service NOT mutated",
                "record remains PENDING with note=null",
                st, short(bd.decode("utf-8", "replace")), ok))

    # X05 E2E: override terminal -> terminal
    sid = seed_record(base_data)
    invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "v1"})
    time.sleep(1.2)
    invoke_function(fn_url, {"submissionId": sid, "status": "INCOMPLETE", "note": "v2"})
    st, _, bd = get(base_data, f"/submissions/{sid}")
    rec = as_json(bd) or {}
    ok = st == 200 and rec.get("status") == "INCOMPLETE" and rec.get("note") == "v2"
    record(Case("X05", "§11.1 (Last-Write-Wins)", "C-container + C-function",
                "E2E: READY overridden by INCOMPLETE via function",
                "GET returns INCOMPLETE with note='v2'",
                st, short(bd.decode("utf-8", "replace")), ok))

    # X06 E2E idempotency across function invocations
    sid = seed_record(base_data)
    invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "stable"})
    st1, _, bd1 = get(base_data, f"/submissions/{sid}")
    r1 = as_json(bd1) or {}
    time.sleep(1.2)
    invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "stable"})
    st2, _, bd2 = get(base_data, f"/submissions/{sid}")
    r2 = as_json(bd2) or {}
    ok = (
        st1 == 200 and st2 == 200
        and r1.get("updatedAt") == r2.get("updatedAt")
    )
    record(Case("X06", "§11.1", "C-container + C-function",
                "E2E idempotency: double invoke leaves updatedAt stable",
                "two GETs show identical updatedAt",
                st2,
                f"t1={r1.get('updatedAt')} t2={r2.get('updatedAt')}",
                ok))

    # X07 E2E: function's returned body equals Data Service's current record
    sid = seed_record(base_data)
    st_fn, _, bd_fn = invoke_function(fn_url, {"submissionId": sid, "status": "READY", "note": "exact"})
    st_ds, _, bd_ds = get(base_data, f"/submissions/{sid}")
    fn_rec = as_json(bd_fn) or {}
    ds_rec = as_json(bd_ds) or {}
    ok = st_fn == 200 and st_ds == 200 and fn_rec == ds_rec
    record(Case("X07", "§9.3 / §B.3", "C-container + C-function",
                "E2E: function body == Data Service GET body (pure forwarder)",
                "two JSON objects strictly equal",
                st_fn,
                "equal" if fn_rec == ds_rec else f"fn={fn_rec}\nds={ds_rec}",
                ok))


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def print_report() -> int:
    passed = sum(1 for c in results if c.passed)
    failed = len(results) - passed

    print("\n== Results ==\n")
    hdr = f"{'ID':<6} {'STATUS':<7} {'PASS':<5} {'CLAUSE':<25} {'INTENT':<60}"
    print(hdr)
    print("-" * len(hdr))
    for c in results:
        mark = "OK" if c.passed else "XX"
        print(f"{c.cid:<6} {c.actual_status:<7} {mark:<5} {c.clause:<25} {short(c.intent, 60):<60}")
        if c.note:
            print(f"       note: {c.note}")
        if not c.passed:
            print(f"       expected: {c.expected}")
            print(f"       body:     {c.actual_body}")

    print("\n== Summary ==")
    print(f"  passed: {passed}/{len(results)}")
    print(f"  failed: {failed}/{len(results)}")
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #


DEFAULT_DATA = "REPLACED_FOR_SAFETY_AND_ANONYMITY"
DEFAULT_FN = "REPLACED_FOR_SAFETY_AND_ANONYMITY"


if __name__ == "__main__":
    data_url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA).rstrip("/")
    fn_url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_FN

    try:
        run_inbound(data_url)
        run_patch_direct(data_url)
        run_function(data_url, fn_url)
        run_e2e(data_url, fn_url)
    finally:
        rc = print_report()
    sys.exit(rc)
