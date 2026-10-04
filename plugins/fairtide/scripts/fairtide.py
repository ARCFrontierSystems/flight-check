#!/usr/bin/env python3
"""Fairtide deterministic tooling entry point. Requires Python 3.9+; standard library only."""

import os
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("Fairtide requires Python 3.9 or newer.\n")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

from fairtide_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
