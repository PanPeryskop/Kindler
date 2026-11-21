import os
import runpy
import sys
from pathlib import Path

from sync_core import ROOT, sync

SOURCE = ROOT / "src"


def main() -> None:
    sync()
    os.environ.setdefault("FLET_FORCE_WEB_SERVER", "1")
    os.environ.setdefault("FLET_SERVER_PORT", "8550")
    os.environ.setdefault("KINDLER_DATA_DIR", str(Path.home() / ".kindler-portable-dev"))
    sys.path.insert(0, str(SOURCE))
    runpy.run_path(str(SOURCE / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
