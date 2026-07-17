import asyncio
from pathlib import Path
import tempfile
import uuid

import subprocess
import shutil

class CalibreConverter:
    
    @staticmethod
    def _run_calibre(input_path: str, output_path: str) -> subprocess.CompletedProcess:
        calibre_path = shutil.which("ebook-convert")
        if not calibre_path:
            fallback = Path("C:/Program Files/Calibre2/ebook-convert.exe")
            if fallback.exists():
                calibre_path = str(fallback)
            else:
                raise FileNotFoundError()
            
        return subprocess.run(
            [calibre_path, input_path, output_path],
            capture_output=True
        )

    @staticmethod
    async def convert_to_epub(input_path: Path) -> tuple[Path | None, str | None]:
        output_filename = f"{uuid.uuid4().hex}.epub"
        output_path = Path(tempfile.gettempdir()) / output_filename
        
        try:
            process = await asyncio.to_thread(
                CalibreConverter._run_calibre,
                str(input_path),
                str(output_path)
            )
            
            if process.returncode == 0 and output_path.exists():
                return output_path, None
            else:
                error_msg = process.stderr.decode('utf-8', errors='replace').strip() or process.stdout.decode('utf-8', errors='replace').strip() or "Unknown Calibre conversion error"
                return None, error_msg
                
        except FileNotFoundError:
            return None, "Command 'ebook-convert' not found. Ensure Calibre is installed and added to the PATH environment variable."
        except Exception as e:
            return None, f"Exception: {type(e).__name__} - {e}"
