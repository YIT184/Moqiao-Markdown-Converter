"""Allow running the package as ``python -m pdf2mdx``."""

from .cli import main
import sys

sys.exit(main())
