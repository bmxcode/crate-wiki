"""crate-wiki — an LLM wiki that compounds what you learn and what you've done."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("crate-wiki")
except PackageNotFoundError:  # running from a source checkout with no install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
