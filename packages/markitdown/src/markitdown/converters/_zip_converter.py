import zipfile
import io
import os
from dataclasses import dataclass

from typing import BinaryIO, Any, TYPE_CHECKING

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._exceptions import (
    UnsupportedFormatException,
    FileConversionException,
    ZipLimitExceeded,
)

# Break otherwise circular import for type hinting
if TYPE_CHECKING:
    from .._markitdown import MarkItDown

ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/zip",
]

ACCEPTED_FILE_EXTENSIONS = [".zip"]


@dataclass(frozen=True)
class ZipLimits:
    """Opt-in resource limits for ZIP conversion. Values are deliberately configurable.

    Limits are shared across nested ZIPs in one conversion. Existing trusted
    callers that omit `zip_limits` retain the legacy conversion behavior.
    """

    max_entries: int = 1024
    max_archive_bytes: int = 64 * 1024 * 1024
    max_member_bytes: int = 16 * 1024 * 1024
    max_total_uncompressed_bytes: int = 64 * 1024 * 1024
    max_depth: int = 3
    max_output_chars: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_entries",
            "max_archive_bytes",
            "max_member_bytes",
            "max_total_uncompressed_bytes",
            "max_depth",
            "max_output_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


class _ZipBudget:
    """Per-conversion mutable counters, never process-global."""

    def __init__(self, limits: ZipLimits):
        self.limits = limits
        self.entries = 0
        self.total_bytes = 0

    def check_depth(self, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise ZipLimitExceeded("ZIP nesting depth limit exceeded")

    def read_member(
        self, archive: zipfile.ZipFile, info: zipfile.ZipInfo
    ) -> io.BytesIO:
        self.entries += 1
        if self.entries > self.limits.max_entries:
            raise ZipLimitExceeded("ZIP entry count limit exceeded")
        if info.file_size > self.limits.max_member_bytes:
            raise ZipLimitExceeded("ZIP member byte limit exceeded")
        if info.file_size > self.limits.max_total_uncompressed_bytes - self.total_bytes:
            raise ZipLimitExceeded("ZIP total decompressed byte limit exceeded")

        data = bytearray()
        with archive.open(info, "r") as member:
            while True:
                chunk = member.read(64 * 1024)
                if not chunk:
                    break
                if len(data) + len(chunk) > self.limits.max_member_bytes:
                    raise ZipLimitExceeded("ZIP member byte limit exceeded")
                if (
                    self.total_bytes + len(chunk)
                    > self.limits.max_total_uncompressed_bytes
                ):
                    raise ZipLimitExceeded("ZIP total decompressed byte limit exceeded")
                data.extend(chunk)
                self.total_bytes += len(chunk)
        return io.BytesIO(data)


class ZipConverter(DocumentConverter):
    """Converts ZIP members in memory and combines supported Markdown output.

    Supported files are passed to registered converters in archive order;
    unsupported or failed members retain the legacy tolerant behavior.
    An explicit ZipLimits policy bounds this converter's archive and member
    resource use. No ZIP members are extracted to a temporary directory.

    Example output format:
    ```markdown
    Content from the zip file `example.zip`:

    ## File: docs/readme.txt

    This is the content of readme.txt
    Multiple lines are preserved

    ## File: images/example.jpg

    ImageSize: 1920x1080
    DateTimeOriginal: 2024-02-15 14:30:00
    Description: A beautiful landscape photo

    ## File: data/report.xlsx

    ## Sheet1
    | Column1 | Column2 | Column3 |
    |---------|---------|---------|
    | data1   | data2   | data3   |
    | data4   | data5   | data6   |
    ```

    Key features:
    - Maintains original file structure in headings
    - Processes nested files recursively
    - Uses appropriate converters for each file type
    - Preserves formatting of converted content
    - Keeps ZIP members in memory without disk extraction
    """

    def __init__(
        self,
        *,
        markitdown: "MarkItDown",
    ):
        super().__init__()
        self._markitdown = markitdown

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        file_path = (
            stream_info.url
            or stream_info.local_path
            or stream_info.filename
            or "(unknown)"
        )
        heading = f"Content from the zip file `{file_path}`:\n\n"
        parts = [heading]
        output_chars = len(heading)

        limits = kwargs.get("zip_limits")
        budget = kwargs.get("_zip_budget")
        if limits is not None and not isinstance(limits, ZipLimits):
            raise TypeError("zip_limits must be a ZipLimits instance")
        if budget is not None and not isinstance(budget, _ZipBudget):
            raise TypeError("invalid internal ZIP budget")
        if budget is None and limits is not None:
            budget = _ZipBudget(limits)
        depth = kwargs.get("_zip_depth", 0) + 1
        if budget is not None:
            budget.check_depth(depth)
            # ZipFile constructs its central-directory index eagerly. Bound
            # the compressed archive bytes before invoking that parser.
            start = file_stream.tell()
            file_stream.seek(0, 2)
            archive_size = file_stream.tell() - start
            file_stream.seek(start)
            if archive_size > budget.limits.max_archive_bytes:
                raise ZipLimitExceeded("ZIP input archive byte limit exceeded")

        # Drop the metadata describing the outer *archive*
        member_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in ("file_extension", "url")
        }

        if budget is not None:
            member_kwargs["_zip_budget"] = budget
            member_kwargs["_zip_depth"] = depth

        with zipfile.ZipFile(file_stream, "r") as zipObj:
            for entry in zipObj.infolist():
                name = entry.filename
                try:
                    # The old trusted mode is retained for compatibility. In
                    # explicit bounded mode, stream member decompression, count
                    # nested entries and share one total byte budget.
                    z_file_stream = (
                        budget.read_member(zipObj, entry)
                        if budget is not None
                        else io.BytesIO(zipObj.read(entry))
                    )
                    z_file_stream_info = StreamInfo(
                        extension=os.path.splitext(name)[1],
                        filename=os.path.basename(name),
                    )
                    result = self._markitdown.convert_stream(
                        stream=z_file_stream,
                        stream_info=z_file_stream_info,
                        **member_kwargs,
                    )
                    if result is not None:
                        segment = f"## File: {name}\n\n{result.markdown}\n\n"
                        if (
                            budget is not None
                            and output_chars + len(segment)
                            > budget.limits.max_output_chars
                        ):
                            raise ZipLimitExceeded(
                                "ZIP Markdown output character limit exceeded"
                            )
                        parts.append(segment)
                        output_chars += len(segment)
                except (UnsupportedFormatException, FileConversionException):
                    # Legacy tolerant conversion: reporting omissions is tracked
                    # separately in the completeness-report issue.
                    pass

        return DocumentConverterResult(markdown="".join(parts).strip())
