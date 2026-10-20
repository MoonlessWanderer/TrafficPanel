from agent.vps_agent import cpu_percent, parse_meminfo, parse_net_dev, parse_os_release

NET_DEV = """
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 10 1 0 0 0 0 0 0 10 1 0 0 0 0 0 0
  eth0: 1000 10 1 2 0 0 0 0 2000 20 3 4 0 0 0 0
"""


def test_net_dev_skips_loopback_and_reads_errors():
    rows = parse_net_dev(NET_DEV)
    assert len(rows) == 1
    assert rows[0]["name"] == "eth0"
    assert rows[0]["rx_bytes"] == 1000
    assert rows[0]["tx_bytes"] == 2000
    assert rows[0]["rx_errors"] == 1
    assert rows[0]["tx_dropped"] == 4


def test_meminfo_uses_available_as_free():
    text = "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n"
    memory = parse_meminfo(text)
    assert memory["total"] == 1000 * 1024
    assert memory["free"] == 400 * 1024
    assert memory["used"] == 600 * 1024


def test_cpu_percent_from_idle_delta():
    assert cpu_percent((10, 110), (20, 210)) == 90
    assert cpu_percent(None, (1, 2)) is None


def test_os_release_pretty_name():
    assert parse_os_release('PRETTY_NAME="Debian GNU/Linux 12"\n') == "Debian GNU/Linux 12"
