"""ZIP conversion and forwarding options to nested converters."""

import io
import os
import zipfile
from typing import Any, BinaryIO, Optional

import pytest

from markitdown import (
    DocumentConverter,
    DocumentConverterResult,
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


def test_zip_source_url_secrets_are_not_rendered() -> None:
    markitdown = MarkItDown(enable_builtins=False)
    markitdown.register_converter(_RecordingConverter())
    converter = ZipConverter(markitdown=markitdown)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("hello.txt", "Hello world")
    buf.seek(0)

    result = converter.convert(
        buf,
        StreamInfo(
            extension=".zip",
            url="https://user:pw@example.com/a.zip?sig=SECRET#frag",
        ),
    ).markdown

    assert result.startswith("Content from the zip file `(remote archive)`:")
    assert "Hello world" in result
    assert all(secret not in result for secret in ("pw", "SECRET", "frag"))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))


# Security/resource-budget regression cases (all synthetic, no network).
def _nested_archive(filename: str, payload: bytes) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(filename, payload)
    buf.seek(0)
    return buf


def _zip_markdown(stream: io.BytesIO, **options: Any) -> str:
    return (
        MarkItDown()
        .convert_stream(
            stream,
            stream_info=StreamInfo(extension=".zip", filename="input.zip"),
            **options,
        )
        .markdown
    )


def test_zip_resource_budget_normal_archive_with_small_limits() -> None:
    buf = _nested_archive("notes.txt", b"Hello")
    out = _zip_markdown(
        buf,
        zip_max_entries=1,
        zip_max_member_bytes=10,
        zip_max_total_bytes=10,
        zip_max_depth=1,
        zip_max_output_chars=120,
    )
    assert "## File: notes.txt" in out and "Hello" in out


def test_zip_rejects_oversize_member_before_converting() -> None:
    from markitdown._exceptions import ResourceLimitException

    buf = _nested_archive("notes.txt", b"x" * 20)
    with pytest.raises(ResourceLimitException, match="member"):
        _zip_markdown(buf, zip_max_member_bytes=10)


def test_zip_rejects_too_many_entries_before_converting() -> None:
    from markitdown._exceptions import ResourceLimitException

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("one.txt", "one")
        archive.writestr("two.txt", "two")
    buf.seek(0)

    with pytest.raises(ResourceLimitException, match="entries"):
        _zip_markdown(buf, zip_max_entries=1)


def test_zip_nested_archives_share_total_uncompressed_budget() -> None:
    from markitdown._exceptions import ResourceLimitException

    inner = _nested_archive("inner.txt", b"nested")
    outer = _nested_archive("nested.zip", inner.getvalue())
    # Enough for outer's stored inner.zip; not enough to include inner.txt.
    with pytest.raises(ResourceLimitException, match="total"):
        _zip_markdown(
            outer,
            zip_max_total_bytes=len(inner.getvalue()) + 2,
            zip_max_member_bytes=1024 * 1024,
        )


def test_zip_rejects_nested_depth_without_silent_skip() -> None:
    from markitdown._exceptions import ResourceLimitException

    inner = _nested_archive("notes.txt", b"nested")
    outer = _nested_archive("nested.zip", inner.getvalue())
    with pytest.raises(ResourceLimitException, match="depth"):
        _zip_markdown(outer, zip_max_depth=1)


def test_zip_output_budget_bounds_accumulated_markdown() -> None:
    from markitdown._exceptions import ResourceLimitException

    buf = _nested_archive("notes.txt", b"some words")
    with pytest.raises(ResourceLimitException, match="output"):
        _zip_markdown(buf, zip_max_output_chars=30)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "many"])
def test_zip_rejects_invalid_budget_values(invalid: Any) -> None:
    from markitdown._exceptions import ResourceLimitException

    buf = _nested_archive("notes.txt", b"Hello")
    with pytest.raises(ResourceLimitException, match="max_entries"):
        _zip_markdown(buf, zip_max_entries=invalid)


# Central-directory metadata must be bounded before ZipFile builds ZipInfo objects.
def _patch_eocd_counts(stream: io.BytesIO, count: int) -> io.BytesIO:
    import struct

    data = bytearray(stream.getvalue())
    offset = data.rfind(b"PK" + bytes((5, 6)))
    assert offset >= 0
    struct.pack_into("<HH", data, offset + 8, count, count)
    return io.BytesIO(data)


def test_zip_preflight_accepts_empty_archive_and_embedded_signature_comment():
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.comment = b"Fake PK" + bytes((5, 6)) + b" in a comment"
    empty.seek(0)
    assert "Content from the zip file" in _zip_markdown(empty, zip_max_entries=1)


def test_zip_preflight_supports_prefixed_zip_with_comment():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("hello.txt", b"Hello")
        archive.comment = b"ordinary zip comment"
    prefixed = io.BytesIO(b"MZ demo-prefix" + buf.getvalue())
    assert "Hello" in _zip_markdown(prefixed, zip_max_directory_bytes=512)


def test_zip_preflight_rejects_claimed_too_many_entries_before_zipfile(monkeypatch):
    from markitdown._exceptions import ResourceLimitException

    buf = _patch_eocd_counts(_nested_archive("hello.txt", b"ok"), 200)

    def forbidden_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed before preflight")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(ResourceLimitException, match="entries"):
        _zip_markdown(buf, zip_max_entries=2)


def test_zip_preflight_rejects_central_directory_count_mismatch():
    from markitdown._exceptions import ResourceLimitException

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("one.txt", b"a")
        archive.writestr("two.txt", b"b")
    buf.seek(0)
    understated = _patch_eocd_counts(buf, 1)
    with pytest.raises(ResourceLimitException, match="entry count mismatch"):
        _zip_markdown(understated, zip_max_entries=10)


def test_zip_preflight_rejects_oversize_central_directory_before_zipfile(
    monkeypatch,
):
    from markitdown._exceptions import ResourceLimitException

    buf = _nested_archive("notes.txt", b"ok")

    def forbidden_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed before preflight")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(ResourceLimitException, match="central directory byte"):
        _zip_markdown(buf, zip_max_directory_bytes=1)


def test_zip_preflight_rejects_declared_expansion_before_zipfile(monkeypatch):
    from markitdown._exceptions import ResourceLimitException
    import struct

    buf = _nested_archive("notes.txt", b"ok")
    data = bytearray(buf.getvalue())
    central = data.find(b"PK" + bytes((1, 2)))
    assert central >= 0
    struct.pack_into("<I", data, central + 24, 1024 * 1024)

    def forbidden_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed before preflight")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(ResourceLimitException, match="member expanded"):
        _zip_markdown(io.BytesIO(data), zip_max_member_bytes=100)


@pytest.mark.parametrize("bad", [0, -1, True, "many"])
def test_zip_preflight_rejects_invalid_directory_budget(bad):
    from markitdown._exceptions import ResourceLimitException

    buf = _nested_archive("notes.txt", b"ok")
    with pytest.raises(ResourceLimitException, match="zip_max_directory_bytes"):
        _zip_markdown(buf, zip_max_directory_bytes=bad)


def test_zip_preflight_rejects_zip64_sentinel():
    from markitdown._exceptions import ResourceLimitException

    buf = _patch_eocd_counts(_nested_archive("notes.txt", b"ok"), 65535)
    with pytest.raises(ResourceLimitException, match="ZIP64"):
        _zip_markdown(buf, zip_max_entries=100000)
