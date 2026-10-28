"""Compute lifecycle adapters, separate from metric tracking backends."""

from .base import ComputeBackend
from .local import LocalBackend

__all__ = ["ComputeBackend", "LocalBackend"]
