"""Entry point for pyside6-deploy and direct source execution."""
import sys
from pathlib import Path

source = Path(__file__).resolve().parent / "src"
if source.is_dir():
    sys.path.insert(0, str(source))

from music_sorter.app import main

if __name__ == "__main__":
    raise SystemExit(main())
