"""CI workflow contract: cancel stale PR checks, never main/manual runs.

This is a static expression gate; GitHub run history remains the real
behavioral verification. No live GitHub API, network, or OS process is used.
"""

from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
EXPECTED_GROUP = (
    "  group: ${{ github.workflow }}-"
    "${{ github.event.pull_request.number || github.run_id }}"
)
EXPECTED_CANCEL = "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}"


@pytest.mark.parametrize("workflow", ("tests.yml", "pre-commit.yml"))
def test_pr_supersession_is_scoped_to_its_workflow_and_run(workflow):
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    assert text.count("\nconcurrency:\n") == 1
    block = text.split("\nconcurrency:\n", 1)[1].split("\njobs:\n", 1)[0]
    assert EXPECTED_GROUP in block
    assert EXPECTED_CANCEL in block
    # Workflow-name grouping keeps independent tests/pre-commit gates distinct.
    assert "github.workflow" in EXPECTED_GROUP
    # Unique run IDs avoid grouping/auto-cancelling push/dispatch jobs.
    assert "github.run_id" in EXPECTED_GROUP


def test_matrix_remains_full_and_all_job_failures_visible():
    tests = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    assert tests.count("fail-fast: false") == 3
    assert "windows-11-vs2026-arm" in tests
    for item in ("3.10", "3.11", "3.12", "3.13", "3.14", "3.15"):
        assert f'"{item}"' in tests
