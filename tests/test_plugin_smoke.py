#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_plugin_smoke.py
# Description: Loads plugin.py against a stubbed Indigo and a fake VeSync
#              client, and drives polls, linking, outages, actions, the on/off
#              buttons and the menu items end to end.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0
#
# The fake device clears its error state on a state write unless told not to,
# exactly as Indigo does. A fake that did not would let the plugin wipe its own
# "offline" flag and every test would still pass - that is how seven of the
# estate's plugins hid that bug until 27-09-2026.

import importlib
import logging
import re
import sys
import types
from datetime import datetime

import pytest

from conftest import real_purifier
import levoit_logic as L

PLUGIN_ID = "com.clives.indigoplugin.levoit"
EMAIL = "clive.example@example.com"
PASSWORD = "Zq7-not-a-real-password"


class FakeDevice:
    def __init__(self, dev_id, name, states=None, props=None, plugin_id=PLUGIN_ID,
                 type_id="levoitPurifier", enabled=True):
        self.id, self.name = dev_id, name
        self.states = dict(states or {})
        self.pluginProps = dict(props or {})
        self.ownerProps = dict(props or {})
        self.sharedProps = {}
        self.pluginId, self.deviceTypeId = plugin_id, type_id
        self.enabled = enabled
        self.errorState = ""
        self.lastChanged = datetime.now()
        self.writes = []

    def updateStatesOnServer(self, items, clearErrorState=True):
        self.writes.append((list(items), clearErrorState))
        for i in items:
            self.states[i["key"]] = i["value"]
        if clearErrorState:
            self.errorState = ""

    def setErrorStateOnServer(self, text):
        self.errorState = text or ""

    def replacePluginPropsOnServer(self, props):
        self.pluginProps = dict(props)
        self.ownerProps = dict(props)

    def replaceSharedPropsOnServer(self, props):
        self.sharedProps = dict(props)

    def stateListOrDisplayStateIdChanged(self):
        self.state_list_refreshed = True


class Devices(dict):
    def add(self, dev):
        self[dev.id] = dev
        return dev

    def __iter__(self):
        return iter(list(self.values()))

    def iter(self, which=None):
        if which == "self":
            return iter([d for d in self.values() if d.pluginId == PLUGIN_ID])
        return iter(list(self.values()))

    subscribed = False

    def subscribeToChanges(self):
        self.subscribed = True


def build_indigo(tmp_path):
    ind = types.ModuleType("indigo")
    ind.devices = Devices()
    ind.Dict = dict

    class PluginBase:
        class StopThread(Exception):
            pass

        def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
            self.pluginId = pluginId
            self.pluginDisplayName = pluginDisplayName
            self.pluginVersion = pluginVersion
            self.pluginPrefs = pluginPrefs
            self.logger = logging.getLogger("levoit-test")
            self.stopThread = False
            self.sleeps = 0

        def sleep(self, seconds):
            self.sleeps += 1
            raise self.StopThread

        def deviceUpdated(self, origDev, newDev):
            pass

    ind.PluginBase = PluginBase
    ind.kDeviceAction = types.SimpleNamespace(TurnOn=1, TurnOff=2, Toggle=3, SetBrightness=4, RequestStatus=11)
    ind.kUniversalAction = types.SimpleNamespace(Beep=30, RequestStatus=11)
    install = tmp_path / "Indigo 2025.2"
    install.mkdir()
    logged = []
    ind.server = types.SimpleNamespace(getInstallFolderPath=lambda: str(install),
                                       log=lambda *a, **k: logged.append(a),
                                       version="2025.2.0", apiVersion="3.8", logged=logged)
    return ind


