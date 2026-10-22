"""Owned, synthetic OWPML fixtures; no content from external documents."""

import io
import zipfile
from xml.sax.saxutils import escape


_NS = (
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:opf="http://www.idpf.org/2007/opf/"'
)


def _paragraph(text):
    return (
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        'columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
        f"<hp:t>{escape(text)}</hp:t></hp:run></hp:p>"
    )


def _cell(text, row, col, colspan=1):
    return (
        '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" '
        'dirty="0" borderFillIDRef="1"><hp:subList id="0" textDirection="HORIZONTAL" '
        'lineWrap="BREAK" vertAlign="CENTER">'
        f'{_paragraph(text)}</hp:subList><hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        f'<hp:cellSpan colSpan="{colspan}" rowSpan="1"/>'
        f'<hp:cellSz width="{4000 * colspan}" height="2000"/>'
        '<hp:cellMargin left="0" right="0" top="0" bottom="0"/></hp:tc>'
    )


def _table(merged):
    first_row = (
        _cell("병합 제목", 0, 0, colspan=2)
        if merged
        else _cell("항목", 0, 0) + _cell("값", 0, 1)
    )
    rows = (
        f"<hp:tr>{first_row}</hp:tr><hp:tr>"
        f'{_cell("사과", 1, 0)}{_cell("둘", 1, 1)}</hp:tr>'
    )
    return (
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">'
        '<hp:tbl id="1" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
        'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" '
        'repeatHeader="0" rowCnt="2" colCnt="2" cellSpacing="0" borderFillIDRef="1">'
        '<hp:sz width="8000" widthRelTo="ABSOLUTE" height="4000" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        f"{rows}</hp:tbl></hp:run></hp:p>"
    )


def build_hwpx():
    """Return a minimal package with Korean paragraphs and both table forms."""
    header = (
        f'<hh:head {_NS} version="1.4" secCnt="1"><hh:refList>'
        '<hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000" '
        'textColor="#000000"/></hh:charProperties>'
        '<hh:paraProperties itemCnt="1"><hh:paraPr id="0" tabPrIDRef="0">'
        '<hh:align horizontal="JUSTIFY" vertical="BASELINE"/>'
        '</hh:paraPr></hh:paraProperties><hh:styles itemCnt="1">'
        '<hh:style id="0" type="PARA" name="Normal" engName="Normal" '
        'paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langID="1042" '
        'lockForm="0"/></hh:styles></hh:refList></hh:head>'
    )
    section = (
        f'<hs:sec {_NS}>{_paragraph("첫 번째 문단입니다.")}'
        f'{_table(False)}{_paragraph("두 번째 문단입니다.")}'
        f'{_table(True)}{_paragraph("마지막 문단입니다.")}</hs:sec>'
    )
    content = (
        f'<opf:package {_NS} version="" unique-identifier="" id="">'
        '<opf:metadata><opf:title>Synthetic fixture</opf:title></opf:metadata>'
        '<opf:manifest><opf:item id="header" href="Contents/header.xml" '
        'media-type="application/xml"/><opf:item id="section0" '
        'href="Contents/section0.xml" media-type="application/xml"/>'
        '</opf:manifest><opf:spine><opf:itemref idref="header"/>'
        '<opf:itemref idref="section0"/></opf:spine></opf:package>'
    )
    container = (
        '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<ocf:rootfiles><ocf:rootfile full-path="Contents/content.hpf" '
        'media-type="application/hwpml-package+xml"/></ocf:rootfiles></ocf:container>'
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("mimetype", "application/hwp+zip")
        for path, xml in (
            ("META-INF/container.xml", container),
            ("Contents/content.hpf", content),
            ("Contents/header.xml", header),
            ("Contents/section0.xml", section),
        ):
            package.writestr(path, xml.encode("utf-8"))
    return stream.getvalue()
