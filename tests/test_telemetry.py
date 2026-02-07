"""Tests for telemetry collectors."""
from mca.telemetry.collectors import collect_all, _cpu_info, _ram_info, _disk_info


class TestCollectors:
    def test_cpu_info(self):
        info = _cpu_info()
        assert "name" in info
        assert info["cores_logical"] > 0
        assert isinstance(info["load_1m"], float)

    def test_ram_info(self):
        info = _ram_info()
        assert info["total_gb"] > 0
        assert 0 <= info["percent"] <= 100

    def test_disk_info(self):
        disks = _disk_info()
        assert len(disks) > 0
        assert disks[0]["mount"] == "/"

    def test_collect_all(self):
        data = collect_all()
        assert "cpu" in data
        assert "ram" in data
        assert "disks" in data
        assert "gpus" in data  # may be empty list
        assert "platform" in data
        assert data["platform"]["system"] == "Linux"
