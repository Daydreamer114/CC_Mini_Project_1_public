"""Data Service HTTP entry point.

Implements API_CONTRACT.md §6 + API_CONTRACT_APPEND.md §Append-7:
  - POST   /submissions                 (only Workflow Service may call)
  - GET    /submissions/{id}            (any upstream)
  - PATCH  /submissions/{id}            (only Result Update Function may call)
  - GET    /submissions/{id}/poster     (new in v1.1.0)
  - GET    /healthz                     (required, <1s)

Field naming is lowerCamelCase at the JSON boundary per §2.2.
"""

from __future__ import annotations

import base64
import os

from flask import Flask, Response, jsonify, request

import store

MAX_POSTER_BYTES = 209_715_200  # 200 MiB (§Append-3.2)
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}

# §Append-10.1: the framework MUST turn "body too large" into the contract
# error envelope. Cap the raw body at 300 MiB — large enough to carry a
# 200 MiB poster after base64 + JSON overhead (base64 inflates ~4/3, so
# 200 MiB raw → ~267 MiB encoded; +JSON keys leaves headroom <300 MiB).
# Anything beyond this is rejected by Werkzeug before the body is fully
# read, and our @errorhandler(413) below converts it to the contract
# envelope instead of an HTML page.
MAX_REQUEST_BYTES = 300 * 1024 * 1024

SCHEMA_PATH = os.environ.get(
    "DATA_SCHEMA_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"),
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
store.init_db(SCHEMA_PATH)


def _err(code: str, message: str, http_status: int):
    return jsonify({"error": {"code": code, "message": message}}), http_status


def _infer_mime_from_filename(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    return None


@app.post("/submissions")
def create_submission():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("BAD_REQUEST", "body must be a JSON object", 400)

    for key in ("title", "description", "posterFilename"):
        value = body.get(key, "")
        if not isinstance(value, str):
            return _err("BAD_REQUEST", f"{key} must be a string", 400)

    # --- v1.1.0 poster validation (§Append-3.2 / §Append-3.4) ---
    poster_image_raw = body.get("posterImage")
    poster_mime_raw = body.get("posterMimeType")

    # posterImage type check
    if poster_image_raw is not None and not isinstance(poster_image_raw, str):
        return _err("BAD_REQUEST", "posterImage must be a string or null", 400)

    # posterMimeType type check
    if poster_mime_raw is not None and not isinstance(poster_mime_raw, str):
        return _err("BAD_REQUEST", "posterMimeType must be a string or null", 400)

    # posterMimeType enum check
    if poster_mime_raw is not None and poster_mime_raw not in ALLOWED_MIME_TYPES:
        return _err("BAD_REQUEST", "posterMimeType must be image/jpeg, image/png, or null", 400)

    poster_image_bytes = None
    poster_mime_type = poster_mime_raw if poster_mime_raw in ALLOWED_MIME_TYPES else None
    poster_size = None

    if isinstance(poster_image_raw, str) and poster_image_raw != "":
        # Decode base64
        try:
            poster_image_bytes = base64.b64decode(poster_image_raw, validate=True)
        except Exception:
            return _err("BAD_REQUEST", "posterImage is not valid base64", 400)

        # Size check
        if len(poster_image_bytes) > MAX_POSTER_BYTES:
            return _err("PAYLOAD_TOO_LARGE", "posterImage exceeds 200 MiB limit", 413)

        poster_size = len(poster_image_bytes)

        # Infer MIME from filename if not provided
        if poster_mime_type is None:
            poster_mime_type = _infer_mime_from_filename(body.get("posterFilename", ""))

    record = store.create(
        body.get("title", ""),
        body.get("description", ""),
        body.get("posterFilename", ""),
        poster_image_bytes=poster_image_bytes,
        poster_mime_type=poster_mime_type,
        poster_size=poster_size,
    )
    response = jsonify(record)
    response.status_code = 201
    response.headers["Location"] = f"/submissions/{record['id']}"
    return response


@app.get("/submissions/<sid>")
def get_submission(sid: str):
    record = store.get(sid)
    if record is None:
        return _err("NOT_FOUND", f"submission {sid} not found", 404)
    return jsonify(record), 200


@app.patch("/submissions/<sid>")
def patch_submission(sid: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("BAD_REQUEST", "body must be a JSON object", 400)

    # §Append-7.4 / §Append-12.13: reject poster* fields in PATCH
    for key in body:
        if key.startswith("poster"):
            return _err(
                "BAD_REQUEST",
                f"PATCH must not contain poster-related field: {key}",
                400,
            )

    status = body.get("status")
    note = body.get("note")

    if not isinstance(status, str):
        return _err("INVALID_STATUS", "status must be a string", 400)
    if note is not None and not isinstance(note, str):
        return _err("BAD_REQUEST", "note must be a string or null", 400)

    result = store.patch(sid, status, note)

    if result == store.PatchResult.INVALID_STATUS:
        return _err(
            "INVALID_STATUS",
            "status must be one of READY / NEEDS REVISION / INCOMPLETE "
            "(PENDING is not writable via PATCH)",
            400,
        )
    if result == store.PatchResult.INVALID_STATUS_TRANSITION:
        return _err(
            "INVALID_STATUS_TRANSITION",
            "cannot set status back to PENDING",
            400,
        )
    if result == store.PatchResult.NOT_FOUND:
        return _err("NOT_FOUND", f"submission {sid} not found", 404)

    return jsonify(result), 200


@app.get("/submissions/<sid>/poster")
def get_poster(sid: str):
    """§Append-7.5 — Return poster image bytes or 404."""
    # Check if submission exists
    record = store.get(sid)
    if record is None:
        return _err("NOT_FOUND", f"submission {sid} not found", 404)

    result = store.get_poster(sid)
    if result is None:
        return _err("NOT_FOUND", f"submission {sid} has no poster", 404)

    image_bytes, mime_type, size = result
    content_type = mime_type or "application/octet-stream"
    response = Response(image_bytes, status=200, content_type=content_type)
    response.headers["Content-Length"] = str(len(image_bytes))
    return response


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True}), 200


@app.errorhandler(404)
def _not_found(_):
    return _err("NOT_FOUND", "route not found", 404)


@app.errorhandler(405)
def _method_not_allowed(_):
    return _err("METHOD_NOT_ALLOWED", "method not allowed", 405)


@app.errorhandler(413)
def _payload_too_large(_):
    return _err("PAYLOAD_TOO_LARGE", "request body too large", 413)


@app.errorhandler(500)
def _internal(_):
    return _err("INTERNAL", "internal server error", 500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "37588"))
    app.run(host="0.0.0.0", port=port)
