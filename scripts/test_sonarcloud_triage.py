"""Offline regression tests for the guarded SonarCloud triage action."""

from io import BytesIO, StringIO
import json
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from scripts import sonarcloud_triage as triage


class SonarCloudTriageTests(TestCase):
    """Ensure only verified PR #9 issues can be classified as false-positive."""

    @staticmethod
    def _search_payload(status: str = "OPEN") -> bytes:
        """Build a synthetic but API-shaped SonarCloud response."""
        return json.dumps(
            {
                "total": 3,
                "issues": [
                    {
                        "key": key,
                        "project": triage.PROJECT_KEY,
                        "pullRequest": triage.PULL_REQUEST,
                        "component": f"{triage.PROJECT_KEY}:{path}",
                        "rule": triage.EXPECTED_RULE,
                        "issueStatus": status,
                    }
                    for key, path in triage.EXPECTED_ISSUES.items()
                ],
            }
        ).encode("utf-8")

    def test_preview_never_posts(self) -> None:
        """A preview only reads issue status, even without confirmation."""
        requests: list[Request] = []

        def _mock_request(request: Request) -> bytes:
            """Record the method without exposing the synthetic token."""
            requests.append(request)
            return self._search_payload()

        with patch.object(triage, "_perform_request", side_effect=_mock_request):
            triage.triage("fictional-token", "preview", "")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get_method(), "GET")
        self.assertIn("pullRequest=9", requests[0].full_url)
        self.assertIn(
            "issueStatuses=OPEN%2CCONFIRMED%2CFALSE_POSITIVE",
            requests[0].full_url,
        )
        self.assertEqual(
            requests[0].get_header("Authorization"), "Bearer fictional-token"
        )

    def test_confirmation_required_before_any_network_request(self) -> None:
        """Do not read or write issues for unconfirmed write requests."""
        with patch.object(triage, "_perform_request") as request_mock:
            with self.assertRaisesRegex(triage.TriageError, "confirmation"):
                triage.triage("fictional-token", "mark_false_positive", "no")
        request_mock.assert_not_called()

    def test_write_only_three_verified_issues(self) -> None:
        """Apply exactly three transitions after preflight and verify again."""
        requests: list[Request] = []
        read_count = 0

        def _mock_request(request: Request) -> bytes:
            """Return open status before writes and false-positive afterward."""
            nonlocal read_count
            requests.append(request)
            if request.get_method() == "POST":
                return b"{}"
            read_count += 1
            return self._search_payload("OPEN" if read_count == 1 else "FALSE_POSITIVE")

        with patch.object(triage, "_perform_request", side_effect=_mock_request):
            triage.triage("fictional-token", "mark_false_positive", triage.CONFIRMATION)
        posts = [request for request in requests if request.get_method() == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertTrue(
            all(
                request.full_url.endswith("/api/issues/do_transition")
                for request in posts
            )
        )
        self.assertEqual(
            {request.data.decode("ascii") for request in posts},
            {
                "issue=%s&transition=falsepositive" % issue_key
                for issue_key in triage.EXPECTED_ISSUES
            },
        )
        self.assertEqual(len(requests), 5)

    def test_already_classified_issues_are_not_modified(self) -> None:
        """A repeated run is idempotent when issues are already classified."""
        requests: list[Request] = []

        def _mock_request(request: Request) -> bytes:
            """Record requests while returning existing false-positive states."""
            requests.append(request)
            return self._search_payload("FALSE_POSITIVE")

        with patch.object(triage, "_perform_request", side_effect=_mock_request):
            triage.triage("fictional-token", "mark_false_positive", triage.CONFIRMATION)
        self.assertEqual([request.get_method() for request in requests], ["GET", "GET"])

    def test_rule_mismatch_blocks_all_writes(self) -> None:
        """Reject a changed Sonar rule before posting any transitions."""
        payload = json.loads(self._search_payload())
        payload["issues"][0]["rule"] = "python:S3776"
        with patch.object(
            triage, "_perform_request", return_value=json.dumps(payload).encode()
        ) as request_mock:
            with self.assertRaisesRegex(triage.TriageError, "Unexpected project"):
                triage.triage(
                    "fictional-token", "mark_false_positive", triage.CONFIRMATION
                )
        self.assertEqual(request_mock.call_count, 1)

    def test_malformed_key_causes_safe_failure(self) -> None:
        """Never accept an unhashable identifier in untrusted API data."""
        payload = json.loads(self._search_payload())
        payload["issues"][0]["key"] = {"invalid": "key"}
        with patch.object(
            triage, "_perform_request", return_value=json.dumps(payload).encode()
        ):
            with self.assertRaises(triage.TriageError):
                triage.triage("fictional-token", "preview", "")

    def test_partial_pagination_blocks_all_writes(self) -> None:
        """Do not make a write decision from an incomplete issue response."""
        payload = json.loads(self._search_payload())
        payload["total"] = 4
        with patch.object(
            triage, "_perform_request", return_value=json.dumps(payload).encode()
        ) as request_mock:
            with self.assertRaisesRegex(triage.TriageError, "incomplete"):
                triage.triage(
                    "fictional-token", "mark_false_positive", triage.CONFIRMATION
                )
        self.assertEqual(request_mock.call_count, 1)

    def test_unsupported_status_blocks_changes(self) -> None:
        """Do not override a status other than open or false-positive."""
        with patch.object(
            triage, "_perform_request", return_value=self._search_payload("ACCEPTED")
        ):
            with self.assertRaisesRegex(triage.TriageError, "not eligible"):
                triage.triage(
                    "fictional-token", "mark_false_positive", triage.CONFIRMATION
                )

    def test_api_errors_do_not_leak_upstream_payload(self) -> None:
        """Do not propagate a SonarCloud response containing secret material."""
        request = Request("https://sonarcloud.io/api/issues/search")
        error = HTTPError(
            request.full_url, 403, "fictional-private-data", {}, BytesIO(b"private")
        )
        with patch.object(triage, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(triage.TriageError) as caught:
                triage._perform_request(request)
        self.assertNotIn("fictional-private-data", str(caught.exception))
        self.assertIn("403", str(caught.exception))

    def test_network_errors_are_redacted(self) -> None:
        """Avoid printing request targets or private proxy error bodies."""
        request = Request("https://sonarcloud.io/api/issues/search")
        with patch.object(triage, "build_opener") as opener:
            opener.return_value.open.side_effect = URLError("fictional-secret-data")
            with self.assertRaises(triage.TriageError) as caught:
                triage._perform_request(request)
        self.assertNotIn("fictional-secret-data", str(caught.exception))

    def test_invalid_json_fails_without_writing(self) -> None:
        """Reject non-JSON upstream responses without emitting their content."""
        with patch.object(triage, "_perform_request", return_value=b"<secret>not JSON"):
            with self.assertRaisesRegex(triage.TriageError, "invalid JSON"):
                triage.triage("fictional-token", "preview", "")

    def test_main_error_exit_is_sanitized(self) -> None:
        """Keep errors free of secrets and return a nonzero exit status."""
        with (
            patch.dict(
                "os.environ",
                {
                    "SONARCLOUD_TRIAGE_TOKEN": "fictional-token",
                    "SONARCLOUD_TRIAGE_MODE": "invalid",
                },
            ),
            patch("sys.stderr", new_callable=StringIO) as output,
        ):
            exit_code = triage.main()
        self.assertEqual(exit_code, 1)
        self.assertNotIn("fictional-token", output.getvalue())


if __name__ == "__main__":
    import unittest

    unittest.main()
