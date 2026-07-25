"""PyInstaller entry point for the desktop application."""

import multiprocessing
from pdf2mdx.gui import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