class FakeClient:
    """Stands in for vesync_client.VeSyncClient; same calls, same errors."""
    made = []

    def __init__(self, username, password, country="GB", **kw):
        self.args = (username, password, country)
        self.list = [real_purifier()]
        self.fail = None
        self.sent = []
        self.closed = False
        self.refreshes = 0
        FakeClient.made.append(self)

    refuse_password = None              # VeSync turns down this password, whichever client asks

    def refresh(self):
        self.refreshes += 1
        if self.fail:
            raise self.fail
        if self.args[1] == FakeClient.refuse_password:
            import vesync_client as vc
            raise vc.AuthError("VeSync turned down the sign-in")
        return list(self.list)

    def send(self, cid, command, value):
        self.sent.append((cid, command, value))
        if self.fail:
            raise self.fail
        device = next(d for d in self.list if d.cid == cid)
        if command == "fanSpeed":
            device.state.fan_level = value
            device.state.mode = "manual"
        elif command == "power":
            from pyvesync.const import DeviceStatus
            device.state.device_status = DeviceStatus.ON if value else DeviceStatus.OFF
        return device

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="levoit-test")
    ind = build_indigo(tmp_path)
    monkeypatch.setitem(sys.modules, "indigo", ind)
    import plugin as plugin_module
    plugin_module = importlib.reload(plugin_module)
    FakeClient.made = []
    FakeClient.refuse_password = None
    monkeypatch.setattr(plugin_module.vc, "VeSyncClient", FakeClient)
    (tmp_path / "IndigoSecrets.py").write_text(
        f'VESYNC_USERNAME = "{EMAIL}"\nVESYNC_PASSWORD = "{PASSWORD}"\n', encoding="utf-8")
    p = plugin_module.Plugin(PLUGIN_ID, "Levoit Purifier", "1.0.0", {"pollSeconds": "60", "logLevel": "INFO"})
    dev = ind.devices.add(FakeDevice(101, "Bedroom Purifier", props={"cid": "vsaqTESTCID0001"}))
    p.startup()
    p.deviceStartComm(dev)
    return types.SimpleNamespace(p=p, ind=ind, dev=dev, mod=plugin_module, tmp=tmp_path, log=caplog)


def tick(env, now=1000.0):
    env.p._tick(now)
    return FakeClient.made[-1] if FakeClient.made else None


def messages(env, level=None):
    """Logged text with the plugin's [HH:MM:SS.mmm] prefix taken off."""
    return [re.sub(r"^\[\d\d:\d\d:\d\d\.\d{3}\] ", "", r.getMessage()) for r in env.log.records
            if r.name == "levoit-test" and (level is None or r.levelname == level)]


# ── polling ──

def test_first_poll_writes_every_state_without_clearing_errors(env):
    client = tick(env)
    assert client.args == (EMAIL, PASSWORD, "GB")
    s = env.dev.states
    assert (s["onOffState"], s["mode"], s["fanLevel"], s["pm25"], s["airQuality"]) == (True, "auto", 2, 4, 1)
    assert (s["airQualityText"], s["filterLife"], s["deviceOnline"]) == ("excellent", 87, True)
    items, clear = env.dev.writes[0]
    assert clear is False
    assert {i["key"]: i.get("uiValue") for i in items}["pm25"] == "4 ug/m3"


def test_only_changed_states_are_written_next_time(env):
    client = tick(env)
    env.dev.writes.clear()
    client.list[0].state.pm25 = 9
    env.p._tick(2000.0)
    keys = {i["key"] for i in env.dev.writes[0][0]}
    assert "pm25" in keys and "fanLevel" not in keys and "mode" not in keys


def test_polls_wait_for_the_interval(env):
    client = tick(env, 1000.0)
    env.p._tick(1030.0)
    assert client.refreshes == 1
    env.p._tick(1061.0)
    assert client.refreshes == 2


def test_sql_logger_is_told_to_skip_last_update(env):
    assert env.dev.sharedProps["sqlLoggerIgnoreStates"] == "lastUpdate"
    assert env.dev.state_list_refreshed


def test_a_device_with_no_purifier_chosen_links_to_the_first_free_one(env):
    spare = env.ind.devices.add(FakeDevice(102, "New Purifier", props={"cid": "none"}))
    client = tick(env)
    client.list.append(real_purifier(list_entry={"cid": "vsaqSECOND", "deviceName": "Lounge"}))
    env.p._tick(5000.0)
    assert spare.pluginProps["cid"] == "vsaqSECOND" and spare.pluginProps["address"] == "Core400S"
    assert any('linked to "Lounge"' in m for m in messages(env, "INFO"))


def test_a_purifier_gone_from_the_account_is_marked_not_listed(env):
    client = tick(env)
    client.list = []
    env.p._tick(5000.0)
    assert env.dev.errorState == "not listed" and env.dev.states["deviceOnline"] is False
    assert sum("no longer lists" in m for m in messages(env, "WARNING")) == 1
    env.p._tick(9000.0)
    assert sum("no longer lists" in m for m in messages(env, "WARNING")) == 1


def test_a_purifier_vesync_says_is_offline_shows_an_error_that_survives_writes(env):
    client = tick(env)
    from pyvesync.const import ConnectionStatus
    client.list[0].state.connection_status = ConnectionStatus.OFFLINE
    env.p._tick(5000.0)
    assert env.dev.errorState == "offline"
    client.list[0].state.pm25 = 30                 # another reading, still offline
    env.p._tick(9000.0)
    assert env.dev.errorState == "offline"
    assert any("dropped off VeSync" in m for m in messages(env, "WARNING"))
    client.list[0].state.connection_status = ConnectionStatus.ONLINE
    env.p._tick(13000.0)
    assert env.dev.errorState == ""


