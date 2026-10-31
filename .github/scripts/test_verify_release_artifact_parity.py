"""Offline fail-closed tests for the PyPI/GitHub registry digest parity audit."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import audit_release_artifact_parity as audit

PROJECT = "markitdown-gc"
VERSION = "0.3.3"
NAMES = (
    "markitdown_gc-0.3.3-py3-none-any.whl",
    "markitdown_gc-0.3.3.tar.gz",
)


def fixture() -> tuple[dict, dict]:
    hashes = ("a" * 64, "b" * 64)
    sizes = (1234, 4567)
    pypi = {
        "info": {"name": PROJECT, "version": VERSION},
        "urls": [
            {
                "filename": filename,
                "size": size,
                "yanked": False,
                "digests": {"sha256": digest},
            }
            for filename, size, digest in zip(NAMES, sizes, hashes)
        ],
    }
    github = {
        "tag_name": "v" + VERSION,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-31T00:00:00Z",
        "assets": [
            {
                "name": filename,
                "size": size,
                "state": "uploaded",
                "digest": "sha256:" + digest,
            }
            for filename, size, digest in zip(NAMES, sizes, hashes)
        ],
    }
    return pypi, github


class TestRegistryArtifactParity(unittest.TestCase):
    def assertHold(self, pypi: dict, github: dict, part: str) -> None:
        with self.assertRaisesRegex(ValueError, part):
            audit.compare_metadata(pypi, github, project=PROJECT, version=VERSION)

    def test_exact_wheel_and_sdist_metadata_match(self) -> None:
        pypi, github = fixture()
        result = audit.compare_metadata(pypi, github, project=PROJECT, version=VERSION)
        self.assertEqual(result["status"], "PASS_RECORDED")
        self.assertEqual(len(result["distributions"]), 2)
        self.assertEqual(
            {entry["filename"] for entry in result["distributions"]}, set(NAMES)
        )
        self.assertIn("not independent byte hashing", result["source"])

    def test_hash_mismatch_holds(self) -> None:
        pypi, github = fixture()
        github["assets"][0]["digest"] = "sha256:" + "c" * 64
        self.assertHold(pypi, github, "SHA-256 or size mismatch")

    def test_size_mismatch_holds(self) -> None:
        pypi, github = fixture()
        github["assets"][0]["size"] += 1
        self.assertHold(pypi, github, "SHA-256 or size mismatch")

    def test_missing_unparseable_or_other_digest_holds(self) -> None:
        for value in (None, "", "md5:" + "f" * 32, "sha256:aaa"):
            with self.subTest(value=value):
                pypi, github = fixture()
                github["assets"][0]["digest"] = value
                self.assertHold(pypi, github, "lacks valid sha256")

    def test_missing_file_or_duplicate_holds(self) -> None:
        pypi, github = fixture()
        github["assets"].pop()
        self.assertHold(pypi, github, "coverage mismatch")
        pypi, github = fixture()
        github["assets"].append(copy.deepcopy(github["assets"][0]))
        self.assertHold(pypi, github, "duplicate distribution")

    def test_wrong_tag_draft_prerelease_or_unpublished_holds(self) -> None:
        for key, wrong in (
            ("tag_name", "v0.3.2"),
            ("draft", True),
            ("prerelease", True),
            ("published_at", None),
        ):
            with self.subTest(key=key):
                pypi, github = fixture()
                github[key] = wrong
                self.assertHold(pypi, github, "absent, draft, prerelease")

    def test_pypi_wrong_name_or_version_holds(self) -> None:
        for field, wrong in (("name", "other-project"), ("version", "0.3.2")):
            with self.subTest(field=field):
                pypi, github = fixture()
                pypi["info"][field] = wrong
                self.assertHold(pypi, github, "project name/version mismatch")

    def test_unexpected_distribution_or_yanked_holds(self) -> None:
        pypi, github = fixture()
        github["assets"].append(
            {
                "name": "other-0.3.3-py3-none-any.whl",
                "size": 55,
                "digest": "sha256:" + "f" * 64,
                "state": "uploaded",
            }
        )
        self.assertHold(pypi, github, "unexpected distribution")
        pypi, github = fixture()
        pypi["urls"][1]["yanked"] = True
        self.assertHold(pypi, github, "yanked")

    def test_missing_one_artifact_kind_holds(self) -> None:
        pypi, github = fixture()
        pypi["urls"] = pypi["urls"][:1]
        github["assets"] = github["assets"][:1]
        self.assertHold(pypi, github, "both wheel and source")

    def test_github_non_uploaded_asset_holds(self) -> None:
        pypi, github = fixture()
        github["assets"][0]["state"] = "new"
        self.assertHold(pypi, github, "not uploaded")

    def test_unrelated_ancillary_asset_does_not_change_distribution_parity(self) -> None:
        pypi, github = fixture()
        github["assets"].append(
            {"name": "provenance.json", "digest": None, "size": 7}
        )
        result = audit.compare_metadata(pypi, github, project=PROJECT, version=VERSION)
        self.assertEqual(result["status"], "PASS_RECORDED")

    def test_audit_reads_only_exact_api_metadata_origins(self) -> None:
        pypi, github = fixture()
        urls = []

        def mocked(url, *, token=""):
            urls.append((url, token))
            return github if url.startswith("https://api.github.com/") else pypi

        with patch.object(audit, "_read_json", side_effect=mocked):
            result = audit.audit(
                audit.DEFAULT_REPO, audit.DEFAULT_PROJECT, VERSION, token="secret"
            )
        self.assertEqual(result["status"], "PASS_RECORDED")
        self.assertEqual(
            urls,
            [
                (
                    "https://api.github.com/repos/greatgc-flow/markitdown-gc/"
                    "releases/tags/v0.3.3",
                    "secret",
                ),
                ("https://pypi.org/pypi/markitdown-gc/0.3.3/json", ""),
            ],
        )

    def test_network_error_results_in_hold(self) -> None:
        with patch.object(audit, "_read_json", side_effect=OSError("offline")):
            result = audit.audit(audit.DEFAULT_REPO, PROJECT, VERSION)
        self.assertEqual(result["status"], "HOLD")

    def test_invalid_repo_version_holds_without_network_call(self) -> None:
        with patch.object(audit, "_read_json") as mock:
            with self.assertRaisesRegex(ValueError, "invalid version"):
                audit.audit(audit.DEFAULT_REPO, PROJECT, "../secret")
            with self.assertRaisesRegex(ValueError, "invalid repository"):
                audit.audit("https://example.org/a", PROJECT, VERSION)
            mock.assert_not_called()

    def test_stdout_and_evidence_are_byte_identical(self) -> None:
        pypi, github = fixture()
        proof = audit.compare_metadata(pypi, github, project=PROJECT, version=VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audit.json"
            stdout = io.StringIO()
            with patch.object(audit, "audit", return_value=proof), redirect_stdout(
                stdout
            ):
                status = audit.main(["--version", VERSION, "--evidence", str(out)])
            self.assertEqual(status, 0)
            self.assertEqual(out.read_bytes(), stdout.getvalue().encode("utf-8"))
            self.assertEqual(json.loads(out.read_text())["status"], "PASS_RECORDED")

    def test_hold_exit_code_and_matching_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "hold.json"
            stdout = io.StringIO()
            proof = {"status": "HOLD", "reason": "PyPI file missing"}
            with patch.object(audit, "audit", return_value=proof), redirect_stdout(
                stdout
            ):
                status = audit.main(["--version", VERSION, "--evidence", str(out)])
            self.assertEqual(status, 2)
            self.assertEqual(out.read_bytes(), stdout.getvalue().encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
