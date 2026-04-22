"""
Presentation Service (Owner: A)
================================
User-facing web service that provides:
  - GET /          → HTML form page for submitting an event poster
  - POST /submit   → Accepts form-urlencoded or multipart/form-data, forwards JSON to Workflow Service
  - GET /submission/<id> → Shows the final status/note of a submission
  - GET /submission/<id>/poster → Proxies poster image from Workflow Service

Only communicates with Workflow Service — never calls Data Service or any Lambda directly.
"""

from __future__ import annotations

import base64
import logging
import os

import requests
from flask import Flask, Response, redirect, render_template, request, url_for

app = Flask(__name__)
logger = logging.getLogger("presentation-service")

WORKFLOW_SERVICE_URL = os.getenv("WORKFLOW_SERVICE_URL", "http://localhost:8001").rstrip("/")
PORT = int(os.getenv("PORT", "37900"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workflow_headers() -> dict:
    return {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    """§4.2.1 — Return the submission form HTML page."""
    return render_template("index.html", error=None, form={})


@app.post("/submit")
def submit():
    """§4.2.2 / §Append-5.2 — Accept form-urlencoded or multipart, forward JSON to Workflow.

    Steps:
    1. Read form fields (title, description, posterFilename).
    2. If multipart with file: base64-encode posterImage, detect posterMimeType.
    3. Construct JSON body and POST to Workflow Service.
    4. On 2xx → redirect to GET /submission/{id}.
    5. On upstream failure → return 502 and re-render form with error + preserved data.
    """
    form_data = {
        "title": request.form.get("title", ""),
        "description": request.form.get("description", ""),
        "posterFilename": request.form.get("posterFilename", ""),
    }

    # v1.1.0: Handle poster image upload (§Append-5.2)
    poster_image = None
    poster_mime_type = None
    file = request.files.get("posterImage")
    if file and file.filename:
        raw_bytes = file.read()
        if raw_bytes:
            poster_image = base64.b64encode(raw_bytes).decode("ascii")
            mime = file.mimetype
            if mime in ("image/jpeg", "image/png"):
                poster_mime_type = mime
            else:
                poster_mime_type = None

    json_body = {
        "title": form_data["title"],
        "description": form_data["description"],
        "posterFilename": form_data["posterFilename"],
        "posterImage": poster_image,
        "posterMimeType": poster_mime_type,
    }

    try:
        resp = requests.post(
            f"{WORKFLOW_SERVICE_URL}/submissions",
            json=json_body,
            headers=_workflow_headers(),
            timeout=300,  # §Append-6.5
        )
        # Accept both 201 and 202 per contract
        if resp.status_code not in (201, 202):
            logger.warning(
                "POST /submit → Workflow returned %d", resp.status_code
            )
            return render_template(
                "index.html",
                error="Invalid submission, please retry.",
                form=form_data,
            ), 502

    except requests.ConnectionError:
        logger.error("POST /submit → Workflow service unreachable")
        return render_template(
            "index.html",
            error="Workflow service is unavailable. Please try again.",
            form=form_data,
        ), 502
    except requests.Timeout:
        logger.error("POST /submit → Workflow service timed out")
        return render_template(
            "index.html",
            error="Workflow service is unavailable. Please try again.",
            form=form_data,
        ), 502
    except requests.RequestException as exc:
        logger.error("POST /submit → unexpected error: %s", exc)
        return render_template(
            "index.html",
            error="Workflow service is unavailable. Please try again.",
            form=form_data,
        ), 502

    record = resp.json()
    submission_id = record.get("id")
    logger.info("POST /submit → created submission %s", submission_id)
    return redirect(url_for("submission_status", submission_id=submission_id))


@app.get("/submission/<submission_id>")
def submission_status(submission_id: str):
    """§4.2.3 — Show the status page for a given submission.

    Calls Workflow Service GET /submissions/{id}, renders an HTML page showing:
    - status text (exact enum string, no translation)
    - note (shows "Processing..." when note is null and status is PENDING)
    - poster image if posterSize is not null
    - Auto-refresh while status is PENDING
    """
    try:
        resp = requests.get(
            f"{WORKFLOW_SERVICE_URL}/submissions/{submission_id}",
            timeout=10,
        )
    except requests.ConnectionError:
        return "Workflow service is unreachable.", 502
    except requests.Timeout:
        return "Workflow service is unavailable.", 502
    except requests.RequestException:
        return "Workflow service is unavailable.", 502

    if resp.status_code == 404:
        return "Submission not found.", 404

    if resp.status_code != 200:
        return "Unable to load submission status.", 502

    record = resp.json()
    status = record.get("status", "PENDING")

    # When note is null and status is PENDING, display "Processing..."
    note = record.get("note")
    if note is None and status == "PENDING":
        note = "Processing..."

    # Color mapping for CSS classes
    color_map = {
        "PENDING": "processing",
        "READY": "ready",
        "NEEDS REVISION": "revision",
        "INCOMPLETE": "incomplete",
    }

    logger.info(
        "GET /submission/%s → status=%s", submission_id, status
    )
    return render_template(
        "status.html",
        record=record,
        display_note=note,
        status_class=color_map.get(status, "processing"),
        auto_refresh=(status == "PENDING"),
    )


@app.get("/submission/<submission_id>/poster")
def submission_poster(submission_id: str):
    """§Append-5.4 — Byte-for-byte proxy of Workflow Service /poster endpoint.

    Status code, Content-Type, Content-Length and body are forwarded verbatim.
    Only when the upstream request itself fails (DNS / connect / timeout /
    generic RequestException) we synthesise a 502 with the contract error
    envelope per base §5 / §Append-6.4.
    """
    try:
        resp = requests.get(
            f"{WORKFLOW_SERVICE_URL}/submissions/{submission_id}/poster",
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.error(
            "GET /submission/%s/poster → workflow unreachable: %s",
            submission_id, exc,
        )
        body = (
            b'{"error":{"code":"UPSTREAM_UNREACHABLE",'
            b'"message":"Workflow service is unreachable"}}'
        )
        return Response(
            body,
            status=502,
            content_type="application/json; charset=utf-8",
        )

    content_type = resp.headers.get("Content-Type", "application/octet-stream")
    return Response(
        resp.content,
        status=resp.status_code,
        content_type=content_type,
    )


@app.get("/healthz")
def healthz():
    """§4.3 — Optional health-check endpoint."""
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Starting Presentation Service on port %d", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
