import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE = ROOT.parent / "kindler"
TARGET = ROOT / "src" / "kindler"


def sync() -> None:
    shutil.rmtree(TARGET, ignore_errors=True)
    shutil.copytree(CORE, TARGET, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


if __name__ == "__main__":
    sync()
