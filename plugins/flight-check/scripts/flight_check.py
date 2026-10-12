#!/usr/bin/env python3
"""Flight Check deterministic tooling entry point. Requires Python 3.9+; standard library only."""

import os
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("Flight Check requires Python 3.9 or newer.\n")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

from flight_check_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
