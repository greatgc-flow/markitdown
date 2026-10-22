"""HWP routing and owned HWPX integration, with optional backend coverage."""

import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from markitdown import (
    FileConversionException,
    MarkItDown,
    MissingDependencyException,
    StreamInfo,
)
from markitdown.converters import HwpConverter
from markitdown.converters._hwp_converter import _replace_image_references

from ._hwpx_builder import build_hwpx


@pytest.fixture
def backend():
    if os.environ.get("MARKITDOWN_REQUIRE_HWP") == "1":
        try:
            import rhwp
        except ImportError:
            pytest.fail("rhwp is required on the designated HWP job")
        return rhwp
    return pytest.importorskip("rhwp")


@pytest.mark.parametrize("extension", [".hwp", ".hwpx", ".HWP", ".HWPX"])
def test_accepts_extension(extension):
    stream = io.BytesIO(b"not read")
    assert HwpConverter().accepts(stream, StreamInfo(extension=extension))
    assert stream.tell() == 0


@pytest.mark.parametrize(
    "mimetype",
    [
        "application/x-hwp",
        "application/x-hwp-v5",
        "application/haansofthwp",
        "application/vnd.hancom.hwp",
        "application/vnd.hancom.hwpx",
        "application/hwpx",
        "application/x-hwpx",
        "application/hwp+zip",
        "APPLICATION/HWPX; version=1",
    ],
)
def test_accepts_mimetype(mimetype):
    assert HwpConverter().accepts(io.BytesIO(), StreamInfo(mimetype=mimetype))


@pytest.mark.parametrize(
    "info",
    [
        StreamInfo(extension=".zip"),
        StreamInfo(extension=".docx"),
        StreamInfo(mimetype="application/zip"),
        StreamInfo(),
    ],
)
def test_rejects_other_formats(info):
    assert not HwpConverter().accepts(io.BytesIO(), info)


@pytest.mark.parametrize("extension", [".hwp", ".hwpx"])
def test_missing_backend(monkeypatch, extension):
    monkeypatch.setitem(sys.modules, "rhwp", None)
    info = StreamInfo(extension=extension)
    stream = io.BytesIO(b"unread")
    with pytest.raises(MissingDependencyException, match=r"markitdown\[hwp\]"):
        HwpConverter().convert(stream, info)
    assert stream.tell() == 0
    with pytest.raises(FileConversionException, match=r"markitdown\[hwp\]") as exc:
        MarkItDown().convert_stream(stream, stream_info=info)
    assert any(
        isinstance(attempt.exc_info[1], MissingDependencyException)
        for attempt in exc.value.attempts
        if attempt.exc_info
    )
    assert "markitdown[all]" not in str(exc.value)


