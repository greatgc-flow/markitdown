"""Conversion with metadata hints must work without Magika on every runtime."""

import io
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from markitdown import MarkItDown, StreamInfo, UnsupportedFormatException


@pytest.fixture
def without_magika(monkeypatch):
    monkeypatch.setitem(sys.modules, "magika", None)
    converter = MarkItDown()
    assert converter._magika is None
    return converter


def test_import_and_construct_without_magika():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.modules['magika'] = None; "
            "from markitdown import MarkItDown; "
            "assert MarkItDown()._magika is None",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "extension, data, expected",
    [
        (".html", b"<h1>Hello</h1>", "# Hello"),
        (".csv", b"a,b\n1,2\n", "| a | b |\n| --- | --- |\n| 1 | 2 |"),
        (".txt", "Hello 한글".encode("utf-8"), "Hello 한글"),
    ],
)
def test_extension_conversion_without_magika(without_magika, extension, data, expected):
    result = without_magika.convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension=extension)
    )
    assert result.markdown == expected


def test_docx_conversion_without_magika(without_magika):
    path = Path(__file__).parent / "test_files" / "test.docx"
    with path.open("rb") as stream:
        result = without_magika.convert_stream(
            stream, stream_info=StreamInfo(extension=".docx")
        )
    assert "AutoGen" in result.markdown


def test_mimetype_conversion_without_magika(without_magika):
    result = without_magika.convert_stream(
        io.BytesIO(b"<h1>Hello</h1>"), stream_info=StreamInfo(mimetype="text/html")
    )
    assert result.markdown == "# Hello"


def test_stream_info_preserved_without_magika(without_magika):
    info = StreamInfo(
        extension=".txt",
        mimetype="text/plain",
        charset="utf-8",
        filename="notes.txt",
        local_path="/notes.txt",
        url="https://example.com/notes.txt",
    )
    stream = io.BytesIO(b"prefix Hello")
    stream.seek(7)
    assert without_magika._get_stream_info_guesses(stream, info) == [info]
    assert stream.tell() == 7


@pytest.mark.parametrize(
    "info",
    [StreamInfo(extension=".html"), StreamInfo(mimetype="text/html")],
)
def test_metadata_enrichment_without_charset_invention(without_magika, info):
    guesses = without_magika._get_stream_info_guesses(io.BytesIO(b"<h1>x</h1>"), info)
    assert len(guesses) == 1
    assert guesses[0].extension in (".html", ".htm")
    assert guesses[0].mimetype == "text/html"
    assert guesses[0].charset is None
    assert info.charset is None


def test_unknown_stream_without_hints_is_unsupported(without_magika):
    data = b"\x00\xff\x80unknown"
    assert without_magika._get_stream_info_guesses(io.BytesIO(data), StreamInfo()) == [
        StreamInfo()
    ]
    with pytest.raises(UnsupportedFormatException):
        without_magika.convert_stream(io.BytesIO(data))


def test_magika_initialization_errors_propagate(monkeypatch):
    module = ModuleType("magika")

    def fail():
        raise RuntimeError("Magika initialization failed")

    module.Magika = fail
    monkeypatch.setitem(sys.modules, "magika", module)
    with pytest.raises(RuntimeError, match="Magika initialization failed"):
        MarkItDown()
