"""ZIP conversion and forwarding options to nested converters."""

import io
import os
import zipfile
from typing import Any, BinaryIO, Optional

import pytest

from markitdown import (
    DocumentConverter,
    DocumentConverterResult,
    FileConversionException,
    ZipLimitExceeded,
    ZipLimits,
    MarkItDown,
    StreamInfo,
)
from markitdown.converters._zip_converter import ZipConverter

# Nested conversion

TEST_FILES_DIR = os.path.join(os.path.dirname(__file__), "test_files")


class _RecordingConverter(DocumentConverter):
    def __init__(self):
        super().__init__()
        self.seen_kwargs = []

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        return (stream_info.extension or "").lower() == ".txt"

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        self.seen_kwargs.append(dict(kwargs))
        return DocumentConverterResult(markdown=file_stream.read().decode("utf-8"))


def test_zip_forwards_kwargs_to_nested_converters() -> None:
    markitdown = MarkItDown(enable_builtins=False)
    recorder = _RecordingConverter()
    markitdown.register_converter(recorder)
    markitdown.register_converter(ZipConverter(markitdown=markitdown))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "Hello world")
    buf.seek(0)

    result = markitdown.convert_stream(
        buf,
        stream_info=StreamInfo(mimetype="application/zip", extension=".zip"),
        keep_data_uris=True,
    )

    assert "Hello world" in result.markdown
    assert len(recorder.seen_kwargs) == 1
    assert recorder.seen_kwargs[0].get("keep_data_uris") is True


def test_zip_does_not_forward_outer_file_metadata() -> None:
    """The archive's own extension/url must not override a member's."""
    markitdown = MarkItDown(enable_builtins=False)
    recorder = _RecordingConverter()
    markitdown.register_converter(recorder)
    markitdown.register_converter(ZipConverter(markitdown=markitdown))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "Hello world")
    buf.seek(0)

    result = markitdown.convert_stream(
        buf,
        stream_info=StreamInfo(
            mimetype="application/zip",
            extension=".zip",
            url="https://example.com/archive.zip",
        ),
    )

    assert "Hello world" in result.markdown
    assert len(recorder.seen_kwargs) == 1
    # The member sees its own extension, not the archive's
    assert recorder.seen_kwargs[0].get("file_extension") == ".txt"
    assert "url" not in recorder.seen_kwargs[0]


def test_zip_member_reaches_its_own_converter() -> None:
    """A ZIP-based member (docx) must not be unpacked as a nested archive."""
    markitdown = MarkItDown()
    result = markitdown.convert(
        os.path.join(TEST_FILES_DIR, "test_files.zip"),
    )

    assert "## File: test.docx" in result.markdown
    # Raw OOXML parts would appear if the docx were treated as a plain zip
    assert "[Content_Types].xml" not in result.markdown


@pytest.mark.parametrize(
    "archive_url",
    [None, "https://example.test/archive.zip"],
    ids=["no_archive_url", "with_archive_url"],
)
def test_zip_member_docx_converts_as_docx(archive_url: Optional[str]) -> None:
    """A DOCX member converts via the DOCX converter, archive URL or not.

    Regression: the archive's own file_extension/url used to be forwarded into
    the nested convert_stream call, where they take precedence over the
    member's StreamInfo -- so the DOCX was re-selected as a ZIP and unpacked
    into its raw OOXML parts instead of being converted.
    """
    markitdown = MarkItDown()
    docx_path = os.path.join(TEST_FILES_DIR, "test.docx")
    standalone = markitdown.convert(docx_path).markdown

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(docx_path, "test.docx")
    buf.seek(0)

    result = markitdown.convert_stream(
        buf,
        stream_info=StreamInfo(extension=".zip", url=archive_url),
    ).markdown

    assert "## File: test.docx" in result
    # The member's real conversion output is present ...
    assert standalone.strip() in result
    # ... and it was not unpacked as a nested archive
    assert "word/document.xml" not in result
    assert "[Content_Types].xml" not in result


# Conversion regressions


