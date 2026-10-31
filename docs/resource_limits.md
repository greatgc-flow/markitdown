# Opt-in resource budgets for document conversion

This document is the full reference for the compact `markitdown-gc --guide formats`
resource-policy section. The CLI guide intentionally stays within its 3,500
character topic budget. Policy limits are optional Python API features. They
do not make arbitrary third-party content or URLs safe.

## Separate trust boundaries

1. **HTTP response buffering**: `http_max_bytes` counts actual received bytes
   before buffering; `http_timeout_seconds` adds a request timeout, not a total
   conversion deadline. These options are for HTTP/HTTPS `convert_uri` calls;
   `convert_response` supports the byte limit for a supplied response.
2. **ZIP/archive conversion**: `zip_limits=ZipLimits(...)` guards compressed
   archive input, ZIP entries, members, nested decompression, depth and output.
   Non-seekable stream copy is also capped before ZIP parsing. It does not
   enforce an HTTP download budget by itself.
3. **Both together**: an HTTP-hosted ZIP needs *both* HTTP and ZIP limits.
   Limits are checked at different stages. An exceeded limit raises its specific
   exception, must not be turned into partial Markdown, and a rejected HTTP ZIP
   response must have its socket closed.
4. **Unavailable controls**: SSRF defense, host/IP/redirect allowlisting,
   process-wide CPU/memory/time isolation, PDF/OCR limits, strict ZIP member
   completeness and default MCP enforcement are separate requirements.

## Archive policy

### bounded ZIP API (optional)

Trusted local conversions retain their existing behavior when `zip_limits` is omitted.
For ZIP input from untrusted sources, use explicit per-conversion limits:

```python
from markitdown import MarkItDown, ZipLimits, ZipLimitExceeded

limits = ZipLimits(max_entries=200, max_archive_bytes=32 * 1024 * 1024,
                   max_member_bytes=8 * 1024 * 1024,
                   max_total_uncompressed_bytes=32 * 1024 * 1024,
                   max_depth=2, max_output_chars=4 * 1024 * 1024)
try:
    result = MarkItDown().convert("archive.zip", zip_limits=limits)
except ZipLimitExceeded:
    # Not a success; reject or quarantine the source, do not use partial content
    raise
```

Limits apply to compressed ZIP input before parsing (including non-seekable
stream buffering), each ZIP member, nested archives and final Markdown output. The limit does not constrain HTTP fetches
or PDF processing before ZIP selection.
No source extraction to disk is performed. A ZIP may still contain unsupported
files which the legacy converter skips; do **not** mistake successful Markdown
generation for full fidelity. Structured completeness reports and limits on
non-ZIP PDF/HTTP inputs remain separate follow-up work.

## HTTP policy

### Optional bounded HTTP conversion (Python API)

When converting remote HTTP/HTTPS documents in unattended workflows, bound the
response bytes and specify a network timeout rather than permitting an unlimited
in-memory buffer:

```python
from markitdown import MarkItDown, HTTPResponseLimitExceeded

try:
    result = MarkItDown().convert_uri(
        "https://example.test/document.txt",
        http_max_bytes=16 * 1024 * 1024,
        http_timeout_seconds=30,
    )
except HTTPResponseLimitExceeded:
    raise  # Reject as incomplete; no partial markdown is returned
```

The byte limit checks the **actual streamed response body**, not just the
Content-Length claim. Both options are opt-in; omission preserves the previous
library behavior. Using either on a `file:` or `data:` URI is rejected rather
than silently pretending the HTTP policy was enforced. `convert_response`
supports `http_max_bytes` when a caller supplies an already-fetched response.

This is **not** an SSRF defense or a full untrusted-input sandbox. Remote URLs
can still reach internal networks/redirects using the process's credentials;
restrict reachable destinations and proxy policy separately. A request
timeout does not provide a total end-to-end conversion deadline. ZIP sources require separate `zip_limits` opt-in. PDF processing, CPU usage,
strict completeness reporting and MCP defaults remain separate follow-up work.

## Operational verification

Tests in `packages/markitdown/tests/test_http_budget.py` and
`packages/markitdown/tests/test_zip.py` are deterministic no-network
regressions that exercise valid and rejected input, missing/invalid options,
non-seekable sources, and HTTP+ZIP interaction. The 24-check CI matrix must
pass for the **specific integrated commit**, not an earlier or separate PR.

Default conversions remain legacy-compatible when these policy options are
omitted. Do not claim that this opt-in API is a sandbox, or that a successful
ZIP conversion implies that every member was processed.
