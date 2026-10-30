"""Generated regressions for issue #22; no binary fixtures required."""

import io
import unittest
import warnings
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from markitdown import MarkItDown, StreamInfo
from markitdown._base_converter import DocumentConverterResult
from markitdown.converters import _xlsx_converter as module
from markitdown.converters._plain_text_converter import PlainTextConverter


def workbook_zip(
    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/xl/workbook.xml" ContentType="{content_type}"/>'
            "</Types>",
        )
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return stream.getvalue()


class OoxmlRoutingTests(unittest.TestCase):
    def test_xlsm_dispatch(self):
        converter = module.XlsConverter()
        for info in [
            StreamInfo(extension=".XLSM"),
            StreamInfo(mimetype="application/vnd.ms-excel.sheet.macroEnabled.12"),
        ]:
            self.assertTrue(converter.accepts(io.BytesIO(), info))

    def test_ooxml_uses_xlsx_path_and_restores_position(self):
        for extension in [".xls", ".xlsm"]:
            for content_type in [
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
            ]:
                with self.subTest(extension=extension, content_type=content_type):
                    stream = io.BytesIO(b"skip" + workbook_zip(content_type))
                    stream.seek(4)
                    with patch.object(
                        module.XlsxConverter,
                        "convert",
                        return_value=DocumentConverterResult(markdown="table"),
                    ) as convert:
                        with self.assertWarnsRegex(
                            UserWarning, "converted as XLSX. Verify output."
                        ):
                            result = module.XlsConverter().convert(
                                stream, StreamInfo(extension=extension)
                            )
                    self.assertEqual(result.markdown, "table")
                    self.assertIs(convert.call_args.args[0], stream)
                    self.assertEqual(stream.tell(), 4)

    def test_non_workbooks_keep_native_cause(self):
        unrelated = io.BytesIO()
        with zipfile.ZipFile(unrelated, "w") as archive:
            archive.writestr("notes.txt", "text")
        encrypted = bytearray(workbook_zip())
        encrypted[6] |= 1
        encrypted[encrypted.index(b"PK\x01\x02") + 8] |= 1
        corrupt = bytearray(workbook_zip())
        corrupt[30 + len("[Content_Types].xml") + 5] ^= 255
        for data in [
            unrelated.getvalue(),
            b"PK\x03\x04broken",
            b"\xd0\xcf\x11\xe0encrypted",
            bytes(encrypted),
            bytes(corrupt),
            workbook_zip("application/other"),
            workbook_zip()[:40],
            workbook_zip("x" * 65537),
        ]:
            for extension in [".xls", ".xlsm"]:
                with self.subTest(extension=extension, data=data[:20]):
                    stream = io.BytesIO(b"skip" + data)
                    stream.seek(4)
                    reader = Mock(side_effect=ValueError("native cause"))
                    with patch.object(
                        module, "_xls_dependency_exc_info", None
                    ), patch.object(
                        module, "pd", SimpleNamespace(read_excel=reader), create=True
                    ):
                        with warnings.catch_warnings(record=True) as caught:
                            with self.assertRaisesRegex(ValueError, "native cause"):
                                module.XlsConverter().convert(
                                    stream, StreamInfo(extension=extension)
                                )
                    self.assertEqual(reader.call_args.kwargs["engine"], "xlrd")
                    self.assertFalse(caught)
                    self.assertEqual(stream.tell(), 4)

    @unittest.skipIf(
        module._xlsx_dependency_exc_info is not None, "pandas/openpyxl unavailable"
    )
    def test_generated_workbook_conversion(self):
        import openpyxl

        workbook = openpyxl.Workbook()
        workbook.active.append(["Header"])
        workbook.active.append(["issue22"])
        stream = io.BytesIO()
        workbook.save(stream)
        workbook.close()
        for extension in [".xls", ".xlsm"]:
            stream.seek(0)
            with self.assertWarnsRegex(UserWarning, "converted as XLSX"):
                result = MarkItDown().convert_stream(
                    stream, stream_info=StreamInfo(extension=extension)
                )
            self.assertIn("issue22", result.markdown)
            self.assertEqual(stream.tell(), 0)


class BomRoutingTests(unittest.TestCase):
    def test_xml_bom_overrides_detected_charset(self):
        converter = MarkItDown(enable_builtins=False)
        output = SimpleNamespace(
            is_text=True, label="xml", extensions=["xml"], mime_type="text/xml"
        )
        converter._magika = SimpleNamespace(
            identify_stream=Mock(
                return_value=SimpleNamespace(
                    status="ok", prediction=SimpleNamespace(output=output)
                )
            )
        )
        text = "<root>한글</root>"
        for encoding in ["utf-16", "utf-8-sig", "utf-16-be"]:
            data = text.encode(encoding)
            if encoding == "utf-16-be":
                data = b"\xfe\xff" + data
            stream = io.BytesIO(b"skip" + data)
            stream.seek(4)
            with patch(
                "markitdown._markitdown.charset_normalizer.from_bytes",
                return_value=SimpleNamespace(
                    best=lambda: SimpleNamespace(encoding="cp949")
                ),
            ):
                guesses = converter._get_stream_info_guesses(
                    stream, StreamInfo(extension=".xml")
                )
                explicit = converter._get_stream_info_guesses(
                    stream, StreamInfo(extension=".xml", charset="cp949")
                )
            self.assertEqual(stream.tell(), 4)
            self.assertEqual(explicit[0].charset, "cp949")
            self.assertEqual(
                PlainTextConverter().convert(stream, guesses[0]).markdown, text
            )


if __name__ == "__main__":
    unittest.main()
