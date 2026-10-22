import re
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
_BIN_IMAGE_RE = re.compile(r"!\[[^\]\r\n]*\]\(bin://(\d+)\)")


def _replace_image_references(markdown: str) -> str:
    return _BIN_IMAGE_RE.sub(r"[Image \1 omitted]", markdown)


class HwpConverter(DocumentConverter):
    """Convert HWP5 and HWPX using the optional rhwp backend."""

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
            import rhwp
        except ImportError as exc:
            raise MissingDependencyException(
                "HwpConverter requires rhwp-python. "
                "Install it with pip install 'markitdown[hwp]'."
            ) from exc

        markdown = rhwp.Document.from_bytes(file_stream.read()).to_ir().to_markdown()
        return DocumentConverterResult(markdown=_replace_image_references(markdown))
