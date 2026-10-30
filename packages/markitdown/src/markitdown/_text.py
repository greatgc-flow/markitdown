"""Conservative text validation for content/extension mismatches and triage."""

import re
import unicodedata


def strict_text(data):
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = data.decode(encoding)
    if any(unicodedata.category(c) == "Cc" and c not in "\t\r\n" for c in text):
        raise ValueError("Text contains control characters")
    return text


def is_html(text):
    return bool(
        re.match(r"\s*<(?:!doctype\s+html\b|(?:html|head|body|table)\b)", text, re.I)
    )