def test_three_failed_checks_mark_devices_offline_then_recovery_clears_it(env):
    import vesync_client as vc
    client = tick(env)
    client.fail = vc.Unavailable("cannot reach VeSync (OSError: down)")
    now = 2000.0
    for _ in range(3):
        env.p._tick(now)
        now = env.p.next_poll + 1
    assert env.dev.errorState == "offline" and env.dev.states["deviceOnline"] is False
    assert sum("Cannot reach VeSync" in m for m in messages(env, "WARNING")) == 1
    assert env.p.next_poll - (now - 1) <= env.mod.MAX_BACKOFF_SECONDS
    client.fail = None
    env.p._tick(env.p.next_poll + 1)
    assert env.dev.errorState == "" and env.dev.states["deviceOnline"] is True
    assert "Back in touch with VeSync." in messages(env, "INFO")


def test_a_refused_sign_in_is_said_once_and_retried_slowly(env):
    FakeClient.refuse_password = PASSWORD
    tick(env, 2000.0)
    assert env.p.next_poll >= 2000.0 + env.mod.AUTH_RETRY_SECONDS
    env.p._tick(env.p.next_poll + 1)
    env.p._tick(env.p.next_poll + 1)
    errors = [m for m in messages(env, "ERROR") if "turned down" in m]
    assert len(errors) == 1 and "VESYNC_PASSWORD" in errors[0]
    assert env.dev.errorState == "login refused"


def test_a_username_that_is_not_an_email_is_named(env):
    write_secrets(env, "Clive Example", PASSWORD)
    FakeClient.refuse_password = PASSWORD
    tick(env, 2000.0)
    errors = [m for m in messages(env, "ERROR") if "turned down" in m]
    assert len(errors) == 1 and "not an email address" in errors[0]
    assert "Clive Example" not in "\n".join(messages(env))


def write_secrets(env, username, password):
    (env.tmp / "IndigoSecrets.py").write_text(
        f'VESYNC_USERNAME = "{username}"\nVESYNC_PASSWORD = "{password}"\n', encoding="utf-8")


def test_a_fixed_secrets_file_is_used_at_the_next_retry_without_a_restart(env):
    FakeClient.refuse_password = PASSWORD
    tick(env, 2000.0)
    write_secrets(env, EMAIL, "the-right-one")
    env.p._tick(env.p.next_poll + 1)
    assert FakeClient.made[-1].args[1] == "the-right-one"
    assert env.dev.errorState == "" and env.dev.states["deviceOnline"] is True
    assert "Back in touch with VeSync." in messages(env, "INFO")


def test_test_connection_reads_the_secrets_file_afresh(env):
    FakeClient.refuse_password = PASSWORD
    tick(env, 2000.0)
    write_secrets(env, EMAIL, "the-right-one")
    env.p.testConnection()
    env.p._tick(2001.0)
    assert any(m.startswith("Connection test PASSED") for m in messages(env, "INFO"))
    assert FakeClient.made[-1].args[1] == "the-right-one"


def test_rate_limit_waits(env):
    import vesync_client as vc
    client = tick(env)
    client.fail = vc.RateLimited("VeSync says the account is asking too often")
    env.p._tick(2000.0)
    assert env.p.next_poll == 2000.0 + env.mod.RATE_LIMIT_SECONDS
    assert env.dev.errorState == ""                # the purifier itself is fine


def test_missing_credentials_are_reported_once_and_nothing_is_contacted(env):
    (env.tmp / "IndigoSecrets.py").write_text("", encoding="utf-8")
    env.p._tick(1000.0)
    env.p.rebuild = True
    env.p._tick(2000.0)
    errors = [m for m in messages(env, "ERROR") if "VESYNC_USERNAME and VESYNC_PASSWORD" in m]
    assert len(errors) == 1 and FakeClient.made == []


def test_configure_fallback_when_secrets_file_has_no_keys(env):
    (env.tmp / "IndigoSecrets.py").write_text("OTHER = 1\n", encoding="utf-8")
    env.p.pluginPrefs.update({"username": "u@example.com", "password": "pw", "country": "us"})
    client = tick(env)
    assert client.args == ("u@example.com", "pw", "US")


def test_saving_configure_rebuilds_the_client(env):
    first = tick(env)
    env.p.closedPrefsConfigUi({"pollSeconds": "120", "logLevel": "INFO"}, False)
    env.p._tick(1001.0)
    assert first.closed and len(FakeClient.made) == 2 and env.p.poll_interval == 120