def test_lazy_import_without_backend_in_fresh_process():
    code = """
import sys, io
sys.modules['rhwp'] = None
from markitdown import MarkItDown, StreamInfo, MissingDependencyException
from markitdown.converters import HwpConverter
MarkItDown()
try:
    HwpConverter().convert(io.BytesIO(), StreamInfo(extension='.hwp'))
except MissingDependencyException as exc:
    assert 'markitdown[hwp]' in str(exc)
else:
    raise AssertionError('Missing dependency was not reported')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("extension", [".hwp", ".hwpx"])
@pytest.mark.parametrize("fail", [False, True])
def test_direct_conversion_reads_remaining_bytes(monkeypatch, extension, fail):
    parse = Mock()
    if fail:
        parse.side_effect = ValueError("invalid document")
    else:
        parse.return_value.to_ir.return_value.to_markdown.return_value = (
            "| a |\n| --- |\n\n<table><tr><td colspan=2>x</td></tr></table>"
            "\n![picture](bin://3)"
        )
    monkeypatch.setitem(
        sys.modules, "rhwp", SimpleNamespace(Document=SimpleNamespace(from_bytes=parse))
    )
    stream = io.BytesIO(b"prefix document")
    stream.seek(7)
    if fail:
        with pytest.raises(ValueError, match="invalid document"):
            HwpConverter().convert(stream, StreamInfo(extension=extension))
    else:
        result = HwpConverter().convert(stream, StreamInfo(extension=extension))
        assert result.markdown == (
            "| a |\n| --- |\n\n<table><tr><td colspan=2>x</td></tr></table>"
            "\n[Image 3 omitted]"
        )
    parse.assert_called_once_with(b"document")
    assert stream.tell() == len(stream.getvalue())


@pytest.mark.parametrize("extension", [".hwp", ".hwpx"])
def test_registered_conversion_with_fake_backend(monkeypatch, extension):
    parse = Mock()
    parse.return_value.to_ir.return_value.to_markdown.return_value = "Korean document"
    monkeypatch.setitem(
        sys.modules, "rhwp", SimpleNamespace(Document=SimpleNamespace(from_bytes=parse))
    )
    result = MarkItDown().convert_stream(
        io.BytesIO(b"synthetic routing input"), stream_info=StreamInfo(extension=extension)
    )
    assert result.markdown == "Korean document"
    parse.assert_called_once_with(b"synthetic routing input")


@pytest.mark.parametrize(
    "source, expected",
    [
        ("![](bin://1)", "[Image 1 omitted]"),
        ("![x](bin://3)", "[Image 3 omitted]"),
        ("![a](bin://2) ![b](bin://9)", "[Image 2 omitted] [Image 9 omitted]"),
        ("![x](https://e.com/a.png)", "![x](https://e.com/a.png)"),
        ("text bin://3 [link](bin://3)", "text bin://3 [link](bin://3)"),
        ("![x](bin://other)", "![x](bin://other)"),
    ],
)
def test_image_placeholders(source, expected):
    assert _replace_image_references(source) == expected


def test_generated_hwpx_backend_output(backend):
    markdown = backend.Document.from_bytes(build_hwpx()).to_ir().to_markdown()
    assert "| 항목 | 값 |" in markdown
    assert "| 사과 | 둘 |" in markdown
    assert "<table" in markdown
    assert 'colspan="2"' in markdown
    assert markdown.index("첫 번째") < markdown.index("두 번째") < markdown.index("마지막")


@pytest.mark.parametrize(
    "info",
    [StreamInfo(extension=".hwpx"), StreamInfo(mimetype="application/hwp+zip")],
)
def test_generated_hwpx_registered_conversion(backend, info):
    result = MarkItDown().convert_stream(io.BytesIO(build_hwpx()), stream_info=info)
    assert "| 항목 | 값 |" in result.markdown
    assert "<table" in result.markdown
    assert result.markdown.index("첫 번째") < result.markdown.index("마지막")


@pytest.mark.parametrize(
    "extension, data", [(".hwpx", b"PK\x03\x04truncated"), (".hwp", b"\xd0\xcf\x11\xe0")]
)
def test_truncated_documents_fail(backend, extension, data):
    with pytest.raises(FileConversionException):
        MarkItDown().convert_stream(
            io.BytesIO(data), stream_info=StreamInfo(extension=extension)
        )


def test_owner_authored_hwp5_fixture(backend):
    path = Path(__file__).parent / "test_files" / "test_hwp5.hwp"
    if not path.exists():
        pytest.skip("owner-authored HWP5 fixture pending")
    assert MarkItDown().convert(path).markdown.strip()


def test_private_samples_local_smoke(backend):
    directory = os.environ.get("MARKITDOWN_HWP_SMOKE_DIR")
    if not directory or os.environ.get("GITHUB_ACTIONS"):
        pytest.skip("local private sample smoke test is opt-in")
    paths = sorted(
        p for p in Path(directory).iterdir() if p.suffix.lower() in (".hwp", ".hwpx")
    )
    assert paths, "No HWP/HWPX samples found"
    for path in paths:
        assert MarkItDown().convert(path).markdown.strip()
