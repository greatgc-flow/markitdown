"""Deterministic, local OCR triage observations; never performs OCR or conversion."""

import hashlib
import os
import re

MAX_BYTES = 256 * 1024**2
MAX_PAGES = 1000
MAX_FRAMES = 100
MAX_IMAGES = 20
_HASH_CHUNK = 1024**2


def _report(samples):
    # Verdict first; bounded evidence last. Do not sort these keys at serialization.
    return {
        "decision": "unknown",
        "coverage": {
            "state": "none",
            "pages_total": None,
            "pages_inspected": 0,
            "limits": {"max_bytes": MAX_BYTES, "max_pages": MAX_PAGES},
        },
        "summary": {
            "scope": "inspected",
            "pages_with_text": 0,
            "nonspace_chars": 0,
            "image_occurrences": 0,
            "candidates_high": 0,
            "candidates_low": 0,
        },
        "candidates": [],
        "issues": [],
        "file": {"sha256": None, "bytes": 0, "format": "unknown"},
        "options": {"samples": bool(samples)},
        "schema_version": 1,
        "rules_version": "1",
        "pages": [],
        "rasters": [],
    }


def _issue(report, code, detail):
    report["issues"].append({"code": code, "detail": detail})


def _candidate(report, identity, kind, page, priority, reasons):
    report["candidates"].append(
        {
            "id": identity,
            "kind": kind,
            "page": page,
            "priority": priority,
            "reasons": reasons,
        }
    )
    report["summary"]["candidates_" + priority] += 1


