"""The only seam to the image logic. Imports assemble_sheet_music (never
reimplements it) and marshals arguments/exceptions for the HTTP layer."""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Mirror pytest's `pythonpath = ["scripts"]`: make the CLI module importable
# without adding scripts/__init__.py.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from assemble_sheet_music import (  # noqa: E402
    DiscoveryError,
    MemoryBudgetError,
    PageBalanceError,
    assemble,
    discover_pieces,
)

_NUM_RE = re.compile(r"^(.*)_(\d+)\.(?:png|jpe?g|pdf)$", re.IGNORECASE)


@dataclass
class ValidationResult:
    ok: bool
    prefix: str
    num_pieces: int
    files: list[str]
    error: str | None = None


@dataclass
class AssembleResult:
    ok: bool
    needs_split: bool = False
    too_large: bool = False
    counts: list[int] | None = None
    uniform_scale: float | None = None
    warnings: list[str] = field(default_factory=list)
    page_files: list[str] = field(default_factory=list)
    pdf_file: str | None = None
    error: str | None = None
    options: str | None = None


def derive_prefix(filenames: list[str]) -> str | None:
    prefixes = set()
    for name in filenames:
        m = _NUM_RE.match(unicodedata.normalize("NFC", Path(name).name))
        if m:
            prefixes.add(m.group(1))
    return next(iter(prefixes)) if len(prefixes) == 1 else None


def validate_upload(in_dir: Path, prefix: str) -> ValidationResult:
    try:
        paths = discover_pieces(in_dir, prefix)
    except DiscoveryError as exc:
        return ValidationResult(False, prefix, 0, [], error=str(exc))
    return ValidationResult(True, prefix, len(paths), [p.name for p in paths])


def run_assemble(
    in_dir: Path,
    prefix: str,
    out_dir: Path,
    margin: int,
    pages_spec: str | None,
    max_megapixels: float | None = None,
) -> AssembleResult:
    try:
        summary = assemble(
            in_dir,
            prefix,
            out_dir,
            pages_spec=pages_spec,
            margin=margin,
            max_megapixels=max_megapixels,
        )
    except PageBalanceError as exc:
        return AssembleResult(False, needs_split=True, error=str(exc), options=str(exc))
    except MemoryBudgetError as exc:
        return AssembleResult(False, too_large=True, error=str(exc))
    except (DiscoveryError, ValueError) as exc:
        return AssembleResult(False, error=str(exc))
    return AssembleResult(
        ok=True,
        counts=summary["counts"],
        uniform_scale=summary["uniform_scale"],
        warnings=summary["warnings"],
        page_files=[p.name for p in summary["page_pngs"]],
        pdf_file=summary["pdf"].name,
    )
