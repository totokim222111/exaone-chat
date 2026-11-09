import asyncio
import io
import json
import uuid
import zipfile

import pytest
from app import attachments as files
from app.chat_metrics import Timing, share_snapshot, snippet


def sample(kind):
    buffer = io.BytesIO()
    if kind == "pdf":
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 100 Td (PDF sample) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(buffer)
    elif kind == "docx":
        from docx import Document
        doc = Document()
        doc.add_paragraph("DOCX sample")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "left", "right"
        doc.save(buffer)
    elif kind == "xlsx":
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["XLSX sample", 42])
        book.save(buffer)
    else:
        from pptx import Presentation
        from pptx.util import Inches
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text = "PPTX sample"
        presentation.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("kind", ["pdf", "docx", "xlsx", "pptx"])
def test_each_document_format(kind):
    result = files.extract("file." + kind, sample(kind))
    assert result["kind"] == kind and kind.upper() + " sample" in result["text"]
    assert result["textChars"] == len(result["text"]) and not result["truncated"]
    if kind == "docx":
        assert "left\tright" in result["text"]


@pytest.mark.parametrize("extension", sorted(files.TEXT_EXTENSIONS))
def test_supported_text_extensions(extension):
    assert files.extract("test." + extension, "한글 text".encode())["text"] == "한글 text"


def test_text_encodings_html_and_name():
    assert files.extract("test.txt", "한글".encode("cp949"))["text"] == "한글"
    assert files.extract("test.log", b"caf\xe9")["text"] == "café"
    assert files.extract("test.html", b"<p>Hello &amp; text</p><script>secret</script>")["text"] == "Hello & text"
    assert files.clean_name("C:\\fakepath\\..\\한글\x00\n.txt") == "한글.txt"
    assert len(files.clean_name("x" * 300)) == 200


@pytest.mark.parametrize("name,raw,code", [
    ("picture.png", b"anything", "image_not_supported"),
    ("disguised.txt", b"\x89PNG\r\n\x1a\n", "image_not_supported"),
    ("bad.docx", b"not zip", "unsupported_type"),
    ("bad.txt", b"binary\x00data", "unsupported_type"),
    ("file.exe", b"MZ", "unsupported_type"),
    ("bad.pdf", b"not pdf", "unsupported_type"),
])
def test_type_and_magic_rejection(name, raw, code):
    with pytest.raises(files.AttachmentError) as error:
        files.identify(name, raw)
    assert error.value.code == code


def test_size_zip_bomb_and_truncation(monkeypatch):
    with pytest.raises(files.AttachmentError) as error:
        files.identify("file.txt", b"x" * (files.MAX_FILE + 1))
    assert error.value.status == 413
    result = files.extract("large.txt", b"x" * (files.MAX_TEXT + 1))
    assert len(result["text"]) == 120000 and result["truncated"]
    assert not files.extract("exact.txt", b"x" * files.MAX_TEXT)["truncated"]
    raw = sample("docx")
    monkeypatch.setattr(files, "MAX_ZIP", 100)
    with pytest.raises(files.AttachmentError) as error:
        files.identify("bomb.docx", raw)
    assert error.value.code == "zip_too_large"


def test_spreadsheet_row_limit():
    from openpyxl import Workbook
    book = Workbook()
    for i in range(2001):
        book.active.append([f"row-{i}"])
    raw = io.BytesIO()
    book.save(raw)
    result = files.extract("rows.xlsx", raw.getvalue())
    assert result["truncated"] and "row-1999" in result["text"] and "row-2000" not in result["text"]


def test_pdf_page_limit():
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(301):
        writer.add_blank_page(width=10, height=10)
    raw = io.BytesIO()
    writer.write(raw)
    assert files.extract("pages.pdf", raw.getvalue())["truncated"]


def test_extraction_subprocess_and_timeout():
    result = asyncio.run(files.extract_isolated("file.txt", b"hello"))
    assert result["text"] == "hello"
    with pytest.raises(files.AttachmentError) as error:
        asyncio.run(files.extract_isolated("file.txt", b"hello", timeout=0))
    assert error.value.code == "extraction_timeout"


def test_prompt_budget_current_first_and_old_file_cap():
    old, current = uuid.uuid4(), uuid.uuid4()
    rows = [{"id": mid, "role": "user", "content": "question", "error": None} for mid in (old, current)]
    def attachment(name, text):
        return dict(name=name, kind="text", text=text, text_chars=len(text))
    grouped = {old: [attachment("old.txt", "O" * 120000)],
               current: [attachment("current.txt", "N" * 120000), attachment("second.txt", "S" * 120000)]}
    output = files.build_history(rows, grouped, current)
    total = sum(len(m["content"]) - len("question") for m in output)
    assert total <= 200000
    assert "N" * 120000 in output[1]["content"] and "(이하 생략)" in output[1]["content"]
    assert "O" * 100 not in output[0]["content"]
    output = files.build_history(rows, {old: grouped[old]}, current)
    assert output[0]["content"].count("O") == 20000


def test_timing_all_phases_and_partial():
    timing = Timing(10)
    timing.search_started, timing.search_ended = 10, 10.5
    timing.token("reasoning", 11)
    timing.token("content", 12)
    assert timing.snapshot(13) == dict(firstTokenMs=1000, reasoningMs=1000, answerMs=1000, totalMs=3000, searchMs=500)
    timing = Timing(10)
    assert timing.snapshot(10.1)["firstTokenMs"] is None
    timing.token("reasoning", 11)
    assert timing.snapshot(12)["reasoningMs"] == 1000 and timing.snapshot(12)["answerMs"] is None
    timing = Timing(10)
    timing.token("content", 11)
    assert timing.snapshot(12)["reasoningMs"] is None


def test_snippet_unicode_and_ellipsis_offsets():
    text = "😀" * 70 + "한글 MATCH" + "뒤" * 80
    result = snippet(text, "match")
    assert result["snippet"].startswith("…") and result["snippet"].endswith("…")
    assert result["snippet"][result["matchStart"]:result["matchStart"] + result["matchLength"]] == "MATCH"
    assert result["matchStart"] == 61 and result["matchLength"] == 5
    assert snippet("nothing", "missing") is None


def test_share_snapshot_excludes_reasoning_and_attachment_data():
    snapshot = share_snapshot({"title": "title", "createdAt": "date"}, [{
        "role": "assistant", "content": "answer", "reasoning": "PRIVATE_REASONING", "error": None, "stopped": True,
        "search": {"sources": []}, "attachments": [{"name": "file.txt", "kind": "text", "size": 5,
            "text": "PRIVATE_ATTACHMENT", "raw": "PRIVATE_RAW", "id": "private-id"}]}])
    encoded = json.dumps(snapshot)
    assert all(secret not in encoded for secret in ("PRIVATE_REASONING", "PRIVATE_ATTACHMENT", "PRIVATE_RAW", "private-id"))
    assert snapshot["messages"][0]["attachments"] == [{"name": "file.txt", "kind": "text", "size": 5}]