def _format(header):
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(
        (
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"GIF87a",
            b"GIF89a",
            b"II*\0",
            b"MM\0*",
            b"II+\0",
            b"MM\0+",
            b"BM",
        )
    ) or (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
        return "image"
    return "unknown"


class _Borrowed:
    """Libraries may close this view without closing the caller's stream."""

    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def close(self):
        pass


def triage(source, *, samples=False):
    """Inspect a local path or seekable binary stream, restoring stream position.

    Failures are recorded as stable issues, without paths or exception messages.
    Optional samples contain at most 120 whitespace-normalized text characters.
    """
    report = _report(samples)
    stream = None
    owned = False
    position = None
    fatal = False
    try:
        if isinstance(source, (str, bytes, os.PathLike)):
            stream = open(source, "rb")
            owned = True
        else:
            stream = source
            position = stream.tell()
        stream.seek(0, 2)
        size = stream.tell()
        report["file"]["bytes"] = size
        stream.seek(0)
        report["file"]["format"] = _format(stream.read(16))
        if size > MAX_BYTES:
            _issue(
                report, "too_large", "Input exceeds max_bytes; not inspected or hashed."
            )
            fatal = True
        else:
            stream.seek(0)
            digest = hashlib.sha256()
            while chunk := stream.read(_HASH_CHUNK):
                digest.update(chunk)
            report["file"]["sha256"] = digest.hexdigest()
            stream.seek(0)
            if report["file"]["format"] == "pdf":
                fatal = not _pdf(report, _Borrowed(stream), samples)
            elif report["file"]["format"] == "image":
                _image(report, _Borrowed(stream))
            else:
                code = "corrupt" if size == 0 else "unsupported_format"
                _issue(
                    report,
                    code,
                    (
                        "Empty input."
                        if size == 0
                        else "Supported formats: PDF and raster images."
                    ),
                )
                fatal = True
    except Exception:
        _issue(report, "corrupt", "Input could not be read or inspected.")
        fatal = True
    finally:
        if stream is not None:
            try:
                if owned:
                    stream.close()
                elif position is not None:
                    stream.seek(position)
            except Exception:
                _issue(
                    report, "corrupt", "Input stream position could not be restored."
                )
                fatal = True
    if not fatal:
        if report["summary"]["candidates_high"]:
            report["decision"] = "ocr_recommended"
        elif report["summary"]["candidates_low"]:
            report["decision"] = "review"
        elif report["coverage"]["state"] == "complete":
            report["decision"] = "no_candidates"
    return report


def _clip(box, bounds):
    x0, top, x1, bottom = box
    bx0, btop, bx1, bbottom = bounds
    return (max(x0, bx0), max(top, btop), min(x1, bx1), min(bottom, bbottom))


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _union_area(boxes):
    """Sweep x slabs and merge y intervals; overlapping placements count once."""
    boxes = [box for box in boxes if _area(box)]
    xs = sorted({x for box in boxes for x in (box[0], box[2])})
    area = 0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((b[1], b[3]) for b in boxes if b[0] < right and b[2] > left)
        end = float("-inf")
        height = 0
        for top, bottom in intervals:
            height += max(0, bottom - max(top, end))
            end = max(end, bottom)
        area += (right - left) * height
    return area


def _names(value):
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import PSLiteral

    value = resolve1(value)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [item.name for item in map(resolve1, value) if isinstance(item, PSLiteral)]


def _bbox(image):
    return (image["x0"], image["top"], image["x1"], image["bottom"])


def _image_row(image, bounds, page_area, skewed=False):
    box = _bbox(image)
    width, height = box[2] - box[0], box[3] - box[1]
    size = image.get("srcsize")
    dpi = None
    if size and width > 0 and height > 0 and not skewed:
        dpi = [round(size[0] * 72 / width, 1), round(size[1] * 72 / height, 1)]
    stream = image.get("stream")
    attrs = stream.attrs if stream is not None else {}
    return {
        "name": image.get("name"),
        "bbox_pt": [round(v, 1) for v in box],
        "px": list(size) if size else None,
        "dpi_xy": dpi,
        "area_ratio": (
            round(_area(_clip(box, bounds)) / page_area, 3) if page_area else 0.0
        ),
        "colorspace": _names(attrs.get("ColorSpace", attrs.get("CS"))),
        "bits": attrs.get("BitsPerComponent", attrs.get("BPC")),
        "imagemask": bool(attrs.get("ImageMask", attrs.get("IM", False))),
        "filter": _names(attrs.get("Filter", attrs.get("F"))),
    }


def _private(text):
    return any(
        0xE000 <= ord(c) <= 0xF8FF
        or 0xF0000 <= ord(c) <= 0xFFFFD
        or 0x100000 <= ord(c) <= 0x10FFFD
        for c in text
    )


def _placement_skews(page):
    """pdfplumber omits placement matrices; retain them from pdfminer's figures.

    The flattened layout order is also the order used by page.images. Right-angle
    rotations remain axis aligned and have meaningful bounding-box DPI.
    """
    from pdfminer.layout import LTFigure, LTImage

    flags = []

    def walk(container, skewed=False):
        if isinstance(container, LTFigure):
            a, b, c, d, _, _ = container.matrix
            skewed = not (
                (abs(b) < 1e-9 and abs(c) < 1e-9) or (abs(a) < 1e-9 and abs(d) < 1e-9)
            )
        if isinstance(container, LTImage):
            flags.append(skewed)
        elif hasattr(container, "__iter__"):
            for child in container:
                walk(child, skewed)

    walk(page.layout)
    return flags


def _page(page, n, samples):
    texts = [char.get("text", "") for char in page.chars]
    cid = [bool(re.fullmatch(r"\(cid:\d+\)", text)) for text in texts]
    replacement = ["\ufffd" in text for text in texts]
    private = [_private(text) for text in texts]
    bad = sum(a or b or c for a, b, c in zip(cid, replacement, private))
    share = bad / len(texts) if texts else 0.0
    # A cid placeholder is one extracted glyph, not seven printable characters.
    nonspace = sum(bool(text.strip()) for text in texts)
    bounds = page.bbox
    page_area = _area(bounds)
    images = page.images
    ratio = (
        _union_area([_clip(_bbox(image), bounds) for image in images]) / page_area
        if page_area
        else 0.0
    )
    vectors = len(page.lines) + len(page.rects) + len(page.curves)
    skews = _placement_skews(page)
    ordered = sorted(
        enumerate(images),
        key=lambda item: (round(item[1]["top"], 1), round(item[1]["x0"], 1)),
    )
    row = {
        "n": n,
        "bbox_pt": [round(v, 1) for v in bounds],
        "rotation": page.rotation,
        "chars": len(texts),
        "nonspace_chars": nonspace,
        "unresolved_glyph_share": round(share, 3),
        "cid_count": sum(cid),
        "replacement_count": sum(replacement),
        "private_use_count": sum(private),
        "vector_objects": vectors,
        "image_area_ratio": round(ratio, 3),
        "image_count": len(images),
        "images_truncated": len(images) > MAX_IMAGES,
        "images": [
            _image_row(
                image, bounds, page_area, skews[index] if index < len(skews) else True
            )
            for index, image in ordered[:MAX_IMAGES]
        ],
    }
    if samples:
        row["text_sample"] = " ".join("".join(texts).split())[:120]
    reasons = []
    high = False
    if nonspace < 30:
        if ratio >= 0.5:
            reasons.append("scan_like")
            high = True
        elif ratio >= 0.1 or vectors >= 50:
            reasons.append("low_text_nonblank")
    elif ratio >= 0.15:
        reasons.append("image_with_text_layer")
    if share > 0.10 and len(texts) >= 5:
        reasons.append("suspect_text_layer")
        high = True
    return row, reasons, "high" if high else "low"


def _pdf(report, stream, samples):
    try:
        import pdfplumber
    except ImportError:
        _issue(
            report,
            "missing_dependency",
            'Install PDF support: pip install "markitdown[pdf]".',
        )
        return False
    try:
        from pdfminer.pdfdocument import PDFPasswordIncorrect

        with pdfplumber.open(stream) as document:
            if document.doc.encryption:
                _issue(report, "encrypted", "Encrypted PDF is not inspected.")
                return False
            total = len(document.pages)
            report["coverage"]["pages_total"] = total
            if total > MAX_PAGES:
                _issue(
                    report, "page_cap", "Only the first max_pages pages were inspected."
                )
            for n, page in enumerate(document.pages[:MAX_PAGES], 1):
                row, reasons, priority = _page(page, n, samples)
                report["pages"].append(row)
                report["coverage"]["pages_inspected"] += 1
                summary = report["summary"]
                summary["pages_with_text"] += bool(row["nonspace_chars"])
                summary["nonspace_chars"] += row["nonspace_chars"]
                summary["image_occurrences"] += row["image_count"]
                if reasons:
                    _candidate(report, f"p{n}", "page", n, priority, reasons)
                page.close()
            report["coverage"]["state"] = "partial" if total > MAX_PAGES else "complete"
        return True
    except PDFPasswordIncorrect:
        _issue(report, "encrypted", "Encrypted PDF is not inspected.")
    except Exception:
        _issue(report, "corrupt", "PDF could not be inspected.")
    report["coverage"]["state"] = "partial" if report["pages"] else "none"
    return False


def _image(report, stream):
    try:
        from PIL import Image

        with Image.open(stream) as image:
            frames = getattr(image, "n_frames", 1)
            for index in range(min(frames, MAX_FRAMES)):
                image.seek(index)
                identity = "image" if frames == 1 else f"f{index + 1}"
                dpi = image.info.get("dpi")
                report["rasters"].append(
                    {
                        "id": identity,
                        "frame": index + 1,
                        "px": list(image.size),
                        "mode": image.mode,
                        "dpi_tag": [round(float(v), 1) for v in dpi] if dpi else None,
                        "exif_orientation": image.getexif().get(274, 1),
                        "bytes": report["file"]["bytes"],
                    }
                )
                _candidate(
                    report, identity, "image", None, "high", ["standalone_image"]
                )
            report["coverage"]["state"] = (
                "partial" if frames > MAX_FRAMES else "complete"
            )
            if frames > MAX_FRAMES:
                _issue(
                    report,
                    "frame_cap",
                    "Only the first max_frames (100) frames were inspected.",
                )
    except Exception:
        # A failed multi-frame probe cannot establish a reliable frame inventory.
        report["rasters"].clear()
        report["rasters"].append(
            {
                "id": "image",
                "frame": None,
                "px": None,
                "mode": None,
                "dpi_tag": None,
                "exif_orientation": None,
                "bytes": report["file"]["bytes"],
            }
        )
        report["candidates"].clear()
        report["summary"]["candidates_high"] = 0
        _candidate(report, "image", "image", None, "high", ["standalone_image"])
        _issue(
            report,
            "image_probe_failed",
            "Image header or frames unavailable; install Pillow or check input.",
        )
