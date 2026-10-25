"""AmritaGO backend package."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Read the installed distribution once, at import: one source of truth, no
    # hand-maintained copy that drifts from pyproject.toml.
    __version__ = version("amrita-go")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0"
