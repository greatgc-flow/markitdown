"""Core acceptance contract, using synthetic public fixtures only."""

import hashlib
import importlib
import io
import json
import os
import socket
import subprocess
import sys

import pdfplumber
import pytest
from PIL import Image

from ._pdf_builder import pdf, scan_pdf


@pytest.fixture
def core():
    return importlib.import_module("markitdown.ocr_triage")


def inspect(core, data, **kwargs):
    return core.triage(io.BytesIO(data), **kwargs)


def premise(data):
    with pdfplumber.open(io.BytesIO(data)) as document:
        return [(len(p.chars), len(p.images)) for p in document.pages]


@pytest.mark.parametrize(
    "chars,ratio,reason",
    [
        (29, 0.49, "low_text_nonblank"),
        (29, 0.5, "scan_like"),
        (30, 0.5, "image_with_text_layer"),
        (30, 0.149, None),
        (30, 0.15, "image_with_text_layer"),
        (0, 0.099, None),
        (0, 0.1, "low_text_nonblank"),
    ],
)
def test_thresholds(core, chars, ratio, reason):
    data = pdf([{"text": "a" * chars, "images": [(0, 0, 100, ratio * 100)]}])
    assert premise(data) == [(chars, 1)]
    report = inspect(core, data)
    assert report["pages"][0]["nonspace_chars"] == chars
    assert report["pages"][0]["image_area_ratio"] == ratio
    assert [c["reasons"] for c in report["candidates"]] == (
        [[reason]] if reason else []
    )


@pytest.mark.parametrize(
    "images,ratio",
    [
        ([(0, 0, 60, 100), (40, 0, 60, 100)], 1),
        ([(0, 0, 50, 100), (50, 0, 50, 100)], 1),
        ([(0, 0, 60, 100)] * 2, 0.6),
        ([(-50, 0, 100, 100)], 0.5),
    ],
)
def test_union_and_clipping(core, images, ratio):
    data = pdf([{"images": images}])
    assert premise(data) == [(0, len(images))]
    row = inspect(core, data)["pages"][0]
    assert row["image_area_ratio"] == ratio
    if images[0][0] < 0:
        assert row["images"][0]["dpi_xy"] == [72.0, 72.0]
        assert row["images"][0]["area_ratio"] == 0.5


def test_mixed_blank_caption_rotation_and_summary(core):
    data = pdf(
        [
            {},
            {"text": "a" * 100},
            {"text": "caption", "images": [(0, 0, 100, 100)]},
            {"images": [(0, 0, 100, 100)], "rotate": 90},
            {"text": "a" * 100, "images": [(0, 0, 50, 50)]},
        ]
    )
    assert premise(data) == [(0, 0), (100, 0), (7, 1), (0, 1), (100, 1)]
    report = inspect(core, data)
    assert [p["n"] for p in report["pages"]] == [1, 2, 3, 4, 5]
    assert report["pages"][3]["rotation"] == 90
    assert report["pages"][3]["image_area_ratio"] == 1
    assert [c["id"] for c in report["candidates"]] == ["p3", "p4", "p5"]
    assert report["summary"] == dict(
        scope="inspected",
        pages_with_text=3,
        nonspace_chars=207,
        image_occurrences=3,
        candidates_high=2,
        candidates_low=1,
    )
    assert report["decision"] == "ocr_recommended"


@pytest.mark.parametrize("count,decision", [(49, "no_candidates"), (50, "review")])
def test_vectors(core, count, decision):
    data = pdf([{"vectors": count}])
    with pdfplumber.open(io.BytesIO(data)) as doc:
        assert len(doc.pages[0].rects) == count
    report = inspect(core, data)
    assert report["pages"][0]["vector_objects"] == count
    assert report["decision"] == decision


@pytest.mark.parametrize(
    "bad,total,expected", [(10, 100, False), (11, 100, True), (1, 4, False)]
)
def test_unresolved_share(core, monkeypatch, bad, total, expected):
    # Real Type0 coverage below; patch extracted glyphs to control exact boundaries.
    chars = [{"text": "(cid:123)"}] * bad + [{"text": " "}] * (total - bad)
    monkeypatch.setattr(pdfplumber.page.Page, "chars", property(lambda self: chars))
    report = inspect(core, pdf([{}]))
    assert report["pages"][0]["unresolved_glyph_share"] == round(bad / total, 3)
    assert report["pages"][0]["cid_count"] == bad
    assert bool(report["candidates"]) == expected


