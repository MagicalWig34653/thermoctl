"""Cache identity for the complete, locally shipped interface."""

from hashlib import sha256
from pathlib import Path

from thermoctl import __version__

STATIC_DIR = Path(__file__).parent / "static"


def asset_version(directory: Path, version: str) -> str:
    """Invalidate the whole asset set even for updates without a version bump."""
    digest = sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in {".css", ".js", ".svg"}:
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return f"{version}-{digest.hexdigest()[:20]}"


ASSET_VERSION = asset_version(STATIC_DIR, __version__)
