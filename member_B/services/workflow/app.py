from __future__ import annotations

import os

import requests
from flask import Blueprint, Flask, Response, jsonify, request, stream_with_context

from shared.aws_lambda import invoke_lambda
from shared.config import get_settings
from shared.contract import error_body
from shared.invoker import invoke_submission_event_async

app = Flask(__name__)
settings = get_settings()
SERVICE_PATH_PREFIX = os.getenv('SERVICE_PATH_PREFIX', '').rstrip('/')
workflow_bp = Blueprint('workflow', __name__, url_prefix=SERVICE_PATH_PREFIX or None)


def _normalize_payload(payload: dict) -> dict:
    return {
        'title': payload.get('title', ''),
        'description': payload.get('description', ''),
        'posterFilename': payload.get('posterFilename', ''),
        'posterImage': payload.get('posterImage', None),
        'posterMimeType': payload.get('posterMimeType', None),
    }


def _validate_payload(payload: object) -> tuple[dict | None, tuple[dict, int] | None]:
    if not isinstance(payload, dict):
        return None, (error_body('BAD_REQUEST', 'request body must be a JSON object'), 400)
    for field in ('title', 'description', 'posterFilename', 'posterImage', 'posterMimeType'):
        if field in payload and payload[field] is not None and not isinstance(payload[field], str):
            return None, (error_body('BAD_REQUEST', f'{field} must be a string'), 400)
    return _normalize_payload(payload), None


@workflow_bp.get('/healthz')
def health() -> tuple[dict, int]:
    return {'ok': True, 'service': 'workflow'}, 200


@workflow_bp.get('/health')
def old_health() -> tuple[dict, int]:
    return health()


@workflow_bp.post('/submissions')
def create_submission():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(error_body('BAD_REQUEST', 'request body must be a JSON object')), 400
    normalized, validation_error = _validate_payload(payload)
    if validation_error:
        return jsonify(validation_error[0]), validation_error[1]

    try:
        response = requests.post(
            f"{settings.data_service_url}/submissions",
            json=normalized,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        return jsonify(error_body('UPSTREAM_UNREACHABLE', 'data service is unavailable')), 502
    stored_record = response.json()

    submission_event = {'submissionId': stored_record['id']}
    try:
        if settings.invoke_mode == 'http' and settings.submission_event_function_url:
            response = requests.post(
                settings.submission_event_function_url,
                json=submission_event,
                timeout=10,
            )
            response.raise_for_status()
        elif settings.serverless_mode == 'lambda':
            invoke_lambda(
                settings.submission_event_function_name,
                submission_event,
                invocation_type='Event',
            )
        else:
            invoke_submission_event_async(submission_event)
        app.logger.info('TRIGGERED submission-event submissionId=%s', stored_record['id'])
    except Exception as exc:
        app.logger.error('FAILED submission-event submissionId=%s error=%s', stored_record['id'], exc)
    return jsonify(stored_record), 202


@workflow_bp.get('/submissions/<submission_id>')
def get_submission(submission_id: str):
    try:
        response = requests.get(
            f"{settings.data_service_url}/submissions/{submission_id}",
            timeout=10,
        )
    except requests.RequestException:
        return jsonify(error_body('UPSTREAM_UNREACHABLE', 'data service is unavailable')), 502
    return jsonify(response.json()), response.status_code


@workflow_bp.get('/submissions/<submission_id>/poster')
def get_submission_poster(submission_id: str):
    try:
        response = requests.get(
            f"{settings.data_service_url}/submissions/{submission_id}/poster",
            timeout=10,
            stream=True,
        )
    except requests.RequestException:
        return jsonify(error_body('UPSTREAM_UNREACHABLE', 'data service is unavailable')), 502

    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:
            body = error_body('UPSTREAM_ERROR', 'data service returned invalid response')
        finally:
            response.close()
        return jsonify(body), response.status_code

    headers: dict[str, str] = {}
    if response.headers.get('Content-Type'):
        headers['Content-Type'] = response.headers['Content-Type']
    if response.headers.get('Content-Length'):
        headers['Content-Length'] = response.headers['Content-Length']

    return Response(
        stream_with_context(response.iter_content(chunk_size=8192)),
        status=200,
        headers=headers,
        direct_passthrough=True,
    )


app.register_blueprint(workflow_bp)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8001, debug=False)
