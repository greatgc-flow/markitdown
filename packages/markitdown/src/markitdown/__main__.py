# SPDX-FileCopyrightText: 2024-present Adam Fourney <adamfo@microsoft.com>
#
# SPDX-License-Identifier: MIT
import argparse
import os
import sys
import codecs
import io
from typing import Any, Dict
from importlib.metadata import entry_points
from .__about__ import __version__
from ._markitdown import MarkItDown, StreamInfo, DocumentConverterResult
from ._exceptions import (
    FileConversionException,
    MissingDependencyException,
    UnsupportedFormatException,
)


def _require_input_or_pipe(parser, args):
    """Without a filename, stdin is read: refuse to block silently on a terminal."""
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if args.filename is None and interactive:
        parser.error("no input: pass a FILENAME or pipe data on stdin (see --help)")


def main():
    parser = argparse.ArgumentParser(
        description="Convert various file formats to markdown.",
        prog="markitdown-gc",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="%(prog)s [OPTIONS] [FILE]",
        epilog="AI agents / quick start: markitdown-gc --guide   (OCR triage: markitdown-gc --guide ocr)",
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
        default=None,
        help="Document Intelligence Endpoint. Required if using Document Intelligence. Defaults to the MARKITDOWN_DOCINTEL_ENDPOINT environment variable.",
    )

    parser.add_argument(
        "--cu-endpoint",
        type=str,
        default=None,
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
        "--docintel-model-id",
        type=str,
        default=None,
        help="Document Intelligence model ID (e.g., 'prebuilt-layout', 'prebuilt-invoice', or a custom model ID). Defaults to 'prebuilt-layout'.",
    )

    parser.add_argument(
        "--docintel-query-fields",
        type=str,
        default=None,
        help="Comma-separated list of field names to extract via the billable Document Intelligence queryFields add-on (OCR file types only).",
    )

    parser.add_argument(
        "--docintel-features",
        type=str,
        default=None,
        help=(
            "Comma-separated Document Intelligence analysis add-ons for OCR file types. "
            "Available values: barcodes, formulas, keyValuePairs, languages, "
            "ocrHighResolution, and styleFont. The formulas, ocrHighResolution, and "
            "styleFont incur additional charges. No add-ons are enabled by default; "
            "use 'none' to explicitly request none."
        ),
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
        "--extract-media",
        metavar="DIR",
        help="Extract original images to DIR and link them from Markdown.",
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
        help="Do not convert: print a JSON report of which pages/images likely need OCR (PDF, raster, DOCX, PPTX, XLSX, HWP5, HWPX). Use -o to write it to a file.",
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
        defaults = {"filename", "output", "ocr_report", "ocr_report_samples", "guide"}
        for name, value in vars(args).items():
            if name not in defaults and value is not None and value is not False:
                parser.error(
                    f"--{name.replace('_', '-')} cannot be used with --ocr-report"
                )
        _require_input_or_pipe(parser, args)
        try:
            _run_ocr_report(args)
        except OSError:
            sys.stderr.write("OCR report output failed.\n")
            sys.exit(1)
        return

    if args.extract_media and args.keep_data_uris:
        parser.error("--extract-media and --keep-data-uris are mutually exclusive")

    args.endpoint = (
        args.endpoint or os.environ.get("MARKITDOWN_DOCINTEL_ENDPOINT") or None
    )
    args.cu_endpoint = (
        args.cu_endpoint or os.environ.get("MARKITDOWN_CU_ENDPOINT") or None
    )
    media_options = {}
    if args.extract_media:
        media_options = {
            "extract_media": args.extract_media,
            "media_base_dir": (
                os.path.dirname(os.path.abspath(args.output))
                if args.output
                else os.getcwd()
            ),
        }

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
                parser.error(f"Invalid MIME type: {mime_type_hint}")
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
                parser.error(f"Invalid charset: {charset_hint}")
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
            parser.error(
                "Document Intelligence Endpoint is required when using Document Intelligence. "
                "Pass -e/--endpoint or set MARKITDOWN_DOCINTEL_ENDPOINT."
            )
        elif args.filename is None:
            parser.error("Filename is required when using Document Intelligence.")

        docintel_kwargs: Dict[str, Any] = {
            "docintel_endpoint": args.endpoint,
        }
        if args.docintel_model_id:
            docintel_kwargs["docintel_model_id"] = args.docintel_model_id
        if args.docintel_features is not None:
            if args.docintel_features.strip().lower() == "none":
                docintel_kwargs["docintel_features"] = []
            else:
                docintel_kwargs["docintel_features"] = [
                    feature.strip()
                    for feature in args.docintel_features.split(",")
                    if feature.strip()
                ]
        if args.docintel_query_fields:
            fields = [
                f.strip() for f in args.docintel_query_fields.split(",") if f.strip()
            ]
            if fields:
                docintel_kwargs["docintel_query_fields"] = fields

        markitdown = MarkItDown(enable_plugins=args.use_plugins, **docintel_kwargs)
    elif args.use_cu:
        if args.cu_endpoint is None:
            parser.error(
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
                    parser.error(f"Unknown file type: {name}")
            cu_kwargs["cu_file_types"] = cu_types

        markitdown = MarkItDown(enable_plugins=args.use_plugins, **cu_kwargs)
    else:
        markitdown = MarkItDown(enable_plugins=args.use_plugins)

    _require_input_or_pipe(parser, args)

    try:
        if args.filename is None:
            # Windows pipe-backed stdin can report seekable() even though it cannot rewind.
            result = markitdown.convert_stream(
                io.BytesIO(sys.stdin.buffer.read()),
                stream_info=stream_info,
                keep_data_uris=args.keep_data_uris,
                **media_options,
            )
        else:
            result = markitdown.convert(
                args.filename,
                stream_info=stream_info,
                keep_data_uris=args.keep_data_uris,
                **media_options,
            )
    except (
        UnsupportedFormatException,
        MissingDependencyException,
        FileConversionException,
    ):
        sys.stderr.write("hint: markitdown-gc --guide errors\n")
        raise

    if not result.markdown.strip():
        sys.stderr.write(
            "hint: no text extracted; inspect the source and run markitdown-gc FILE --ocr-report\n"
        )

    if result.image_content_omitted is True:
        sys.stderr.write(
            "hint: image content omitted or truncated; run markitdown-gc FILE --ocr-report. See markitdown-gc --guide ocr.\n"
        )
    if result.unsupported_media is True:
        sys.stderr.write("warning: unsupported media omitted; inspect the source.\n")
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
            ("--extract-media", args.extract_media),
            ("--endpoint", args.endpoint),
            ("--cu-endpoint", args.cu_endpoint),
            ("--cu-analyzer", args.cu_analyzer),
            ("--cu-file-types", args.cu_file_types),
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
        sys.stderr.buffer.write(
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


if __name__ == "__main__":
    main()
