"""AmritaGO backend package."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Read the installed version to avoid a duplicate that can drift.
    __version__ = version("amrita-go")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0"
