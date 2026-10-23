"""Minimal owned CFB/HWP5: regular streams only, no external document content."""

import struct
import zlib


def build_hwp5(image):
    free, end, fat = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))

    def entry(name, kind, *, right=free, child=free, start=end, size=0):
        row = bytearray(128)
        encoded = (name + "\0").encode("utf-16-le")
        row[: len(encoded)] = encoded
        struct.pack_into("<HBBIII", row, 64, len(encoded), kind, 1, free, right, child)
        struct.pack_into("<IQ", row, 116, start, size)
        return row

    file_header = bytearray(4096)
    file_header[:17] = b"HWP Document File"
    struct.pack_into("<II", file_header, 32, 0x05000300, 1)
    compressor = zlib.compressobj(wbits=-15)
    stored_image = (compressor.compress(image) + compressor.flush()).ljust(4096, b"\0")
    assert len(stored_image) == 4096
    directory = b"".join(
        [
            entry("Root Entry", 5, child=1),
            entry("FileHeader", 2, right=2, start=3, size=4096),
            entry("BinData", 1, child=3),
            entry("BIN0001.png", 2, right=4, start=11, size=4096),
            entry("BIN0002.wmf", 2, start=19, size=4096),
        ]
    ).ljust(1024, b"\0")
    allocation = [free] * 128
    allocation[0] = fat
    allocation[1:3] = [2, end]
    for start in (3, 11, 19):
        allocation[start : start + 8] = [*range(start + 1, start + 8), end]
    return (
        bytes(header)
        + struct.pack("<128I", *allocation)
        + directory
        + bytes(file_header)
        + stored_image
        + b"synthetic WMF".ljust(4096, b"\0")
    )