def test_zip_duplicate_filenames_preserve_each_entry() -> None:
    """Same-named ZIP entries must retain their own content and archive order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("notes.txt", "First archived entry.")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("notes.txt", "Second archived entry.")
    buf.seek(0)

    result = MarkItDown().convert_stream(
        buf, stream_info=StreamInfo(extension=".zip", filename="duplicate.zip")
    )

    assert result.markdown == (
        "Content from the zip file `duplicate.zip`:\n\n"
        "## File: notes.txt\n\nFirst archived entry.\n\n"
        "## File: notes.txt\n\nSecond archived entry."
    )


def test_zip_stream_no_filename_header() -> None:
    """Regression test: ZipConverter must not render the literal string 'None'
    in the output header when the stream has no associated URL, local path, or
    filename (e.g. when called via convert_stream() without stream_info)."""
    markitdown = MarkItDown()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "Hello world")
    buf.seek(0)

    result = markitdown.convert_stream(
        buf, stream_info=StreamInfo(mimetype="application/zip")
    )
    assert result.markdown.startswith("Content from the zip file `(unknown)`:\n\n")
    assert "Hello world" in result.markdown


# Bounded ZIP conversion is an explicitly selected API policy. These tests
# use local, deterministic in-memory archives; no provider/network calls.
def _archive_bytes(**entries: bytes) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    buf.seek(0)
    return buf


def _convert_bounded(buf: io.BytesIO, limits: ZipLimits):
    return MarkItDown().convert_stream(
        buf, stream_info=StreamInfo(extension=".zip"), zip_limits=limits
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_entries": 0},
        {"max_archive_bytes": 0},
        {"max_member_bytes": -1},
        {"max_total_uncompressed_bytes": True},
        {"max_depth": 0},
        {"max_output_chars": None},
    ],
)
def test_zip_limits_reject_invalid_values(kwargs):
    with pytest.raises(ValueError):
        ZipLimits(**kwargs)


def test_zip_limits_accept_small_input_without_changing_markdown():
    archive = _archive_bytes(**{"notes.txt": b"one", "more.txt": b"two"})
    expected = (
        MarkItDown()
        .convert_stream(archive, stream_info=StreamInfo(extension=".zip"))
        .markdown
    )
    archive.seek(0)
    assert _convert_bounded(archive, ZipLimits()).markdown == expected


@pytest.mark.parametrize(
    "limits",
    [
        ZipLimits(max_entries=1),
        ZipLimits(max_member_bytes=3),
        ZipLimits(max_total_uncompressed_bytes=6),
        ZipLimits(max_output_chars=35),
    ],
)
def test_zip_limits_fail_closed_not_partial_markdown(limits):
    archive = _archive_bytes(**{"first.txt": b"four", "second.txt": b"five"})
    with pytest.raises(ZipLimitExceeded):
        _convert_bounded(archive, limits)


def test_zip_limits_reject_compressed_archive_before_open():
    archive = _archive_bytes(**{"data.txt": b"hello"})
    with pytest.raises(ZipLimitExceeded, match="archive"):
        _convert_bounded(
            archive,
            ZipLimits(max_archive_bytes=len(archive.getvalue()) - 1),
        )


def test_zip_limits_count_bytes_through_nested_archive():
    inner = _archive_bytes(**{"payload.txt": b"1234567890"}).getvalue()
    outer = _archive_bytes(**{"child.zip": inner})
    # Outer member counts as bytes; inner plaintext also consumes the same
    # total budget, so it cannot be concealed in a nested archive.
    with pytest.raises(ZipLimitExceeded):
        _convert_bounded(
            outer,
            ZipLimits(max_total_uncompressed_bytes=len(inner) + 5),
        )


def test_zip_limits_reject_excess_nesting_before_child_read():
    inner = _archive_bytes(**{"child.txt": b"Hello"}).getvalue()
    outer = _archive_bytes(**{"child.zip": inner})
    with pytest.raises(ZipLimitExceeded, match="depth"):
        _convert_bounded(outer, ZipLimits(max_depth=1))


def test_zip_limits_do_not_change_trusted_unbounded_default():
    archive = _archive_bytes(**{"large.txt": b"x" * 128})
    result = _convert_bounded(archive, ZipLimits(max_member_bytes=128))
    assert "x" * 128 in result.markdown


def test_zip_limits_reject_invalid_policy_object():
    with pytest.raises((TypeError, FileConversionException)):
        MarkItDown().convert_stream(
            _archive_bytes(**{"a.txt": b"x"}),
            stream_info=StreamInfo(extension=".zip"),
            zip_limits={"max_entries": 1},
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
