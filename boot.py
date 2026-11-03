"""Boot-time filesystem setup.

CircuitPython can only write to its own flash when the USB host has *not*
mounted the CIRCUITPY drive.  When the tree is running on its own (plugged
into a USB charger or battery) we remount read/write so that admin changes
can be saved to /config.json.  When you plug it into a computer to edit the
code, the host keeps the drive and config saves fall back to RAM only --
the web UI tells you when that happens.
"""

import storage

try:
    import supervisor

    usb = getattr(supervisor.runtime, "usb_connected", True)
except Exception:  # pragma: no cover - very old builds
    usb = True

if usb:
    print("boot: USB host present, leaving filesystem read-only to code")
else:
    try:
        storage.remount("/", readonly=False)
        print("boot: filesystem remounted writable")
    except Exception as err:  # pragma: no cover
        print("boot: remount failed:", err)
