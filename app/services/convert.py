import asyncio
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CONVERSION_TIMEOUT_SECONDS = 300
CALIBRE_FALLBACK = Path("C:/Program Files/Calibre2/ebook-convert.exe")


@dataclass(frozen=True)
class Preset:
    label: str
    options: tuple[str, ...] = ()
    pdf_options: tuple[str, ...] = ()
    pdf_only: bool = False
    for_epub: bool = False


PRESETS = {
    "standard": Preset("Convert to EPUB"),
    "cleanup": Preset(
        "Convert and fix paragraphs",
        options=("--enable-heuristics", "--smarten-punctuation"),
        pdf_options=("--unwrap-factor", "0.45"),
        for_epub=True,
    ),
    "text": Preset("Convert text only (no images)", options=("--no-images",), pdf_only=True),
    "pdftohtml": Preset(
        "Convert with the alternative PDF engine",
        options=("--pdf-engine", "pdftohtml"),
        pdf_only=True,
    ),
}

CALIBRE_MISSING = (
    "Command 'ebook-convert' not found. "
    "Ensure Calibre is installed and added to the PATH environment variable."
)


def _calibre_path() -> str:
    path = shutil.which("ebook-convert")
    if path:
        return path
    if CALIBRE_FALLBACK.exists():
        return str(CALIBRE_FALLBACK)
    raise FileNotFoundError(CALIBRE_MISSING)


def presets_for(extension: str) -> dict[str, str]:
    extension = extension.lower().lstrip(".")
    return {
        name: preset.label
        for name, preset in PRESETS.items()
        if (extension != "epub" or preset.for_epub) and (extension == "pdf" or not preset.pdf_only)
    }


def options_for(name: str, extension: str) -> list[str]:
    preset = PRESETS[name]
    extra = preset.pdf_options if extension.lower().lstrip(".") == "pdf" else ()
    return [*preset.options, *extra]


def _run_calibre(
    input_path: Path, output_path: Path, options: Sequence[str] = ()
) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [_calibre_path(), str(input_path), str(output_path), *options],
        capture_output=True,
        timeout=CONVERSION_TIMEOUT_SECONDS,
    )


def _process_error(process: subprocess.CompletedProcess) -> str:
    for stream in (process.stderr, process.stdout):
        text = stream.decode("utf-8", errors="replace").strip()
        if text:
            return text
    return "Unknown Calibre conversion error"


class CalibreConverter:
    @staticmethod
    async def convert_to_epub(
        input_path: Path, options: Sequence[str] = ()
    ) -> tuple[Path | None, str | None]:
        output_path = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}.epub"
        try:
            process = await asyncio.to_thread(_run_calibre, input_path, output_path, options)
        except FileNotFoundError:
            error = CALIBRE_MISSING
        except subprocess.TimeoutExpired:
            error = f"Calibre conversion timed out after {CONVERSION_TIMEOUT_SECONDS}s."
        except Exception as exc:
            error = f"Exception: {type(exc).__name__} - {exc}"
        else:
            if process.returncode == 0 and output_path.exists():
                return output_path, None
            error = _process_error(process)

        output_path.unlink(missing_ok=True)
        return None, error
