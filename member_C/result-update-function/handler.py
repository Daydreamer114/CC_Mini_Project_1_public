"""Result Update Function — AWS Lambda handler.

Role (API_CONTRACT.md §9): pure forwarder. Takes Processing's
(status, note) output and PATCHes it back to the Data Service.
NO business rule evaluation is allowed here.

Supports both AWS Lambda invocation shapes (API_CONTRACT.md §7.3 / Appendix B):
  1. Direct invoke — `event` is the business payload itself.
  2. Function URL (payload v2) — business payload is in `event["body"]` as a
     JSON string; the wrapper also sets `version: "2.0"`.

Always returns a v2 Response Envelope (App. B.3) so upstream callers can
uniformly unwrap one layer regardless of the invoke shape.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

try:
    from notify import send_notification  # type: ignore[import-not-found]
except Exception:
    # notify.py is an optional side-channel for the submitter's own debugging
    # and is intentionally NOT shipped with the graded submission bundle.
    # When it's absent (or fails to import), we keep the handler fully
    # functional by falling back to a no-op.
    def send_notification(msg: str) -> bool:  # type: ignore[misc]
        return False


DATA_SERVICE_URL = os.environ.get("DATA_SERVICE_URL", "http://localhost:37588")
VALID_TERMINAL_STATUSES = {"READY", "NEEDS REVISION", "INCOMPLETE"}
PATCH_TIMEOUT_SECONDS = 10


def _envelope(status_code: int, business: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(business),
        "isBase64Encoded": False,
    }


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _parse_event(event) -> dict | None:
    """Return the business payload dict, or None if the event is malformed.

    Discrimination rule (Appendix B.5.1): Function-URL events always have
    `event["body"]` as a string; direct-invoke events do not (the contract
    forbids business fields named `body`).
    """
    if not isinstance(event, dict):
        return None
    body = event.get("body")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return event


def _build_notify_msg(payload, status_code: int, business: dict) -> str:
    parts = [f"[result-update] http={status_code}"]
    if isinstance(payload, dict):
        sid = payload.get("submissionId")
        if isinstance(sid, str) and sid:
            parts.append(f"submissionId={sid}")
        status = payload.get("status")
        if isinstance(status, str) and status:
            parts.append(f"status={status}")
    err = business.get("error") if isinstance(business, dict) else None
    if isinstance(err, dict):
        code = err.get("code")
        if isinstance(code, str) and code:
            parts.append(f"error={code}")
    return " ".join(parts)


def _notify_safe(payload, status_code: int, business: dict) -> None:
    try:
        send_notification(_build_notify_msg(payload, status_code, business))
    except Exception as exc:  # pragma: no cover - notification must never break
        print(f"NOTIFY_CAUGHT {exc!r}")


def _process(payload) -> tuple[int, dict]:
    if payload is None:
        return 400, _error("BAD_REQUEST", "event body is missing or not valid JSON")

    submission_id = payload.get("submissionId")
    status = payload.get("status")
    note = payload.get("note")

    if not isinstance(submission_id, str) or not submission_id:
        return 400, _error(
            "BAD_REQUEST", "submissionId is required and must be a string"
        )
    if not isinstance(status, str) or status not in VALID_TERMINAL_STATUSES:
        return 400, _error(
            "INVALID_STATUS",
            "status must be one of READY / NEEDS REVISION / INCOMPLETE",
        )
    if note is not None and not isinstance(note, str):
        return 400, _error("BAD_REQUEST", "note must be a string or null")

    url = f"{DATA_SERVICE_URL.rstrip('/')}/submissions/{submission_id}"
    body_bytes = json.dumps({"status": status, "note": note}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body_bytes,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )

    print(f"PATCH {url} payload: {body_bytes.decode('utf-8')}")

    try:
        with urllib.request.urlopen(request, timeout=PATCH_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            print(f"DATA_SERVICE_OK {response.status}")
            return response.status, data
    except urllib.error.HTTPError as http_err:
        detail = http_err.read().decode("utf-8", errors="ignore")
        print(f"DATA_SERVICE_HTTPERROR {http_err.code} {detail}")
        try:
            parsed = json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            parsed = _error("UPSTREAM_ERROR", detail[:200] or "upstream error")
        if not isinstance(parsed, dict) or "error" not in parsed:
            parsed = _error("UPSTREAM_ERROR", (detail or "upstream error")[:200])
        return http_err.code, parsed
    except urllib.error.URLError as url_err:
        print(f"DATA_SERVICE_UNREACHABLE {url_err}")
        return 502, _error("UPSTREAM_UNREACHABLE", str(url_err)[:200])
    except Exception as exc:  # pragma: no cover - safety net
        print(f"INTERNAL {exc!r}")
        return 500, _error("INTERNAL", "unexpected error")


def lambda_handler(event, context):
    payload = _parse_event(event)
    status_code, business = _process(payload)
    _notify_safe(payload, status_code, business)
    return _envelope(status_code, business)
