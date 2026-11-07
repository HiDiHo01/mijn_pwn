# SonarCloud triage for PR #9

This repository provides a narrowly scoped GitHub Actions workflow for **three
verified SonarCloud false positives** in PR #9. It does not automatically
accept, suppress, or change any other findings.

## Setup

1. Use a SonarCloud **user token** owned by an account with *Browse* and
   *Administer Issues* permissions on `HiDiHo01_mijn_pwn`. Do not use an
   analysis-only token. Set a short expiry and rotate it regularly.
2. Store the token as the GitHub Actions repository secret
   `SONARCLOUD_TRIAGE_TOKEN`. Do not paste it into issues, pull requests or logs.
3. Configure the GitHub Actions environment `sonarcloud-triage` under repository
   **Settings → Environments**. Require an authorized reviewer for deployments
   to this environment, and restrict allowed deployment branches to `main`.
   Consider moving the secret from repository-level to this environment so it
   is inaccessible to unrelated workflows. An unprotected environment does not
   provide an approval gate.
4. Merge the reviewed workflow into `main` before using **Actions →
   SonarCloud issue triage (PR 9) → Run workflow**. GitHub only exposes manual
   `workflow_dispatch` execution for workflows present on the default branch.

## Running

- Start with `mode=preview`; this only fetches and verifies the three issues.
- For a status change, use `mode=mark_false_positive`, enter the exact
  confirmation `MARK_PR9_FALSE_POSITIVES`, and approve the environment gate.
- The workflow allows only the three known issue keys, rule `python:S7503`,
  project `HiDiHo01_mijn_pwn`, PR `9`, and verified file paths. It validates
  all targets before writing, skips already-classified issues, and checks
  the statuses after the API transitions.
- A rejected, incomplete or unexpected SonarCloud API response fails the job.
  If a run fails after a partial transition, inspect the status before retrying;
  the script skips issues that are already `FALSE_POSITIVE`.

The token is passed only to the manually invoked job, as an environment
variable. The PR-test job cannot access it. The workflow uses immutable action
SHAs, read-only GitHub permissions, fixed HTTPS endpoints, bounded HTTP requests,
no redirects, and sanitized failure messages. It does not execute account
operations or meter submissions against PWN.
