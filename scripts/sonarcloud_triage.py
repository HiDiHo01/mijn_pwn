"""Manually review and classify three verified Mijn PWN SonarCloud issues.

Only SonarCloud issue metadata is read. A false-positive transition requires
explicit confirmation and a complete, verified preflight of the issue set.
"""

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

SONARCLOUD_URL = "https://sonarcloud.io"
PROJECT_KEY = "HiDiHo01_mijn_pwn"
PULL_REQUEST = "9"
EXPECTED_RULE = "python:S7503"
EXPECTED_ISSUES: dict[str, str] = {
    "AaG1Qt-ME7uBDFkTL2k6": "custom_components/mijn_pwn/date.py",
    "AaG1ISYMMOgPm5XefWwq": "custom_components/mijn_pwn/button.py",
    "AaG02mNRyu4V9oipvU3D": "custom_components/mijn_pwn/number.py",
}
CONFIRMATION = "MARK_PR9_FALSE_POSITIVES"
MAX_RESPONSE_BYTES = 1024 * 1024


class TriageError(Exception):
    """Signal a safe, redacted validation or HTTP failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent an authenticated request from following a different host."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        """Reject all API redirects rather than forwarding the bearer token."""
        return None


def _perform_request(request: Request) -> bytes:
    """Send one bounded HTTPS request without following redirects."""
    try:
        with build_opener(_NoRedirectHandler()).open(request, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status_code = error.code
        error.close()
        raise TriageError(
            "SonarCloud request failed (HTTP %s); "
            "check token permissions or API availability" % status_code
        ) from None
    except OSError:
        raise TriageError("SonarCloud request failed (network or timeout)") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise TriageError("SonarCloud response exceeded the safe size limit")
    return body


def _request_json(
    endpoint: str,
    token: str,
    *,
    fields: dict[str, str] | None = None,
) -> dict[str, object]:
    """Call a fixed SonarCloud API endpoint with bearer authentication."""
    if not endpoint.startswith("/api/issues/") or "//" in endpoint:
        raise TriageError("Unsupported SonarCloud endpoint")
    if not token or "\n" in token or "\r" in token:
        raise TriageError("A valid SONARCLOUD_TRIAGE_TOKEN is required")
    payload = urlencode(fields).encode("ascii") if fields is not None else None
    request = Request(
        SONARCLOUD_URL + endpoint,
        data=payload,
        method="POST" if fields is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **(
                {"Content-Type": "application/x-www-form-urlencoded"}
                if payload is not None
                else {}
            ),
        },
    )
    raw_response = _perform_request(request)
    try:
        result = json.loads(raw_response)
    except ValueError:
        raise TriageError("SonarCloud returned invalid JSON") from None
    if not isinstance(result, dict):
        raise TriageError("SonarCloud returned an unexpected response object")
    return result


def _fetch_issues(token: str) -> dict[str, dict[str, object]]:
    """Read all issues for the exact project and PR without trusting pagination."""
    query = urlencode(
        {
            "componentKeys": PROJECT_KEY,
            "pullRequest": PULL_REQUEST,
            "issueStatuses": "OPEN,CONFIRMED,FALSE_POSITIVE",
            "ps": 500,
        }
    )
    response = _request_json(f"/api/issues/search?{query}", token)
    items = response.get("issues")
    total = response.get("total")
    if (
        not isinstance(items, list)
        or type(total) is not int
        or total != len(items)
        or total > 500
    ):
        raise TriageError("SonarCloud returned incomplete or invalid issue results")
    if not all(isinstance(item, dict) for item in items):
        raise TriageError("SonarCloud returned malformed issue metadata")
    keys = [item.get("key") for item in items]
    if not all(isinstance(key, str) for key in keys):
        raise TriageError("SonarCloud returned malformed issue keys")
    if len(keys) != len(set(keys)):
        raise TriageError("SonarCloud returned duplicate issue keys")
    return {item["key"]: item for item in items if isinstance(item.get("key"), str)}


def _validate_target_issues(issues: dict[str, dict[str, object]]) -> dict[str, str]:
    """Fail closed unless every expected issue has the right rule and location."""
    statuses: dict[str, str] = {}
    for issue_key, expected_path in EXPECTED_ISSUES.items():
        issue = issues.get(issue_key)
        if issue is None:
            raise TriageError("Expected SonarCloud issue %s was not found" % issue_key)
        if (
            issue.get("project") != PROJECT_KEY
            or str(issue.get("pullRequest")) != PULL_REQUEST
            or issue.get("rule") != EXPECTED_RULE
            or issue.get("component") != f"{PROJECT_KEY}:{expected_path}"
        ):
            raise TriageError("Unexpected project, PR, rule or path for %s" % issue_key)
        status = issue.get("issueStatus")
        if status not in {"OPEN", "CONFIRMED", "FALSE_POSITIVE"}:
            raise TriageError(
                "Issue %s is not eligible for false-positive triage" % issue_key
            )
        statuses[issue_key] = status
    return statuses


def triage(token: str, mode: str, confirmation: str) -> None:
    """Preview, or explicitly mark the three audited PR issues false-positive."""
    if mode not in {"preview", "mark_false_positive"}:
        raise TriageError("Unsupported SonarCloud triage mode")
    if mode == "mark_false_positive" and confirmation != CONFIRMATION:
        raise TriageError("Required explicit false-positive confirmation is missing")

    statuses = _validate_target_issues(_fetch_issues(token))
    for issue_key, status in statuses.items():
        print("%s: %s" % (issue_key, status))
    if mode == "preview":
        print("Preview only: no SonarCloud issues were modified")
        return

    for issue_key, status in statuses.items():
        if status == "FALSE_POSITIVE":
            print("%s: already classified; skipped" % issue_key)
            continue
        _request_json(
            "/api/issues/do_transition",
            token,
            fields={"issue": issue_key, "transition": "falsepositive"},
        )
        print("%s: false-positive transition accepted by SonarCloud" % issue_key)

    final_statuses = _validate_target_issues(_fetch_issues(token))
    if any(status != "FALSE_POSITIVE" for status in final_statuses.values()):
        raise TriageError("SonarCloud did not confirm all false-positive statuses")
    print("Verified: all three PR #9 issues are FALSE_POSITIVE")


def main() -> int:
    """Validate the workflow environment and return a safe process exit code."""
    try:
        triage(
            os.environ.get("SONARCLOUD_TRIAGE_TOKEN", ""),
            os.environ.get("SONARCLOUD_TRIAGE_MODE", "preview"),
            os.environ.get("SONARCLOUD_TRIAGE_CONFIRMATION", ""),
        )
    except TriageError as error:
        print("SonarCloud triage failed: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