def test_the_password_never_reaches_the_log(env):
    import vesync_client as vc
    client = tick(env)
    env.p.testConnection()
    env.p._tick(1001.0)
    client.fail = vc.AuthError("VeSync turned down the sign-in")
    env.p._tick(5000.0)
    text = "\n".join(messages(env))
    assert PASSWORD not in text and EMAIL not in text
    assert "c***@example.com" in text


# ── commands ──

def test_set_fan_speed_is_queued_sent_and_read_back(env):
    client = tick(env)
    env.p.actionSetFanSpeed(types.SimpleNamespace(props={"speed": "3"}), env.dev)
    assert client.sent == []                       # nothing on the callback's thread
    env.p._tick(1001.0)
    assert client.sent == [("vsaqTESTCID0001", "fanSpeed", 3)]
    assert env.dev.states["fanLevel"] == 3 and env.dev.states["mode"] == "manual"
    assert "Bedroom Purifier: fan speed set to 3." in messages(env, "INFO")


@pytest.mark.parametrize("method, props, command, value", [
    ("actionSetMode", {"mode": "sleep"}, "mode", "sleep"),
    ("actionSetAutoPreference", {"preference": "quiet"}, "autoPreference", "quiet"),
    ("actionSetChildLock", {"value": "on"}, "childLock", True),
    ("actionSetChildLock", {"value": "toggle"}, "childLock", True),     # it reads off now
    ("actionSetDisplay", {"value": "toggle"}, "display", False),        # it reads on now
    ("actionSetSleepTimer", {"minutes": "45"}, "timer", 45),
    ("actionResetFilter", {}, "resetFilter", None),
])
def test_every_action_reaches_the_client(env, method, props, command, value):
    client = tick(env)
    getattr(env.p, method)(types.SimpleNamespace(props=props), env.dev)
    env.p._tick(1001.0)
    assert client.sent == [("vsaqTESTCID0001", command, value)]


@pytest.mark.parametrize("method, props", [
    ("actionSetFanSpeed", {"speed": "9"}), ("actionSetMode", {"mode": "turbo"}),
    ("actionSetSleepTimer", {"minutes": "lots"}), ("actionSetChildLock", {}),
])
def test_a_bad_value_is_refused_where_it_was_made(env, method, props):
    client = tick(env)
    getattr(env.p, method)(types.SimpleNamespace(props=props), env.dev)
    env.p._tick(1001.0)
    assert client.sent == []
    assert any("nothing was sent" in m for m in messages(env, "ERROR"))


@pytest.mark.parametrize("act, value", [(1, True), (2, False), (3, False)])
def test_the_on_off_buttons(env, act, value):
    client = tick(env)
    env.p.actionControlDevice(types.SimpleNamespace(deviceAction=act), env.dev)
    env.p._tick(1001.0)
    assert client.sent == [("vsaqTESTCID0001", "power", value)]
    assert env.dev.states["onOffState"] is value


def test_status_request_polls_now(env):
    client = tick(env)
    env.p.actionControlDevice(types.SimpleNamespace(deviceAction=11), env.dev)
    env.p._tick(1001.0)
    assert client.refreshes == 2


# VeSync's status lags a command by minutes (measured 24-10-2026 on a real Core
# 400S: the display changed at once, getPurifierStatus caught up 1.5-4 min later).
# The fake leaves displayOn as it was after a display command, as the real
# status did, so these drive the hold.

class Clock:
    def __init__(self, t=10000.0):
        self.t = t

    def __call__(self):
        return self.t


def stale_display_command(env, value="off"):
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    env.p.actionSetDisplay(types.SimpleNamespace(props={"value": value}), env.dev)
    env.p._tick(clock.t)
    return client, clock


def test_an_accepted_command_shows_at_once_though_vesync_status_is_behind(env):
    client, _ = stale_display_command(env)
    assert client.sent == [("vsaqTESTCID0001", "display", False)]
    assert env.dev.states["displayOn"] is False                     # what was asked for
    assert "Bedroom Purifier: display off." in messages(env, "INFO")
    assert not messages(env, "WARNING")


def test_a_stale_poll_does_not_undo_a_held_command(env):
    client, clock = stale_display_command(env)
    clock.t += 120
    client.list[0].state.pm25 = 9                                   # a real change still comes through
    env.p._tick(clock.t)
    assert env.dev.states["displayOn"] is False and env.dev.states["pm25"] == 9
    assert env.p.holds


def test_the_hold_ends_when_vesync_catches_up(env):
    from pyvesync.const import DeviceStatus
    client, clock = stale_display_command(env)
    clock.t += 150
    client.list[0].state.display_status = DeviceStatus.OFF          # the status catches up
    env.p._tick(clock.t)
    assert not env.p.holds and env.dev.states["displayOn"] is False
    assert not messages(env, "WARNING")


