from __future__ import annotations

import json
from typing import Any


JSON_HEADERS = {'Content-Type': 'application/json'}


def error_body(code: str, message: str) -> dict[str, Any]:
    return {'error': {'code': code, 'message': message}}


def lambda_envelope(status_code: int, business: dict[str, Any]) -> dict[str, Any]:
    return {
        'statusCode': status_code,
        'headers': JSON_HEADERS,
        'body': json.dumps(business),
        'isBase64Encoded': False,
    }


def parse_lambda_event(event: Any) -> dict[str, Any] | None:
    if isinstance(event, dict) and isinstance(event.get('body'), str):
        try:
            parsed = json.loads(event['body'])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return event if isinstance(event, dict) else None


def unpack_lambda_envelope(response: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if 'statusCode' not in response:
        return 200, response

    status_code = int(response.get('statusCode', 500))
    body = response.get('body') or '{}'
    if isinstance(body, str):
        try:
            return status_code, json.loads(body)
        except json.JSONDecodeError:
            return status_code, {}
    if isinstance(body, dict):
        return status_code, body
    return status_code, {}
