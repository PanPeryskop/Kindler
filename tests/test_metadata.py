import zipfile
from pathlib import Path

import fitz
from ebooklib import epub

from app.services.metadata import apply_metadata, probe_metadata, read_raw_metadata


def make_epub(path: Path, title: str, author: str) -> None:
    book = epub.EpubBook()
    book.set_identifier("id1")
    if title:
        book.set_title(title)
    if author:
        book.add_author(author)
    chapter = epub.EpubHtml(title="c", file_name="c.xhtml", content="<h1>hi</h1>")
    book.add_item(chapter)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def make_pdf(path: Path, title: str, author: str) -> None:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.set_metadata({"title": title, "author": author})
    doc.save(str(path))
    doc.close()


def opf_counts(path: Path) -> tuple[int, int]:
    with zipfile.ZipFile(path) as archive:
        opf = next(n for n in archive.namelist() if n.endswith(".opf"))
        xml = archive.read(opf).decode("utf-8", "replace")
    return xml.count("<dc:title"), xml.count("<dc:creator")


def test_probe_reads_epub_metadata(tmp_path: Path):
    f = tmp_path / "b.epub"
    make_epub(f, "Original Title", "Original Author")
    assert probe_metadata(f, "epub") == ("Original Title", "Original Author")


def test_probe_reads_pdf_metadata(tmp_path: Path):
    f = tmp_path / "b.pdf"
    make_pdf(f, "PDF Title", "PDF Author")
    assert probe_metadata(f, "pdf") == ("PDF Title", "PDF Author")


def test_probe_falls_back_to_stem(tmp_path: Path):
    f = tmp_path / "some_scan.pdf"
    make_pdf(f, "", "")
    assert probe_metadata(f, "pdf") == ("some_scan", "")


def test_edit_overwrites_epub_without_duplicating_entries(tmp_path: Path):
    f = tmp_path / "b.epub"
    make_epub(f, "Original Title", "Original Author")

    apply_metadata(f, "epub", "Nowy Tytul", "Nowy Autor")

    assert read_raw_metadata(f, "epub") == ("Nowy Tytul", "Nowy Autor")
    assert opf_counts(f) == (1, 1)


def test_unchanged_values_leave_file_untouched(tmp_path: Path):
    f = tmp_path / "b.epub"
    make_epub(f, "Keep Title", "Keep Author")
    before = f.read_bytes()

    apply_metadata(f, "epub", "Keep Title", "Keep Author")

    assert f.read_bytes() == before


def test_missing_epub_metadata_gets_filled(tmp_path: Path):
    f = tmp_path / "b.epub"
    make_epub(f, "", "")

    apply_metadata(f, "epub", "Z Formularza", "Autor")

    assert read_raw_metadata(f, "epub") == ("Z Formularza", "Autor")


def test_edit_overwrites_pdf_and_keeps_file_readable(tmp_path: Path):
    f = tmp_path / "b.pdf"
    make_pdf(f, "PDF Title", "PDF Author")

    apply_metadata(f, "pdf", "Edytowany", "Autor")

    assert read_raw_metadata(f, "pdf") == ("Edytowany", "Autor")
    assert fitz.open(str(f)).page_count == 1


def test_unsupported_type_is_ignored(tmp_path: Path):
    f = tmp_path / "b.txt"
    f.write_bytes(b"hello")
    before = f.read_bytes()

    apply_metadata(f, "txt", "T", "A")

    assert f.read_bytes() == before