def test_a_hold_that_never_agrees_gives_way_and_says_so_once(env):
    client, clock = stale_display_command(env)
    clock.t += env.mod.HOLD_SECONDS + 1
    env.p._tick(clock.t)
    assert env.dev.states["displayOn"] is True                      # back to what VeSync reports
    warnings = [m for m in messages(env, "WARNING") if "still does not show it" in m]
    assert len(warnings) == 1 and "turn the display off" in warnings[0]
    clock.t += 120
    env.p._tick(clock.t)
    assert len([m for m in messages(env, "WARNING") if "still does not show it" in m]) == 1


def test_a_command_vesync_shows_at_once_ends_quietly(env):
    clock = Clock()
    env.p.clock = clock
    tick(env, clock.t)
    env.p.actionSetFanSpeed(types.SimpleNamespace(props={"speed": "4"}), env.dev)   # the fake applies it
    env.p._tick(clock.t)
    assert env.dev.states["fanLevel"] == 4
    assert "Bedroom Purifier: fan speed set to 4." in messages(env, "INFO")
    clock.t += env.mod.HOLD_SECONDS + 1
    env.p._tick(clock.t)
    assert not env.p.holds and not messages(env, "WARNING")


def test_quiet_then_display_keeps_quiet_through_a_stale_poll(env):
    # 24-10-2026 on the Air page: the display's hold dropped Quiet's, and
    # VeSync's stale status showed Default for 80 s.
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    env.p.actionSetAutoPreference(types.SimpleNamespace(props={"preference": "quiet"}), env.dev)
    env.p._tick(clock.t)
    clock.t += 10
    env.p.actionSetDisplay(types.SimpleNamespace(props={"value": "off"}), env.dev)
    env.p._tick(clock.t)
    clock.t += 60
    env.p._tick(clock.t)                                               # stale: efficient, display on
    assert env.dev.states["autoPreference"] == "quiet" and env.dev.states["displayOn"] is False
    assert client.list[0].state.auto_preference_type == "efficient"


def test_a_late_off_does_not_undo_a_later_on(env):
    from pyvesync.const import DeviceStatus
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    env.p.actionSetDisplay(types.SimpleNamespace(props={"value": "off"}), env.dev)
    env.p._tick(clock.t)
    clock.t += 5
    env.p.actionSetDisplay(types.SimpleNamespace(props={"value": "on"}), env.dev)
    env.p._tick(clock.t)
    clock.t += 90
    client.list[0].state.display_status = DeviceStatus.OFF             # the off lands late
    env.p._tick(clock.t)
    assert env.dev.states["displayOn"] is True


def test_a_double_tap_is_sent_once(env):
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    for _ in range(4):
        env.p.actionSetFanSpeed(types.SimpleNamespace(props={"speed": "4"}), env.dev)
    env.p._tick(clock.t)
    assert client.sent == [("vsaqTESTCID0001", "fanSpeed", 4)]
    assert messages(env, "INFO").count("Bedroom Purifier: fan speed set to 4.") == 1
    clock.t += env.mod.DUPLICATE_SECONDS + 1
    env.p.actionSetFanSpeed(types.SimpleNamespace(props={"speed": "4"}), env.dev)
    env.p._tick(clock.t)
    assert len(client.sent) == 2                                       # a deliberate repeat goes


def test_a_failed_command_can_be_retried_at_once(env):
    import vesync_client as vc
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    client.fail = vc.Unavailable("VeSync did not answer in time")
    env.p.actionSetMode(types.SimpleNamespace(props={"mode": "sleep"}), env.dev)
    env.p._tick(clock.t)
    client.fail = None
    env.p.actionSetMode(types.SimpleNamespace(props={"mode": "sleep"}), env.dev)
    env.p._tick(clock.t)
    assert len(client.sent) == 2 and env.dev.states["mode"] == "sleep"


def test_fan_speed_expects_manual_and_on():
    assert L.command_expects("fanSpeed", 4) == {"fanLevel": 4, "mode": "manual", "onOffState": True}


def test_a_timer_has_nothing_to_hold(env):
    tick(env)
    env.p.actionSetSleepTimer(types.SimpleNamespace(props={"minutes": "30"}), env.dev)
    env.p._tick(5000.0)
    assert not env.p.holds


def test_a_failed_command_says_why_and_changes_nothing(env):
    import vesync_client as vc
    client = tick(env)
    client.fail = vc.CommandRefused("VeSync did not accept the command")
    env.p.actionSetFanSpeed(types.SimpleNamespace(props={"speed": "4"}), env.dev)
    env.p._tick(1001.0)
    assert env.dev.states["fanLevel"] == 2
    assert ("Bedroom Purifier: could not set the fan speed to 4, because VeSync did not accept the command."
            in messages(env, "ERROR"))


