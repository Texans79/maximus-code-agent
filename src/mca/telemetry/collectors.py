"""System telemetry collectors: CPU, RAM, disk, GPU, NVMe."""
from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any

import psutil

from mca.log import get_logger

log = get_logger("telemetry")


def _cpu_info() -> dict[str, Any]:
    """Collect CPU information."""
    try:
        load_1, load_5, load_15 = psutil.getloadavg()
        cpu_count = psutil.cpu_count(logical=True) or 1
        load_pct_1 = (load_1 / cpu_count) * 100
    except (AttributeError, OSError):
        load_pct_1 = psutil.cpu_percent(interval=0.5)
        load_5 = load_15 = 0.0

    # CPU name
    name = platform.processor() or "Unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "model name" in line:
                    name = line.split(":")[1].strip()
                    break
    except (FileNotFoundError, PermissionError):
        pass

    return {
        "name": name,
        "cores_physical": psutil.cpu_count(logical=False) or 0,
        "cores_logical": psutil.cpu_count(logical=True) or 0,
        "load_1m": round(load_pct_1, 1),
        "freq_mhz": round(psutil.cpu_freq().current, 0) if psutil.cpu_freq() else 0,
    }


def _ram_info() -> dict[str, Any]:
    """Collect RAM information."""
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024 ** 3), 1),
        "used_gb": round(mem.used / (1024 ** 3), 1),
        "available_gb": round(mem.available / (1024 ** 3), 1),
        "percent": round(mem.percent, 1),
    }


def _disk_info() -> list[dict[str, Any]]:
    """Collect disk usage for mounted partitions."""
    disks = []
    for part in psutil.disk_partitions(all=False):
        # Skip snap and squashfs mounts
        if part.fstype in ("squashfs",) or "/snap/" in part.mountpoint:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "mount": part.mountpoint,
                "device": part.device,
                "fstype": part.fstype,
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "percent": round(usage.percent, 1),
            })
        except (PermissionError, OSError):
            continue
    return disks


def _gpu_info() -> list[dict[str, Any]]:
    """Collect GPU info via nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return []

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 7:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "temp_c": int(float(parts[2])),
                    "util_percent": int(float(parts[3])),
                    "mem_used_mb": int(float(parts[4])),
                    "mem_total_mb": int(float(parts[5])),
                    "power_w": round(float(parts[6]), 1),
                })
        return gpus
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        log.debug("nvidia-smi error: %s", e)
        return []


def _nvme_info() -> list[dict[str, Any]]:
    """Collect NVMe temperatures if nvme-cli or sensors is available."""
    results = []

    # Try sensors first
    if shutil.which("sensors"):
        try:
            r = subprocess.run(
                ["sensors", "-j"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                import json
                data = json.loads(r.stdout)
                for chip, readings in data.items():
                    if "nvme" in chip.lower():
                        for key, val in readings.items():
                            if isinstance(val, dict):
                                for k2, v2 in val.items():
                                    if "input" in k2 and isinstance(v2, (int, float)):
                                        results.append({"device": chip, "temp_c": round(v2, 1)})
                                        break
        except Exception:
            pass

    # Try nvme-cli
    if not results and shutil.which("nvme"):
        try:
            r = subprocess.run(
                ["nvme", "smart-log", "/dev/nvme0"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if "temperature" in line.lower():
                        parts = line.split(":")
                        if len(parts) >= 2:
                            temp_str = parts[1].strip().split()[0]
                            try:
                                results.append({"device": "/dev/nvme0", "temp_c": int(temp_str)})
                            except ValueError:
                                pass
                        break
        except Exception:
            pass

    return results


def collect_all() -> dict[str, Any]:
    """Collect all telemetry data."""
    return {
        "cpu": _cpu_info(),
        "ram": _ram_info(),
        "disks": _disk_info(),
        "gpus": _gpu_info(),
        "nvme": _nvme_info(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }
