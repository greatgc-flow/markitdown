import importlib.util

import pytest

# Magika (and its onnxruntime dependency) is unavailable on Python 3.15 for now.
# Tests that rely on content sniffing of hint-free streams are skipped there.
requires_magika = pytest.mark.skipif(
    importlib.util.find_spec("magika") is None,
    reason="requires magika content sniffing (not installed on this Python)",
)
