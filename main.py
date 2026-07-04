"""R.A.G. Entry Point."""

import sys

from src.engine import main

if __name__ == "__main__":
    exit_code: int = main()
    sys.exit(exit_code)
