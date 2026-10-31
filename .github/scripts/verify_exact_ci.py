"""Fail-closed PyPI gate: exact tag SHA must have full successful main-push CI.

This script only reads the GitHub API. No release, tag or package operations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml


def expected_jobs(workflow: Path) -> set[str]:
    """Derive the complete expected job matrix from the checked-out tests.yml."""
    spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    jobs = spec.get("jobs", {})
    if not jobs:
        raise ValueError("tests workflow has no jobs")
    expected: set[str] = set()
    for job_id, job in jobs.items():
        matrix = job.get("strategy", {}).get("matrix")
        if not matrix:
            expected.add(job_id)
            continue
        if set(matrix) - {"os", "python-version", "include", "exclude"}:
            raise ValueError(f"unsupported matrix axes in {job_id}")
        if "os" not in matrix or "python-version" not in matrix:
            raise ValueError(f"unsupported matrix in {job_id}")
        combos = {
            (str(system), str(py))
            for system in matrix["os"]
            for py in matrix["python-version"]
        }
        for cell in matrix.get("include", []):
            if not {"os", "python-version"} <= set(cell):
                raise ValueError(f"ambiguous include in {job_id}")
            combos.add((str(cell["os"]), str(cell["python-version"])))
        for cell in matrix.get("exclude", []):
            combos.discard((str(cell["os"]), str(cell["python-version"])))
        expected.update(f"{job_id} ({system}, {py})" for system, py in combos)
    if not expected:
        raise ValueError("empty test matrix")
    return expected


def validate_run(
    run: dict[str, Any], jobs: list[dict[str, Any]], sha: str, expected: set[str]
) -> None:
    """Enforce exact immutable source identity and complete successful matrix."""
    if (
        run.get("head_sha") != sha
        or run.get("event") != "push"
        or run.get("head_branch") != "main"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("source CI run does not match successful main push at tag SHA")
    observed = [str(job.get("name", "")) for job in jobs]
    if len(observed) != len(set(observed)):
        raise ValueError("duplicate job name in CI attempt")
    if set(observed) != expected:
        raise ValueError(
            "CI job coverage mismatch: missing="
            + repr(sorted(expected - set(observed)))
            + " extra="
            + repr(sorted(set(observed) - expected))
        )
    if any(
        j.get("status") != "completed" or j.get("conclusion") != "success" for j in jobs
    ):
        raise ValueError("CI contains incomplete, skipped, cancelled or failed job")


def _request_json(repo: str, suffix: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/" + suffix,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "markitdown-gc-release-ci-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def verify(repo: str, sha: str, token: str, workflow: Path) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository name")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("release SHA must be a full lowercase commit ID")
    if not token:
        raise ValueError("GitHub actions-read token required")
    expected = expected_jobs(workflow)
    query = urllib.parse.urlencode({"event": "push", "head_sha": sha, "per_page": 100})
    data = _request_json(repo, "actions/workflows/tests.yml/runs?" + query, token)
    if data.get("total_count", 0) > 100:
        raise ValueError("too many source runs to verify exhaustively")
    candidates = sorted(
        data.get("workflow_runs", []), key=lambda run: run.get("id", 0), reverse=True
    )
    for run in candidates:
        if (
            run.get("head_sha") != sha
            or run.get("head_branch") != "main"
            or run.get("event") != "push"
            or run.get("conclusion") != "success"
        ):
            continue
        attempt = int(run.get("run_attempt") or 1)
        run_id = int(run["id"])
        jobs: list[dict[str, Any]] = []
        page = 1
        while True:
            part = _request_json(
                repo,
                f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}",
                token,
            ).get("jobs", [])
            jobs.extend(part)
            if len(part) < 100:
                break
            page += 1
            if page > 10:
                raise ValueError("CI job pagination budget exceeded")
        validate_run(run, jobs, sha, expected)
        return {
            "status": "PASS",
            "source_sha": sha,
            "workflow": "tests.yml",
            "run_id": run_id,
            "run_attempt": attempt,
            "successful_jobs": len(jobs),
        }
    raise ValueError("no successful main-push tests.yml run on exact release SHA")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/tests.yml"),
    )
    args = parser.parse_args()
    try:
        proof = verify(
            args.repo, args.sha, os.environ.get("GH_TOKEN", ""), args.workflow
        )
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"HOLD: exact source CI verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
