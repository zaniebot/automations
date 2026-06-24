from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "fetch_issues.py"
SPEC = importlib.util.spec_from_file_location("fetch_issues", MODULE_PATH)
assert SPEC and SPEC.loader
fetch_issues = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_issues)


class FakeResponse:
    def __init__(self, body: bytes, link: str = "") -> None:
        self.body = body
        self.headers = {"Link": link}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


class FetchIssuesTests(unittest.TestCase):
    def test_parse_duration(self) -> None:
        self.assertEqual(fetch_issues.parse_duration("15m"), timedelta(minutes=15))
        self.assertEqual(fetch_issues.parse_duration("7d"), timedelta(days=7))

    def test_search_is_scoped_to_issues_and_window(self) -> None:
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 2, tzinfo=timezone.utc)
        url = fetch_issues.search_url(
            "https://api.github.test", "astral-sh/uv", start, end
        )
        self.assertIn("repo%3Aastral-sh%2Fuv", url)
        self.assertIn("is%3Aissue", url)
        self.assertIn(
            "updated%3A2026-06-01T00%3A00%3A00Z..2026-06-02T00%3A00%3A00Z",
            url,
        )

    def test_collect_preserves_raw_pages_and_advances_state(self) -> None:
        first = b'{"total_count":2,"items":[{"id":1}]}\n'
        second = b'{"total_count":2,"items":[{"id":2}]}\n'
        responses = iter(
            [
                FakeResponse(
                    first,
                    '<https://api.github.test/search/issues?page=2>; rel="next"',
                ),
                FakeResponse(second),
            ]
        )

        def opener(_request):
            return next(responses)

        now = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            manifest = fetch_issues.collect(
                repository="astral-sh/uv",
                cache_dir=cache_dir,
                token="test-token",
                api_url="https://api.github.test",
                initial_lookback=timedelta(days=7),
                overlap=timedelta(hours=1),
                now=now,
                opener=opener,
            )
            response_dir = (
                cache_dir / "responses/2026/06/24/20260624T120000Z"
            )
            self.assertEqual((response_dir / "page-001.json").read_bytes(), first)
            self.assertEqual((response_dir / "page-002.json").read_bytes(), second)
            self.assertEqual(manifest["pages"], 2)
            state = json.loads((cache_dir / "state.json").read_text())
            self.assertEqual(state["through"], "2026-06-24T12:00:00Z")

    def test_failed_fetch_does_not_advance_state(self) -> None:
        body = b'{"total_count":1001,"items":[]}'
        now = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "1,000-result limit"):
                fetch_issues.collect(
                    repository="astral-sh/uv",
                    cache_dir=cache_dir,
                    token="test-token",
                    api_url="https://api.github.test",
                    initial_lookback=timedelta(days=7),
                    overlap=timedelta(hours=1),
                    now=now,
                    opener=lambda _request: FakeResponse(body),
                )
            self.assertFalse((cache_dir / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