def test_private_use_replacement_and_samples(core, monkeypatch):
    chars = [
        {"text": t}
        for t in ["\ue000", "\U000f0000", "\U00100000", "\ufffd", "(cid:9)", " a\n b "]
    ]
    monkeypatch.setattr(pdfplumber.page.Page, "chars", property(lambda self: chars))
    row = inspect(core, pdf([{}]), samples=True)["pages"][0]
    assert (row["cid_count"], row["replacement_count"], row["private_use_count"]) == (
        1,
        1,
        3,
    )
    assert row["unresolved_glyph_share"] == 0.833
    assert "\n" not in row["text_sample"]


def test_type0_extraction_failure(core):
    data = pdf([{"cid": True}])
    with pdfplumber.open(io.BytesIO(data)) as doc:
        assert "(cid:" in doc.pages[0].extract_text()
    report = inspect(core, data)
    assert report["pages"][0]["cid_count"] == 5
    assert report["candidates"][0]["reasons"] == ["suspect_text_layer"]


def test_inline_image(core):
    data = pdf([{"inline": True}])
    assert premise(data) == [(0, 1)]
    row = inspect(core, data)["pages"][0]
    assert row["image_area_ratio"] == 1
    assert row["images"][0]["px"] == [1, 1]
    assert row["images"][0]["colorspace"] == ["RGB"]
    assert row["images"][0]["bits"] == 8


def test_real_image_metadata_and_truncation(core):
    data = scan_pdf()
    assert premise(data) == [(0, 1)]
    row = inspect(core, data)["pages"][0]["images"][0]
    assert row == dict(
        name="image",
        bbox_pt=[0.0, 0.0, 100.0, 100.0],
        px=[100, 100],
        dpi_xy=[72.0, 72.0],
        area_ratio=1.0,
        colorspace=["DeviceRGB"],
        bits=8,
        imagemask=False,
        filter=["DCTDecode"],
    )
    data = pdf([{"images": [(x, y, 1, 1) for y in (80, 20, 60) for x in range(8)]}])
    assert premise(data) == [(0, 24)]
    page = inspect(core, data)["pages"][0]
    assert page["image_count"] == 24 and page["images_truncated"]
    assert len(page["images"]) == 20
    boxes = [i["bbox_pt"] for i in page["images"]]
    assert boxes == sorted(boxes, key=lambda b: (b[1], b[0]))


def raster(fmt="PNG", frames=1):
    stream = io.BytesIO()
    images = [
        Image.new("RGB", (12, 15), (n % 256, n // 256, 80)) for n in range(frames)
    ]
    kwargs = dict(save_all=True, append_images=images[1:]) if frames > 1 else {}
    images[0].save(stream, fmt, **kwargs)
    return stream.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "BMP", "WEBP", "TIFF", "GIF"])
def test_magic_raster(core, fmt, tmp_path):
    data = raster(fmt)
    path = tmp_path / "misleading.pdf"
    path.write_bytes(data)
    report = core.triage(path)
    assert report["file"] == dict(
        sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), format="image"
    )
    assert report["decision"] == "ocr_recommended"
    assert report["candidates"] == [
        dict(
            id="image",
            kind="image",
            page=None,
            priority="high",
            reasons=["standalone_image"],
        )
    ]
    assert report["rasters"][0]["px"] == [12, 15]
    assert report["rasters"][0]["frame"] == 1
    assert report["rasters"][0]["bytes"] == len(data)
    assert report["rasters"][0]["exif_orientation"] == 1
    assert report["pages"] == []


@pytest.mark.parametrize(
    "fmt,frames", [("TIFF", 3), ("GIF", 3), ("TIFF", 101), ("GIF", 101)]
)
def test_multiframe(core, fmt, frames):
    data = raster(fmt, frames)
    with Image.open(io.BytesIO(data)) as im:
        assert im.n_frames == frames
    report = inspect(core, data)
    count = min(frames, 100)
    assert [c["id"] for c in report["candidates"]] == [
        f"f{n}" for n in range(1, count + 1)
    ]
    assert [r["frame"] for r in report["rasters"]] == list(range(1, count + 1))
    assert report["coverage"]["state"] == ("partial" if frames > 100 else "complete")
    assert [i["code"] for i in report["issues"]] == (
        ["frame_cap"] if frames > 100 else []
    )


@pytest.mark.parametrize(
    "data,code",
    [(b"", "corrupt"), (b"%PDF-1.4\n", "corrupt"), (b"random", "unsupported_format")],
)
def test_bad_input(core, tmp_path, data, code):
    path = tmp_path / "input.pdf"
    path.write_bytes(data)
    report = core.triage(path)
    assert report["decision"] == "unknown"
    assert report["issues"][0]["code"] == code


