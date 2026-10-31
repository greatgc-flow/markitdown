"""Read-only post-release registry metadata parity audit (NOT byte attestation).

Compare public PyPI JSON SHA-256 values against GitHub Release asset digest
metadata for the SAME wheel and source archive filenames. No release/tag,
download of distribution binaries, or package installation is performed.

This audit trusts the registries' reported digest metadata. A PASS_RECORDED
is not an independent recomputation of release binary bytes or source signing.

Examples (after an OWNER-approved release only):
    python .github/scripts/audit_release_artifact_parity.py --version 0.3.3
    python .github/scripts/audit_release_artifact_parity.py --version 0.3.3 --evidence parity.json

Exit codes: 0 PASS_RECORDED, 2 HOLD (missing/mismatched/unavailable proof),
1 local argument/evidence-write error. --evidence is the ONLY file written.
The JSON evidence is byte-identical to the console output.
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

DEFAULT_REPO = "greatgc-flow/markitdown-gc"
DEFAULT_PROJECT = "markitdown-gc"
_MAX_JSON_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _validate_name(value: str, pattern: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"invalid {label}")
    return value


def _read_json(url: str, *, token: str = "") -> dict[str, Any]:
    """Fetch a bounded JSON metadata document from an exact known origin."""
    hostname = urllib.parse.urlsplit(url).hostname
    if hostname not in ("api.github.com", "pypi.org"):
        raise ValueError("unsupported metadata origin")
    headers = {
        "Accept": (
            "application/vnd.github+json"
            if hostname == "api.github.com"
            else "application/json"
        ),
        "User-Agent": "markitdown-gc-readonly-release-parity",
    }
    if hostname == "api.github.com" and token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read(_MAX_JSON_BYTES + 1)
    if len(data) > _MAX_JSON_BYTES:
        raise ValueError("release metadata response exceeded byte limit")
    document = json.loads(data.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("release metadata must be a JSON object")
    return document


def _distribution_filename(filename: Any, project: str, version: str) -> bool:
    """Accept this project's fixed-version wheels and exact source archive."""
    if not isinstance(filename, str) or "/" in filename or "\\" in filename:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", filename):
        return False
    prefix = re.escape(project.replace("-", "_")) + "-" + re.escape(version)
    return bool(re.fullmatch(prefix + r"(-[A-Za-z0-9_.+-]+\.whl|\.tar\.gz)", filename))


def compare_metadata(
    pypi: dict[str, Any], github: dict[str, Any], *, project: str, version: str
) -> dict[str, Any]:
    """Pure fail-closed comparison; no network, files, or external side effects."""
    if (
        not isinstance(pypi, dict)
        or not isinstance(github, dict)
        or github.get("tag_name") != "v" + version
        or github.get("draft") is not False
        or github.get("prerelease") is not False
        or not github.get("published_at")
    ):
        raise ValueError("GitHub release is absent, draft, prerelease or wrong tag")
    info = pypi.get("info")
    if (
        not isinstance(info, dict)
        or info.get("version") != version
        or re.sub(r"[-_.]+", "-", str(info.get("name", "")).lower())
        != re.sub(r"[-_.]+", "-", project.lower())
    ):
        raise ValueError("PyPI project name/version mismatch")

    pypi_urls, assets = pypi.get("urls"), github.get("assets")
    if not isinstance(pypi_urls, list) or not isinstance(assets, list):
        raise ValueError("missing PyPI files or GitHub release assets")

    def read_rows(rows: list[Any], source: str) -> dict[str, tuple[int, str]]:
        index: dict[str, tuple[int, str]] = {}
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError(f"{source} contains invalid asset metadata")
            filename = item.get("filename" if source == "PyPI" else "name")
            if not isinstance(filename, str):
                raise ValueError(f"{source} has no asset filename")
            is_distribution = filename.endswith((".whl", ".tar.gz"))
            if not is_distribution:
                continue  # E.g. unrelated release checksums or provenance JSON.
            if not _distribution_filename(filename, project, version):
                raise ValueError(f"{source} contains unexpected distribution filename")
            if filename in index:
                raise ValueError(f"{source} contains duplicate distribution filename")
            size = item.get("size")
            if type(size) is not int or size < 1:
                raise ValueError(f"{source} has missing or invalid asset size")
            if source == "PyPI":
                if item.get("yanked") is not False:
                    raise ValueError("PyPI distribution is yanked or unverified")
                digests = item.get("digests")
                digest = digests.get("sha256") if isinstance(digests, dict) else None
            else:
                if item.get("state") != "uploaded":
                    raise ValueError("GitHub release asset not uploaded")
                raw = item.get("digest")
                digest = (
                    raw[7:]
                    if isinstance(raw, str) and raw.startswith("sha256:")
                    else None
                )
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ValueError(f"{source} lacks valid sha256 metadata")
            index[filename] = (size, digest)
        return index

    px = read_rows(pypi_urls, "PyPI")
    gx = read_rows(assets, "GitHub")
    if not px or set(px) != set(gx):
        raise ValueError("PyPI/GitHub distribution filename coverage mismatch")
    if not any(f.endswith(".whl") for f in px) or not any(
        f.endswith(".tar.gz") for f in px
    ):
        raise ValueError("release requires both wheel and source archive")
    for filename in sorted(px):
        if px[filename] != gx[filename]:
            raise ValueError("PyPI/GitHub SHA-256 or size mismatch for " + filename)
    return {
        "status": "PASS_RECORDED",
        "source": "registry-reported SHA-256 and file sizes, not independent byte hashing",
        "project": project,
        "version": version,
        "tag": github["tag_name"],
        "distributions": [
            {"filename": filename, "size": px[filename][0], "sha256": px[filename][1]}
            for filename in sorted(px)
        ],
    }


def audit(repo: str, project: str, version: str, token: str = "") -> dict[str, Any]:
    repo = _validate_name(repo, r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "repository")
    project = _validate_name(project, r"[A-Za-z0-9][A-Za-z0-9_.-]*", "project")
    version = _validate_name(version, r"[0-9]+\.[0-9]+\.[0-9]+", "version")
    try:
        tag = "v" + version
        github = _read_json(
            "https://api.github.com/repos/"
            + repo
            + "/releases/tags/"
            + urllib.parse.quote(tag),
            token=token,
        )
        pypi = _read_json(
            "https://pypi.org/pypi/"
            + urllib.parse.quote(project)
            + "/"
            + urllib.parse.quote(version)
            + "/json",
        )
        return compare_metadata(pypi, github, project=project, version=version)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "status": "HOLD",
            "project": project,
            "version": version,
            "reason": str(exc),
            "source": "registry-reported metadata, no independent byte attestation",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--version", required=True)
    parser.add_argument("--evidence", type=Path, help="Optional exact stdout JSON file")
    args = parser.parse_args(argv)
    try:
        result = audit(
            args.repo, args.project, args.version, os.environ.get("GH_TOKEN", "")
        )
        output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.evidence is not None:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_bytes(output.encode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: unable to run or write release audit: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0 if result["status"] == "PASS_RECORDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
