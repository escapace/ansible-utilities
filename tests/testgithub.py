"""Run with: uv run python -m unittest discover -s tests."""

from datetime import datetime, timezone
from email.utils import format_datetime
import unittest

from ansible.errors import AnsibleFilterError

from plugins.filter.github import (
    github_archive_retry as gh_archive_retry,
    github_metadata_retry as gh_metadata_retry,
)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"max_attempts": 4, "max_wait_seconds": 90}
        self.state = {
            "attempt": 1,
            "waited_seconds": 0,
            "now_epoch": 1700000000.0,
            "jitter_seconds": 0,
        }

    def metadata(self, status, **headers):
        return gh_metadata_retry(
            {"status": status, "failed": status != 200, **headers},
            self.policy,
            self.state,
        )

    def date(self, seconds=0):
        return format_datetime(
            datetime.fromtimestamp(self.state["now_epoch"] + seconds, timezone.utc),
            usegmt=True,
        )

    def test_success(self):
        self.assertEqual(self.metadata(200), {"action": "success"})

    def test_transient_backoff_and_jitter(self):
        for status in (408, 500, 502, 503, 504):
            for attempt, delay in ((1, 2), (2, 4), (3, 8)):
                with self.subTest(status=status, attempt=attempt):
                    self.state.update(attempt=attempt, jitter_seconds=1)
                    self.assertEqual(
                        self.metadata(status),
                        {"action": "retry", "delay_seconds": delay + 1},
                    )

    def test_terminal_and_unclassified_errors(self):
        for status in (None, -1, 200, 400, 401, 403, 404, 410, 422, 501, 505):
            with self.subTest(status=status):
                result = gh_metadata_retry(
                    {"status": status, "failed": True, "msg": "original diagnostic"},
                    self.policy,
                    self.state,
                )
                self.assertEqual(result["action"], "fail")

    def test_retry_after_seconds_and_http_date(self):
        for header in ("12", self.date(11)):
            with self.subTest(header=header):
                self.assertEqual(
                    self.metadata(429, retry_after=header),
                    {"action": "retry", "delay_seconds": 12},
                )
        self.assertEqual(self.metadata(503, retry_after="12")["delay_seconds"], 12)
        self.assertEqual(self.metadata(429, retry_after="0")["delay_seconds"], 2)

    def test_obsolete_http_dates(self):
        for header in ("Tuesday, 14-Nov-23 22:13:30 GMT", "Tue Nov 14 22:13:30 2023"):
            with self.subTest(header=header):
                self.assertEqual(
                    self.metadata(429, retry_after=header)["delay_seconds"], 11
                )

    def test_primary_and_conflicting_rate_limit_instructions(self):
        for status in (403, 429):
            with self.subTest(status=status):
                self.assertEqual(
                    self.metadata(
                        status,
                        retry_after="5",
                        x_ratelimit_remaining="0",
                        x_ratelimit_reset=str(int(self.state["now_epoch"]) + 10),
                    ),
                    {"action": "retry", "delay_seconds": 11},
                )
                self.assertEqual(
                    self.metadata(
                        status,
                        retry_after="20",
                        x_ratelimit_remaining="0",
                        x_ratelimit_reset=str(int(self.state["now_epoch"]) + 10),
                    )["delay_seconds"],
                    20,
                )
        self.assertEqual(self.metadata(403, retry_after="5")["action"], "retry")

    def test_server_date_protects_against_controller_clock_skew(self):
        server_date = self.date()
        deadline = self.date(10)
        reset = str(int(self.state["now_epoch"]) + 10)
        self.state["now_epoch"] += 3600
        self.assertEqual(
            self.metadata(429, date=server_date, retry_after=deadline)["delay_seconds"],
            11,
        )
        self.assertEqual(
            self.metadata(
                403,
                date=server_date,
                x_ratelimit_remaining="0",
                x_ratelimit_reset=reset,
            )["delay_seconds"],
            11,
        )

    def test_secondary_rate_limits(self):
        self.assertEqual(self.metadata(429)["delay_seconds"], 60)
        response = {"message": "You have exceeded a secondary rate limit."}
        self.assertEqual(self.metadata(403, json=response)["delay_seconds"], 60)
        self.assertEqual(
            self.metadata(429, x_ratelimit_remaining="3")["delay_seconds"], 60
        )
        self.state.update(attempt=2, waited_seconds=60)
        decision = self.metadata(429)
        self.assertEqual(decision["action"], "fail")
        self.assertIn("required wait 120 seconds", decision["reason"])

    def test_malformed_headers_fail_without_retry(self):
        for headers in (
            {"retry_after": "invalid"},
            {"retry_after": "-1"},
            {"retry_after": "1.5"},
            {"retry_after": None},
            {"retry_after": "１２"},
            {"retry_after": "5", "date": "invalid"},
            {"x_ratelimit_remaining": "0"},
            {"x_ratelimit_remaining": "0", "x_ratelimit_reset": "invalid"},
            {"x_ratelimit_remaining": "invalid"},
        ):
            with self.subTest(headers=headers):
                result = self.metadata(429, **headers)
                self.assertEqual(result["action"], "fail")
                self.assertIn("invalid retry headers", result["reason"])

    def test_wait_budget_never_shortens_server_instructions(self):
        self.assertEqual(self.metadata(429, retry_after="90")["delay_seconds"], 90)
        for header in ("91", "3600", "9" * 40):
            with self.subTest(header=header):
                decision = self.metadata(429, retry_after=header)
                self.assertEqual(decision["action"], "fail")
                self.assertIn("wait budget", decision["reason"])
                self.assertIn("retry no earlier than", decision["reason"])
        self.state.update(attempt=2, waited_seconds=88)
        self.assertEqual(self.metadata(503)["action"], "fail")

    def test_fourth_failure_exhausts_attempts_but_fourth_success_is_allowed(self):
        self.state["attempt"] = 4
        self.assertIn("attempt limit", self.metadata(503)["reason"])
        self.assertEqual(self.metadata(200)["action"], "success")

    def test_archive_policy(self):
        for response in ({"failed": False}, {"failed": False, "status_code": 304}):
            self.assertEqual(
                gh_archive_retry(response, self.policy, self.state)["action"], "success"
            )
        for status in (408, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(
                    gh_archive_retry(
                        {"failed": True, "status_code": status}, self.policy, self.state
                    ),
                    {"action": "retry", "delay_seconds": 2},
                )
        for response in (
            {"failed": True, "status_code": 403},
            {"failed": True, "status_code": 429},
            {"failed": True, "status_code": 404},
            {"failed": True, "msg": "checksum mismatch"},
            {"failed": True, "msg": "certificate verification failed"},
        ):
            with self.subTest(response=response):
                self.assertEqual(
                    gh_archive_retry(response, self.policy, self.state)["action"],
                    "fail",
                )

    def test_serialized_ansible_counter_is_validated_before_conversion(self):
        self.state["waited_seconds"] = "60"
        self.assertEqual(self.metadata(503)["delay_seconds"], 2)
        self.assertEqual(self.state["waited_seconds"], "60")
        for value in ("invalid", "-1", "1.5", "９", "91"):
            with self.subTest(value=value):
                self.state["waited_seconds"] = value
                with self.assertRaises(AnsibleFilterError):
                    self.metadata(503)

    def test_http_errors_are_not_success_when_failed_was_overridden(self):
        self.assertEqual(
            gh_archive_retry(
                {"failed": False, "status_code": 503}, self.policy, self.state
            ),
            {"action": "retry", "delay_seconds": 2},
        )
        self.assertEqual(
            gh_archive_retry(
                {"failed": False, "status_code": 403}, self.policy, self.state
            )["action"],
            "fail",
        )

    def test_response_requires_an_explicit_failure_signal(self):
        for response in ({}, {"failed": None}, {"failed": "false"}, {"failed": 0}):
            for filter_function in (gh_metadata_retry, gh_archive_retry):
                with self.subTest(response=response, filter=filter_function.__name__):
                    with self.assertRaisesRegex(
                        AnsibleFilterError, "boolean failed field"
                    ):
                        filter_function(response, self.policy, self.state)

    def test_invalid_internal_state_is_explicit(self):
        for key, value in (
            ("attempt", 0),
            ("attempt", 5),
            ("attempt", True),
            ("waited_seconds", -1),
            ("waited_seconds", 91),
            ("now_epoch", float("nan")),
            ("now_epoch", "invalid"),
            ("now_epoch", float("inf")),
            ("now_epoch", 10**400),
            ("jitter_seconds", 2),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(AnsibleFilterError):
                    gh_metadata_retry(
                        {"failed": True}, self.policy, {**self.state, key: value}
                    )
        for response, policy, state in (
            (None, self.policy, self.state),
            ({"failed": True}, {}, self.state),
        ):
            with self.assertRaises(AnsibleFilterError):
                gh_metadata_retry(response, policy, state)


if __name__ == "__main__":
    unittest.main()
