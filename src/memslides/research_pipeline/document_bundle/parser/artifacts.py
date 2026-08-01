"""Safe extraction and byte-preserving MinerU artifact mapping."""

from __future__ import annotations

import fnmatch
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

from memslides.research_pipeline.document_bundle.errors import RawArtifactError, UnsafeArchiveError


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    """Extract a ZIP while rejecting absolute paths and path traversal."""

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RawArtifactError("Downloaded result is not a valid ZIP archive") from exc

    with archive:
        for member in archive.infolist():
            member_path = Path(member.filename.replace("\\", "/"))
            if member_path.is_absolute() or ".." in member_path.parts:
                raise UnsafeArchiveError(
                    f"Unsafe ZIP member path: {member.filename!r}"
                )
            target = (root / member_path).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise UnsafeArchiveError(
                    f"ZIP member escapes extraction directory: {member.filename!r}"
                ) from exc

        for member in archive.infolist():
            member_path = Path(member.filename.replace("\\", "/"))
            target = root / member_path
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _all_files(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=str)


def _select_unique(
    files: list[Path],
    root: Path,
    description: str,
    predicate: Callable[[Path], bool],
) -> Path:
    matches = [path for path in files if predicate(path)]
    if len(matches) != 1:
        candidates = [path.relative_to(root).as_posix() for path in matches]
        if not matches:
            raise RawArtifactError(f"Missing required MinerU artifact: {description}")
        raise RawArtifactError(
            f"Multiple candidates for {description}: {candidates}"
        )
    if matches[0].stat().st_size == 0:
        raise RawArtifactError(f"Required MinerU artifact is empty: {description}")
    return matches[0]


def _basename_matches(path: Path, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path.name, pattern)


def discover_raw_artifacts(extracted_root: Path) -> dict[str, Path]:
    """Find exactly one source file for every frozen raw target."""

    files = _all_files(extracted_root)
    document_md = _select_unique(
        files, extracted_root, "full.md", lambda path: path.name == "full.md"
    )
    model = _select_unique(
        files,
        extracted_root,
        "*_model.json",
        lambda path: _basename_matches(path, "*_model.json"),
    )
    content_list = _select_unique(
        files,
        extracted_root,
        "*_content_list.json",
        lambda path: _basename_matches(path, "*_content_list.json"),
    )

    layout_candidates = [path for path in files if path.name == "layout.json"]
    if layout_candidates:
        layout = _select_unique(
            files,
            extracted_root,
            "layout.json",
            lambda path: path.name == "layout.json",
        )
    else:
        layout = _select_unique(
            files,
            extracted_root,
            "*_middle.json",
            lambda path: _basename_matches(path, "*_middle.json"),
        )

    return {
        "document.md": document_md,
        "model.json": model,
        "content_list.json": content_list,
        "layout.json": layout,
    }


def map_raw_artifacts(extracted_root: Path, raw_directory: Path) -> dict[str, Path]:
    """Copy required artifacts without changing a byte of their contents."""

    sources = discover_raw_artifacts(extracted_root)
    raw_directory.mkdir(parents=True, exist_ok=True)
    targets: dict[str, Path] = {}
    for target_name, source in sources.items():
        target = raw_directory / target_name
        shutil.copyfile(source, target)
        targets[target_name] = target
    return targets