def test_a_command_for_an_unlinked_device_is_refused(env):
    unlinked = env.ind.devices.add(FakeDevice(103, "Spare", props={"cid": "none"}))
    client = tick(env)
    client.list = list(client.list)                # nothing spare to link to
    env.p.actionSetMode(types.SimpleNamespace(props={"mode": "auto"}), unlinked)
    env.p._tick(1001.0)
    assert all(s[0] != "none" for s in client.sent)
    assert any("not linked to a purifier" in m for m in messages(env, "ERROR"))


# ── menus, dialogs and the worker ──

def test_test_connection_logs_the_banner_and_the_result(env):
    tick(env)
    env.p.testConnection()
    env.p._tick(1001.0)
    info = messages(env, "INFO")
    assert any(m.startswith("Connection test PASSED - signed in as c***@example.com (GB); 1 air purifier ")
               for m in info)
    assert any(m.startswith("Bedroom Purifier (Core400S) is on") for m in info)


def test_test_connection_failure(env):
    tick(env)
    FakeClient.refuse_password = PASSWORD
    env.p.testConnection()
    env.p._tick(1001.0)
    assert any(m.startswith("Connection test FAILED") for m in messages(env, "ERROR"))


def test_list_account_devices(env):
    tick(env)
    env.p.listAccountDevices()
    env.p._tick(1001.0)
    assert any('in Indigo as "Bedroom Purifier"' in m for m in messages(env, "INFO"))


def test_purifier_menu_never_offers_an_empty_value(env):
    assert all(v != "" for v, _ in env.p.purifierMenu())
    tick(env)
    menu = env.p.purifierMenu()
    assert ("vsaqTESTCID0001", "Bedroom Purifier (Core400S)") in menu
    assert all(v != "" for v, _ in menu)


def test_device_dialog_defaults_and_validation(env):
    tick(env)
    values, _ = env.p.getDeviceConfigUiValues({}, "levoitPurifier", 0)
    assert values["cid"] == "none"
    ok, values = env.p.validateDeviceConfigUi({"cid": "vsaqTESTCID0001"}, "levoitPurifier", 101)
    assert ok and values["address"] == "Core400S" and values["SupportsStatusRequest"] is True


@pytest.mark.parametrize("values, field", [({"pollSeconds": "x"}, "pollSeconds"),
                                           ({"pollSeconds": "60", "country": "GBR"}, "country")])
def test_settings_validation(env, values, field):
    result = env.p.validatePrefsConfigUi(values)
    assert result[0] is False and field in result[2]


def test_sleep_timer_dialog_validation(env):
    assert env.p.validateActionConfigUi({"minutes": "x"}, "setSleepTimer", 101)[0] is False
    assert env.p.validateActionConfigUi({"minutes": "30"}, "setSleepTimer", 101)[0] is True


def test_the_worker_closes_the_client_when_it_stops(env):
    client = tick(env)
    env.p.runConcurrentThread()                    # the stub's sleep() raises StopThread
    assert client.closed and env.p.client is None


def test_one_bad_tick_does_not_end_the_thread(env, monkeypatch):
    def boom(now):
        raise RuntimeError("surprise")
    monkeypatch.setattr(env.p, "_tick", boom)
    env.p.runConcurrentThread()
    assert any("carrying on" in r.getMessage() for r in env.log.records)


def test_library_log_goes_to_the_plugin_file_only(env):
    lib = logging.getLogger("pyvesync")
    assert lib.propagate is False


def test_log_lines_are_ascii(env):
    import vesync_client as vc
    client = tick(env)
    client.fail = vc.Unavailable("down")
    for i in range(4):
        env.p._tick(env.p.next_poll + 1)
    for m in messages(env):
        m.encode("ascii")


def test_decoded_states_match_the_declared_ones():
    import xml.etree.ElementTree as ET
    from conftest import SRC
    declared = {s.get("id") for s in ET.parse(f"{SRC}/Devices.xml").getroot().iter("State")}
    written = set(L.decode(real_purifier())) - {"onOffState"}      # onOffState is the relay's own
    assert written == declared


# ── switching back on after the power returns (CliveS, 24-10-2026: a Core 400S
#    comes back OFF after being unplugged and moved to another room) ──

PLUG_PLUGIN = "com.clives.indigoplugin.shellydirect"


def plug_snapshot(on=True, online=True, uptime=14227, watts=4.2):
    d = FakeDevice(566451865, "Air Purifier Plug", plugin_id=PLUG_PLUGIN, type_id="shellyRelay",
                   states={"onOffState": on, "deviceOnline": online, "sysUptime": uptime, "powerWatts": watts})
    d.onState = on
    return d


