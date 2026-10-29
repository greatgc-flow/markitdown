# MarkItDown GC

> Unofficial fork of [Microsoft MarkItDown](https://github.com/microsoft/markitdown), maintained at [greatgc-flow/markitdown](https://github.com/greatgc-flow/markitdown). This fork is not affiliated with or endorsed by Microsoft.
> Install the `markitdown-gc` distribution. Do not install it together with upstream `markitdown` in the same environment: both provide the `markitdown` import and CLI, and the same plugin group. Use a fresh virtual environment.

> [!TIP]
> MarkItDown is a Python package and command-line utility for converting various files to Markdown (e.g., for indexing, text analysis, etc).
>
> For more information, and full documentation, see the project [README.md](https://github.com/greatgc-flow/markitdown) on GitHub.

> [!IMPORTANT]
> MarkItDown performs I/O with the privileges of the current process. Like open() or requests.get(), it will access resources that the process itself can access. Sanitize your inputs in untrusted environments, and call the narrowest `convert_*` function needed for your use case (e.g., `convert_stream()`, or `convert_local()`). See the [Security Considerations](https://github.com/greatgc-flow/markitdown#security-considerations) section of the documentation for more information.

## Installation

From PyPI:

```bash
pip install markitdown-gc
```

All document converters (PDF, DOCX, PPTX, XLSX/XLS, Outlook, HWP5/HWPX) are included in the default install; `markitdown-gc --guide` explains usage. Audio, YouTube and Azure support stay optional (`[all]` adds them). The backend is pure Python for all platforms. Tables use pipe rows (merged cells lose their spans), images become `[IMAGE]` placeholders, and note bodies are included. HWP < 5, encrypted/distribution documents, headers/footers, and exact layout are unsupported. See the project [HWP/HWPX documentation](https://github.com/greatgc-flow/markitdown#hwphwpx) for details.

From source:

```bash
git clone https://github.com/greatgc-flow/markitdown.git
cd markitdown
pip install -e 'packages/markitdown[all]'
```

## Usage

### Command-Line

```bash
markitdown path-to-file.pdf > document.md
```

### Python API

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("test.xlsx")
print(result.markdown)
```

### More Information

For more information, and full documentation, see the project [README.md](https://github.com/greatgc-flow/markitdown) on GitHub.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
