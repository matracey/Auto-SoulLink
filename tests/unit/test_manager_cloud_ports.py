import pytest

from server import manager


@pytest.fixture
def cloud_ports(monkeypatch):
    monkeypatch.setenv("SLINK_TCP_PORT_POOL", "54321,54322,54323,54324,54325")
    monkeypatch.setattr(manager, "_port_free", lambda _port: True)


def test_cloud_pool_allocates_only_configured_ports(cloud_ports):
    assert manager._next_ports([]) == (54321, 8081)


def test_stopped_runs_do_not_hold_cloud_ports(cloud_ports):
    runs = [
        {"status": "stopped", "tcp_port": 54321, "http_port": 8081},
        {"status": "running", "tcp_port": 54322, "http_port": 8082},
    ]

    assert manager._next_ports(runs) == (54321, 8081)


def test_sixth_simultaneous_run_is_refused(cloud_ports):
    runs = [
        {"status": "running", "tcp_port": 54321 + index, "http_port": 8081 + index}
        for index in range(5)
    ]

    with pytest.raises(RuntimeError, match="All 5 configured game ports"):
        manager._next_ports(runs)


def test_cloud_stop_releases_both_ports(cloud_ports):
    assert manager._released_ports() == {"tcp_port": None, "http_port": None}


def test_local_mode_keeps_existing_unbounded_allocation(monkeypatch):
    monkeypatch.delenv("SLINK_TCP_PORT_POOL", raising=False)
    monkeypatch.setattr(manager, "_port_free", lambda _port: True)
    runs = [{"status": "stopped", "tcp_port": 54321, "http_port": 8081}]

    assert manager._next_ports(runs) == (54322, 8082)
    assert manager._released_ports() == {}


@pytest.mark.parametrize("value", ["54321,54321", "0", "abc", ","])
def test_invalid_pool_is_refused(monkeypatch, value):
    monkeypatch.setenv("SLINK_TCP_PORT_POOL", value)

    with pytest.raises(RuntimeError, match="SLINK_TCP_PORT_POOL"):
        manager._tcp_port_pool()
