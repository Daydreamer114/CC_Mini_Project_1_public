"""Local unit tests for handler.lambda_handler.

Run:
    python -m unittest test_handler.py  -v

The Data Service HTTP call is mocked by patching urllib.request.urlopen so
these tests do not require a running Data Service.
"""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

# Opt out of the external Qmsg webhook so tests never hit the network and
# never send real notifications. notify.send_notification reads this lazily.
os.environ.setdefault("NOTIFY_DISABLE", "1")

import handler  # noqa: E402  (import after env setup is intentional)


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_response(submission_id: str, status: str, note: str | None):
    return _FakeResponse(
        200,
        {
            "id": submission_id,
            "title": "t",
            "description": "d",
            "posterFilename": "p.jpg",
            "status": status,
            "note": note,
            "createdAt": "2026-04-18T03:12:07Z",
            "updatedAt": "2026-04-18T03:12:08Z",
        },
    )


class DirectInvokeTests(unittest.TestCase):
    @patch("handler.urllib.request.urlopen")
    def test_ready_ok(self, mocked):
        mocked.return_value = _ok_response("sid-1", "READY", "ok")
        event = {"submissionId": "sid-1", "status": "READY", "note": "ok"}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body["status"], "READY")

    def test_missing_submission_id(self):
        result = handler.lambda_handler({"status": "READY", "note": "x"}, None)
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")

    def test_invalid_status_pending(self):
        event = {"submissionId": "sid-1", "status": "PENDING", "note": "x"}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "INVALID_STATUS")

    def test_invalid_status_garbage(self):
        event = {"submissionId": "sid-1", "status": "MAYBE", "note": "x"}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "INVALID_STATUS")

    def test_invalid_note_type(self):
        event = {"submissionId": "sid-1", "status": "READY", "note": 123}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")


class FunctionUrlInvokeTests(unittest.TestCase):
    @patch("handler.urllib.request.urlopen")
    def test_v2_payload_ok(self, mocked):
        mocked.return_value = _ok_response("sid-2", "NEEDS REVISION", "short desc")
        business = {
            "submissionId": "sid-2",
            "status": "NEEDS REVISION",
            "note": "short desc",
        }
        event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/",
            "headers": {"content-type": "application/json"},
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps(business),
            "isBase64Encoded": False,
        }
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body["status"], "NEEDS REVISION")

    def test_v2_malformed_body(self):
        event = {"version": "2.0", "body": "not json", "isBase64Encoded": False}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")


class UpstreamErrorTests(unittest.TestCase):
    @patch("handler.urllib.request.urlopen")
    def test_data_service_404_passthrough(self, mocked):
        err_body = json.dumps(
            {"error": {"code": "NOT_FOUND", "message": "submission x not found"}}
        ).encode("utf-8")
        mocked.side_effect = urllib.error.HTTPError(
            url="http://x/submissions/x",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(err_body),
        )
        event = {"submissionId": "missing", "status": "READY", "note": "x"}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 404)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "NOT_FOUND")

    @patch("handler.urllib.request.urlopen")
    def test_data_service_unreachable(self, mocked):
        mocked.side_effect = urllib.error.URLError("connection refused")
        event = {"submissionId": "sid", "status": "READY", "note": "x"}
        result = handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 502)
        body = json.loads(result["body"])
        self.assertEqual(body["error"]["code"], "UPSTREAM_UNREACHABLE")


if __name__ == "__main__":
    unittest.main()
