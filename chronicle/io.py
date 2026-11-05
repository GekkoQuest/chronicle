"""Read a local kit without importing its code, extracting archives, or following links."""
from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath
import zipfile

from .models import Page, Snapshot

MAX_ARCHIVE_BYTES = 150 * 1024 * 1024
MAX_PAGE_BYTES = 4 * 1024 * 1024


def _safe_relative(name: str) -> PurePosixPath:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or ":" in name:
        raise ValueError(f"Unsafe relative path: {name!r}")
    return p


def _validate_archive(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    if len(entries) > 12000 or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the supported size limits.")
    names = set()
    for entry in entries:
        name = str(_safe_relative(entry.filename))
        if name in names or ((entry.external_attr >> 16) & 0o170000) == 0o120000 or entry.flag_bits & 1:
            raise ValueError("Duplicate paths, symlinks and encrypted archive entries are unsupported.")
        names.add(name)


def _snapshot(manifest: dict, read) -> Snapshot:
    pages = []
    for item in manifest["pages"]:
        name = str(_safe_relative(item["file"]))
        content = read(name)
        if len(content) > MAX_PAGE_BYTES or b"\x00" in content:
            raise ValueError("Page is too large or is not plain text.")
        pages.append(Page(item["url"], item.get("title", ""), content.decode("utf-8"),
                          item.get("fetched_at", manifest["taken_on"])))
    return Snapshot(manifest["snapshot_id"], manifest["taken_on"], pages)


def _from_zip(archive: zipfile.ZipFile) -> list[Snapshot]:
    _validate_archive(archive)
    snapshots = []
    for name in sorted(archive.namelist()):
        if name.endswith("manifest.json") and "/selftest/" not in name:
            base = PurePosixPath(name).parent
            manifest = json.loads(archive.read(name))
            if "snapshot_id" in manifest and "pages" in manifest:
                snapshots.append(_snapshot(manifest, lambda path, base=base: archive.read(str(base / path))))
        elif name.endswith(".zip") and "/snapshots_zip/" in "/" + name:
            with zipfile.ZipFile(io.BytesIO(archive.read(name))) as nested:
                snapshots.extend(_from_zip(nested))
    return snapshots


def load_snapshots(path: str | Path) -> list[Snapshot]:
    path = Path(path).resolve()
    if path.is_file():
        with zipfile.ZipFile(path) as archive:
            snapshots = _from_zip(archive)
    else:
        if (path / "snapshots").is_dir():
            path = path / "snapshots"
        manifests = sorted(path.glob("*/manifest.json"))
        if (path / "manifest.json").exists():
            manifests = [path / "manifest.json"]
        snapshots = []
        for manifest_path in manifests:
            base = manifest_path.parent.resolve()

            def read(name: str) -> bytes:
                target = (base / name).resolve()
                if not target.is_relative_to(base):
                    raise ValueError("Manifest points outside its snapshot.")
                return target.read_bytes()

            snapshots.append(_snapshot(json.loads(manifest_path.read_text(encoding="utf-8")), read))
        if not snapshots:
            for archive_path in sorted(path.glob("*.zip")):
                with zipfile.ZipFile(archive_path) as archive:
                    snapshots.extend(_from_zip(archive))
    if not snapshots:
        raise ValueError("No snapshots found. Supply a participant ZIP or a snapshot directory.")
    return sorted(snapshots, key=lambda s: (s.taken_on, s.snapshot_id))
