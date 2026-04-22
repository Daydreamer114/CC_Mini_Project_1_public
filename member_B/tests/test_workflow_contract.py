from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.workflow.app import app


class WorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()

    @patch('services.workflow.app.requests.post')
    def test_create_submission_accepts_append_fields(self, mock_post: Mock) -> None:
        data_response = Mock()
        data_response.raise_for_status.return_value = None
        data_response.json.return_value = {
            'id': 'abc-123',
            'title': 'Event',
            'description': 'This description is long enough for workflow testing.',
            'posterFilename': 'poster.png',
            'posterMimeType': 'image/png',
            'posterSize': 42,
            'status': 'PENDING',
            'note': None,
            'createdAt': '2026-04-21T00:00:00Z',
            'updatedAt': '2026-04-21T00:00:00Z',
        }
        trigger_response = Mock()
        trigger_response.raise_for_status.return_value = None
        mock_post.side_effect = [data_response, trigger_response]

        response = self.client.post(
            '/submissions',
            json={
                'title': 'Event',
                'description': 'This description is long enough for workflow testing.',
                'posterFilename': 'poster.png',
                'posterImage': 'ZmFrZS1iYXNlNjQ=',
                'posterMimeType': 'image/png',
            },
        )

        self.assertEqual(response.status_code, 202)
        first_call = mock_post.call_args_list[0]
        self.assertEqual(first_call.kwargs['json']['posterImage'], 'ZmFrZS1iYXNlNjQ=')
        self.assertEqual(first_call.kwargs['json']['posterMimeType'], 'image/png')

    def test_create_submission_rejects_invalid_append_field_type(self) -> None:
        response = self.client.post(
            '/submissions',
            json={
                'title': 'Event',
                'description': 'This description is long enough for workflow testing.',
                'posterFilename': 'poster.png',
                'posterImage': 123,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['error']['code'], 'BAD_REQUEST')

    @patch('services.workflow.app.requests.get')
    def test_proxy_poster_success(self, mock_get: Mock) -> None:
        upstream = Mock()
        upstream.status_code = 200
        upstream.headers = {'Content-Type': 'image/png', 'Content-Length': '4'}
        upstream.iter_content.return_value = [b'test']
        mock_get.return_value = upstream

        response = self.client.get('/submissions/abc-123/poster')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'image/png')
        self.assertEqual(response.data, b'test')

    @patch('services.workflow.app.requests.get')
    def test_proxy_poster_not_found_passthrough(self, mock_get: Mock) -> None:
        upstream = Mock()
        upstream.status_code = 404
        upstream.json.return_value = {
            'error': {
                'code': 'NOT_FOUND',
                'message': 'submission abc-123 has no poster',
            }
        }
        upstream.close.return_value = None
        mock_get.return_value = upstream

        response = self.client.get('/submissions/abc-123/poster')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json['error']['code'], 'NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
