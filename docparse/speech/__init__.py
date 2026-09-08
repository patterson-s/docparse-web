"""docparse speech preprocessing: markdown -> spoken script for TTS."""

from .preprocess import (
    to_voice_script,
    strip_citations,
    clean_markdown,
    process_file,
    process_case,
    process_dir,
    strip_frontmatter,
)

__all__ = [
    "to_voice_script",
    "strip_citations",
    "clean_markdown",
    "process_file",
    "process_case",
    "process_dir",
    "strip_frontmatter",
]
