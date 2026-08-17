"""On-disk storage for Capability artifacts (versioned JSON files, no DB).

Each capability_id gets its own directory under STORE_DIR; each version is a
separate JSON file, so nothing is ever overwritten and the full version
history stays on disk.
"""

from pathlib import Path

from artifacts.models import Capability

STORE_DIR = Path(__file__).parent / "store"


def _capability_dir(capability_id: str) -> Path:
    return STORE_DIR / capability_id


def _version_path(capability_id: str, version: int) -> Path:
    return _capability_dir(capability_id) / f"v{version}.json"


def save(capability: Capability) -> Path:
    """Persist a capability at its own version path.

    Refuses to overwrite an existing version — bump Capability.version for a
    new recording instead, keeping prior versions intact on disk.
    """
    path = _version_path(capability.capability_id, capability.version)
    if path.exists():
        raise FileExistsError(
            f"{path} already exists — bump Capability.version instead of overwriting "
            "a saved artifact"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(capability.model_dump_json(indent=2) + "\n")
    return path


def load(capability_id: str, version: int) -> Capability:
    path = _version_path(capability_id, version)
    return Capability.model_validate_json(path.read_text())


def latest_version(capability_id: str) -> int | None:
    directory = _capability_dir(capability_id)
    if not directory.exists():
        return None
    versions = [int(p.stem[1:]) for p in directory.glob("v*.json")]
    return max(versions) if versions else None


def load_latest(capability_id: str) -> Capability:
    version = latest_version(capability_id)
    if version is None:
        raise FileNotFoundError(f"No saved capability with id {capability_id!r}")
    return load(capability_id, version)


def list_capability_ids() -> list[str]:
    if not STORE_DIR.exists():
        return []
    return sorted(p.name for p in STORE_DIR.iterdir() if p.is_dir())
