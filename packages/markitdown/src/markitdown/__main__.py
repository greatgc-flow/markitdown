# SPDX-FileCopyrightText: 2024-present Adam Fourney <adamfo@microsoft.com>
#
# SPDX-License-Identifier: MIT
import argparse
import os
import sys
import codecs
import io
from typing import Any, Dict
from textwrap import dedent
from importlib.metadata import entry_points
from .__about__ import __version__
from ._markitdown import MarkItDown, StreamInfo, DocumentConverterResult
from ._exceptions import (
    FileConversionException,
    MissingDependencyException,
    UnsupportedFormatException,
)


def main():
    parser = argparse.ArgumentParser(
        description="Convert various file formats to markdown.",
        prog="markitdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage=dedent("""
            SYNTAX:

                markitdown <OPTIONAL: FILENAME>
                If FILENAME is empty, markitdown reads from stdin.

            EXAMPLE:

                markitdown example.pdf

                OR

                cat example.pdf | markitdown

                OR

                markitdown < example.pdf

                OR to save to a file use

                markitdown example.pdf -o example.md

                OR

                markitdown example.pdf > example.md
            """).strip(),
        epilog="AI agents / quick start: markitdown --guide   (OCR triage: markitdown --guide ocr)",
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show the version number and exit",
    )

    parser.add_argument(
        "-o",
        "--output",
        help="Output file name. If not provided, output is written to stdout.",
    )

    parser.add_argument(
        "-x",
        "--extension",
        help="Provide a hint about the file extension (e.g., when reading from stdin).",
    )

    parser.add_argument(
        "-m",
        "--mime-type",
        help="Provide a hint about the file's MIME type.",
    )

    parser.add_argument(
        "-c",
        "--charset",
        help="Provide a hint about the file's charset (e.g., UTF-8).",
    )

    cloud_group = parser.add_mutually_exclusive_group()
    cloud_group.add_argument(
        "-d",
        "--use-docintel",
        action="store_true",
        help="Use Document Intelligence to extract text instead of offline conversion. Requires a valid Document Intelligence Endpoint.",
    )

    cloud_group.add_argument(
        "--use-cu",
        "--use-content-understanding",
        action="store_true",
        dest="use_cu",
        help="Use Azure Content Understanding to extract text. Requires --cu-endpoint.",
    )

    parser.add_argument(
        "-e",
        "--endpoint",
        type=str,
        default=os.environ.get("MARKITDOWN_DOCINTEL_ENDPOINT") or None,
        help="Document Intelligence Endpoint. Required if using Document Intelligence. Defaults to the MARKITDOWN_DOCINTEL_ENDPOINT environment variable.",
    )

    parser.add_argument(
        "--cu-endpoint",
        type=str,
        default=os.environ.get("MARKITDOWN_CU_ENDPOINT") or None,
        help="Content Understanding Endpoint. Required if using --use-cu. Defaults to the MARKITDOWN_CU_ENDPOINT environment variable.",
    )

    parser.add_argument(
        "--cu-analyzer",
        type=str,
        help="Content Understanding analyzer ID. If not specified, auto-selects by file type.",
    )

    parser.add_argument(
        "--cu-file-types",
        type=str,
        help="Comma-separated list of file types to route to Content Understanding (e.g., pdf,jpeg,mp4). If omitted, all supported types are routed.",
    )

    parser.add_argument(
        "-p",
        "--use-plugins",
        action="store_true",
        help="Use 3rd-party plugins to convert files. Use --list-plugins to see installed plugins.",
    )

    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List installed 3rd-party plugins. Plugins are loaded when using the -p or --use-plugins option.",
    )

    parser.add_argument(
        "--keep-data-uris",
        action="store_true",
        help="Keep data URIs (like base64-encoded images) in the output. By default, data URIs are truncated.",
    )

    parser.add_argument(
        "--guide",
        nargs="?",
        const="",
        default=None,
        metavar="TOPIC",
        help="Print the usage guide and exit: quick card, or one topic (formats, ocr, errors, install) or 'all'.",
    )

    parser.add_argument(
        "--ocr-report",
        action="store_true",
        help="Do not convert: print a JSON report of which pages/images likely need OCR (PDF, raster, DOCX, PPTX, XLSX, HWPX). Use -o to write it to a file.",
    )

    parser.add_argument(
        "--ocr-report-samples",
        action="store_true",
        help="With --ocr-report, include up to 120 characters of text per page (may contain sensitive text).",
    )

    parser.add_argument("filename", nargs="?")
    args = parser.parse_args()

    if args.guide is not None:
        _check_exclusive(parser, args, "--guide", allowed=("guide",))
        _print_guide(parser, args.guide)
        return

    if args.ocr_report_samples and not args.ocr_report:
        parser.error("--ocr-report-samples requires --ocr-report")
    if args.ocr_report:
        for flag, value in (
            ("-d/--use-docintel", args.use_docintel),
            ("--use-cu", args.use_cu),
            ("-p/--use-plugins", args.use_plugins),
            ("--keep-data-uris", args.keep_data_uris),
            ("-c/--charset", args.charset),
        ):
            if value:
                parser.error(f"{flag} cannot be used with --ocr-report")
        _run_ocr_report(args)
        return

    # Parse the extension hint
    extension_hint = args.extension
    if extension_hint is not None:
        extension_hint = extension_hint.strip().lower()
        if len(extension_hint) > 0:
            if not extension_hint.startswith("."):
                extension_hint = "." + extension_hint
        else:
            extension_hint = None

    # Parse the mime type
    mime_type_hint = args.mime_type
    if mime_type_hint is not None:
        mime_type_hint = mime_type_hint.strip()
        if len(mime_type_hint) > 0:
            if mime_type_hint.count("/") != 1:
                _exit_with_error(f"Invalid MIME type: {mime_type_hint}")
        else:
            mime_type_hint = None

    # Parse the charset
    charset_hint = args.charset
    if charset_hint is not None:
        charset_hint = charset_hint.strip()
        if len(charset_hint) > 0:
            try:
                charset_hint = codecs.lookup(charset_hint).name
            except LookupError:
                _exit_with_error(f"Invalid charset: {charset_hint}")
        else:
            charset_hint = None

    stream_info = None
    if (
        extension_hint is not None
        or mime_type_hint is not None
        or charset_hint is not None
    ):
        stream_info = StreamInfo(
            extension=extension_hint, mimetype=mime_type_hint, charset=charset_hint
        )

    if args.list_plugins:
        # List installed plugins, then exit
        print("Installed MarkItDown 3rd-party Plugins:\n")
        plugin_entry_points = list(entry_points(group="markitdown.plugin"))
        if len(plugin_entry_points) == 0:
            print("  * No 3rd-party plugins installed.")
            print(
                "\nFind plugins by searching for the hashtag #markitdown-plugin on GitHub.\n"
            )
        else:
            for entry_point in plugin_entry_points:
                print(f"  * {entry_point.name:<16}\t(package: {entry_point.value})")
            print(
                "\nUse the -p (or --use-plugins) option to enable 3rd-party plugins.\n"
            )
        sys.exit(0)

    if args.use_docintel:
        if args.endpoint is None:
            _exit_with_error(
                "Document Intelligence Endpoint is required when using Document Intelligence. "
                "Pass -e/--endpoint or set MARKITDOWN_DOCINTEL_ENDPOINT."
            )
        elif args.filename is None:
            _exit_with_error("Filename is required when using Document Intelligence.")

        markitdown = MarkItDown(
            enable_plugins=args.use_plugins, docintel_endpoint=args.endpoint
        )
    elif args.use_cu:
        if args.cu_endpoint is None:
            _exit_with_error(
                "Content Understanding Endpoint (--cu-endpoint) is required when using --use-cu. "
                "Pass --cu-endpoint or set MARKITDOWN_CU_ENDPOINT."
            )

        cu_kwargs: Dict[str, Any] = {
            "cu_endpoint": args.cu_endpoint,
        }
        if args.cu_analyzer is not None:
            cu_kwargs["cu_analyzer_id"] = args.cu_analyzer
        if args.cu_file_types is not None:
            # Parse comma-separated file types into ContentUnderstandingFileType list
            from .converters import ContentUnderstandingFileType

            type_names = [
                t.strip().lower() for t in args.cu_file_types.split(",") if t.strip()
            ]
            cu_types = []
            for name in type_names:
                # Try matching by value (e.g., "pdf", "jpeg", "mp4")
                try:
                    cu_types.append(ContentUnderstandingFileType(name))
                except ValueError:
                    _exit_with_error(f"Unknown file type: {name}")
            cu_kwargs["cu_file_types"] = cu_types

        markitdown = MarkItDown(enable_plugins=args.use_plugins, **cu_kwargs)
    else:
        markitdown = MarkItDown(enable_plugins=args.use_plugins)

    try:
        if args.filename is None:
            # Windows pipe-backed stdin can report seekable() even though it cannot rewind.
            result = markitdown.convert_stream(
                io.BytesIO(sys.stdin.buffer.read()),
                stream_info=stream_info,
                keep_data_uris=args.keep_data_uris,
            )
        else:
            result = markitdown.convert(
                args.filename,
                stream_info=stream_info,
                keep_data_uris=args.keep_data_uris,
            )
    except (
        UnsupportedFormatException,
        MissingDependencyException,
        FileConversionException,
    ):
        sys.stderr.write("hint: markitdown --guide errors\n")
        raise

    if not result.markdown.strip():
        sys.stderr.write(
            f"no text extracted; try: markitdown {args.filename or 'FILE'} --ocr-report\n"
        )

    _handle_output(args, result)


GUIDE_TOPICS = ("formats", "ocr", "errors", "install")


def _check_exclusive(parser, args, flag, allowed):
    """Reject conversion/report arguments combined with `flag`."""
    used = [
        name
        for name, value in (
            ("filename", args.filename),
            ("-o", args.output),
            ("-x", args.extension),
            ("-m", args.mime_type),
            ("-c", args.charset),
            ("-d", args.use_docintel),
            ("--use-cu", args.use_cu),
            ("-p", args.use_plugins),
            ("--list-plugins", args.list_plugins),
            ("--keep-data-uris", args.keep_data_uris),
            ("--ocr-report", args.ocr_report),
            ("--ocr-report-samples", args.ocr_report_samples),
        )
        if value
    ]
    if used:
        parser.error(f"{flag} cannot be combined with: {', '.join(used)}")


def _guide_text(topic: str) -> str:
    """Return the quick card (topic ''), one `## topic` section, or everything ('all')."""
    from importlib import resources

    text = (
        resources.files(__package__).joinpath("GUIDE.md").read_text(encoding="utf-8")
    ).replace("\r\n", "\n")
    if topic == "all":
        return text
    parts = text.split("\n## ")
    if topic == "":
        return parts[0].rstrip("\n") + "\n"
    for part in parts[1:]:
        if part.split("\n", 1)[0].strip() == topic:
            return "## " + part.rstrip("\n") + "\n"
    raise KeyError(topic)


def _print_guide(parser, topic: str):
    """Write the requested guide part to stdout as UTF-8."""
    try:
        guide = _guide_text(topic)
    except KeyError:
        parser.error(
            f"unknown guide topic {topic!r}; topics: {' '.join(GUIDE_TOPICS)} | all"
        )
    sys.stdout.flush()
    sys.stdout.buffer.write(guide.encode("utf-8"))
    sys.stdout.buffer.flush()


def _run_ocr_report(args):
    """Print (or write with -o) the OCR triage report; exit 0 when a report was produced."""
    import json
    from .ocr_triage import triage

    if args.filename is None:
        source = io.BytesIO(sys.stdin.buffer.read())
    else:
        source = args.filename
    report = triage(source, samples=args.ocr_report_samples)
    text = json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        summary = report["summary"]
        sys.stdout.buffer.write(
            (
                f"ocr-report: decision={report['decision']} "
                f"high={summary['candidates_high']} low={summary['candidates_low']} "
                f"coverage={report['coverage']['state']} issues={len(report['issues'])} "
                f"-> {args.output}\n"
            ).encode("utf-8")
        )
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _handle_output(args, result: DocumentConverterResult):
    """Handle output to stdout or file"""
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result.markdown)
    else:
        # Handle stdout encoding errors more gracefully, with fallback for
        # cases where sys.stdout.encoding is None (e.g. redirected streams).
        encoding = sys.stdout.encoding or "utf-8"
        print(result.markdown.encode(encoding, errors="replace").decode(encoding))


def _exit_with_error(message: str):
    print(message)
    sys.exit(1)


if __name__ == "__main__":
    main()