@pytest.mark.parametrize("failure", ["encrypted", "missing_dependency", "corrupt"])
def test_pdf_failures(core, monkeypatch, failure):
    if failure == "missing_dependency":
        monkeypatch.setitem(sys.modules, "pdfplumber", None)
    else:
        from pdfminer.pdfdocument import PDFPasswordIncorrect

        def fail(*args, **kwargs):
            raise (
                PDFPasswordIncorrect()
                if failure == "encrypted"
                else ValueError("private path MUST NOT LEAK")
            )

        monkeypatch.setattr(pdfplumber, "open", fail)
    report = inspect(core, pdf([{}]))
    assert report["decision"] == "unknown"
    assert report["issues"][0]["code"] == failure
    assert "private path" not in json.dumps(report)
    if failure == "missing_dependency":
        assert "pip install" in report["issues"][0]["detail"]


@pytest.mark.parametrize("missing", [False, True])
def test_image_probe_failure_keeps_candidate(core, monkeypatch, missing):
    data = raster()
    if missing:
        monkeypatch.setitem(sys.modules, "PIL", None)
        monkeypatch.setitem(sys.modules, "PIL.Image", None)
    if not missing:
        data = data[:8]
    report = inspect(core, data)
    assert report["decision"] == "ocr_recommended"
    assert report["candidates"][0]["id"] == "image"
    assert report["rasters"] == [
        dict(
            id="image",
            frame=None,
            px=None,
            mode=None,
            dpi_tag=None,
            exif_orientation=None,
            bytes=len(data),
        )
    ]
    assert report["coverage"]["state"] == "none"
    assert report["issues"][0]["code"] == "image_probe_failed"


@pytest.mark.parametrize("has_candidate", [False, True])
def test_page_cap(core, monkeypatch, has_candidate):
    monkeypatch.setattr(core, "MAX_PAGES", 1)
    data = pdf([{"images": [(0, 0, 100, 100)]} if has_candidate else {}, {}])
    report = inspect(core, data)
    assert report["coverage"] == dict(
        state="partial",
        pages_total=2,
        pages_inspected=1,
        limits=dict(max_bytes=256 * 1024**2, max_pages=1),
    )
    assert len(report["pages"]) == 1
    assert report["issues"][0]["code"] == "page_cap"
    assert report["decision"] == ("ocr_recommended" if has_candidate else "unknown")


def test_byte_cap_hash_chunks_and_restore(core, monkeypatch):
    data = pdf([{}])
    monkeypatch.setattr(core, "MAX_BYTES", len(data) - 1)
    report = inspect(core, data)
    assert report["decision"] == "unknown"
    assert report["file"]["sha256"] is None
    assert report["issues"][0]["code"] == "too_large"
    monkeypatch.setattr(core, "MAX_BYTES", len(data))
    assert inspect(core, data)["file"]["sha256"] == hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(core, "MAX_BYTES", 256 * 1024**2)

    class Watched(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 1024**2
            return super().read(size)

    stream = Watched(b"random" * 300000)
    stream.seek(17)
    report = core.triage(stream)
    assert stream.tell() == 17
    assert not stream.closed
    assert report["file"]["sha256"] == hashlib.sha256(stream.getvalue()).hexdigest()


def test_restore_pdf_and_no_network(core, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    stream = io.BytesIO(scan_pdf())
    stream.seek(13)
    assert core.triage(stream)["decision"] == "ocr_recommended"
    assert stream.tell() == 13 and not stream.closed


def test_schema_order_and_samples(core):
    data = pdf([{"text": "word " * 40}])
    report = inspect(core, data)
    assert list(report) == [
        "decision",
        "coverage",
        "summary",
        "candidates",
        "issues",
        "file",
        "options",
        "schema_version",
        "rules_version",
        "pages",
        "rasters",
    ]
    assert report["schema_version"] == 1 and report["rules_version"] == "1"
    assert report["options"] == {"samples": False}
    assert "text_sample" not in report["pages"][0]
    sampled = inspect(core, data, samples=True)
    assert sampled["options"] == {"samples": True}
    assert sampled["pages"][0]["text_sample"] == ("word " * 40).strip()[:120]
    assert report["pages"][0]["nonspace_chars"] == 160


def test_deterministic_subprocesses(core, tmp_path):
    path = tmp_path / "fixture.pdf"
    path.write_bytes(pdf([{"images": [(0, 0, 100, 100)], "text": "caption"}]))
    script = "import json,sys;from markitdown.ocr_triage import triage;print(json.dumps(triage(sys.argv[1]),ensure_ascii=False,separators=(',',':')))"
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script, str(path)],
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        for seed in ("1", "987")
    ]
    assert outputs[0] == outputs[1]


