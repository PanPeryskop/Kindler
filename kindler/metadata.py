import base64
import binascii
import logging
import mimetypes
from pathlib import Path

import ebooklib
from ebooklib import epub
from pypdf import PdfReader, PdfWriter
from slugify import slugify

from kindler.covers import epub_cover

logger = logging.getLogger(__name__)

COVER_ID = "kindler-cover"


def extract_epub_metadata(epub_path: Path) -> tuple[str, str, str]:
    try:
        book = epub.read_epub(str(epub_path))
        titles = book.get_metadata("DC", "title")
        authors = book.get_metadata("DC", "creator")

        title = titles[0][0] if titles else "Unknown Title"
        author = authors[0][0] if authors else "Unknown Author"

        clean_title = slugify(title, separator="_")
        clean_author = slugify(author, separator="_")
        new_filename = f"{clean_author}-{clean_title}.epub"

        return new_filename, title, author
    except Exception:
        fallback_name = f"{slugify(epub_path.stem)}.epub"
        return fallback_name, epub_path.stem, "Unknown Author"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def read_raw_metadata(path: Path, file_type: str) -> tuple[str, str]:
    try:
        if file_type == "epub":
            book = epub.read_epub(str(path))
            titles = book.get_metadata("DC", "title")
            authors = book.get_metadata("DC", "creator")
            return (titles[0][0] if titles else ""), (authors[0][0] if authors else "")
        if file_type == "pdf":
            info = PdfReader(path).metadata
            if info is None:
                return "", ""
            return _text(info.title), _text(info.author)
    except Exception:
        logger.warning("Could not read metadata from %s", path.name, exc_info=True)
    return "", ""


def probe_metadata(path: Path, file_type: str, fallback_title: str = "") -> tuple[str, str]:
    title, author = read_raw_metadata(path, file_type)
    return title or fallback_title or path.stem, author


def _write_epub_metadata(path: Path, title: str, author: str) -> None:
    book = epub.read_epub(str(path))
    dublin_core = book.metadata.setdefault(epub.NAMESPACES["DC"], {})
    if title:
        dublin_core.pop("title", None)
        book.set_title(title)
    if author:
        dublin_core.pop("creator", None)
        book.add_author(author)
    epub.write_epub(str(path), book)


def _write_pdf_metadata(path: Path, title: str, author: str) -> None:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs keep their own metadata.")
    writer = PdfWriter(clone_from=reader)
    changes = {}
    if title:
        changes["/Title"] = title
    if author:
        changes["/Author"] = author
    writer.add_metadata(changes)
    staging = path.with_name(f"{path.name}.partial")
    with staging.open("wb") as output:
        writer.write(output)
    staging.replace(path)


def apply_metadata(path: Path, file_type: str, title: str, author: str) -> None:
    if file_type not in ("epub", "pdf"):
        return

    current_title, current_author = read_raw_metadata(path, file_type)
    new_title = title if title and title != current_title else ""
    new_author = author if author and author != current_author else ""
    if not new_title and not new_author:
        return

    try:
        if file_type == "epub":
            _write_epub_metadata(path, new_title, new_author)
        else:
            _write_pdf_metadata(path, new_title, new_author)
    except Exception:
        logger.warning("Could not write metadata to %s", path.name, exc_info=True)


def _decode_data_uri(uri: str) -> tuple[str, bytes] | None:
    header, _, payload = uri.partition(",")
    if not header.startswith("data:image/") or not header.endswith(";base64"):
        return None
    try:
        return header[5:].removesuffix(";base64"), base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def _epub_cover_item(book: epub.EpubBook) -> epub.EpubItem | None:
    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        return item
    for _, attrs in book.get_metadata("OPF", "meta"):
        if attrs.get("name") == "cover":
            return book.get_item_with_id(attrs.get("content", ""))
    return None


def _write_epub_cover(path: Path, media_type: str, image: bytes) -> None:
    book = epub.read_epub(str(path))
    current = _epub_cover_item(book)
    if current is not None:
        current.content = image
        current.media_type = media_type
    else:
        extension = mimetypes.guess_extension(media_type) or ".jpg"
        cover = epub.EpubCover(uid=COVER_ID, file_name=f"{COVER_ID}{extension}")
        cover.content = image
        cover.media_type = media_type
        book.add_item(cover)
        opf = book.metadata.setdefault(epub.NAMESPACES["OPF"], {})
        opf["meta"] = [meta for meta in opf.get("meta", []) if meta[1].get("name") != "cover"]
        book.add_metadata(None, "meta", "", {"name": "cover", "content": COVER_ID})
    epub.write_epub(str(path), book)


def apply_cover(path: Path, file_type: str, cover: str | None) -> None:
    if file_type != "epub" or not cover or cover == epub_cover(path):
        return
    decoded = _decode_data_uri(cover)
    if decoded is None:
        return
    try:
        _write_epub_cover(path, *decoded)
    except Exception:
        logger.warning("Could not write cover to %s", path.name, exc_info=True)
