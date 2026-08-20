"""GitSquid — a repository-local Git client assistant."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("gitsquid")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
