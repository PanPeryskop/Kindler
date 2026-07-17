from pathlib import Path

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


def probe_metadata(path: Path, file_type: str) -> tuple[str, str]:
    if file_type == "epub":
        _, title, author = extract_epub_metadata(path)
        return title, author
    return path.stem, ""
