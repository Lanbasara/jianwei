"""Resolve local-only golden evidence without exposing its configured path."""
from __future__ import annotations

import os
from pathlib import Path

QA_HOME_ENV = "JIANWEI_QA_HOME"
GOLDEN_PREFIX = Path("artifacts/golden")


class EvidencePathError(ValueError):
    """The local evidence root or a requested path is unsafe."""


def evidence_root() -> Path:
    """Return the explicit, existing local golden root."""
    raw = os.environ.get(QA_HOME_ENV)
    if not raw:
        raise EvidencePathError(
            f"{QA_HOME_ENV} is required and must name an absolute local golden directory"
        )
    configured = Path(raw)
    if not configured.is_absolute():
        raise EvidencePathError(f"{QA_HOME_ENV} must be an absolute path")
    try:
        resolved = configured.resolve(strict=True)
    except OSError as error:
        raise EvidencePathError(f"{QA_HOME_ENV} must name an existing directory") from error
    if not resolved.is_dir():
        raise EvidencePathError(f"{QA_HOME_ENV} must name an existing directory")
    return resolved


def path_within(root: Path, relative: str | Path) -> Path:
    """Resolve a relative path and reject absolute or root-escaping results."""
    rel = Path(relative)
    if rel.is_absolute():
        raise EvidencePathError("evidence paths must be relative")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise EvidencePathError("evidence root must be an existing directory") from error
    if not resolved_root.is_dir():
        raise EvidencePathError("evidence root must be an existing directory")
    candidate = (resolved_root / rel).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise EvidencePathError("evidence path escapes the configured root") from error
    return candidate


def golden_path(root: Path, repo_relative: str | Path) -> Path:
    """Map an artifacts/golden repo reference into the local golden root."""
    rel = Path(repo_relative)
    try:
        suffix = rel.relative_to(GOLDEN_PREFIX)
    except ValueError as error:
        raise EvidencePathError("evidence reference must start with artifacts/golden") from error
    return path_within(root, suffix)
