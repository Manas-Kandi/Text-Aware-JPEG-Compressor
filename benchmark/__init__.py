"""Reproducible JPEG-versus-text context benchmark."""

from .scenarios import DEFAULT_LENGTHS, DEFAULT_SEEDS, build_trajectory
from .scoring import score_answer

__all__ = ["DEFAULT_LENGTHS", "DEFAULT_SEEDS", "build_trajectory", "score_answer"]
