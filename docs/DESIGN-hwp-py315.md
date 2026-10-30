# Design: HWP/HWPX support and Python 3.15 (fork greatgc-flow/markitdown-gc)

Status: round-4 backend change agreed by owner + cc + cx; TDD rework. Q-A (owner-authored HWP5 fixture) remains open.
Principle: "so very simple" - minimal change, deliberate non-goals.

## Evidence (verified 2026-10-22, Python 3.15.1, Windows x64)
- `requires-python` is `<3.15`; pip refuses 3.15.
- With override, resolution still fails: `magika` -> `onnxruntime` has no cp315 wheel (microsoft/onnxruntime#33271).
- `[all]` also blocked by `youtube-transcript-api~=1.2.3` (releases declare `<3.15`).
- All other deps resolve (lxml, pandas, numpy, pillow, cryptography, mammoth, pdf*, pptx, olefile, openpyxl, xlrd).
- `hwp-hwpx-parser` 1.0.0: pure Python, Apache-2.0, py3-none-any; dependencies olefile + python-docx.
  Supports HWP5/HWPX; public read API requires a path. Tables are returned inline during extraction.
  Merged-cell spans are lost. `extract_text_with_notes()` returns note bodies separately; SIMPLE images emit `[IMAGE]`.
- setup-python manifest has stable 3.15.1 for linux/win/macOS (x64 and arm64).
- Converter priority: specific = 0 (tried before Zip/PlainText/Html = 10).
- Upstream PR microsoft/markitdown#2230 (rhwp, adds to `[all]`, uv.lock, fixtures of unclear license) is unreviewed.

## Round 4 decision
Switch to `hwp-hwpx-parser>=1.0,<1.1`, including in `[all]`: rhwp 0.8.1 fails Linux import
with undefined FreeType symbol `FT_Palette_Data_Get` (CI and [rhwp#31](https://github.com/DanMeon/rhwp-python/issues/31)).
Accept merged-cell fidelity loss: flatten cells into pipe rows, preserving text and order. No hybrid or Linux workaround.
Keep the adapter small (roughly 40 lines of conversion logic plus routing/cleanup), allowing a later swap if rhwp#31 is fixed.
The parser's reported hang risk remains; private smoke probes use 20-second subprocess timeouts, not production time limits.

## Decisions
### Branch/PR 1 - Python 3.15 compatibility
1. `requires-python = ">=3.10,<3.16"`; add `Programming Language :: Python :: 3.15` classifier; README "3.10 through 3.15".
2. magika: dependency marker `; python_full_version < '3.15'` (comment links onnxruntime#33271, TODO remove) AND
   guarded import moved into `MarkItDown.__init__` (replaces top-level `import magika` and `magika.Magika()`):
   catch `ImportError` only -> `self._magika = None`; other Magika init errors propagate.
3. Without magika `_get_stream_info_guesses` returns `[enhanced_guess]` (extension/mimetype enrichment only).
   Never assign a charset to arbitrary bytes (PlainTextConverter accepts any charset). Unexpected Magika init errors stay visible.
   Known, documented regression (default built-ins only): extension-less streams with no mimetype hint raise `UnsupportedFormatException`;
   wrong extensions lose magika's corrective guess. No replacement detector (non-goal).
4. youtube-transcript-api: marker `; python_full_version < '3.15'` in both `[all]` and `youtube-transcription`.
   YouTubeConverter already degrades to metadata without transcripts - preserve, do not raise.
5. CI: add "3.15" to the 3 matrices in `.github/workflows/tests.yml` (required, not continue-on-error) AND an explicit
   `windows-latest` + 3.15 (x64) include (matrix additions alone give Ubuntu only). Install tests use the checkout/built wheel, never published markitdown.
6. Check sibling packages (mcp/ocr/sample-plugin: `requires-python >=3.10`, depend on markitdown) install on 3.15.

### Branch/PR 2 - HWP/HWPX
1. Backend: `hwp-hwpx-parser>=1.0,<1.1` only, no own parser or hybrid.
2. `HwpConverter` accepts `.hwp`/`.hwpx` or mimetype prefixes in
   {application/x-hwp, application/x-hwp-v5, application/haansofthwp, application/vnd.hancom.hwp,
   application/vnd.hancom.hwpx, application/hwpx, application/x-hwpx, application/hwp+zip}.
   Lazy `import hwp_hwpx_parser`; missing dependency recommends `markitdown[hwp]`.
   Register in `enable_builtins`. Select temporary suffix from extension, then MIME, then OLE/ZIP magic.
   Write a NamedTemporaryFile(delete=False), CLOSE before public `read(path)` (Windows), always close Reader and unlink in finally.
3. Extra `hwp = ["hwp-hwpx-parser>=1.0,<1.1"]`; same dependency in `[all]` (pure Python, all platforms).
4. `ExtractOptions(table_style=MARKDOWN, image_marker=SIMPLE)` and `extract_text_with_notes()`.
   Pipe tables remain inline in document order; merged-cell spans are deliberately lost, cell text preserved once.
   Images become `[IMAGE]`; append footnote/endnote Markdown definitions. No bin-reference regex or image extraction.
5. Normal CI tests install `[all]` and require the backend on all platforms. No dedicated HWP job or optional-backend skips.
6. HWPX: owned in-memory stdlib ZIP fixture with Korean paragraphs, ordinary/merged tables, picture reference, footnote/endnote.
   HWP5: pending owner-authored `tests/test_files/test_hwp5.hwp` with provenance; fake-backend routing until available.
   Never copy external fixtures or private content. Owner-private files remain local-only and uncommitted.

### Delivery
- Fork-first: two focused branches/PRs on greatgc-flow/markitdown-gc (batch, CI is slow). Local tests first.
- Upstream: no competing PR; share backend portability, output, and fixture-provenance findings on #2230; 3.15 change is a separate upstream candidate.

## Acceptance tests (write first; each falsifiable)
Py3.15 / magika (run on every job, py3.10-3.14 included, via `monkeypatch.setitem(sys.modules, "magika", None)` before constructing `MarkItDown`):
- construct works, `_magika is None`; HTML/CSV/txt/docx with extension convert; MIME-only success (explicit mimetype, no extension); supplied StreamInfo metadata preserved.
- extension-less/no-hint unknown stream -> `UnsupportedFormatException`.
- `Magika()` raising `RuntimeError` propagates (not swallowed).
- Packaging: clean install of the checkout/built wheel on 3.15 for `markitdown` and `markitdown[all]` resolves (CI Ubuntu + Windows x64 3.15); suite passes on 3.10-3.15; siblings (mcp/ocr/sample-plugin) install on 3.15.
HWP:
- Positive accepts for each extension/MIME; negative for ZIP, DOCX, no hints; accepts never consumes bytes.
- Missing parser: direct MissingDependencyException matching `markitdown[hwp]`; public FileConversionException wrapping it.
  Public failure tests use `MarkItDown(enable_builtins=False)` + only HwpConverter, independent of Magika/PlainText fallback.
  Verify lazy import in a fresh subprocess.
- Generated HWPX: Korean paragraphs/tables in order, ordinary pipe table; every ordinary/merged cell text exactly once.
  Real extraction emits `[IMAGE]`; footnote/endnote bodies and matching Markdown definitions appear once.
- Garbage, truncated ZIP, OLE-less HWP through restricted public API -> FileConversionException.
  Valid ZIP with invalid XML can still fall through to ZipConverter in the general pipeline: pre-existing non-goal.
- Path-only API: closed temporary input, correct suffix by extension/MIME/magic; Reader closed and file unlinked on success,
  reader creation/extraction/close failure, and write failure. Direct conversion consumes remaining stream bytes.
- Backend tests always run in `[all]` CI; no importorskip or backend-require flag.
- Pending owner-authored HWP5 fixture skips with reason 'owner-authored HWP5 fixture pending'.
- Env-gated `MARKITDOWN_HWP_SMOKE_DIR`: local private conversions in 20-second subprocesses, nonempty/no exception,
  counts only, never in CI or committed/uploaded.

## Non-goals
Own HWP/HWPX parser, HWP < 5.0, encrypted/distribution docs, exact page layout, headers/footers, image extraction/OCR/data URIs,
merged-cell fidelity (accepted text-preserving flattening), table-format options, platform-marker tricks, uv.lock,
valid-zip-with-junk-XML detection, production timeouts/size guards, magika replacement detector.
