"""WiFi data collector module for WiFi Sense."""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from app.models import (
    WiFiStatus, ConnectionStatus, HardwareDiagnostics, HardwareCapability
)

IFACE_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_interface_name(name: str) -> bool:
    """Validate interface name against safe regex pattern."""
    if not name or not isinstance(name, str):
        return False
    return bool(IFACE_REGEX.match(name))


def _freq_to_channel(freq_mhz: int) -> Optional[int]:
    """Convert WiFi frequency in MHz to channel number."""
    if freq_mhz == 2484:
        return 14
    if 2412 <= freq_mhz <= 2472:
        return (freq_mhz - 2407) // 5
    if 5170 <= freq_mhz <= 5825:
        return (freq_mhz - 5000) // 5
    if 5955 <= freq_mhz <= 7115:  # 6 GHz (WiFi 6E)
        return (freq_mhz - 5950) // 5
    return None


def _run_cmd(cmd: List[str], timeout: float = 3.0) -> Optional[str]:
    """Safely run a command without shell=True and return stdout."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False
        )
        if proc.returncode == 0:
            return proc.stdout
        return proc.stdout or None
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None


def _find_wireless_interfaces() -> List[str]:
    """Parse iw dev output and /sys/class/net to find wireless interfaces."""
    interfaces: List[str] = []
    
    # Try iw dev first
    output = _run_cmd(["iw", "dev"])
    if output:
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                parts = line.split()
                if len(parts) >= 2:
                    iface = parts[1].strip()
                    if _validate_interface_name(iface) and iface not in interfaces:
                        interfaces.append(iface)
    
    # Fallback / augment with /sys/class/net wireless check
    net_path = "/sys/class/net"
    if os.path.isdir(net_path):
        try:
            for entry in os.listdir(net_path):
                if _validate_interface_name(entry):
                    wireless_dir = os.path.join(net_path, entry, "wireless")
                    phy80211_dir = os.path.join(net_path, entry, "phy80211")
                    if os.path.exists(wireless_dir) or os.path.exists(phy80211_dir):
                        if entry not in interfaces:
                            interfaces.append(entry)
        except Exception:
            pass

    return interfaces


def _get_nmcli_status(iface: str) -> Dict[str, Any]:
    """Get connection details via nmcli for a specific interface."""
    data: Dict[str, Any] = {}
    if not _validate_interface_name(iface):
        return data

    # Query device status
    dev_out = _run_cmd(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,GENERAL.DEVICE,IP4.ADDRESS", "device", "show", iface])
    if dev_out:
        for line in dev_out.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if key == "GENERAL.STATE":
                data["state"] = val
                if "connected" in val.lower():
                    data["connected"] = True
                elif "disconnected" in val.lower():
                    data["connected"] = False
            elif key == "GENERAL.CONNECTION":
                if val and val != "--":
                    data["connection_name"] = val
            elif key.startswith("IP4.ADDRESS") and "ip_address" not in data:
                # Remove subnet mask e.g. 192.168.1.5/24 -> 192.168.1.5
                data["ip_address"] = val.split("/")[0]

    # Query wifi scan/status for active link
    wifi_out = _run_cmd(["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,SIGNAL,FREQ", "device", "wifi", "list"])
    if wifi_out:
        for line in wifi_out.splitlines():
            line = line.strip()
            if line.startswith("*"):
                # Active WiFi line
                # Format: IN-USE:SSID:BSSID:SIGNAL:FREQ
                parts = line.split(":")
                # Handle possible colons in BSSID
                # When escaping is used or standard colon separator
                if len(parts) >= 5:
                    data["in_use"] = True
                    # BSSID might have colons, nmcli -t escapes them with backslash
                    # Let's search with regex for signal and freq at the end
                    m = re.search(r":(\d+):(\d+)\s*MHz$", line)
                    if m:
                        data["signal_percent"] = int(m.group(1))
                        data["freq_mhz"] = int(m.group(2))
                    
                    # Extract SSID from second field
                    ssid_val = parts[1].replace(r"\:", ":")
                    if ssid_val:
                        data["ssid"] = ssid_val
                break

    return data


def _get_iw_link(iface: str) -> Dict[str, Any]:
    """Parse iw dev <iface> link output."""
    data: Dict[str, Any] = {}
    if not _validate_interface_name(iface):
        return data

    out = _run_cmd(["iw", "dev", iface, "link"])
    if not out:
        return data

    if "Not connected." in out:
        data["connected"] = False
        return data

    data["connected"] = True
    
    # Connected to BSSID
    bssid_match = re.search(r"Connected to\s+([0-9a-fA-F:]{17})", out)
    if bssid_match:
        data["bssid"] = bssid_match.group(1).lower()

    # SSID
    ssid_match = re.search(r"SSID:\s*(.+)$", out, re.MULTILINE)
    if ssid_match:
        data["ssid"] = ssid_match.group(1).strip()

    # Frequency
    freq_match = re.search(r"freq:\s*(\d+)", out)
    if freq_match:
        data["freq_mhz"] = int(freq_match.group(1))

    # Signal in dBm
    signal_match = re.search(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", out)
    if signal_match:
        data["signal_dbm"] = float(signal_match.group(1))

    # RX bitrate
    rx_match = re.search(r"rx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s", out)
    if rx_match:
        data["rx_bitrate"] = float(rx_match.group(1))

    # TX bitrate
    tx_match = re.search(r"tx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s", out)
    if tx_match:
        data["tx_bitrate"] = float(tx_match.group(1))

    return data


def _get_iwconfig_status(iface: str) -> Dict[str, Any]:
    """Fallback parser for iwconfig output."""
    data: Dict[str, Any] = {}
    if not _validate_interface_name(iface):
        return data

    out = _run_cmd(["iwconfig", iface])
    if not out:
        return data

    # ESSID
    essid_match = re.search(r'ESSID:"([^"]*)"', out)
    if essid_match and essid_match.group(1) != "off/any":
        data["ssid"] = essid_match.group(1)

    # Access Point
    ap_match = re.search(r"Access Point:\s*([0-9a-fA-F:]{17})", out)
    if ap_match and ap_match.group(1) != "Not-Associated":
        data["bssid"] = ap_match.group(1).lower()

    # Frequency
    freq_match = re.search(r"Frequency:(\d+(?:\.\d+)?)\s*GHz", out)
    if freq_match:
        data["freq_mhz"] = int(float(freq_match.group(1)) * 1000)

    # Bitrate
    bitrate_match = re.search(r"Bit Rate=(\d+(?:\.\d+)?)\s*Mb/s", out)
    if bitrate_match:
        data["bitrate"] = float(bitrate_match.group(1))

    # Link quality
    lq_match = re.search(r"Link Quality=(\d+)/(\d+)", out)
    if lq_match:
        val = int(lq_match.group(1))
        max_val = int(lq_match.group(2))
        data["link_quality"] = int((val / max_val) * 100) if max_val > 0 else val

    # Signal level
    sig_match = re.search(r"Signal level=(-?\d+(?:\.\d+)?)\s*dBm", out)
    if sig_match:
        data["signal_dbm"] = float(sig_match.group(1))
    else:
        # Some versions report Signal level=X/Y
        sig_frac = re.search(r"Signal level=(\d+)/(\d+)", out)
        if sig_frac:
            # Approximate dBm from quality ratio: 0-100 -> -100 to -50
            ratio = float(sig_frac.group(1)) / float(sig_frac.group(2))
            data["signal_dbm"] = -100.0 + (ratio * 50.0)

    # Noise level
    noise_match = re.search(r"Noise level=(-?\d+(?:\.\d+)?)\s*dBm", out)
    if noise_match:
        data["noise_dbm"] = float(noise_match.group(1))

    return data


def _get_mac_address(iface: str) -> Optional[str]:
    """Read MAC address from sysfs."""
    addr_file = f"/sys/class/net/{iface}/address"
    if os.path.exists(addr_file):
        try:
            with open(addr_file, "r") as f:
                mac = f.read().strip()
                if mac:
                    return mac.lower()
        except Exception:
            pass
    return None


def _get_driver_name(iface: str) -> Optional[str]:
    """Read driver name from sysfs."""
    driver_link = f"/sys/class/net/{iface}/device/driver"
    if os.path.exists(driver_link):
        try:
            target = os.readlink(driver_link)
            return os.path.basename(target)
        except Exception:
            pass
    return None


def _check_monitor_mode(phy: str) -> bool:
    """Check if monitor mode is supported by the wireless phy."""
    if not phy:
        return False
    # Validate phy name (e.g. phy0)
    if not re.match(r"^[a-zA-Z0-9]+$", phy):
        return False
    out = _run_cmd(["iw", "phy", phy, "info"])
    if not out:
        return False
    # Look in the "Supported interface modes:" section
    in_modes = False
    for line in out.splitlines():
        if "Supported interface modes:" in line:
            in_modes = True
            continue
        if in_modes:
            if not line.startswith("\t\t") and not line.startswith("   "):
                if line.strip() and not line.startswith("*"):
                    break
            if "monitor" in line.lower():
                return True
    return False


def _check_csi_tools() -> bool:
    """Check if any CSI extraction tool or driver is installed."""
    csi_binaries = ["log_to_file", "csi_logger", "nexmon", "atheroscsimon", "recvCSI"]
    for b in csi_binaries:
        if shutil.which(b) is not None:
            return True

    # Check for custom kernel modules or CSI debugfs
    csi_paths = [
        "/proc/net/csi",
        "/sys/kernel/debug/ieee80211/csi",
        "/dev/CSI_DATA"
    ]
    for p in csi_paths:
        if os.path.exists(p):
            return True
            
    return False


def get_wifi_status() -> WiFiStatus:
    """Get current WiFi connection status and metrics."""
    interfaces = _find_wireless_interfaces()
    if not interfaces:
        return WiFiStatus(
            connection_status=ConnectionStatus.DISCONNECTED,
            last_update=datetime.now(timezone.utc),
            error="No wireless interfaces found on this system."
        )

    # Select the primary active wireless interface
    primary_iface = interfaces[0]
    iw_data = _get_iw_link(primary_iface)
    
    # If not connected on first, check other wireless interfaces
    if not iw_data.get("connected") and len(interfaces) > 1:
        for alt_iface in interfaces[1:]:
            alt_data = _get_iw_link(alt_iface)
            if alt_data.get("connected"):
                primary_iface = alt_iface
                iw_data = alt_data
                break

    nm_data = _get_nmcli_status(primary_iface)
    iwc_data = _get_iwconfig_status(primary_iface)

    # Determine connection status
    is_connected = iw_data.get("connected", False) or nm_data.get("connected", False) or bool(iwc_data.get("ssid"))
    conn_status = ConnectionStatus.CONNECTED if is_connected else ConnectionStatus.DISCONNECTED

    # Determine SSID
    ssid = iw_data.get("ssid") or nm_data.get("ssid") or iwc_data.get("ssid") or nm_data.get("connection_name")

    # Determine BSSID
    bssid = iw_data.get("bssid") or iwc_data.get("bssid")

    # Determine frequency and channel
    freq = iw_data.get("freq_mhz") or nm_data.get("freq_mhz") or iwc_data.get("freq_mhz")
    channel = _freq_to_channel(freq) if freq else None

    # Determine signal dBm
    signal_dbm = iw_data.get("signal_dbm")
    if signal_dbm is None:
        signal_dbm = iwc_data.get("signal_dbm")
    if signal_dbm is None and "signal_percent" in nm_data:
        # Convert nmcli 0-100% to approximate dBm: 100% -> -50 dBm, 0% -> -100 dBm
        pct = nm_data["signal_percent"]
        signal_dbm = round(-100.0 + (pct * 0.5), 1)

    # Noise & Link quality
    noise_dbm = iwc_data.get("noise_dbm")
    link_quality = iwc_data.get("link_quality")
    if link_quality is None and "signal_percent" in nm_data:
        link_quality = nm_data["signal_percent"]

    # Bitrates
    tx_bitrate = iw_data.get("tx_bitrate") or iwc_data.get("bitrate")
    rx_bitrate = iw_data.get("rx_bitrate")

    # IP and MAC addresses
    ip_address = nm_data.get("ip_address")
    mac_address = _get_mac_address(primary_iface)
    driver = _get_driver_name(primary_iface)

    return WiFiStatus(
        interface=primary_iface,
        driver=driver,
        ssid=ssid,
        bssid=bssid,
        frequency_mhz=freq,
        channel=channel,
        signal_dbm=signal_dbm,
        noise_dbm=noise_dbm,
        link_quality=link_quality,
        tx_bitrate=tx_bitrate,
        rx_bitrate=rx_bitrate,
        connection_status=conn_status,
        ip_address=ip_address,
        mac_address=mac_address,
        last_update=datetime.now(timezone.utc),
        error=None
    )


def get_hardware_diagnostics() -> HardwareDiagnostics:
    """Full hardware diagnostic report."""
    interfaces = _find_wireless_interfaces()
    if not interfaces:
        return HardwareDiagnostics(
            notes=["No wireless interfaces detected via 'iw dev' or /sys/class/net."]
        )

    iface = interfaces[0]
    driver = _get_driver_name(iface)
    
    # PCI ID from sysfs
    pci_id = None
    vendor_path = f"/sys/class/net/{iface}/device/vendor"
    device_path = f"/sys/class/net/{iface}/device/device"
    if os.path.exists(vendor_path) and os.path.exists(device_path):
        try:
            with open(vendor_path, "r") as vf, open(device_path, "r") as df:
                v = vf.read().strip().replace("0x", "")
                d = df.read().strip().replace("0x", "")
                pci_id = f"{v}:{d}"
        except Exception:
            pass

    # Adapter name from lspci or uevent
    adapter_name = None
    uevent_path = f"/sys/class/net/{iface}/device/uevent"
    if os.path.exists(uevent_path):
        try:
            with open(uevent_path, "r") as f:
                for line in f:
                    if line.startswith("PCI_SLOT_NAME="):
                        slot = line.strip().split("=")[1]
                        lspci_out = _run_cmd(["lspci", "-s", slot])
                        if lspci_out:
                            # Example: "02:00.0 Network controller: Intel Corporation Centrino Wireless-N 2230 (rev c4)"
                            parts = lspci_out.split(":", 2)
                            if len(parts) >= 3:
                                adapter_name = parts[2].strip()
                            else:
                                adapter_name = lspci_out.strip()
                        break
        except Exception:
            pass

    if not adapter_name and pci_id:
        if pci_id.lower() == "8086:0887" or pci_id.lower() == "8086:0888":
            adapter_name = "Intel Corporation Centrino Wireless-N 2230"
        else:
            adapter_name = f"PCI WiFi Adapter ({pci_id})"

    # Find phy for monitor mode check
    phy_name = None
    phy_link = f"/sys/class/net/{iface}/phy80211/name"
    if os.path.exists(phy_link):
        try:
            with open(phy_link, "r") as f:
                phy_name = f.read().strip()
        except Exception:
            pass

    if not phy_name:
        iw_out = _run_cmd(["iw", "dev", iface, "info"])
        if iw_out:
            m = re.search(r"wiphy\s+(\d+)", iw_out)
            if m:
                phy_name = f"phy{m.group(1)}"

    monitor_supported = _check_monitor_mode(phy_name) if phy_name else False
    csi_available = _check_csi_tools()

    capabilities = [
        HardwareCapability(
            name="RSSI Signal Polling",
            available=True,
            detail="Driver supports signal strength querying via iw/nmcli at ~1-2 Hz."
        ),
        HardwareCapability(
            name="Link Quality & Bitrate",
            available=True,
            detail="Real-time link quality percentage and TX/RX bitrates accessible."
        ),
        HardwareCapability(
            name="Monitor Mode",
            available=monitor_supported,
            detail="Supported by driver/hardware." if monitor_supported else "Monitor mode not supported or restricted."
        ),
        HardwareCapability(
            name="CSI (Channel State Information)",
            available=csi_available,
            detail="Available via CSI logging tools." if csi_available else "Standard iwlwifi driver does not expose raw CSI subcarrier matrices."
        )
    ]

    notes = [
        f"Primary interface: {iface} (driver: {driver or 'unknown'}).",
        f"PCI Device Identifier: {pci_id or 'unknown'}.",
        f"Monitor mode supported: {'Yes' if monitor_supported else 'No'}.",
        f"CSI extraction available: {'Yes' if csi_available else 'No'}. The Intel 2230 requires specialized research firmware/kernel patches (e.g., Linux 802.11n CSI Tool on Linux 3.x/4.x kernels) to dump CSI frames. It is unavailable in standard mainline kernels.",
        "Sensing capabilities on this device are strictly limited to RSSI time-series amplitude observations."
    ]

    return HardwareDiagnostics(
        adapter_name=adapter_name,
        driver=driver,
        pci_id=pci_id,
        interface=iface,
        monitor_mode_supported=monitor_supported,
        csi_available=csi_available,
        capabilities=capabilities,
        notes=notes
    )
