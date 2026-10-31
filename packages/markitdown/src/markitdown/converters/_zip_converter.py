import zipfile
import io
import os
from dataclasses import dataclass

from typing import BinaryIO, Any, TYPE_CHECKING

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._exceptions import UnsupportedFormatException, FileConversionException, ResourceLimitException

# Break otherwise circular import for type hinting
if TYPE_CHECKING:
    from .._markitdown import MarkItDown

ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/zip",
]

ACCEPTED_FILE_EXTENSIONS = [".zip"]


@dataclass
class _ZipBudget:
    """Shared per-conversion ZIP limits, including nested archives.

    Defaults are deliberate conservative application caps, not ZIP format limits.
    Override using positive integer zip_max_* kwargs only for trusted inputs.
    """

    max_entries: int
    max_member_bytes: int
    max_total_bytes: int
    max_depth: int
    max_output_chars: int
    entries: int = 0
    total_bytes: int = 0
    depth: int = 0

    @classmethod
    def configure(cls, opts: dict[str, Any]) -> "_ZipBudget":
        defaults = {
            "zip_max_entries": 10_000,
            "zip_max_member_bytes": 128 * 1024 * 1024,
            "zip_max_total_bytes": 512 * 1024 * 1024,
            "zip_max_depth": 4,
            "zip_max_output_chars": 32 * 1024 * 1024,
        }
        values = []
        for key, default in defaults.items():
            value = opts.get(key, default)
            if type(value) is not int or value < 1:
                raise ResourceLimitException(f"{key} must be a positive integer")
            values.append(value)
        return cls(*values)

    def enter(self) -> None:
        if self.depth >= self.max_depth:
            raise ResourceLimitException("ZIP nesting depth limit exceeded")
        self.depth += 1

    def reserve(self, entries: list[zipfile.ZipInfo]) -> None:
        # Before decompressing, account for every central-directory entry.
        if self.entries + len(entries) > self.max_entries:
            raise ResourceLimitException("ZIP entries limit exceeded")
        self.entries += len(entries)
        for entry in entries:
            if entry.file_size > self.max_member_bytes:
                raise ResourceLimitException("ZIP member expanded size limit exceeded")
            if self.total_bytes + entry.file_size > self.max_total_bytes:
                raise ResourceLimitException("ZIP total expanded size limit exceeded")
            self.total_bytes += entry.file_size


class ZipConverter(DocumentConverter):
    """Converts ZIP files to markdown by extracting and converting all contained files.

    The converter extracts the ZIP contents to a temporary directory, processes each file
    using appropriate converters based on file extensions, and then combines the results
    into a single markdown document. The temporary directory is cleaned up after processing.

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
    - Cleans up temporary files after processing
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
            "(remote archive)"
            if stream_info.url
            else stream_info.local_path or stream_info.filename or "(unknown)"
        )
        budget = kwargs.get("_zip_budget")
        if budget is None:
            budget = _ZipBudget.configure(kwargs)
        if not isinstance(budget, _ZipBudget):
            raise ResourceLimitException("invalid internal ZIP budget")

        # Keep nested ZIP conversions on the same counters and depth.
        member_kwargs = {
            key: value for key, value in kwargs.items()
            if key not in ("file_extension", "url")
        }
        member_kwargs["_zip_budget"] = budget

        header = f"Content from the zip file `{file_path}`:\n\n"
        parts = [header]
        written = len(header)
        if written > budget.max_output_chars:
            raise ResourceLimitException("ZIP output size limit exceeded")

        budget.enter()
        try:
            with zipfile.ZipFile(file_stream, "r") as archive:
                entries = archive.infolist()
                budget.reserve(entries)
                for entry in entries:
                    if entry.is_dir():
                        continue
                    try:
                        # Reject declared bombs before allocation; read at most
                        # the checked expanded size plus one byte as a guard.
                        with archive.open(entry) as member:
                            payload = member.read(entry.file_size + 1)
                        if len(payload) > entry.file_size:
                            raise ResourceLimitException("ZIP member expanded size changed")
                        result = self._markitdown.convert_stream(
                            stream=io.BytesIO(payload),
                            stream_info=StreamInfo(
                                extension=os.path.splitext(entry.filename)[1],
                                filename=os.path.basename(entry.filename),
                            ),
                            **member_kwargs,
                        )
                        if result is not None:
                            addition = f"## File: {entry.filename}\n\n{result.markdown}\n\n"
                            if written + len(addition) > budget.max_output_chars:
                                raise ResourceLimitException("ZIP output size limit exceeded")
                            parts.append(addition)
                            written += len(addition)
                    except (UnsupportedFormatException, FileConversionException):
                        # Preserve existing skip semantics for unsupported or
                        # malformed members; hard resource failures never skip.
                        continue
        finally:
            budget.depth -= 1

        return DocumentConverterResult(markdown="".join(parts).strip())
