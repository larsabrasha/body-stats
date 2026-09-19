import subprocess

import pytest

from wiiscale import release


def build_sysfs(tmp_path, *, event="event2", address="34:AF:2C:E4:EA:C5",
                hid_uniq=True):
    """A copy of the paths a connected board really creates.

    Modelled on a live Pi Zero W:

        /sys/devices/platform/soc/.../bluetooth/hci0/hci0:12/
            0005:057E:0306.0001/input/input2

    The address lives in the HID parent's uevent as HID_UNIQ, put there by
    hidp. It is deliberately NOT placed on the ACL connection object here:
    that attribute was removed from the kernel, and assuming it existed is
    what made the first version of this return None on real hardware while
    the test passed.
    """
    connection = (
        tmp_path / "devices" / "platform" / "soc" / "bluetooth" / "hci0" / "hci0:12"
    )
    hid = connection / "0005:057E:0306.0001"
    device = hid / "input" / "input2"
    device.mkdir(parents=True)
    (connection / "uevent").write_text("DEVTYPE=link\n")
    uevent = "HID_ID=0005:0000057E:00000306\nHID_NAME=Nintendo RVL-WBC-01\n"
    if hid_uniq:
        uevent += f"HID_PHYS=b8:27:eb:c7:8b:1c\nHID_UNIQ={address.lower()}\n"
    (hid / "uevent").write_text(uevent)
    (device / "uevent").write_text("PRODUCT=5/57e/306/600\n")

    inputs = tmp_path / "class" / "input" / event
    inputs.mkdir(parents=True)
    (inputs / "device").symlink_to(device)
    return tmp_path / "class"


def test_address_is_recovered_from_the_hid_parent(tmp_path, monkeypatch):
    # evdev's `uniq` is empty for hid-wiimote, so the address has to come from
    # the HID device above it.
    monkeypatch.setattr(release, "SYS_CLASS", str(build_sysfs(tmp_path)))
    assert release.address_from_input_device("/dev/input/event2") == "34:AF:2C:E4:EA:C5"


def test_a_hid_device_without_an_address_gives_nothing(tmp_path, monkeypatch):
    # A USB board, or any HID device that is not on Bluetooth.
    monkeypatch.setattr(
        release, "SYS_CLASS", str(build_sysfs(tmp_path, hid_uniq=False))
    )
    assert release.address_from_input_device("/dev/input/event2") is None


def test_missing_sysfs_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "SYS_CLASS", str(tmp_path / "nothing"))
    assert release.address_from_input_device("/dev/input/event2") is None


def test_disconnect_reports_success(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "Successful disconnected", "")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.disconnect("34:AF:2C:E4:EA:C5") is True
    assert calls == [["bluetoothctl", "disconnect", "34:AF:2C:E4:EA:C5"]]


def test_disconnect_survives_a_missing_bluetoothctl(monkeypatch, caplog):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.disconnect("34:AF:2C:E4:EA:C5") is False


def test_disconnect_says_what_bluetoothctl_printed(monkeypatch, caplog):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "Device not available")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        assert release.disconnect("34:AF:2C:E4:EA:C5") is False
    assert "Device not available" in caplog.text
