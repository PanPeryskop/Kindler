import base64
from pathlib import Path

import ebooklib
import fitz
from ebooklib import epub

IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def image_media_type(data: bytes) -> str | None:
    return next((mime for magic, mime in IMAGE_SIGNATURES if data.startswith(magic)), None)


def data_uri(data: bytes, mime: str) -> str:
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
                return data_uri(item.get_content(), media)
    except Exception:
        return None
    return None


def pdf_cover(path: Path) -> str | None:
    try:
        doc = fitz.open(str(path))
        pixmap = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
        data = pixmap.tobytes("png")
        doc.close()
        return data_uri(data, "image/png")
    except Exception:
        return None


def extract_cover(path: Path, file_type: str) -> str | None:
    if file_type == "epub":
        return epub_cover(path)
    if file_type == "pdf":
        return pdf_cover(path)
    return None
