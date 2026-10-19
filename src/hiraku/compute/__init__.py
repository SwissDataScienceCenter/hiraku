"""Compute lifecycle adapters, separate from metric tracking backends."""

from .base import ComputeBackend
from .local import LocalBackend
from .slurm import SlurmBackend

__all__ = ["ComputeBackend", "LocalBackend", "SlurmBackend"]
