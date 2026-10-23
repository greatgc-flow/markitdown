"""Small synthetic PDFs: stdlib writer, plus an optional Pillow scan helper."""

import io


def pdf(pages):
    """Pages specify text, images (x, y, width, height), rotate, vectors, cid, inline."""
    objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]

    def add(value):
        objects.append(value)
        return len(objects)

    def stream(attrs, data):
        return (
            b"<< "
            + attrs
            + f" /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream"
        )

    image = add(
        stream(
            b"/Type /XObject /Subtype /Image /Width 100 /Height 100 /ColorSpace /DeviceRGB /BitsPerComponent 8",
            b"\xff\xff\xff" * 10000,
        )
    )
    children = []
    for page in pages:
        font = 3
        if page.get("cid"):
            descendant = add(
                b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Synthetic /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>"
            )
            font = add(
                f"<< /Type /Font /Subtype /Type0 /BaseFont /Synthetic /Encoding /Identity-H /DescendantFonts [{descendant} 0 R] >>".encode()
            )
        text = (
            page.get("text", "")
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        commands = [f"BT /F1 1 Tf 1 50 Td ({text}) Tj ET"] if text else []
        if page.get("cid"):
            commands.append("BT /F1 1 Tf 1 50 Td <00010002000300040005> Tj ET")
        for x, y, w, h in page.get("images", []):
            commands.append(f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q")
        for matrix in page.get("matrices", []):
            commands.append(f"q {' '.join(map(str, matrix))} cm /Im1 Do Q")
        if page.get("inline"):
            commands.append(
                "q 100 0 0 100 0 0 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID abc EI Q"
            )
        commands.extend("0 0 1 1 re S" for _ in range(page.get("vectors", 0)))
        content = add(stream(b"", "\n".join(commands).encode("ascii")))
        children.append(
            add(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Rotate {page.get('rotate', 0)} /Resources << /Font << /F1 {font} 0 R >> /XObject << /Im1 {image} 0 R >> >> /Contents {content} 0 R >>".encode()
            )
        )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (
        f"<< /Type /Pages /Count {len(children)} /Kids [{' '.join(f'{n} 0 R' for n in children)}] >>".encode()
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(output)


def scan_pdf():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, "PDF", resolution=72)
    return output.getvalue()
