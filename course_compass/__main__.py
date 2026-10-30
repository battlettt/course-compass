"""Allow `python3 -m course_compass`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
