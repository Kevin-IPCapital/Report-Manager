"""Conservative, side-effect-free classification of legacy collector subprocess output.

A nonzero exit is a failure unless the expected, nonfatal N-central 404 case
is proven from THIS execution's stdout. This module never contacts an API/DB.
"""
import re
from dataclasses import dataclass

PATCH_SUMMARY = re.compile(
    r"^\s*(?P<devices>\d+) devices, (?P<entries>\d+) entries, "
    r"(?P<missing>\d+) missing, (?P<unavailable>\d+) unavailable\s*$"
)
DEVICE_NOT_FOUND = re.compile(
    r"^\s*warn: device \d+ \(.+\): Request failed \(404\) on "
    r"/api/devices/\d+/assets: .*DeviceNotFoundException: Device was not found!",
    re.IGNORECASE,
)
OK_CUSTOMER = re.compile(r"^\s*ok\s+.+?\s+\d+ devices\s+\d+ entries\s+\d+ missing\b")


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    devices: int | None = None
    entries: int | None = None
    unavailable: int | None = None


def classify_outcome(job: str, returncode: int, output: str) -> Outcome:
    """Return SUCCESS/PARTIAL/FAILED, never promoting unrecognized errors.

    PARTIAL is supported only for N-central patch collection where the sole failures are confirmed device-asset 404/DeviceNotFoundException.
    NC-01 (decided 12 Nov 2026): those devices count as **disconnected**, and that is shown per device on the Server Health page; the run
    itself stays PARTIAL on the Data Collection page, because it collected every other device's inventory.
    """
    if returncode == 0:
        return Outcome("SUCCESS", "collector exit code 0")
    if job != "ncentral_patches" or returncode != 1:
        return Outcome("FAILED", f"collector exit code {returncode}")

    summary = None
    warnings = 0
    customer_count = 0
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        match = PATCH_SUMMARY.fullmatch(line)
        if match:
            if summary is not None:
                return Outcome("FAILED", "multiple patch summaries in run output")
            summary = {key: int(value) for key, value in match.groupdict().items()}
        elif DEVICE_NOT_FOUND.match(line):
            warnings += 1
        elif OK_CUSTOMER.match(line):
            customer_count += 1
        else:
            # Unknown text (e.g. traceback, auth/DB error, timeout) is NOT partial.
            return Outcome("FAILED", "unrecognized output alongside nonzero exit")

    if summary is None or customer_count == 0 or warnings == 0:
        return Outcome("FAILED", "missing complete patch summary or known device 404s")
    if warnings != summary["unavailable"] or summary["devices"] == 0:
        return Outcome("FAILED", "404 warning count does not match unavailable devices")
    return Outcome(
        "PARTIAL", "device-asset 404s only; other device inventories collected",
        devices=summary["devices"], entries=summary["entries"],
        unavailable=summary["unavailable"],
    )
