#!/usr/bin/env python3
"""Entry point: python3 mr.py <command>. Standard library only; works from any working directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.cli import main  # noqa: E402

if __name__ == '__main__':
    main()
