"""Layered `markitdown --guide`: quick card, per-topic sections and token budgets."""

import re
import subprocess
import sys
from importlib import resources

import pytest

from markitdown import MarkItDown

CARD_MAX = 2000
TOPIC_MAX = 3500
ALL_MAX = 12000
TOPICS = ["formats", "ocr", "errors", "install"]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "markitdown", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _packaged() -> str:
    return resources.files("markitdown").joinpath("GUIDE.md").read_text("utf-8")


def test_quick_card_is_default_and_small() -> None:
    result = _run("--guide")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) <= CARD_MAX
    assert len(result.stdout.splitlines()) <= 20
    assert result.stdout.startswith("MarkItDown:")
    assert "## " not in result.stdout


@pytest.mark.parametrize("topic", TOPICS)
def test_each_topic_is_selectable_and_within_budget(topic: str) -> None:
    result = _run("--guide", topic)
    assert result.returncode == 0, result.stderr
    assert 0 < len(result.stdout) <= TOPIC_MAX
    assert result.stdout.lstrip().startswith(f"## {topic}")
    # only that section
    assert len(re.findall(r"^## ", result.stdout, re.M)) == 1


def test_all_prints_everything_within_budget() -> None:
    result = _run("--guide", "all")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) <= ALL_MAX
    for topic in TOPICS:
        assert f"## {topic}" in result.stdout
    assert result.stdout.strip() == _packaged().strip()


def test_every_topic_named_in_card_exists() -> None:
    card = _run("--guide").stdout
    listed = re.search(r"topics: ([a-z ]+)", card)
    assert listed, "card must list topics"
    assert listed.group(1).split() == TOPICS


def test_unknown_topic_lists_topics_and_exits_2() -> None:
    result = _run("--guide", "nope")
    assert result.returncode == 2
    assert result.stdout == ""
    for topic in TOPICS:
        assert topic in result.stderr


def test_guide_is_exclusive_with_conversion_arguments() -> None:
    for extra in (["x.pdf"], ["-o", "o.md"], ["--ocr-report"], ["-x", ".pdf"]):
        result = _run("--guide", "formats", *extra)
        assert result.returncode == 2, extra
        assert result.stdout == ""


def test_help_points_to_guide() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert (
        "--guide" in result.stdout.splitlines()[-1] or "--guide" in result.stdout[-400:]
    )


def test_ocr_topic_is_operational() -> None:
    text = _run("--guide", "ocr").stdout
    for needle in ("--ocr-report", "-w", "peerhub ask", "[illegible]", "approval"):
        assert needle in text, needle


def _recipe(name: str) -> str:
    text = _run("--guide", "ocr").stdout
    match = re.search(rf"```python\n# recipe:{name}\n(.*?)```", text, re.S)
    assert match, f"recipe {name} missing from the ocr topic"
    return match.group(1)


def test_ocr_topic_recipes_run(tmp_path) -> None:
    from PIL import Image

    pdf = tmp_path / "scan.pdf"
    pages = [Image.new("RGB", (200, 200), "white") for _ in range(2)]
    pages[0].save(pdf, "PDF", resolution=72, save_all=True, append_images=pages[1:])
    report = tmp_path / "r.json"
    done = _run("--ocr-report", str(pdf), "-o", str(report))
    assert done.returncode == 0, done.stderr

    namespace = {"report_path": str(report)}
    exec(_recipe("read"), namespace)  # prints the verdict view
    assert namespace["view"]["decision"] == "ocr_recommended"

    out_dir = tmp_path / "work"
    namespace = {
        "pdf_path": str(pdf),
        "ids": ["p1", "p2"],
        "out_dir": str(out_dir),
        "dpi": 100,
    }
    exec(_recipe("render"), namespace)
    assert (out_dir / "p1.png").stat().st_size > 0
    assert (out_dir / "p2.png").stat().st_size > 0
    assert (out_dir / "manifest.json").exists()


def test_conversion_api_unaffected() -> None:
    assert MarkItDown() is not None


def test_install_topic_documents_external_programs() -> None:
    text = _run("--guide", "install").stdout
    for needle in (
        "exiftool",
        "Gyan.FFmpeg",
        "winget",
        "brew install",
        "apt install",
        "ko-KR",
        "EXIFTOOL_PATH",
    ):
        assert needle in text, needle
