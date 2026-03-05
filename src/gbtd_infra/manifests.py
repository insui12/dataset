from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

import yaml

from sqlalchemy import select

from .config import AppConfig
from .models import (
    CollectionMode,
    DatasetRole,
    ManifestVersion,
    ProtocolType,
    RegistryEntry,
    RegistryEntryKind,
    RegistryStatus,
    TrackerFamily,
    TrackerInstance,
    TrackerTier,
    Visibility,
)


@dataclass(frozen=True)
class ManifestCandidate:
    family_slug: str
    family_name: str
    instance_name: str
    instance_base_url: str
    instance_api_base_url: str | None
    entry_name: str
    entry_kind: RegistryEntryKind
    entry_tracker_id: str | None
    tracker_api_key: str | None
    tracker_url: str | None
    api_url: str | None
    tier: TrackerTier
    collection_mode: CollectionMode
    dataset_role: DatasetRole
    protocol: ProtocolType
    visibility: Visibility
    status: RegistryStatus
    is_bounded: bool
    parent_key: str | None = None


class ManifestLoader:
    """Load YAML manifest that defines tracker families, instances, and entries."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"manifest file not found: {self.path}")

    def read(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as f:
            payload = yaml.safe_load(f)
        if not isinstance(payload, dict):
            raise ValueError("manifest must be a mapping")
        return payload

    def load(self) -> tuple[str, list[ManifestCandidate]]:
        payload = self.read()
        metadata = payload.get("manifest") or {}
        version = str(metadata.get("version", datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")))

        families: list[dict[str, Any]] = payload.get("families", [])
        if not isinstance(families, list):
            raise ValueError("manifest.families must be a list")

        candidates: list[ManifestCandidate] = []
        for fam in families:
            family_slug = str(fam["slug"])
            family_name = str(fam.get("name", family_slug))
            for inst in fam.get("instances", []):
                instance_name = str(inst["name"])
                base_url = str(inst["base_url"])
                api_base_url = inst.get("api_base_url")
                for entry in inst.get("entries", []):
                    candidates.append(
                        ManifestCandidate(
                            family_slug=family_slug,
                            family_name=family_name,
                            instance_name=instance_name,
                            instance_base_url=base_url,
                            instance_api_base_url=api_base_url,
                            entry_name=str(entry["name"]),
                            entry_kind=RegistryEntryKind(entry.get("kind", "project")),
                            entry_tracker_id=entry.get("tracker_id"),
                            tracker_api_key=entry.get("tracker_api_key"),
                            tracker_url=entry.get("tracker_url"),
                            api_url=entry.get("api_url"),
                            tier=TrackerTier(entry.get("tier", fam.get("tier", "core"))),
                            collection_mode=CollectionMode(entry.get("collection_mode", fam.get("collection_mode", "manifest_exhaustive"))),
                            dataset_role=DatasetRole(entry.get("dataset_role", fam.get("dataset_role", "software_product"))),
                            protocol=ProtocolType(entry.get("protocol", fam.get("protocol", "REST"))),
                            visibility=Visibility(entry.get("visibility", fam.get("visibility", "public"))),
                            status=RegistryStatus(entry.get("status", fam.get("status", "unknown"))),
                            is_bounded=bool(inst.get("is_bounded", True)),
                            parent_key=entry.get("parent_key"),
                        )
                    )

        return version, candidates

    @staticmethod
    def checksum(path: Path) -> str:
        with path.open("rb") as f:
            return sha256(f.read()).hexdigest()


def persist_manifest_version(session, manifest_path: Path, metadata: dict[str, Any] | None = None) -> ManifestVersion:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    m = payload.get("manifest", {}) if isinstance(payload, dict) else {}
    manifest_name = str(m.get("name", "default"))
    version = str(m.get("version", datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")))
    mhash = ManifestLoader.checksum(manifest_path)
    record = session.scalar(
        select(ManifestVersion).where(
            ManifestVersion.manifest_name == manifest_name,
            ManifestVersion.version == version,
        )
    )
    if record is None:
        record = ManifestVersion(
            manifest_name=manifest_name,
            version=version,
            manifest_file=str(manifest_path),
            manifest_hash=mhash,
            notes=metadata or {},
        )
        session.add(record)
        session.flush()
    return record


def flatten_candidates(candidates: Iterable[ManifestCandidate], parent_lookup: dict[str, int]) -> list[ManifestCandidate]:
    return list(candidates)


def sync_manifest_to_registry(session, manifest_path: str) -> ManifestVersion:
    path = Path(manifest_path)
    loader = ManifestLoader(path)
    version, candidates = loader.load()
    manifest_record = persist_manifest_version(session, path, metadata={"version": version, "candidate_count": len(candidates)})

    families_index: dict[str, TrackerFamily] = {}
    for cand in candidates:
        fam = families_index.get(cand.family_slug)
        if fam is None:
            fam = session.scalar(select(TrackerFamily).where(TrackerFamily.slug == cand.family_slug))
            if fam is None:
                fam = TrackerFamily(
                    slug=cand.family_slug,
                    name=cand.family_name,
                    default_protocol=cand.protocol,
                )
                session.add(fam)
                session.flush()
            families_index[cand.family_slug] = fam

        inst = session.scalar(
            select(TrackerInstance).where(
                TrackerInstance.family_id == fam.id,
                TrackerInstance.canonical_name == cand.instance_name,
            )
        )
        if inst is None:
            inst = TrackerInstance(
                family_id=fam.id,
                canonical_name=cand.instance_name,
                base_url=cand.instance_base_url,
                api_base_url=cand.instance_api_base_url,
                tier=cand.tier,
                collection_mode=cand.collection_mode,
                dataset_role=cand.dataset_role,
                protocol=cand.protocol,
                visibility=cand.visibility,
                status=cand.status,
            )
            session.add(inst)
            session.flush()

        existing = session.scalar(
            select(RegistryEntry).where(
                RegistryEntry.instance_id == inst.id,
                RegistryEntry.entry_kind == cand.entry_kind,
                RegistryEntry.tracker_native_id == cand.entry_tracker_id,
            )
        )
        if existing is None:
            existing = RegistryEntry(
                family_id=fam.id,
                instance_id=inst.id,
                entry_kind=cand.entry_kind,
                name=cand.entry_name,
                tracker_native_id=cand.entry_tracker_id,
                tracker_api_key=cand.tracker_api_key,
                tracker_url=cand.tracker_url,
                api_url=cand.api_url,
                tier=cand.tier,
                collection_mode=cand.collection_mode,
                dataset_role=cand.dataset_role,
                protocol=cand.protocol,
                visibility=cand.visibility,
                status=cand.status,
                is_bounded_instance=cand.is_bounded,
                manifest_version_id=manifest_record.id,
            )
            session.add(existing)
    return manifest_record


def manifest_diff(old: list[ManifestCandidate], new: list[ManifestCandidate]) -> dict[str, list[ManifestCandidate]]:
    key_old = {f"{c.family_slug}|{c.instance_name}|{c.entry_kind.value}|{c.entry_tracker_id}": c for c in old}
    key_new = {f"{c.family_slug}|{c.instance_name}|{c.entry_kind.value}|{c.entry_tracker_id}": c for c in new}

    added = [v for k, v in key_new.items() if k not in key_old]
    removed = [v for k, v in key_old.items() if k not in key_new]
    unchanged = [v for k, v in key_new.items() if k in key_old]

    return {
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
    }
