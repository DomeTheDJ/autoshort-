"""Diagnose: Arbeitsspeicher (cgroup), Dateisystem und Speicherplatz – für die Fehlersuche."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

CG2 = Path("/sys/fs/cgroup")
CG1 = Path("/sys/fs/cgroup/memory")


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text().strip()
        return None if raw == "max" else int(raw)
    except (OSError, ValueError):
        return None


def _mb(v: int | None) -> int | None:
    return None if v is None else v // (1024 * 1024)


def memory() -> dict:
    """Speicherverbrauch des ganzen Containers (so, wie Render ihn zählt)."""
    if (CG2 / "memory.current").exists():
        stat = {}
        try:
            for line in (CG2 / "memory.stat").read_text().splitlines():
                k, v = line.split()
                stat[k] = int(v)
        except (OSError, ValueError):
            pass
        return {"used_mb": _mb(_read_int(CG2 / "memory.current")),
                "peak_mb": _mb(_read_int(CG2 / "memory.peak")),
                "limit_mb": _mb(_read_int(CG2 / "memory.max")),
                "programs_mb": _mb(stat.get("anon")),
                "files_in_ram_mb": _mb(stat.get("shmem")),
                "cache_mb": _mb(stat.get("file"))}
    if (CG1 / "memory.usage_in_bytes").exists():
        limit = _read_int(CG1 / "memory.limit_in_bytes")
        return {"used_mb": _mb(_read_int(CG1 / "memory.usage_in_bytes")),
                "peak_mb": _mb(_read_int(CG1 / "memory.max_usage_in_bytes")),
                "limit_mb": _mb(limit) if limit and limit < 1 << 50 else None}
    return {}


def filesystem(path: Path) -> dict:
    """Welches Dateisystem liegt unter `path`? tmpfs = Dateien liegen im Arbeitsspeicher."""
    path = path.resolve()
    best, fstype = "", "?"
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and str(path).startswith(parts[1]) and len(parts[1]) > len(best):
                best, fstype = parts[1], parts[2]
    except OSError:
        pass
    try:
        free = shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        free = None
    return {"path": str(path), "mount": best, "type": fstype, "free_mb": free,
            "in_ram": fstype in ("tmpfs", "ramfs")}


def snapshot(data_dir: Path) -> dict:
    return {"memory": memory(), "disk": filesystem(data_dir), "cpus": os.cpu_count()}


def summary(snap: dict) -> str:
    m, d = snap.get("memory", {}), snap.get("disk", {})
    parts = []
    if m.get("used_mb") is not None:
        parts.append(f"Speicher {m.get('used_mb')}/{m.get('limit_mb') or '?'} MB "
                     f"(Programme {m.get('programs_mb', '?')}, Cache {m.get('cache_mb', '?')}, "
                     f"Dateien im RAM {m.get('files_in_ram_mb') or 0}, Spitze {m.get('peak_mb') or '?'})")
    parts.append(f"Ablage: {d.get('type')}{' = im Arbeitsspeicher!' if d.get('in_ram') else ''}, "
                 f"frei {d.get('free_mb')} MB")
    return "; ".join(parts)
