import io
import ipaddress
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from connection import Endpoint, connection_addresses, connection_page, main, qr_image, show_page, wifi_wake_status
from server import ROOT, load_config


def interface(name, cidr, **options):
    address = ipaddress.ip_interface(cidr)
    return dict(ifname=name, addr_info=[{"family": "inet", "scope": "global",
                "local": str(address.ip), "prefixlen": address.network.prefixlen}], **options)


class ConnectionTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config.example.json")
        self.config["port"] = 18442
        self.interfaces = [
            interface("wg0", "10.0.5.100/24"), interface("docker0", "172.17.0.1/16"),
            interface("wlan0", "192.168.3.12/24"), interface("eth0", "192.168.5.55/24"),
            interface("lo", "127.0.0.1/8"), interface("down", "192.168.0.4/24", operstate="DOWN"),
            interface("public", "8.8.8.8/24"), interface("link-local", "169.254.3.5/16")]

    def test_wildcard_and_private_interfaces(self):
        endpoints = connection_addresses(self.config, self.interfaces)
        self.assertEqual(len(endpoints), 4)
        self.assertEqual({item.interface for item in endpoints if not item.alternate},
                         {"Wi-Fi · wlan0", "有线网络 · eth0"})
        self.assertEqual({item.url for item in endpoints if item.alternate},
                         {"http://10.0.5.100:18442/", "http://172.17.0.1:18442/"})
        self.assertTrue(endpoints[-1].alternate)

    def test_restricted_bind_and_mobile_source(self):
        self.config["bind"] = "192.168.3.12"
        self.config["networks"] = [ipaddress.ip_network("192.168.3.22/32")]
        endpoints = connection_addresses(self.config, self.interfaces)
        self.assertEqual(endpoints, [Endpoint("Wi-Fi · wlan0", "http://192.168.3.12:18442/", False)])
        self.config["networks"] = [ipaddress.ip_network("10.15.0.0/16")]
        self.assertTrue(connection_addresses(self.config, self.interfaces)[0].alternate)
        self.config["networks"] = [ipaddress.ip_network("127.0.0.0/8")]
        self.assertEqual(connection_addresses(self.config, self.interfaces), [])
        self.config = load_config(ROOT / "config.example.json")
        self.config["bind"] = "127.0.0.1"
        self.assertEqual(connection_addresses(self.config, self.interfaces), [])

    @patch("connection.qr_image", return_value="")
    @patch("connection.wifi_wake_status", return_value="无线驱动报告支持唤醒包")
    def test_wake_pairing_uses_local_wifi_mac_and_actual_subnet(self, probe, qr):
        hw = {"link_type": "ether", "address": "02:11:22:33:44:55"}
        wifi = interface("wlan0", "192.168.6.90/23", **hw)
        virtual = interface("docker0", "172.17.0.1/16", **hw)
        endpoints = connection_addresses(self.config, [virtual, wifi])
        self.assertEqual((endpoints[0].mac, endpoints[0].broadcast), (hw["address"], "192.168.7.255"))
        self.assertFalse(endpoints[1].mac)
        probe.assert_called_once_with("wlan0")
        html = connection_page(endpoints, "")
        self.assertIn("无线驱动报告支持唤醒包", html)
        self.assertIn("192.168.7.255", html)
        self.assertIn(hw["address"], html)
        self.assertEqual([call[0][0] for call in qr.call_args_list], [item.url for item in endpoints])
        for mac in ("00:00:00:00:00:00", "01:23:45:67:89:ab", "invalid"):
            self.assertFalse(connection_addresses(self.config, [dict(wifi, address=mac)])[0].mac)
        self.assertFalse(connection_addresses(self.config, [interface("wlan0", "192.168.7.90/32", **hw)])[0].mac)

    @patch("connection.subprocess.run")
    @patch("connection.Path.read_text", return_value="phy2\n")
    def test_wireless_wake_check(self, read, run):
        run.return_value.stdout = b"WoWLAN support:\n * wake up on magic packet\n"
        self.assertIn("报告支持", wifi_wake_status("wlan0"))
        self.assertEqual(run.call_args[0][0], ["iw", "phy", "phy2", "info"])
        run.return_value.stdout = b"WoWLAN support:\n * wake up on disconnect\n"
        self.assertIn("未报告支持", wifi_wake_status("wlan0"))
        run.side_effect = FileNotFoundError()
        self.assertIn("未能读取", wifi_wake_status("wlan0"))

    @patch("connection.subprocess.run")
    def test_qr_uses_url_only_and_offline_fallback(self, run):
        url = "http://192.168.23.9:18765/"
        run.return_value.stdout = b"\x89PNG\r\n\x1a\nimage"
        self.assertIn("data:image/png;base64,", qr_image(url))
        self.assertEqual(run.call_args[0][0], ["qrencode", "-t", "PNG", "-s", "10", "-m", "4", "-o", "-", url])
        run.side_effect = FileNotFoundError()
        html = connection_page([Endpoint("<interface>", url, False)], "")
        self.assertIn(url, html)
        self.assertIn("&lt;interface&gt;", html)
        self.assertNotIn("data:image/png", html)

    @patch("connection.subprocess.run")
    def test_reopening_page_replaces_private_cache(self, run):
        with tempfile.TemporaryDirectory() as cache, patch.dict(os.environ, XDG_CACHE_HOME=cache):
            show_page("old address")
            show_page("new address")
            page = Path(cache) / "ipad-tv-remote/connection.html"
            self.assertEqual(list(page.parent.iterdir()), [page])
            self.assertEqual(stat.S_IMODE(page.stat().st_mode), 0o600)
            self.assertEqual(page.read_text(), "new address")
            self.assertEqual(run.call_args[0][0], ["gio", "open", page.resolve().as_uri()])

    @patch("connection.show_page")
    @patch("connection.discover_addresses", side_effect=FileNotFoundError())
    @patch("connection.os.geteuid", return_value=1000)
    def test_desktop_launcher_displays_discovery_failure(self, uid, discovery, show):
        with patch("connection.load_config", return_value=self.config), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(), 1)
        self.assertIn("ip 命令", show.call_args[0][0])
