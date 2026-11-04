import subprocess
from pathlib import Path

from app.services import convert
from app.services.convert import CalibreConverter


async def test_failed_conversion_removes_partial_output(tmp_path: Path, monkeypatch):
    written: list[Path] = []

    def fake_run(input_path: Path, output_path: Path) -> subprocess.CompletedProcess:
        output_path.write_bytes(b"partial")
        written.append(output_path)
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"broken input")

    monkeypatch.setattr(convert, "_run_calibre", fake_run)

    path, error = await CalibreConverter.convert_to_epub(tmp_path / "book.mobi")

    assert path is None
    assert error == "broken input"
    assert not written[0].exists()


async def test_missing_calibre_is_reported(tmp_path: Path, monkeypatch):
    def fake_run(input_path: Path, output_path: Path) -> subprocess.CompletedProcess:
        raise FileNotFoundError

    monkeypatch.setattr(convert, "_run_calibre", fake_run)

    path, error = await CalibreConverter.convert_to_epub(tmp_path / "book.mobi")

    assert path is None
    assert error == convert.CALIBRE_MISSING
