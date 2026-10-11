"""Where things live. Running from source: next to the .py files. Built with PyInstaller: user data (models, work, fonts,
config.json, logs) sits next to the .exe, bundled read-only files (static/) come from the unpacked bundle."""
import sys
from pathlib import Path
FROZEN = bool(getattr(sys, "frozen", False))
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))                    # read-only bundled files
APP = Path(sys.executable).parent if FROZEN else Path(__file__).parent         # writable user data
