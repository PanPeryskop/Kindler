from pathlib import Path

import fitz
from ebooklib import epub
from slugify import slugify


def extract_epub_metadata(epub_path: Path) -> tuple[str, str, str]:
    """Return (kindle_filename, title, author) for an EPUB.

    Falls back to the file stem if metadata can't be read.
    """
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


def read_raw_metadata(path: Path, file_type: str) -> tuple[str, str]:
    try:
        if file_type == "epub":
            book = epub.read_epub(str(path))
            titles = book.get_metadata("DC", "title")
            authors = book.get_metadata("DC", "creator")
            return (titles[0][0] if titles else ""), (authors[0][0] if authors else "")
        if file_type == "pdf":
            doc = fitz.open(str(path))
            info = doc.metadata or {}
            doc.close()
            return (info.get("title") or ""), (info.get("author") or "")
    except Exception:
        pass
    return "", ""


def probe_metadata(path: Path, file_type: str) -> tuple[str, str]:
    title, author = read_raw_metadata(path, file_type)
    return title or path.stem, author


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
    doc = fitz.open(str(path))
    info = doc.metadata or {}
    if title:
        info["title"] = title
    if author:
        info["author"] = author
    doc.set_metadata(info)
    doc.save(str(path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()


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
        pass
