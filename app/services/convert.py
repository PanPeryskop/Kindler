import asyncio
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

CONVERSION_TIMEOUT_SECONDS = 300
CALIBRE_FALLBACK = Path("C:/Program Files/Calibre2/ebook-convert.exe")
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


def _run_calibre(input_path: Path, output_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [_calibre_path(), str(input_path), str(output_path)],
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
    async def convert_to_epub(input_path: Path) -> tuple[Path | None, str | None]:
        output_path = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}.epub"
        try:
            process = await asyncio.to_thread(_run_calibre, input_path, output_path)
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