def with_plug(env, restore=True):
    env.dev.pluginProps.update({"powerPlug": "566451865", "restoreOn": restore})
    env.ind.devices.add(plug_snapshot())
    env.p.deviceStartComm(env.dev)
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    return client, clock


def power_returns(env, before=None, after=None):
    env.p.deviceUpdated(before or plug_snapshot(uptime=14227), after or plug_snapshot(uptime=3))


def test_startup_subscribes_to_device_changes(env):
    assert env.ind.devices.subscribed


def test_a_plug_restart_switches_the_purifier_back_on_once_it_reconnects(env):
    from pyvesync.const import DeviceStatus
    client, clock = with_plug(env)
    client.list[0].state.device_status = DeviceStatus.OFF       # it came back off
    power_returns(env)
    assert any("has power again" in m for m in messages(env, "INFO"))
    env.p._tick(clock.t)
    assert ("vsaqTESTCID0001", "power", True) not in client.sent  # still booting: wait first
    clock.t += env.mod.RESTORE_FIRST_TRY_SECONDS + 1
    env.p._tick(clock.t)
    assert ("vsaqTESTCID0001", "power", True) in client.sent
    assert env.dev.states["onOffState"] is True
    assert any("switched back on, because its plug" in m for m in messages(env, "INFO"))
    clock.t += env.mod.RESTORE_VERIFY_SECONDS + 1                # its plug reads 4.2 W: it is running
    env.p._tick(clock.t)
    assert not env.p.restores
    assert [s[1] for s in client.sent].count("power") == 1


@pytest.mark.parametrize("before, after", [
    (dict(online=False), dict(online=True)),
    (dict(on=False), dict(on=True)),
])
def test_other_signs_of_power_returning(env, before, after):
    client, clock = with_plug(env)
    power_returns(env, plug_snapshot(**before), plug_snapshot(**after))
    assert env.dev.id in env.p.restores


def test_ordinary_plug_readings_do_nothing(env):
    client, clock = with_plug(env)
    power_returns(env, plug_snapshot(uptime=100, watts=4.2), plug_snapshot(uptime=130, watts=7.3))
    assert not env.p.restores


def test_a_purifier_still_booting_is_retried_then_given_up(env):
    import vesync_client as vc
    client, clock = with_plug(env)
    client.fail = vc.CommandRefused("VeSync did not accept the command")
    power_returns(env)
    for _ in range(40):                                         # well past the ten-minute give-up
        clock.t += env.mod.RESTORE_SLOW_RETRY_SECONDS
        env.p._tick(clock.t)
    tries = [s for s in client.sent if s[1] == "power"]
    assert 2 <= len(tries) <= env.mod.RESTORE_GIVE_UP_SECONDS // env.mod.RESTORE_RETRY_SECONDS + 1
    assert not env.p.restores
    assert sum("could not switch it back on" in m for m in messages(env, "WARNING")) == 1


def test_retries_are_quick_at_first_then_slower(env):
    import vesync_client as vc
    client, clock = with_plug(env)
    client.fail = vc.CommandRefused("VeSync did not accept the command")
    power_returns(env)
    start = clock.t
    times = []
    while clock.t < start + 300:
        clock.t += 1
        before = len(client.sent)
        env.p._tick(clock.t)
        if len(client.sent) > before:
            times.append(clock.t - start)
    assert times[0] <= env.mod.RESTORE_FIRST_TRY_SECONDS + 1
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps[0] <= env.mod.RESTORE_RETRY_SECONDS + 1                 # quick while it is rejoining Wi-Fi
    assert gaps[-1] >= env.mod.RESTORE_SLOW_RETRY_SECONDS - 1           # slower after two minutes


def test_restore_can_be_switched_off(env):
    client, clock = with_plug(env, restore=False)
    power_returns(env)
    assert not env.p.restores


def test_our_own_device_updates_are_ignored(env):
    client, clock = with_plug(env)
    own_before, own_after = FakeDevice(101, "x", states={"sysUptime": 9}), FakeDevice(101, "x", states={"sysUptime": 1})
    env.p.plug_of[101] = {env.dev.id}
    env.p.deviceUpdated(own_before, own_after)
    assert not env.p.restores


def test_without_a_plug_a_return_to_vesync_switches_it_on_if_it_was_on(env):
    from pyvesync.const import ConnectionStatus, DeviceStatus
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)                                 # online and on
    client.list[0].state.connection_status = ConnectionStatus.OFFLINE
    clock.t += 60
    env.p._tick(clock.t)
    client.list[0].state.connection_status = ConnectionStatus.ONLINE
    client.list[0].state.device_status = DeviceStatus.OFF       # back, and off
    clock.t += 60
    env.p._tick(clock.t)
    env.p._tick(clock.t)
    assert ("vsaqTESTCID0001", "power", True) in client.sent


