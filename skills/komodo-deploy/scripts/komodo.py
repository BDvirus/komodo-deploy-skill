#!/usr/bin/env python3
"""komodo-deploy helper CLI. Run `python3 komodo.py --help` (`python` on Windows)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from komodo_deploy.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
