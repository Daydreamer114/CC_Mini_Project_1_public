"""
Submission Event Function (Owner: A)
======================================
AWS Lambda function that converts a "new submission" event into a synchronous
call to the Processing Function (owned by B).

Contract reference: API_CONTRACT §7

Behaviour:
  1. Parse event (supports both direct Lambda invoke and Function URL payloads).
  2. Validate submissionId (must be non-empty string).
  3. Synchronously invoke Processing Function with {submissionId}.
  4. Return {accepted: true, submissionId} on success, or error envelope on failure.

Constraints:
  - No business logic, no Data Service access, no retry on upstream failure.
  - Always returns v2 Response Envelope (statusCode + headers + body string).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("submission-event-function")

# ---------------------------------------------------------------------------
# Environment variables (§7.7)
# ---------------------------------------------------------------------------
PROCESSING_FUNCTION_URL = os.getenv("PROCESSING_FUNCTION_URL", "")


# ---------------------------------------------------------------------------
# Event parsing helpers (§B.5)
# ---------------------------------------------------------------------------

def _parse_event(event: Any) -> dict | None:
    """Parse the incoming Lambda event into a business payload dict.

    Supports two invocation forms:
      - Direct Lambda invoke: event IS the business payload dict.
      - Function URL invoke: event.body is a JSON string containing the payload.

    Returns None if parsing fails (caller should respond 400).
    """
    if isinstance(event, dict) and isinstance(event.get("body"), str):
        # Function URL form — check for v2 payload version (§B.6)
        version = event.get("version")
        if version and version != "2.0":
            raise ValueError("UNSUPPORTED_VERSION")
        try:
            return json.loads(event["body"])
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(event, dict):
        # Direct Lambda invoke — event itself is the payload
        return event
    return None


def _envelope(status_code: int, business: dict) -> dict:
    """Build a v2 Response Envelope (§B.3).

    All Lambda handlers in this project MUST return this format,
    regardless of whether they are called via direct invoke or Function URL.
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(business),
    }


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------

def _invoke_processing_http(submission_id: str) -> dict:
    """Invoke Processing Function via HTTP POST to its Function URL."""
    from urllib import request as url_request, error as url_error

    payload = json.dumps({"submissionId": submission_id}).encode("utf-8")
    url = PROCESSING_FUNCTION_URL

    logger.info("INVOKED processing url=%s submissionId=%s", url, submission_id)

    req = url_request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with url_request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            # HTTP mode: resp.status is the business statusCode, body is the business JSON
            return {"statusCode": resp.status, "body": body}
    except url_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        logger.error("Processing function returned HTTP %d: %s", e.code, body)
        raise RuntimeError(f"Processing function HTTP {e.code}")
    except Exception as e:
        logger.error("Processing function unreachable: %s", e)
        raise RuntimeError(f"Processing function unreachable: {e}")


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handler(event: dict, context: Any) -> dict:
    """AWS Lambda entry point for the Submission Event Function.

    Steps (§7.5):
      1. Parse and validate submissionId.
      2. Synchronously invoke Processing Function.
      3. Return success or error envelope.
    """
    try:
        payload = _parse_event(event)
    except ValueError:
        logger.warning("Unsupported payload version")
        return _envelope(500, {"error": {"code": "INTERNAL", "message": "unsupported payload version"}})

    if payload is None:
        logger.warning("Received unparseable event")
        return _envelope(400, {"error": {"code": "BAD_REQUEST", "message": "invalid json body"}})

    # --- Validate submissionId (§7.5 step 1) ---
    submission_id = payload.get("submissionId")
    if not submission_id or not isinstance(submission_id, str):
        return _envelope(
            400,
            {"error": {"code": "BAD_REQUEST", "message": "submissionId is required and must be a non-empty string"}},
        )

    # --- Invoke Processing Function (§7.5 step 2) ---
    try:
        result = _invoke_processing_http(submission_id)
    except RuntimeError as exc:
        error_msg = str(exc)
        if "unreachable" in error_msg.lower():
            return _envelope(
                502,
                {"error": {"code": "UPSTREAM_UNREACHABLE", "message": "Processing function is unreachable"}},
            )
        return _envelope(
            502,
            {"error": {"code": "UPSTREAM_ERROR", "message": error_msg}},
        )
    except Exception as exc:
        logger.exception("Unexpected error invoking processing function")
        return _envelope(
            500,
            {"error": {"code": "INTERNAL", "message": "unexpected error"}},
        )

    # --- Success (§7.5 step 3) ---
    logger.info("Processing completed successfully for submissionId=%s", submission_id)
    return _envelope(200, {"accepted": True, "submissionId": submission_id})
