# Design: HWP/HWPX support and Python 3.15 (fork greatgc-flow/markitdown)

Status: design approved by owner session + cc + cx (round 1, 2026-10-22). Next step: TDD.
Principle: "so very simple" - minimal change, deliberate non-goals.

## Evidence (verified 2026-10-22, Python 3.15.1, Windows x64)
- `requires-python` is `<3.15`; pip refuses 3.15.
- With override, resolution still fails: `magika` -> `onnxruntime` has no cp315 wheel (microsoft/onnxruntime#33271).
- `[all]` also blocked by `youtube-transcript-api~=1.2.3` (releases declare `<3.15`).
- All other deps resolve (lxml, pandas, numpy, pillow, cryptography, mammoth, pdf*, pptx, olefile, openpyxl, xlrd).
- `rhwp-python` 0.8.1 (MIT, PyO3, abi3 cp310) installs/runs on 3.15.1; ~0.1s for a 400 KB HWP and an 8.7 MB HWPX.
  Wheels: win_amd64, macOS x64/arm64, manylinux x86_64/aarch64. No musllinux, no win_arm64 (sdist needs Rust).
- `Document.from_bytes(data)` exists -> no temp file needed. `to_ir().to_markdown()` has no options.
  Tables: GFM when no merged cells, inline HTML `<table>` when any merged cell. Images: `![](bin://N)` (dangling). Headers/footers omitted.
- setup-python manifest has stable 3.15.1 for linux/win/macOS (x64 and arm64).
- Converter priority: specific = 0 (tried before Zip/PlainText/Html = 10).
- Upstream PR microsoft/markitdown#2230 (rhwp-python, adds to `[all]`, uv.lock, fixtures of unclear license) is unreviewed.

## Decisions
### Branch/PR 1 - Python 3.15 compatibility
1. `requires-python = ">=3.10,<3.16"`; add `Programming Language :: Python :: 3.15` classifier; README "3.10 through 3.15".
2. magika: dependency marker `; python_full_version < '3.15'` (comment links onnxruntime#33271, TODO remove) AND
   guarded import in `_markitdown.py`: `self._magika = None` when unavailable.
3. Without magika `_get_stream_info_guesses` returns `[enhanced_guess]` (extension/mimetype enrichment only).
   Never assign a charset to arbitrary bytes (PlainTextConverter accepts any charset). Unexpected Magika init errors stay visible.
   Known, documented regression: extension-less streams with no mimetype hint raise `UnsupportedFormatException`.
   Extra sniff only if the stubbed-magika test run proves a regression (then: only when ext, mimetype and charset are all None).
4. youtube-transcript-api: marker `; python_full_version < '3.15'` in both `[all]` and `youtube-transcription`.
   YouTubeConverter already degrades to metadata without transcripts - preserve, do not raise.
5. CI: add "3.15" to the 3 matrices in `.github/workflows/tests.yml` (required, not continue-on-error).
6. Check sibling packages (mcp/ocr/sample-plugin: `requires-python >=3.10`, depend on markitdown) install on 3.15.

### Branch/PR 2 - HWP/HWPX
1. Backend: `rhwp-python>=0.8.1,<0.9` only. No own parser, no hybrid. Accept single-maintainer 0.x risk; adapter stays ~40 lines.
2. `converters/_hwp_converter.py` `HwpConverter`: accepts `.hwp`/`.hwpx` + hwp mimetypes (as in #2230);
   lazy `import rhwp`; `MissingDependencyException` naming extra `hwp`; `rhwp.Document.from_bytes(file_stream.read())`
   then `.to_ir().to_markdown()`; preserve/restore stream position; register in `enable_builtins`.
3. Extra `hwp = ["rhwp-python>=0.8.1,<0.9"]`. NOT in `[all]` (no wheels for win-arm64/musl; markers cannot express musl).
   Missing-dep message recommends `markitdown[hwp]`. Document wheel matrix in README.
4. Output: pass tables through untouched (GFM or HTML). Replace `![alt](bin://N)` with `[Image N omitted]` (regex limited to bin:// image refs).
   No image extraction, OCR, table options.
5. CI: HWP job(s) install `[all,hwp]`; ARM job stays `[all]`. HWP integration tests fail (not skip) when rhwp is absent on the designated job.
6. Fixtures: tiny, owned, synthetic (Korean text, ordering, one merged-cell table) for both formats. Do NOT use #2230 fixtures (KOGL type 2) or the owner's private samples
   (`D:\PkgDev\workspace\*.hwp*`, local-only smoke tests, never committed/uploaded).

### Delivery
- Fork-first: two focused branches/PRs on greatgc-flow/markitdown (batch, CI is slow). Local tests first.
- Upstream: no competing PR; comment findings on #2230 (all breaks on win-arm/musl, dangling bin:// refs, fixture licensing); 3.15 change is a separate upstream candidate.

## Acceptance tests (write first)
Py3.15: `pip install markitdown` and `markitdown[all]` resolve on 3.15; suite passes on 3.15 and still on 3.10-3.14 (magika present);
with magika stubbed out: HTML/CSV/txt/docx with extension convert; extension-less unknown stream raises UnsupportedFormat.
HWP: accepts() by ext/mimetype; missing rhwp -> MissingDependencyException matching `markitdown\[hwp\]`;
real hwp+hwpx fixtures -> non-empty, Korean text in order, merged-cell table kept, no `bin://` left, `[Image N omitted]` present;
stream position preserved; corrupt/truncated hwpx raises FileConversionException (no silent fallthrough to ZipConverter);
output for fixtures asserted by content checks (not just non-empty).

## Non-goals
Own HWP/HWPX parser, HWP < 5.0, encrypted/distribution docs, exact page layout, headers/footers, image extraction/OCR/data URIs,
table-format options, platform-marker tricks for `[all]`, uv.lock, timeouts/size guards, magika replacement detector.
