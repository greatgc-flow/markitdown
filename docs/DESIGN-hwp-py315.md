# Design: HWP/HWPX support and Python 3.15 (fork greatgc-flow/markitdown)

Status: design agreed by owner session + cc + cx (rounds 1-2, 2026-10-22); open question Q-A (HWP5 fixture author). Next step: TDD.
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
1. Backend: `rhwp-python>=0.8.1,<0.9` only. No own parser, no hybrid. Accept single-maintainer 0.x risk; adapter stays ~40 lines.
2. `converters/_hwp_converter.py` `HwpConverter`: accepts extension `.hwp`/`.hwpx` or mimetype prefix in
   {application/x-hwp, application/x-hwp-v5, application/haansofthwp, application/vnd.hancom.hwp, application/vnd.hancom.hwpx,
   application/hwpx, application/x-hwpx, application/hwp+zip} (superset of #2230; `application/hwp+zip` is the HWPX mimetype file);
   lazy `import rhwp`; `MissingDependencyException` naming extra `hwp`; `rhwp.Document.from_bytes(file_stream.read())`
   then `.to_ir().to_markdown()`; preserve/restore stream position; register in `enable_builtins`.
3. Extra `hwp = ["rhwp-python>=0.8.1,<0.9"]`. NOT in `[all]` (no wheels for win-arm64/musl; markers cannot express musl).
   Missing-dep message recommends `markitdown[hwp]`. Document wheel matrix in README.
4. Output: pass tables through untouched (GFM or HTML). Replace `![alt](bin://N)` with `[Image N omitted]` (regex limited to bin:// image refs).
   No image extraction, OCR, table options.
5. CI: HWP job(s) install `[all,hwp]`; ARM job stays `[all]`. HWP integration tests fail (not skip) when rhwp is absent on the designated job.
6. Fixtures. HWPX: generated in-memory by a small test helper (stdlib zipfile; verified 2026-10-22 that rhwp parses a hand-built
   mimetype+container+content.hpf+header.xml+section0.xml package) - Korean text, ordering, ordinary table, merged-cell table; no binary committed.
   HWP5 (binary OLE, no hand-build route; rhwp cannot export HWP5): one tiny owner-authored `.hwp` committed with provenance
   (owner-saved from Hancom: few Korean lines + 2x2 table + merged table; question Q-A open). Until it exists HWP5 is covered by mock-routing tests only.
   Do NOT use #2230 fixtures (KOGL type 2), rhwp repo samples (unclear provenance) or the owner's private samples
   (`D:\PkgDev\workspace\*.hwp*`: local-only env-gated smoke tests, never committed/uploaded).

### Delivery
- Fork-first: two focused branches/PRs on greatgc-flow/markitdown (batch, CI is slow). Local tests first.
- Upstream: no competing PR; comment findings on #2230 (all breaks on win-arm/musl, dangling bin:// refs, fixture licensing); 3.15 change is a separate upstream candidate.

## Acceptance tests (write first; each falsifiable)
Py3.15 / magika (run on every job, py3.10-3.14 included, via `monkeypatch.setitem(sys.modules, "magika", None)` before constructing `MarkItDown`):
- construct works, `_magika is None`; HTML/CSV/txt/docx with extension convert; MIME-only success (explicit mimetype, no extension); supplied StreamInfo metadata preserved.
- extension-less/no-hint unknown stream -> `UnsupportedFormatException`.
- `Magika()` raising `RuntimeError` propagates (not swallowed).
- Packaging: clean install of the checkout/built wheel on 3.15 for `markitdown` and `markitdown[all]` resolves (CI Ubuntu + Windows x64 3.15); suite passes on 3.10-3.15; siblings (mcp/ocr/sample-plugin) install on 3.15.
HWP:
- `accepts()`: positive for each extension and each listed mimetype; negative for `.zip`, `.docx`, `application/zip`, no hints.
- direct `HwpConverter.convert` with rhwp absent -> `MissingDependencyException` matching `markitdown\[hwp\]`; through `MarkItDown.convert_stream` -> `FileConversionException` (wraps it). Mirror guarded-import test in a fresh subprocess.
- generated HWPX: non-empty, Korean text in document order, ordinary table as GFM, merged-cell table stays `<table>` (guards fixture degradation).
- image placeholder: pure unit test of the regex on literals: `![x](bin://3)` -> `[Image 3 omitted]`; `![x](https://e.com/a.png)` untouched; text containing "bin://" outside image syntax untouched.
- truncated HWPX bytes (and truncated HWP) via public API -> `FileConversionException`. Known limitation (non-goal): a *valid* zip with junk XML may be converted by ZipConverter via the extra `.zip` guess when magika is present; not handled.
- stream position: converter restores/leaves position as the harness expects on success and on failure (harness already seeks in `finally`; only assert for direct `convert` calls).
- CI: designated HWP job installs `[all,hwp]` and FAILS if rhwp is missing (`MARKITDOWN_REQUIRE_HWP=1` -> importorskip becomes assert); ARM job stays `[all]`, HWP tests skipped there.
- Owner-private samples: env-gated (`MARKITDOWN_HWP_SMOKE_DIR`) local smoke only, assert non-empty/no exception, never in CI.

## Non-goals
Own HWP/HWPX parser, HWP < 5.0, encrypted/distribution docs, exact page layout, headers/footers, image extraction/OCR/data URIs,
table-format options, platform-marker tricks for `[all]`, uv.lock, valid-zip-with-junk-XML detection, timeouts/size guards, magika replacement detector.
