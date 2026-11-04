import subprocess
from pathlib import Path

from app.services import convert
from app.services.convert import CalibreConverter


async def test_failed_conversion_removes_partial_output(tmp_path: Path, monkeypatch):
    written: list[Path] = []

    def fake_run(input_path, output_path, options=()) -> subprocess.CompletedProcess:
        output_path.write_bytes(b"partial")
        written.append(output_path)
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"broken input")

    monkeypatch.setattr(convert, "_run_calibre", fake_run)

    path, error = await CalibreConverter.convert_to_epub(tmp_path / "book.mobi")

    assert path is None
    assert error == "broken input"
    assert not written[0].exists()


async def test_missing_calibre_is_reported(tmp_path: Path, monkeypatch):
    def fake_run(input_path, output_path, options=()) -> subprocess.CompletedProcess:
        raise FileNotFoundError

    monkeypatch.setattr(convert, "_run_calibre", fake_run)

    path, error = await CalibreConverter.convert_to_epub(tmp_path / "book.mobi")

    assert path is None
    assert error == convert.CALIBRE_MISSING


def test_presets_fit_the_input_format():
    assert list(convert.presets_for(".pdf")) == ["standard", "cleanup", "text", "pdftohtml"]
    assert list(convert.presets_for("mobi")) == ["standard", "cleanup"]
    assert list(convert.presets_for(".EPUB")) == ["cleanup"]


def test_pdf_specific_options_are_added_only_for_pdfs():
    assert convert.options_for("cleanup", ".pdf")[-2:] == ["--unwrap-factor", "0.45"]
    assert "--unwrap-factor" not in convert.options_for("cleanup", ".mobi")
    assert convert.options_for("standard", ".pdf") == []
