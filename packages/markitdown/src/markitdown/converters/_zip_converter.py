import zipfile
import io
import os
import struct
from dataclasses import dataclass

from typing import BinaryIO, Any, TYPE_CHECKING

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._exceptions import (
    UnsupportedFormatException,
    FileConversionException,
    ResourceLimitException,
)

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
    max_directory_bytes: int
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
            "zip_max_directory_bytes": 16 * 1024 * 1024,
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


def _preflight_central_directory(file_stream: BinaryIO, budget: _ZipBudget) -> None:
    """Bound ZIP metadata *before* ZipFile allocates one ZipInfo per entry.

    Scan the central directory with fixed-size reads, rejecting ZIP64/multi-disk
    archives whose metadata cannot be safely bounded by this simple policy.
    Supports ordinary comments, empty ZIPs and prepended self-extracting data.
    The original stream position is preserved for ZipFile and other converters.
    """
    original = file_stream.tell()
    try:
        file_stream.seek(0, os.SEEK_END)
        end = file_stream.tell()
        tail_len = min(end, 65557)  # EOCD's 22 bytes + 65535-byte comment.
        file_stream.seek(end - tail_len)
        tail = file_stream.read(tail_len)

        eocd = None
        # A comment may contain the EOCD signature: require the trailing
        # length to match, rather than blindly using rfind().
        for pos in range(len(tail) - 22, -1, -1):
            if tail[pos : pos + 4] != b"PK\\x05\\x06":
                continue
            length = struct.unpack_from("<H", tail, pos + 20)[0]
            if pos + 22 + length == len(tail):
                eocd = pos
                break
        if eocd is None:
            raise zipfile.BadZipFile("ZIP end-of-central-directory missing")

        _, disk, cd_disk, disk_count, count, cd_size, cd_offset, _ = (
            struct.unpack_from("<4s4H2IH", tail, eocd)
        )
        if disk or cd_disk or disk_count != count:
            raise ResourceLimitException("split ZIP archives are unsupported")
        if count == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
            raise ResourceLimitException("ZIP64 central directory is unsupported")
        if budget.entries + count > budget.max_entries:
            raise ResourceLimitException("ZIP entries limit exceeded")
        if cd_size > budget.max_directory_bytes:
            raise ResourceLimitException("ZIP central directory byte limit exceeded")

        # ZIP offsets are relative to the ZIP payload, so allow an SFX prefix.
        cd_start = end - tail_len + eocd - cd_size
        if cd_start < cd_offset or cd_start < 0:
            raise zipfile.BadZipFile("invalid ZIP central directory offset")
        cd_end = cd_start + cd_size
        file_stream.seek(cd_start)

        seen = 0
        expanded_total = budget.total_bytes
        while file_stream.tell() < cd_end:
            if seen >= count:
                raise ResourceLimitException("ZIP central directory entry count mismatch")
            header = file_stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\\x01\\x02":
                raise zipfile.BadZipFile("invalid ZIP central directory record")
            expanded = struct.unpack_from("<I", header, 24)[0]
            if expanded == 0xFFFFFFFF:
                raise ResourceLimitException("ZIP64 member size is unsupported")
            if expanded > budget.max_member_bytes:
                raise ResourceLimitException("ZIP member expanded size limit exceeded")
            expanded_total += expanded
            if expanded_total > budget.max_total_bytes:
                raise ResourceLimitException("ZIP total expanded size limit exceeded")
            name_len, extra_len, comment_len = struct.unpack_from("<3H", header, 28)
            next_record = file_stream.tell() + name_len + extra_len + comment_len
            if next_record > cd_end:
                raise zipfile.BadZipFile("truncated ZIP central directory record")
            file_stream.seek(next_record)
            seen += 1

        if seen != count or file_stream.tell() != cd_end:
            raise ResourceLimitException("ZIP central directory entry count mismatch")
    finally:
        file_stream.seek(original)


class ZipConverter(DocumentConverter):
    """Converts ZIP files to markdown by extracting and converting all contained files.

    The converter reads members in memory, processes each file with its
    matching converter, and combines bounded Markdown output. No files
    are extracted to disk.

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
    - Shares resource limits across nested archives
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
            key: value
            for key, value in kwargs.items()
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
            _preflight_central_directory(file_stream, budget)
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
                            raise ResourceLimitException(
                                "ZIP member expanded size changed"
                            )
                        result = self._markitdown.convert_stream(
                            stream=io.BytesIO(payload),
                            stream_info=StreamInfo(
                                extension=os.path.splitext(entry.filename)[1],
                                filename=os.path.basename(entry.filename),
                            ),
                            **member_kwargs,
                        )
                        if result is not None:
                            addition = (
                                f"## File: {entry.filename}\n\n{result.markdown}\n\n"
                            )
                            if written + len(addition) > budget.max_output_chars:
                                raise ResourceLimitException(
                                    "ZIP output size limit exceeded"
                                )
                            parts.append(addition)
                            written += len(addition)
                    except (UnsupportedFormatException, FileConversionException):
                        # Preserve existing skip semantics for unsupported or
                        # malformed members; hard resource failures never skip.
                        continue
        finally:
            budget.depth -= 1

        return DocumentConverterResult(markdown="".join(parts).strip())
