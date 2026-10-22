# MarkItDown guide (for AI agents)

Converts files/URLs to Markdown for LLM input. Offline by default; text only, no layout fidelity.

## Run
- CLI: `markitdown FILE [-o out.md]` | `cat FILE | markitdown -x .pdf` (stdin needs `-x EXT` or `-m MIME`).
- Python: `from markitdown import MarkItDown; MarkItDown().convert(path_or_url).markdown`
  (`convert_stream(binary_file_obj, stream_info=StreamInfo(extension=".pdf"))` for bytes).
- Install extras per format: `pip install 'markitdown[all]'` (or `[pdf]`, `[docx]`, `[pptx]`, `[xlsx]`, `[hwp]`, ...).
- Python 3.15: supported, but magika (content sniffing) is unavailable. Always pass the extension or MIME type
  for stdin/bytes; files with a normal extension are fine.
- `convert()` also fetches `http(s)://`, `file://`, `data:` URIs. Never pass untrusted input as the source string.

## Formats (what you actually get)
| Input | Output notes |
|---|---|
| PDF | text per page; tables best-effort; scanned pages give NO text (see OCR) |
| DOCX / PPTX | headings, lists, tables, alt text; images become `![alt](file)` or truncated `data:` URIs; PPTX has `<!-- Slide number: N -->` and notes |
| XLSX / XLS / CSV | one table per sheet |
| HWP / HWPX | text + tables as pipe rows (merged cells flattened); images become `[IMAGE]`; footnotes appended; HWP < 5.0 and encrypted docs unsupported |
| HTML, EPUB, RSS, IPYNB, JSON/XML/text, ZIP (recursive), MSG | text/structure |
| Image | EXIF metadata only if `exiftool` is installed, description only with an LLM client; otherwise empty |
| Audio | metadata + speech transcript (needs `[audio-transcription]`, ffmpeg, network) |

## Silent empty output (the main trap)
Conversion succeeds but returns empty or tiny text for: scanned/image-only PDFs, image files, slides that are all pictures,
HWP files made of page images. Exceptions are not raised. Check the result before trusting it:
- `len(text.strip()) == 0`, or fewer than ~30 non-space characters per PDF page / slide.
- Many `![...](...)` or `[IMAGE]` markers but little surrounding text.
- Garbled text: many `(cid:N)`, `�` or private-use characters means the PDF text layer is unusable.

## OCR
MarkItDown does not OCR by default. Options: the `markitdown-ocr` plugin (needs an OpenAI-compatible `llm_client`),
Azure Document Intelligence (`-d -e ENDPOINT`), or have a vision-capable agent read the page/image and write the text.
Do not run bulk OCR without owner approval (cost). Cache results by file hash + page and never OCR the same page twice.

## Errors
- `UnsupportedFormatException`: no converter accepted the input (missing extension/MIME hint, or unknown format).
- `MissingDependencyException`: install the extra named in the message.
- `FileConversionException`: a converter accepted the file but failed (corrupt/encrypted file); the message lists attempts.

## Options worth knowing
`--keep-data-uris` keep base64 images (huge output; default truncates). `-c CHARSET` charset hint.
`-p/--use-plugins` enable plugins. Output is UTF-8; on Windows set `PYTHONUTF8=1`.
Batch: loop per file in your own code, catch the three exceptions above, and log failures instead of stopping.
