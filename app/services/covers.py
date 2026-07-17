import base64
from pathlib import Path

import ebooklib
import fitz
from ebooklib import epub


def _data_uri(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode()
    return f"data:{mime};base64,{encoded}"


def epub_cover(path: Path) -> str | None:
    try:
        book = epub.read_epub(str(path))
        for item in book.get_items():
            name = (item.get_name() or "").lower()
            is_cover = item.get_type() == ebooklib.ITEM_COVER or "cover" in name
            media = item.media_type or ""
            if is_cover and media.startswith("image/"):
                return _data_uri(item.get_content(), media)
    except Exception:
        return None
    return None


def pdf_cover(path: Path) -> str | None:
    try:
        doc = fitz.open(str(path))
        pixmap = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
        data = pixmap.tobytes("png")
        doc.close()
        return _data_uri(data, "image/png")
    except Exception:
        return None


def extract_cover(path: Path, file_type: str) -> str | None:
    if file_type == "epub":
        return epub_cover(path)
    if file_type == "pdf":
        return pdf_cover(path)
    return None
