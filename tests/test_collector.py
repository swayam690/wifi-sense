"""Tests for WiFi Sense collector module."""
import pytest
from unittest.mock import patch, mock_open
from app.collector import (
    _validate_interface_name,
    _freq_to_channel,
    _find_wireless_interfaces,
    _get_iw_link,
    get_wifi_status,
    get_hardware_diagnostics,
)
from app.models import ConnectionStatus


class TestValidateInterfaceName:
    def test_valid_names(self):
        assert _validate_interface_name("wlan0") is True
        assert _validate_interface_name("wlp2s0") is True
        assert _validate_interface_name("eth0") is True
        assert _validate_interface_name("wlan-1") is True
        assert _validate_interface_name("wifi_adapter") is True

    def test_invalid_names(self):
        assert _validate_interface_name("") is False
        assert _validate_interface_name("wlan0; rm -rf /") is False
        assert _validate_interface_name("../etc/passwd") is False
        assert _validate_interface_name("wlan 0") is False
        assert _validate_interface_name("$HOME") is False
        assert _validate_interface_name("wlan\n0") is False


class TestFreqToChannel:
    def test_channel_1(self):
        assert _freq_to_channel(2412) == 1

    def test_channel_6(self):
        assert _freq_to_channel(2437) == 6

    def test_channel_9(self):
        assert _freq_to_channel(2452) == 9

    def test_channel_11(self):
        assert _freq_to_channel(2462) == 11

    def test_channel_14(self):
        assert _freq_to_channel(2484) == 14

    def test_5ghz_channel_36(self):
        assert _freq_to_channel(5180) == 36

    def test_5ghz_channel_149(self):
        assert _freq_to_channel(5745) == 149

    def test_unknown_freq(self):
        assert _freq_to_channel(0) is None
        assert _freq_to_channel(1000) is None


class TestFindWirelessInterfaces:
    @patch("app.collector.subprocess.run")
    def test_parse_iw_dev_output(self, mock_run):
        mock_run.return_value = type("Result", (), {
            "stdout": "phy#0\n\tInterface wlan0\n\t\tifindex 3\n\t\twdev 0x1\n\t\taddr aa:bb:cc:dd:ee:ff\n\t\ttype managed\n",
            "stderr": "",
            "returncode": 0
        })()
        interfaces = _find_wireless_interfaces()
        assert "wlan0" in interfaces

    @patch("os.path.isdir", return_value=False)
    @patch("app.collector.subprocess.run")
    def test_iw_not_found(self, mock_run, mock_isdir):
        mock_run.side_effect = FileNotFoundError("iw not found")
        interfaces = _find_wireless_interfaces()
        assert interfaces == []


class TestGetIwLink:
    @patch("app.collector.subprocess.run")
    def test_parse_connected(self, mock_run):
        mock_run.return_value = type("Result", (), {
            "stdout": """Connected to aa:bb:cc:dd:ee:ff (on wlan0)
\tSSID: Airtel_sand_4040
\tfreq: 2452
\tsignal: -52 dBm
\ttx bitrate: 72.2 MBit/s
\trx bitrate: 65.0 MBit/s""",
            "stderr": "",
            "returncode": 0
        })()
        result = _get_iw_link("wlan0")
        assert result["ssid"] == "Airtel_sand_4040"
        assert result["signal_dbm"] == -52.0
        assert result["freq_mhz"] == 2452
        assert result["bssid"] == "aa:bb:cc:dd:ee:ff"

    @patch("app.collector.subprocess.run")
    def test_parse_not_connected(self, mock_run):
        mock_run.return_value = type("Result", (), {
            "stdout": "Not connected.\n",
            "stderr": "",
            "returncode": 0
        })()
        result = _get_iw_link("wlan0")
        assert result.get("connected") is False


class TestGetWifiStatus:
    @patch("app.collector._find_wireless_interfaces")
    def test_no_interfaces(self, mock_find):
        mock_find.return_value = []
        status = get_wifi_status()
        assert status.connection_status == ConnectionStatus.DISCONNECTED
        assert status.error is not None

    @patch("app.collector._find_wireless_interfaces")
    @patch("app.collector._get_iw_link")
    @patch("app.collector._get_nmcli_status")
    def test_connected_status(self, mock_nmcli, mock_iw, mock_find):
        mock_find.return_value = ["wlan0"]
        mock_iw.return_value = {
            "ssid": "TestNet",
            "bssid": "aa:bb:cc:dd:ee:ff",
            "freq_mhz": 2452,
            "signal_dbm": -55.0,
            "tx_bitrate": 72.2,
            "rx_bitrate": 65.0,
            "connected": True,
        }
        mock_nmcli.return_value = {
            "ip_address": "192.168.1.5",
            "state": "connected",
        }
        status = get_wifi_status()
        assert status.interface == "wlan0"
        assert status.ssid == "TestNet"
        assert status.signal_dbm == -55.0
        assert status.connection_status == ConnectionStatus.CONNECTED


class TestGetHardwareDiagnostics:
    @patch("app.collector._find_wireless_interfaces")
    @patch("app.collector.subprocess.run")
    def test_diagnostics_returns(self, mock_run, mock_find):
        mock_find.return_value = ["wlan0"]
        # Mock all subprocess calls to return empty
        mock_run.return_value = type("Result", (), {
            "stdout": "",
            "stderr": "",
            "returncode": 0
        })()
        diag = get_hardware_diagnostics()
        assert diag.interface == "wlan0"
        assert diag.csi_available is False
