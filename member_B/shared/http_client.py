from __future__ import annotations

import json
from typing import Any
from urllib import error, request


class HttpClientError(RuntimeError):
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.body = body or {}
        super().__init__(f'HTTP request failed with status {status_code}')


def request_json(
    url: str,
    method: str = 'GET',
    payload: dict[str, Any] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    req = request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            if not body:
                return {}
            return json.loads(body.decode('utf-8'))
    except error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        raise HttpClientError(exc.code, body) from exc


def post_json(url: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    return request_json(url, method='POST', payload=payload, timeout=timeout)