def test_source_failure(core, tmp_path):
    assert core.triage(tmp_path / "missing.pdf")["decision"] == "unknown"
    assert core.triage(object())["issues"]


@pytest.mark.parametrize(
    "matrix,dpi",
    [
        ((100, 20, 0, 100, 0, 0), None),
        ((0, 100, -100, 0, 100, 0), [72.0, 72.0]),
        ((100, 0, 0, 0, 0, 0), None),
    ],
)
def test_placement_dpi(core, matrix, dpi):
    data = pdf([{"matrices": [matrix]}])
    assert premise(data) == [(0, 1)]
    assert inspect(core, data)["pages"][0]["images"][0]["dpi_xy"] == dpi


def test_filter_list_from_stream_attrs(core, monkeypatch):
    from pdfminer.psparser import LIT

    data = pdf([{"images": [(0, 0, 100, 100)]}])
    with pdfplumber.open(io.BytesIO(data)) as doc:
        images = doc.pages[0].images
        assert images[0]["srcsize"] == (100, 100)
    images[0]["stream"].attrs.update(
        Filter=[LIT("ASCII85Decode"), LIT("FlateDecode")],
        ImageMask=True,
        BitsPerComponent=1,
    )
    monkeypatch.setattr(pdfplumber.page.Page, "images", property(lambda self: images))
    row = inspect(core, data)["pages"][0]["images"][0]
    assert row["filter"] == ["ASCII85Decode", "FlateDecode"]
    assert row["imagemask"] is True and row["bits"] == 1


def test_raw_thresholds_not_rounded_before_rules(core):
    data = pdf([{"text": "a" * 30, "images": [(0, 0, 100, 14.99)]}])
    assert premise(data) == [(30, 1)]
    report = inspect(core, data)
    assert report["pages"][0]["image_area_ratio"] == 0.15
    assert report["candidates"] == []


def test_raster_dpi_and_exif(core):
    stream = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (12, 15)).save(stream, "JPEG", dpi=(150, 150), exif=exif)
    row = inspect(core, stream.getvalue())["rasters"][0]
    assert row["dpi_tag"] == [150.0, 150.0]
    assert row["exif_orientation"] == 6


def test_partial_pdf_failure_overrides_candidates(core, monkeypatch):
    original = pdfplumber.page.Page.chars

    def chars(page):
        if page.page_number == 2:
            raise ValueError("private detail")
        return original.fget(page)

    monkeypatch.setattr(pdfplumber.page.Page, "chars", property(chars))
    report = inspect(core, pdf([{"images": [(0, 0, 100, 100)]}, {}]))
    assert report["decision"] == "unknown"
    assert report["coverage"]["state"] == "partial"
    assert report["coverage"]["pages_inspected"] == 1
    assert report["issues"][0]["code"] == "corrupt"


def test_no_conversion_or_llm(core, monkeypatch):
    import markitdown

    def forbidden(*args, **kwargs):
        pytest.fail("Conversion or client construction")

    monkeypatch.setattr(markitdown, "MarkItDown", forbidden)
    assert inspect(core, scan_pdf())["decision"] == "ocr_recommended"


@pytest.mark.parametrize("fmt,frames", [("PNG", 1), ("TIFF", 3)])
def test_raster_stream_restore(core, fmt, frames):
    stream = io.BytesIO(raster(fmt, frames))
    stream.seek(7)
    assert core.triage(stream)["decision"] == "ocr_recommended"
    assert not stream.closed and stream.tell() == 7


def test_colorspace_contains_names_only(core, monkeypatch):
    from pdfminer.psparser import LIT

    data = pdf([{"images": [(0, 0, 100, 100)]}])
    with pdfplumber.open(io.BytesIO(data)) as doc:
        images = doc.pages[0].images
        assert images[0]["srcsize"] == (100, 100)
    images[0]["stream"].attrs["ColorSpace"] = [
        LIT("Indexed"),
        LIT("DeviceRGB"),
        1,
        b"palette data",
    ]
    monkeypatch.setattr(pdfplumber.page.Page, "images", property(lambda self: images))
    row = inspect(core, data)["pages"][0]["images"][0]
    assert row["colorspace"] == ["Indexed", "DeviceRGB"]
