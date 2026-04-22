from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    workflow_service_url: str
    data_service_url: str
    processing_delay_seconds: float
    serverless_mode: str
    invoke_mode: str
    aws_endpoint_url: str | None
    aws_region: str
    submission_event_function_name: str
    processing_function_name: str
    result_update_function_name: str
    submission_event_function_url: str
    processing_function_url: str
    result_update_function_url: str
    workflow_external_base_path: str
    data_external_base_path: str


_cached_settings: Settings | None = None


def get_settings() -> Settings:
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings(
            workflow_service_url=os.getenv('WORKFLOW_SERVICE_URL', 'http://localhost:8001').rstrip('/'),
            data_service_url=os.getenv('DATA_SERVICE_URL', 'http://localhost:8080').rstrip('/'),
            processing_delay_seconds=float(os.getenv('PROCESSING_DELAY_SECONDS', '0')),
            serverless_mode=os.getenv('SERVERLESS_MODE', 'local').strip().lower(),
            invoke_mode=os.getenv(
                'INVOKE_MODE',
                os.getenv('SERVERLESS_MODE', 'local'),
            ).strip().lower(),
            aws_endpoint_url=(
                os.getenv('AWS_ENDPOINT_URL').rstrip('/')
                if os.getenv('AWS_ENDPOINT_URL')
                else None
            ),
            aws_region=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'),
            submission_event_function_name=os.getenv(
                'SUBMISSION_EVENT_FUNCTION_NAME',
                'submission-event-function',
            ),
            processing_function_name=os.getenv(
                'PROCESSING_FUNCTION_NAME',
                'processing-function',
            ),
            result_update_function_name=os.getenv(
                'RESULT_UPDATE_FUNCTION_NAME',
                'result-update-function',
            ),
            submission_event_function_url=os.getenv('SUBMISSION_EVENT_FUNCTION_URL', '').rstrip('/'),
            processing_function_url=os.getenv('PROCESSING_FUNCTION_URL', '').rstrip('/'),
            result_update_function_url=os.getenv('RESULT_UPDATE_FUNCTION_URL', '').rstrip('/'),
            workflow_external_base_path=os.getenv('WORKFLOW_EXTERNAL_BASE_PATH', '').rstrip('/'),
            data_external_base_path=os.getenv('DATA_EXTERNAL_BASE_PATH', '').rstrip('/'),
        )
    return _cached_settings
