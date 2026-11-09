import asyncio
import csv
import io
import json
import mimetypes
import os
import re
import sys
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath

MAX_FILE = 10 * 1024 * 1024
MAX_TEXT = 120000
MAX_ZIP = 100 * 1024 * 1024
TEXT_EXTENSIONS = set("txt md csv tsv json xml yaml yml toml ini log html htm py js ts tsx jsx java kt c h cpp cs go rs rb php swift sql sh ps1 bat r m scala lua dart vue svelte css scss".split())
IMAGE_EXTENSIONS = set("png jpg jpeg gif webp bmp heic".split())
MIMES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
META_COLUMNS = "a.id,a.message_id,a.name,a.mime,a.size,a.kind,a.text_chars,a.truncated,a.position"


class AttachmentError(Exception):
    def __init__(self, code, message, status=415):
        self.code, self.message, self.status = code, message, status


def clean_name(name):
    leaf = (name or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(c for c in leaf if not unicodedata.category(c).startswith("C"))[:200].strip() or "attachment"


def check_zip(raw, kind):
    try:
        if not raw.startswith(b"PK\x03\x04"):
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_ZIP:
                raise AttachmentError("zip_too_large", "압축 해제 크기는 100MB 이하여야 합니다.", 413)
            expected = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[kind]
            if expected not in archive.namelist() or "[Content_Types].xml" not in archive.namelist():
                raise ValueError()
    except (zipfile.BadZipFile, ValueError, KeyError):
        raise AttachmentError("unsupported_type", "파일 내용이 확장자와 일치하지 않습니다.") from None


def identify(name, raw):
    if len(raw) > MAX_FILE:
        raise AttachmentError("file_too_large", "파일은 10MB 이하여야 합니다.", 413)
    ext = PurePosixPath(name).suffix.lower().lstrip(".")
    image = (raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM"))
             or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
             or (raw[4:8] == b"ftyp" and raw[8:12] in (b"heic", b"heix", b"hevc", b"mif1")))
    if ext in IMAGE_EXTENSIONS or image:
        raise AttachmentError("image_not_supported", "EXAONE 은 이미지를 읽을 수 없습니다")
    if ext in ("docx", "xlsx", "pptx"):
        check_zip(raw, ext)
        return ext, MIMES[ext]
    if ext == "pdf" and raw.startswith(b"%PDF-"):
        return "pdf", MIMES["pdf"]
    if ext in TEXT_EXTENSIONS and not raw.startswith((b"%PDF-", b"PK\x03\x04", b"MZ", b"\x7fELF", b"\xd0\xcf\x11\xe0")):
        decode_text(raw)
        return "text", mimetypes.guess_type(name)[0] or "text/plain"
    raise AttachmentError("unsupported_type", "지원하지 않는 파일 형식입니다.")


def decode_text(raw):
    for encoding in ("utf-8-sig", "cp949", "latin-1"):
        try:
            value = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if "\x00" in value or sum(ord(c) < 32 and c not in "\r\n\t\f" for c in value) > max(0, len(value) // 100):
        raise AttachmentError("unsupported_type", "텍스트로 읽을 수 없는 파일입니다.")
    return value


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def extract(name, raw):
    kind, mime = identify(name, raw)
    parts, count, truncated = [], 0, False

    def add(value):
        nonlocal count, truncated
        value = str(value).replace("\x00", "")
        room = MAX_TEXT - count
        if len(value) > room:
            truncated = True
        parts.append(value[:room])
        count += min(room, len(value))
        return count >= MAX_TEXT

    if kind == "text":
        value = decode_text(raw)
        if PurePosixPath(name).suffix.lower() in (".html", ".htm"):
            parser = HTMLText()
            parser.feed(value)
            value = "".join(parser.parts).strip()
        add(value)
    elif kind == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > 300:
            truncated = True
        for page in reader.pages[:300]:
            if add((page.extract_text() or "") + "\n"):
                break
    elif kind == "docx":
        from docx import Document
        document = Document(io.BytesIO(raw))
        for paragraph in document.paragraphs:
            if add(paragraph.text + "\n"):
                break
        if count < MAX_TEXT:
            for table in document.tables:
                for row in table.rows:
                    if add("\t".join(cell.text for cell in row.cells) + "\n"):
                        break
                if count >= MAX_TEXT:
                    break
    elif kind == "xlsx":
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            for sheet in book:
                if add(f"\n[시트: {sheet.title}]\n"):
                    break
                if sheet.max_row and sheet.max_row > 2000:
                    truncated = True
                for row in sheet.iter_rows(max_row=2000, values_only=True):
                    buffer = io.StringIO()
                    csv.writer(buffer).writerow(["" if c is None else str(c) for c in row])
                    if add(buffer.getvalue()):
                        break
                if count >= MAX_TEXT:
                    break
        finally:
            book.close()
    else:
        from pptx import Presentation
        presentation = Presentation(io.BytesIO(raw))
        def shapes(items):
            for shape in items:
                yield shape
                if hasattr(shape, "shapes"):
                    yield from shapes(shape.shapes)
        for i, slide in enumerate(presentation.slides):
            if add(f"\n[슬라이드 {i + 1}]\n"):
                break
            for shape in shapes(slide.shapes):
                value = getattr(shape, "text", "")
                if getattr(shape, "has_table", False):
                    value += "\n" + "\n".join("\t".join(c.text for c in row.cells) for row in shape.table.rows)
                if add(value + "\n"):
                    break
            if count >= MAX_TEXT:
                break
    text = "".join(parts)
    return dict(kind=kind, mime=mime, text=text, textChars=len(text), truncated=truncated, preview=text[:500])


async def extract_isolated(name, raw, timeout=20):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.extract_worker", name,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(raw), timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise AttachmentError("extraction_timeout", "파일 추출 제한 시간 20초를 초과했습니다.", 408) from None
    try:
        result = json.loads(output)
        if "error" in result:
            raise AttachmentError(**result["error"])
        return result
    except (ValueError, TypeError):
        raise AttachmentError("extraction_failed", "파일에서 텍스트를 추출할 수 없습니다.", 422) from None


def metadata(row):
    return dict(id=str(row["id"]), name=row["name"], mime=row["mime"], size=row["size"], kind=row["kind"],
                textChars=row["text_chars"], truncated=row["truncated"])


async def message_files(conn, cid, client_id, include_text=False):
    columns = META_COLUMNS + (",a.text" if include_text else "")
    return await conn.fetch(f"SELECT {columns} FROM attachments a JOIN messages m ON m.id=a.message_id JOIN conversations c ON c.id=m.conversation_id WHERE c.id=$1 AND c.client_id=$2 AND a.client_id=$2 ORDER BY m.seq,a.position,a.created_at,a.id", cid, client_id)


async def enrich(conn, rows, cid, client_id, include_text=False):
    grouped = {}
    for item in await message_files(conn, cid, client_id, include_text):
        grouped.setdefault(item["message_id"], []).append(item)
    return [dict(r) | {"attachments": [metadata(a) for a in grouped.get(r["id"], [])]} for r in rows], grouped


async def bind(conn, client_id, message_id, ids, error):
    if ids is None:
        return
    rows = await conn.fetch("SELECT id,message_id FROM attachments WHERE client_id=$1 AND id=ANY($2::uuid[]) FOR UPDATE", client_id, ids)
    if len(rows) != len(ids):
        raise error(404, "not_found", "첨부 파일을 찾을 수 없습니다.")
    if any(r["message_id"] is not None and r["message_id"] != message_id for r in rows):
        raise error(400, "attachment_in_use", "다른 메시지에 연결된 첨부 파일입니다.")
    await conn.execute("UPDATE attachments SET message_id=NULL,created_at=now() WHERE client_id=$1 AND message_id=$2 AND NOT(id=ANY($3::uuid[]))", client_id, message_id, ids)
    for position, aid in enumerate(ids):
        await conn.execute("UPDATE attachments SET message_id=$1,position=$2 WHERE id=$3 AND client_id=$4", message_id, position, aid, client_id)


def build_history(rows, files, current_id, budget=200000):
    """Allocate the whole budget to the current message first, then recent history."""
    blocks = {}
    ordered = [r for r in rows if r["id"] == current_id] + [r for r in reversed(rows) if r["id"] != current_id]
    for row in ordered:
        chunks = []
        for item in files.get(row["id"], []):
            header = f'<첨부 파일: {item["name"]} ({item["kind"]}, {item["text_chars"]}자)>\n```\n'
            footer, marker = "\n```\n", "(이하 생략)"
            text = item["text"]
            cap = len(text) if row["id"] == current_id else min(20000, len(text))
            room = max(0, budget - len(header) - len(footer))
            omitted = len(text) > min(cap, room)
            if omitted:
                room = max(0, room - len(marker))
            piece = header + text[:min(cap, room)] + (marker if omitted else "") + footer
            if len(piece) > budget:
                piece = marker[:budget]
            budget -= len(piece)
            chunks.append(piece)
        blocks[row["id"]] = "".join(chunks)
    return [{"role": r["role"], "content": blocks.get(r["id"], "") + r["content"]}
            for r in rows if r["error"] is None]
