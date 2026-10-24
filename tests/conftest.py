#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    conftest.py
# Description: Puts the bundle's Server Plugin folder on sys.path, and builds
#              REAL pyvesync Core 400S objects from response-shaped dicts, so
#              the decoding is tested against the library, not a guess at it.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0
#
# pyvesync is installed from requirements.txt in CI. The device is built
# offline: no sign-in, no network, exactly as pyvesync builds it from a real
# device-list reply and a real getPurifierStatus reply.

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "Levoit.indigoPlugin", "Contents", "Server Plugin")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Shaped like VeSync's device list entry and getPurifierStatus result for a
# Core 400S (field names from pyvesync 3.4.2's models). Values are invented.
LIST_ENTRY = {
    "deviceRegion": "EU", "isOwner": True, "deviceName": "Bedroom Purifier",
    "cid": "vsaqTESTCID0001", "connectionType": "wifi", "deviceType": "Core400S",
    "type": "wifi-air", "configModule": "WiFi_AirPurifier_Core400S_EU",
    "deviceStatus": "on", "connectionStatus": "online",
}
STATUS = {
    "enabled": True, "filter_life": 87, "mode": "auto", "level": 2, "device_error_code": 0,
    "air_quality": 1, "air_quality_value": 4, "display": True, "child_lock": False,
    "configuration": {"display": True, "display_forever": True,
                      "auto_preference": {"type": "efficient", "room_size": 538}},
}


def real_purifier(list_entry=None, status=None):
    """A real pyvesync VeSyncAirBypass with its state set from `status`."""
    pyvesync = pytest.importorskip("pyvesync")
    from pyvesync.models.purifier_models import PurifierCoreDetailsResult
    from pyvesync.models.vesync_models import ResponseDeviceDetailsModel

    manager = pyvesync.VeSync("someone@example.com", "not-a-password", country_code="GB")
    entry = dict(LIST_ENTRY, **(list_entry or {}))
    manager.devices.add_device_from_model(ResponseDeviceDetailsModel.from_dict(entry), manager)
    device = manager.devices.air_purifiers[0]
    if status is not False:
        device._set_purifier_state(PurifierCoreDetailsResult.from_dict(dict(STATUS, **(status or {}))))
    return device
