import os
import tempfile
from typing import Any, BinaryIO

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import MissingDependencyException
from .._stream_info import StreamInfo

ACCEPTED_FILE_EXTENSIONS = [".hwp", ".hwpx"]
ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/x-hwp",
    "application/x-hwp-v5",
    "application/haansofthwp",
    "application/vnd.hancom.hwp",
    "application/vnd.hancom.hwpx",
    "application/hwpx",
    "application/x-hwpx",
    "application/hwp+zip",
]


class HwpConverter(DocumentConverter):
    """Convert HWP5 and HWPX using the optional pure-Python parser."""

    def accepts(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> bool:
        extension = (stream_info.extension or "").lower()
        mimetype = (stream_info.mimetype or "").lower()
        return extension in ACCEPTED_FILE_EXTENSIONS or any(
            mimetype.startswith(prefix) for prefix in ACCEPTED_MIME_TYPE_PREFIXES
        )

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        try:
            import hwp_hwpx_parser as parser
        except ImportError as exc:
            raise MissingDependencyException(
                "HwpConverter requires hwp-hwpx-parser. "
                "Install it with pip install 'markitdown[hwp]'."
            ) from exc

        from .._media import check_media_options

        check_media_options(kwargs)
        data = file_stream.read()
        suffix = (stream_info.extension or "").lower()
        if suffix not in ACCEPTED_FILE_EXTENSIONS:
            mimetype = (stream_info.mimetype or "").lower()
            if any(token in mimetype for token in ("hwpx", "hwp+zip")):
                suffix = ".hwpx"
            elif any(mimetype.startswith(p) for p in ACCEPTED_MIME_TYPE_PREFIXES):
                suffix = ".hwp"
            elif data.startswith(b"\xd0\xcf\x11\xe0"):
                suffix = ".hwp"
            elif data.startswith(b"PK"):
                suffix = ".hwpx"
            else:
                raise ValueError("Unrecognized HWP/HWPX format")

        temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            with temporary:
                temporary.write(data)
            reader = parser.read(temporary.name)
            try:
                result = reader.extract_text_with_notes(
                    parser.ExtractOptions(
                        table_style=parser.TableStyle.MARKDOWN,
                        image_marker=(
                            parser.ImageMarkerStyle.WITH_NAME
                            if kwargs.get("extract_media")
                            else parser.ImageMarkerStyle.SIMPLE
                        ),
                    )
                )
                markdown = result.text
                for notes, prefix in ((result.footnotes, ""), (result.endnotes, "e")):
                    for note in notes:
                        body = note.text.replace("\n", "\n    ")
                        markdown += f"\n\n[^{prefix}{note.number}]: {body}"
                from .._hwp_media import has_hwp_images

                has_images = has_hwp_images(data, suffix)
                unsupported = False
                if kwargs.get("extract_media"):
                    from .._hwp_media import hwp_images, extract_hwp_markdown

                    rows = hwp_images(data, suffix, reader)
                    markdown = extract_hwp_markdown(markdown, rows, **kwargs)
                    unsupported = any(not row[3] for row in rows)
                return DocumentConverterResult(
                    markdown=markdown,
                    image_content_omitted=(
                        has_images and not kwargs.get("extract_media")
                    )
                    or unsupported,
                    unsupported_media=unsupported,
                )
            finally:
                reader.close()
        finally:
            os.unlink(temporary.name)