def test_without_a_plug_one_switched_off_before_it_dropped_stays_off(env):
    from pyvesync.const import ConnectionStatus, DeviceStatus
    clock = Clock()
    env.p.clock = clock
    client = tick(env, clock.t)
    client.list[0].state.device_status = DeviceStatus.OFF       # someone switched it off
    clock.t += 60
    env.p._tick(clock.t)
    client.list[0].state.connection_status = ConnectionStatus.OFFLINE
    clock.t += 60
    env.p._tick(clock.t)
    client.list[0].state.connection_status = ConnectionStatus.ONLINE
    clock.t += 60
    env.p._tick(clock.t)
    env.p._tick(clock.t)
    assert ("vsaqTESTCID0001", "power", True) not in client.sent


def test_plug_menu_lists_power_measuring_devices_only(env):
    env.ind.devices.add(plug_snapshot())
    env.ind.devices.add(FakeDevice(7, "Hall Lamp", plugin_id="other", states={"onOffState": True}))
    menu = env.p.plugMenu()
    assert menu[0] == ("none", "- no plug -") and ("566451865", "Air Purifier Plug") in menu
    assert all(v != "Hall Lamp" for _, v in menu) and all(v != "" for v, _ in menu)


# ── the faster signal: the plug's power falling to standby (24-10-2026: moved to
#    the living room, the purifier came back off, its plug read 1.7 W within 30 s
#    while VeSync still said it was on six minutes later) ──

def set_plug_watts(env, watts):
    env.ind.devices[566451865].states["powerWatts"] = watts


def standby_drop(env, was=4.1, now=1.7):
    set_plug_watts(env, now)
    env.p.deviceUpdated(plug_snapshot(watts=was), plug_snapshot(watts=now))


def test_a_drop_to_standby_switches_it_back_on_quickly(env):
    client, clock = with_plug(env)
    standby_drop(env)
    assert any("gone to standby" in m for m in messages(env, "INFO"))
    clock.t += env.mod.RESTORE_AFTER_STANDBY_SECONDS + 1
    env.p._tick(clock.t)
    assert ("vsaqTESTCID0001", "power", True) in client.sent
    set_plug_watts(env, 4.0)                                    # it came on
    clock.t += env.mod.RESTORE_VERIFY_SECONDS + 1
    env.p._tick(clock.t)
    assert not env.p.restores and [s[1] for s in client.sent].count("power") == 1


def test_a_command_vesync_accepted_but_did_not_take_is_sent_again_then_reported(env):
    client, clock = with_plug(env)
    standby_drop(env)                                           # and the plug stays at 1.7 W
    for _ in range(10):
        clock.t += env.mod.RESTORE_VERIFY_SECONDS + 1
        env.p._tick(clock.t)
    assert [s[1] for s in client.sent].count("power") == env.mod.RESTORE_ATTEMPTS
    assert sum("still shows it in standby" in m for m in messages(env, "WARNING")) == 1
    assert not env.p.restores


def test_switched_off_from_indigo_stays_off(env):
    client, clock = with_plug(env)
    env.p.actionControlDevice(types.SimpleNamespace(deviceAction=2), env.dev)     # Turn Off
    env.p._tick(clock.t)
    assert env.dev.pluginProps["keptOff"] == "true"
    standby_drop(env)
    power_returns(env)
    assert not env.p.restores
    clock.t += 10
    env.p.actionControlDevice(types.SimpleNamespace(deviceAction=1), env.dev)     # Turn On again
    env.p._tick(clock.t)
    assert env.dev.pluginProps["keptOff"] == ""
    standby_drop(env)
    assert env.dev.id in env.p.restores


def test_sleep_mode_low_power_is_not_mistaken_for_standby(env):
    client, clock = with_plug(env)
    env.p.facts[env.dev.id]["mode"] = "sleep"
    standby_drop(env, was=2.8, now=2.2)
    assert not env.p.restores


def test_a_power_reading_that_stays_up_does_nothing(env):
    client, clock = with_plug(env)
    standby_drop(env, was=4.1, now=7.3)
    assert not env.p.restores


@pytest.mark.parametrize("watts, ok", [("2.5", True), ("1", True), ("x", False), ("0", False), ("", False)])
def test_standby_threshold_is_checked(env, watts, ok):
    result = env.p.validateDeviceConfigUi({"cid": "vsaqTESTCID0001", "standbyWatts": watts}, "levoitPurifier", 101)
    assert result[0] is ok
