"""QNAP NAS backend detection and helpers.

Detects QNAP QTS / QuTS hero / QuTScloud systems and exposes
paths / commands the storage collector needs to read pool
information correctly.
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..settings import settings
from ..utils import host_path, read_text, run_command

# QNAP shared-folder base directories we care about
_QNAP_SHARE_BASES = (
    "/share/CACHEDEV",       # classic CACHEDEV1_DATA … CACHEDEVn_DATA (data volume on each storage pool)
    "/share/MD0_DATA",       # legacy / older firmware
    "/share/PUBLIC",         # default public share (rare in modern firmware)
    "/share/External",       # eSATA / USB external enclosures
)

# Filesystem types QNAP commonly uses
_QNAP_FSTYPES = ("ext4", "ext3", "xfs", "zfs", "btrfs")

# LVM cachedev pattern: /dev/mapper/cachedev<N>
_CACHEDEV_RE = re.compile(r"^/dev/mapper/cachedev(\d+)$")

# QNAP `/proc/mdstat` mdadm array marker
_MDSTAT_ARRAY_RE = re.compile(r"^md\d+\s*:\s*active", re.MULTILINE)


def is_qnap() -> bool:
    """Detect whether we are running on a QNAP system.

    Detection order (first match wins):
    1. /etc/config/qpkg.conf  →  QPKG metadata, present on every QTS / QuTS box
    2. /etc/default_config/uLinux.conf  →  uLinux marker
    3. /proc/mtd with model=Linux…  (very weak, only as last resort)
    """
    candidates = (
        host_path(settings.host_etc, "config/qpkg.conf"),
        host_path(settings.host_etc, "default_config/uLinux.conf"),
        host_path(settings.host_etc, "qsync.conf"),
        host_path(settings.host_etc, "model.conf"),
    )
    for path in candidates:
        if path.exists() and read_text(path):
            return True
    return False


def list_share_mounts() -> list[str]:
    """Return QNAP share mount points (df output filtered).

    QNAP mounts storage-pool roots under /share/CACHEDEV<n>_DATA
    and (legacy) /share/MD0_DATA. We pick the canonical data
    mount per storage pool, not the bind-mounted sub-shares.
    """
    code, stdout, _ = run_command(
        ["nsenter", "-t", "1", "-m", "df", "-B1", "-T"], timeout=4
    )
    if code != 0:
        code, stdout, _ = run_command(["df", "-B1", "-T"], timeout=4)
    if code != 0:
        return []

    pools: dict[str, str] = {}  # pool_id → mount
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        filesystem, fstype, _total, _used, _free, _pct, mount = parts[:7]
        if fstype not in _QNAP_FSTYPES:
            continue
        if not mount.startswith(_QNAP_SHARE_BASES):
            continue
        # Identify pool id from CACHEDEV<N> or MD0
        m = _CACHEDEV_RE.match(filesystem)
        if m:
            pool_id = f"cachedev{m.group(1)}"
        elif mount.startswith("/share/MD0_DATA"):
            pool_id = "md0"
        else:
            pool_id = mount.split("/")[-1]
        if pool_id not in pools:
            pools[pool_id] = mount
    return sorted(pools.values())


def read_pool_layout() -> list[dict[str, Any]]:
    """Read storage pool layout via `lvdisplay` (LVM cachedev) or `zpool list` (QuTS hero ZFS).

    Returns a list of pool descriptors:
        [{"id": "cachedev1", "raid_type": "raid5", "raid_status": "healthy"}]
    """
    pools: dict[str, dict[str, Any]] = {}

    # --- LVM cachedev (QTS classic) ---
    code, stdout, _ = run_command(
        ["nsenter", "-t", "1", "-m", "lvs", "--noheadings", "-o", "lv_name,vg_name,attr,lv_size"],
        timeout=4,
    )
    if code != 0:
        code, stdout, _ = run_command(["lvs", "--noheadings", "-o", "lv_name,vg_name,attr,lv_size"], timeout=4)
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        lv_name, vg_name, attrs, _size = parts[:4]
        if not lv_name.startswith("cachedev"):
            continue
        # 'a' = active, '-' = normal (not degraded/resync)
        state = "healthy" if "a" in attrs[:5] and "-" in attrs[5] else "degraded"
        pools[lv_name] = {
            "id": lv_name,
            "raid_type": "lvm-ext4",
            "raid_status": state,
        }

    # --- ZFS pools (QuTS hero) ---
    code, stdout, _ = run_command(["nsenter", "-t", "1", "-m", "zpool", "list", "-Hp", "-o", "name,health,size,alloc,free"], timeout=4)
    if code != 0:
        code, stdout, _ = run_command(["zpool", "list", "-Hp", "-o", "name,health,size,alloc,free"], timeout=4)
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        name, health, _size, _alloc, _free = parts[:5]
        # Map zpool health string to our enum
        health_map = {"ONLINE": "healthy", "DEGRADED": "degraded", "FAULTED": "critical", "OFFLINE": "critical"}
        pools[name] = {
            "id": name,
            "raid_type": "zfs",
            "raid_status": health_map.get(health, "unknown"),
        }

    return sorted(pools.values(), key=lambda p: p["id"])


def is_storage_degraded() -> tuple[str, str]:
    """Return (raid_status, raid_health) aggregated across pools.

    Mirrors the contract used by the ZSpace backend in storage.py.
    """
    pools = read_pool_layout()
    if not pools:
        # Fall back to /proc/mdstat (rare on QNAP but possible)
        mdstat = read_text(host_path(settings.host_proc, "mdstat"))
        if not mdstat:
            return "unknown", "unknown"
        if "inactive" in mdstat or "recovering" in mdstat or "_" in mdstat:
            return "degraded", "warning"
        if _MDSTAT_ARRAY_RE.search(mdstat):
            return "healthy", "ok"
        return "unknown", "unknown"

    statuses = {p["raid_status"] for p in pools}
    if "critical" in statuses:
        return "critical", "critical"
    if "degraded" in statuses:
        return "degraded", "warning"
    if statuses == {"healthy"}:
        return "healthy", "ok"
    return "unknown", "unknown"
