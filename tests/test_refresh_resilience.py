"""Offline checks for how the monthly refresh behaves when upstream fails (no network).

The 2026-11 issue is the case under test: every literature topic and two API sources
failed with HTTPError, and the issue still read "✅ no API changes" and "0 candidates".
Run: python -m unittest tests/test_refresh_resilience.py
"""
import email.message
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import check_data_freshness as cdf  # noqa: E402
import http_retry  # noqa: E402
import literature_watch as lw  # noqa: E402


def http_error(code, reason="Nope", retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x.test", code, reason, headers, io.BytesIO(b""))


class FakeOpener:
    """Raises each exception in turn, then answers."""

    def __init__(self, *failures):
        self.failures, self.calls = list(failures), 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return io.BytesIO(b"body")


class Fetch(unittest.TestCase):
    def run_fetch(self, opener):
        self.slept = []
        return http_retry.fetch("req", 1, sleep=self.slept.append, opener=opener)

    def test_a_passing_failure_is_retried(self):
        op = FakeOpener(http_error(503))
        self.assertEqual(self.run_fetch(op), b"body")
        self.assertEqual((op.calls, self.slept), (2, [5]))

    def test_403_is_retried(self):         # a CDN's passing block of a shared CI IP
        self.assertEqual(self.run_fetch(FakeOpener(http_error(403))), b"body")

    def test_a_permanent_error_is_not_retried(self):
        op = FakeOpener(http_error(404))
        with self.assertRaises(urllib.error.HTTPError):
            self.run_fetch(op)
        self.assertEqual(op.calls, 1)

    def test_gives_up_after_the_last_attempt_and_raises_it(self):
        op = FakeOpener(http_error(503), http_error(502), http_error(504))
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.run_fetch(op)
        self.assertEqual((c.exception.code, op.calls, self.slept), (504, 3, [5, 30]))

    def test_network_errors_are_retried(self):
        op = FakeOpener(urllib.error.URLError("reset"), TimeoutError(), ConnectionResetError())
        with self.assertRaises(ConnectionResetError):
            self.run_fetch(op)
        self.assertEqual(op.calls, 3)

    def test_retry_after_is_honoured_and_capped(self):
        self.run_fetch(FakeOpener(http_error(429, retry_after="7"), http_error(429, retry_after="99999")))
        self.assertEqual(self.slept, [7, http_retry.MAX_RETRY_AFTER])

    def test_retry_after_as_a_date_falls_back_to_the_schedule(self):
        self.run_fetch(FakeOpener(http_error(503, retry_after="Sun, 01 Nov 2026 12:00:00 GMT")))
        self.assertEqual(self.slept, [5])


class Describe(unittest.TestCase):
    def test_keeps_the_status_code(self):
        self.assertEqual(http_retry.describe(http_error(503, "Service Unavailable")),
                         "HTTP 503 Service Unavailable")

    def test_keeps_the_network_reason(self):
        self.assertEqual(http_retry.describe(urllib.error.URLError("timed out")), "URLError: timed out")

    def test_other_errors_give_their_type(self):
        self.assertEqual(http_retry.describe(TimeoutError()), "TimeoutError")


class Reports(unittest.TestCase):
    """Run each script's main() with upstream down; read the report and the Action outputs."""

    def run_main(self, module, argv):
        with tempfile.TemporaryDirectory() as d:
            out, gh = os.path.join(d, "report.md"), os.path.join(d, "gh_output")
            with mock.patch.object(sys, "argv", ["x", "--out", out, *argv]), \
                    mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh}):
                module.main()
            return (open(out, encoding="utf-8").read(), open(gh, encoding="utf-8").read())

    def test_literature_outage_is_not_a_quiet_month(self):
        with mock.patch.object(lw, "epmc_search", side_effect=http_error(503, "Service Unavailable")), \
                mock.patch.object(lw.llm_triage, "available", return_value=False):
            report, gh = self.run_main(lw, [])
        topics = len(json.load(open(lw.CONFIG, encoding="utf-8"))["topics"])
        self.assertIn(f"INCOMPLETE: {topics} of {topics} topics", report)
        self.assertIn("HTTP 503 Service Unavailable", report)
        self.assertIn(f"across 0 of {topics} topics", report)
        self.assertIn(f"n_failed={topics}", gh)

    def test_a_healthy_scan_reports_no_failure(self):
        with mock.patch.object(lw, "epmc_search", return_value=(0, [])), \
                mock.patch.object(lw.llm_triage, "available", return_value=False):
            report, gh = self.run_main(lw, [])
        self.assertNotIn("INCOMPLETE", report)
        self.assertIn("n_failed=0", gh)

    def test_unreachable_api_source_is_not_called_up_to_date(self):
        with mock.patch.object(cdf, "_get_json", side_effect=http_error(403, "Forbidden")):
            report, gh = self.run_main(cdf, [])
        self.assertNotIn("✅ No API source has newer data", report)
        self.assertIn("could not be checked", report)
        self.assertIn("HTTP 403 Forbidden", report)
        self.assertIn("n_errors=3", gh)


if __name__ == "__main__":
    unittest.main()
