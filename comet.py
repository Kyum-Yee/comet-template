#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10"]
# ///
"""Entry point: `uv run comet.py <command> ...`, `python3 comet.py <command> ...`, or the `comet` command
that `comet.py setup` installs."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comet_tpl.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
