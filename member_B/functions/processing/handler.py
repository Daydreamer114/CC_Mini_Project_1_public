from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from shared.aws_lambda import invoke_lambda
from shared.config import get_settings
from shared.contract import error_body, lambda_envelope, parse_lambda_event, unpack_lambda_envelope
from shared.http_client import HttpClientError
from shared.http_client import request_json
from shared.invoker import invoke

ALLOWED_EXTENSIONS = ('.jpg', '.jpeg', '.png')


def _clean(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def evaluate_submission(record: dict[str, Any]) -> dict[str, Any]:
    title = _clean(record.get('title'))
    description = _clean(record.get('description'))
    poster_filename = _clean(record.get('posterFilename'))

    missing_fields: list[str] = []
    if not title:
        missing_fields.append('title')
    if not description:
        missing_fields.append('description')
    if not poster_filename:
        missing_fields.append('posterFilename')

    if missing_fields:
        fields = ', '.join(missing_fields)
        return {
            'status': 'INCOMPLETE',
            'note': f'Missing required field(s): {fields}.',
        }

    revision_reasons: list[str] = []
    if len(description) < 30:
        revision_reasons.append('Description must be at least 30 characters long.')
    if not poster_filename.lower().endswith(ALLOWED_EXTENSIONS):
        revision_reasons.append('Poster filename must end with .jpg, .jpeg, or .png.')

    if revision_reasons:
        return {
            'status': 'NEEDS REVISION',
            'note': ' '.join(revision_reasons),
        }

    return {
        'status': 'READY',
        'note': 'Submission passed all checks and is ready to share.',
    }


def _invoke_result_update(settings, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if settings.invoke_mode == 'http':
        if not settings.result_update_function_url:
            return 502, error_body('UPSTREAM_UNREACHABLE', 'result update function url is not configured')
        req = request.Request(
            url=settings.result_update_function_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with request.urlopen(req, timeout=10) as response:
                body = response.read().decode('utf-8')
                return response.status, json.loads(body) if body else {}
        except error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            try:
                return exc.code, json.loads(body) if body else {}
            except json.JSONDecodeError:
                return exc.code, error_body('UPSTREAM_ERROR', 'result update returned invalid json')
        except OSError:
            return 502, error_body('UPSTREAM_UNREACHABLE', 'result update function is unreachable')

    if settings.serverless_mode == 'lambda' or settings.invoke_mode == 'lambda':
        envelope = invoke_lambda(
            settings.result_update_function_name,
            payload,
        )
        return unpack_lambda_envelope(envelope)
    business = invoke('functions.result_update.handler:handler', payload)
    return unpack_lambda_envelope(business)


def handler(event: dict[str, Any], context: object | None) -> dict[str, Any]:
    payload = parse_lambda_event(event)
    if payload is None or not isinstance(payload.get('submissionId'), str) or not payload.get('submissionId').strip():
        return lambda_envelope(400, error_body('BAD_REQUEST', 'submissionId must be a non-empty string'))

    submission_id = payload['submissionId'].strip()
    settings = get_settings()

    if settings.processing_delay_seconds > 0:
        time.sleep(settings.processing_delay_seconds)

    try:
        record = request_json(
            f"{settings.data_service_url}/submissions/{submission_id}",
            method='GET',
            timeout=10,
        )
    except HttpClientError as exc:
        if exc.status_code == 404:
            return lambda_envelope(404, error_body('NOT_FOUND', 'submission not found'))
        return lambda_envelope(502, error_body('UPSTREAM_ERROR', 'data service returned an error'))
    except OSError:
        return lambda_envelope(502, error_body('UPSTREAM_UNREACHABLE', 'data service is unreachable'))

    evaluation = evaluate_submission(record)
    update_event = {
        'submissionId': submission_id,
        'status': evaluation['status'],
        'note': evaluation['note'],
    }
    print(f"VERDICT status={evaluation['status']} note={evaluation['note']}")
    status_code, body = _invoke_result_update(settings, update_event)
    return lambda_envelope(status_code, body)
