from __future__ import annotations

import json
import os
from typing import Any

from shared.config import get_settings


_cached_client = None


def get_lambda_client():
    global _cached_client
    if _cached_client is None:
        import boto3

        settings = get_settings()
        client_kwargs = {
            'region_name': settings.aws_region,
        }
        if settings.aws_endpoint_url:
            client_kwargs['endpoint_url'] = settings.aws_endpoint_url
            client_kwargs['aws_access_key_id'] = os.getenv('AWS_ACCESS_KEY_ID', 'test')
            client_kwargs['aws_secret_access_key'] = os.getenv('AWS_SECRET_ACCESS_KEY', 'test')
        _cached_client = boto3.client('lambda', **client_kwargs)
    return _cached_client


def invoke_lambda(
    function_name: str,
    payload: dict[str, Any],
    invocation_type: str = 'RequestResponse',
) -> dict[str, Any]:
    client = get_lambda_client()
    response = client.invoke(
        FunctionName=function_name,
        InvocationType=invocation_type,
        Payload=json.dumps(payload).encode('utf-8'),
    )
    if invocation_type == 'Event':
        return {'status_code': response.get('StatusCode', 202)}

    if 'FunctionError' in response:
        error_body = response['Payload'].read().decode('utf-8', errors='replace')
        raise RuntimeError(f"Lambda invocation failed for {function_name}: {error_body}")

    raw_payload = response['Payload'].read()
    if not raw_payload:
        return {}
    return json.loads(raw_payload.decode('utf-8'))
