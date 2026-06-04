from __future__ import annotations

import os
import re
from typing import Any

from ..settings import settings
from ..utils import host_path, read_text, run_command

_SYSTEM_TYPES = {"tmpfs", "efivarfs", "devtmpfs", "proc", "sysfs", "overlay", "vfat"}
_SYSTEM_PREFIXES = ("/boot", "/run", "/sys", "/proc", "/dev", "/tmp", "/var")
_SYSTEM_ZSPACE = {"/zspace/zsrp", "/zspace/applications/logs", "/zspace/extdev"}


def _resolve_backend() -> str:
    forced = os.getenv("NAS_AGENT_BACKEND", "").strip().lower()
    if forced in {"qnap", "zspace", "generic"}:
        return forced
    from . import _qnap
    if _qnap.is_qnap():
        return "qnap"
    code, stdout, _ = run_command(
        ["nsenter", "-t", "1", "-m", "df", "-B1", "-T"], timeout=4
    )
    if code != 0:
        code, stdout, _ = run_command(["df", "-B1", "-T"], timeout=4)
    if "zspace" in stdout or "zdocker" in stdout:
        return "zspace"
    return "generic"


def _df_entries() -> list[dict[str, Any]]:
    """Original ZSpace-style df parser. Used for both zspace + generic backends."""
    args = ["nsenter", "-t", "1", "-m", "df", "-B1", "-T"]
    code, stdout, _ = run_command(args, timeout=4)
    if code != 0:
        code, stdout, _ = run_command(["df", "-B1", "-T"], timeout=4)
    if code != 0:
        return []
    entries: list[dict[str, Any]] = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        filesystem, fstype, total, used, free, used_pct, mount = parts[:7]
        if fstype in _SYSTEM_TYPES:
            continue
        if mount.startswith(_SYSTEM_PREFIXES):
            continue
        if mount in _SYSTEM_ZSPACE:
            continue
        if not mount.startswith("/data"):
            continue
        if "/zdocker" in mount or "/overlay2" in mount:
            continue
        try:
            total_i = int(total)
            used_i = int(used)
            free_i = int(free)
        except ValueError:
            continue
        entries.append({
            "filesystem": filesystem, "fstype": fstype, "mount": mount,
            "total_bytes": total_i, "used_bytes": used_i, "free_bytes": free_i,
            "used_pct": round((used_i / total_i) * 100.0, 1) if total_i else None,
        })
    return entries


def _raid_status() -> tuple[str, str]:
    mdstat = read_text(host_path(settings.host_proc, "mdstat"))
    if not mdstat:
        return "unknown", "unknown"
    if "inactive" in mdstat or "recovering" in mdstat or "_" in mdstat:
        return "degraded", "warning"
    if "md" in mdstat:
        return "healthy", "ok"
    return "unknown", "unknown"


def _mdadm_summary() -> str:
    code, stdout, _ = run_command(["mdadm", "--detail", "/dev/md0", "/dev/md1"], timeout=4)
    return stdout if code == 0 else ""


def _collect_zspace() -> dict[str, Any]:
    entries = _df_entries()
    raid_status, raid_health = _raid_status()
    mdadm_text = _mdadm_summary()
    pools: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        mount = entry["mount"]
        volume_id = mount.strip("/").replace("/", "_") or f"volume{idx}"
        volumes.append({
            "id": volume_id, "name": mount, "pool_id": volume_id,
            "filesystem": entry["fstype"],
            "total_bytes": entry["total_bytes"], "used_bytes": entry["used_bytes"],
            "free_bytes": entry["free_bytes"], "used_pct": entry["used_pct"],
            "health": "ok",
        })
        if mount.startswith("/data_"):
            pools.append({
                "id": volume_id, "name": mount,
                "raid_type": "mdraid/btrfs" if "Raid Level" in mdadm_text else "btrfs",
                "raid_status": raid_status, "health": raid_health,
                "total_bytes": entry["total_bytes"], "used_bytes": entry["used_bytes"],
                "free_bytes": entry["free_bytes"], "used_pct": entry["used_pct"],
            })
    if not pools and volumes:
        first = volumes[0]
        pools.append({
            "id": first["id"], "name": first["name"],
            "raid_type": "unknown", "raid_status": raid_status, "health": raid_health,
            "total_bytes": first["total_bytes"], "used_bytes": first["used_bytes"],
            "free_bytes": first["free_bytes"], "used_pct": first["used_pct"],
        })
    return {"pools": pools[:8], "volumes": volumes[:16]}


def collect() -> dict[str, Any]:
    """Multi-backend dispatcher.

    Auto-detects QNAP via /etc/config/qpkg.conf and routes to a QNAP-aware
    collector that knows about /share/CACHEDEV*_DATA LVM volumes and zpool
    state. The ZSpace behaviour is preserved as the default.
    """
    backend = _resolve_backend()
    if backend == "qnap":
        from . import _qnap
        return _qnap.collect()
    return _collect_zspace()
