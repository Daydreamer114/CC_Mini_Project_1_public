from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from functions.processing.handler import evaluate_submission


class ProcessingRuleTests(unittest.TestCase):
    def test_missing_fields_take_priority(self) -> None:
        result = evaluate_submission(
            {
                'title': '',
                'description': 'too short',
                'posterFilename': 'poster.gif',
            }
        )
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertIn('title', result['note'])

    def test_null_title_is_incomplete(self) -> None:
        result = evaluate_submission(
            {
                'title': None,
                'description': 'This description is long enough for the rule.',
                'posterFilename': 'poster.png',
            }
        )
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_whitespace_title_is_incomplete(self) -> None:
        result = evaluate_submission(
            {
                'title': '   ',
                'description': 'This description is long enough for the rule.',
                'posterFilename': 'poster.png',
            }
        )
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_short_description_needs_revision(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'x' * 29,
                'posterFilename': 'poster.jpg',
            }
        )
        self.assertEqual(result['status'], 'NEEDS REVISION')
        self.assertIn('30 characters', result['note'])

    def test_exactly_30_character_description_is_ready_when_filename_valid(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'x' * 30,
                'posterFilename': 'poster.jpg',
            }
        )
        self.assertEqual(result['status'], 'READY')

    def test_invalid_filename_needs_revision(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'This description definitely contains more than thirty characters.',
                'posterFilename': 'poster.bmp',
            }
        )
        self.assertEqual(result['status'], 'NEEDS REVISION')
        self.assertIn('.jpg', result['note'])

    def test_uppercase_extension_is_valid(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'This description definitely contains more than thirty characters.',
                'posterFilename': 'poster.JPG',
            }
        )
        self.assertEqual(result['status'], 'READY')

    def test_double_extension_backup_is_invalid(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'This description definitely contains more than thirty characters.',
                'posterFilename': 'poster.jpg.bak',
            }
        )
        self.assertEqual(result['status'], 'NEEDS REVISION')

    def test_revision_note_contains_both_reasons(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'short',
                'posterFilename': 'poster.gif',
            }
        )
        self.assertEqual(result['status'], 'NEEDS REVISION')
        self.assertIn('30 characters', result['note'])
        self.assertIn('.jpg', result['note'])

    def test_valid_submission_is_ready(self) -> None:
        result = evaluate_submission(
            {
                'title': 'Launch Event',
                'description': 'This description definitely contains more than thirty characters.',
                'posterFilename': 'poster.png',
            }
        )
        self.assertEqual(result['status'], 'READY')


if __name__ == '__main__':
    unittest.main()
