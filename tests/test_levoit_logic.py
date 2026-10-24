#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_levoit_logic.py
# Description: levoit_logic: decoding a real pyvesync purifier, command checks,
#              settings, credentials, and the plain-English log lines.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0

from datetime import datetime
from types import SimpleNamespace

import pytest

from conftest import real_purifier
import levoit_logic as L


NOW = datetime(2026, 10, 24, 13, 30, 5)


# ── decode, against the real library ──

def test_decode_reads_a_real_core_400s():
    facts = L.decode(real_purifier(), now=NOW)
    assert facts == {
        "onOffState": True, "mode": "auto", "fanLevel": 2, "pm25": 4, "airQuality": 1,
        "airQualityText": "excellent", "filterLife": 87, "childLock": False, "displayOn": True,
        "autoPreference": "efficient", "deviceOnline": True, "lastUpdate": "2026-10-24 13:30:05",
    }


def test_decode_gives_plain_python_values_not_pyvesync_enums():
    for key, value in L.decode(real_purifier(), now=NOW).items():
        assert type(value) in (bool, int, str), (key, type(value))


def test_decode_off_and_poor_air():
    facts = L.decode(real_purifier(status={"enabled": False, "air_quality": 4, "air_quality_value": 61,
                                           "display": False, "child_lock": True, "mode": "sleep"}))
    assert facts["onOffState"] is False and facts["mode"] == "sleep"
    assert (facts["airQuality"], facts["airQualityText"], facts["pm25"]) == (4, "poor", 61)
    assert facts["displayOn"] is False and facts["childLock"] is True


def test_decode_unknown_air_quality_is_zero_not_minus_one():
    # pyvesync reads an air quality it does not recognise as -1 (UNKNOWN).
    facts = L.decode(real_purifier(status={"air_quality": 9}))
    assert (facts["airQuality"], facts["airQualityText"]) == (0, "unknown")


def test_decode_offline_purifier_from_the_device_list():
    facts = L.decode(real_purifier(list_entry={"connectionStatus": "offline", "deviceStatus": "off"},
                                   status=False))
    assert facts["deviceOnline"] is False and facts["onOffState"] is False
    assert facts["mode"] == "unknown" and facts["autoPreference"] == "unknown"


def test_decode_after_set_fan_speed_reads_the_strenum_mode_as_text():
    pv = pytest.importorskip("pyvesync.const")
    device = real_purifier()
    device.state.mode = pv.PurifierModes.MANUAL       # what set_fan_speed() stores
    device.state.fan_level = 3
    facts = L.decode(device)
    assert facts["mode"] == "manual" and facts["fanLevel"] == 3


def test_decode_tolerates_missing_attributes():
    device = SimpleNamespace(state=SimpleNamespace(device_status="on", connection_status="online"))
    facts = L.decode(device, now=NOW)
    assert facts["onOffState"] and facts["fanLevel"] == 0 and facts["filterLife"] == 0


def test_filter_life_is_clamped():
    device = SimpleNamespace(state=SimpleNamespace(filter_life=140))
    assert L.decode(device)["filterLife"] == 100


# ── commands ──

@pytest.mark.parametrize("command, raw, current, expected", [
    ("power", "on", None, True), ("power", "off", True, False), ("power", "toggle", True, False),
    ("power", "toggle", False, True), ("power", True, None, True),
    ("childLock", "on", None, True), ("display", "toggle", True, False),
    ("fanSpeed", "3", None, 3), ("fanSpeed", 1, None, 1), ("fanSpeed", "4.0", None, 4),
    ("mode", "Sleep", None, "sleep"), ("mode", "auto", None, "auto"),
    ("autoPreference", "quiet", None, "quiet"),
    ("timer", "90", None, 90), ("timer", "0", None, 0),
    ("resetFilter", None, None, None),
])
def test_check_command_accepts(command, raw, current, expected):
    assert L.check_command(command, raw, current) == expected


@pytest.mark.parametrize("command, raw", [
    ("power", "maybe"), ("power", None), ("fanSpeed", "5"), ("fanSpeed", "0"), ("fanSpeed", "fast"),
    ("fanSpeed", None), ("mode", "turbo"), ("mode", ""), ("autoPreference", "loud"),
    ("timer", "-1"), ("timer", "1441"), ("timer", "soon"), ("nightlight", "on"),
])
def test_check_command_refuses(command, raw):
    with pytest.raises(ValueError):
        L.check_command(command, raw)


def test_every_command_has_words():
    for command in L.COMMANDS:
        for words in (L.command_words(command, 1), L.command_request(command, 1)):
            assert words and words != command and "|" not in words and "=" not in words


@pytest.mark.parametrize("command, value, facts, took", [
    ("display", True, {"displayOn": False}, False),
    ("display", False, {"displayOn": False}, True),
    ("power", True, {"onOffState": True}, True),
    ("fanSpeed", 3, {"fanLevel": 3, "mode": "manual", "onOffState": True}, True),
    ("fanSpeed", 3, {"fanLevel": 3, "mode": "auto", "onOffState": True}, False),
    ("mode", "sleep", {"mode": "auto"}, False),
    ("autoPreference", "quiet", {"autoPreference": "quiet"}, True),
    ("childLock", True, {"childLock": False}, False),
    ("timer", 30, {}, None),
    ("resetFilter", None, {}, None),
])
def test_command_took(command, value, facts, took):
    assert L.command_took(command, value, facts) is took


def test_command_took_covers_every_command():
    assert set(L.COMMANDS) == {"power", "fanSpeed", "mode", "autoPreference", "childLock", "display",
                               "timer", "resetFilter"}


def test_every_expected_state_is_a_real_state():
    states = set(L.decode(real_purifier()))
    for command in L.COMMANDS:
        assert set(L.command_expects(command, 1)) <= states, command


def held(command, value, status, now=0.0, holds=None):
    return L.add_holds(holds, command, value, status, now, 100.0)


def test_hold_overlay_holds_while_vesync_is_behind():
    holds = held("display", False, {"displayOn": True})
    facts, kept, expired = L.hold_overlay({"displayOn": True, "pm25": 9}, holds, 50.0)
    assert facts == {"displayOn": False, "pm25": 9} and "displayOn" in kept and expired == []


def test_hold_overlay_releases_when_vesync_catches_up():
    holds = held("display", False, {"displayOn": True})
    assert L.hold_overlay({"displayOn": False}, holds, 50.0) == ({"displayOn": False}, {}, [])


def test_hold_overlay_expired_gives_way_and_names_the_request():
    holds = held("display", False, {"displayOn": True})
    assert L.hold_overlay({"displayOn": True}, holds, 100.0) == ({"displayOn": True}, {}, ["turn the display off"])
    assert L.hold_overlay({"displayOn": True}, {}, 50.0) == ({"displayOn": True}, {}, [])


def test_each_setting_is_held_on_its_own():
    # 24-10-2026: Quiet, then the display; the display's hold must not drop Quiet's.
    holds = held("autoPreference", "quiet", {"autoPreference": "default", "displayOn": True})
    holds = held("display", False, {"autoPreference": "default", "displayOn": True}, 10.0, holds)
    facts, kept, _ = L.hold_overlay({"autoPreference": "default", "displayOn": True}, holds, 60.0)
    assert facts == {"autoPreference": "quiet", "displayOn": False} and set(kept) == {"autoPreference", "displayOn"}


def test_agreement_with_a_stale_status_proves_nothing():
    # 24-10-2026: display off then on within seconds. The status still read on
    # from before the off, so "on" agreed at once - and the off landed later.
    holds = held("display", True, {"displayOn": True})
    facts, kept, _ = L.hold_overlay({"displayOn": True}, holds, 30.0)
    assert facts["displayOn"] is True and "displayOn" in kept          # not released
    facts, kept, _ = L.hold_overlay({"displayOn": False}, kept, 90.0)   # the off lands
    assert facts["displayOn"] is True and "displayOn" in kept          # still shows on
    assert L.hold_overlay({"displayOn": True}, kept, 120.0)[1] == {}   # now a real catch-up


def test_an_unproven_hold_that_agrees_at_the_end_goes_quietly():
    holds = held("display", True, {"displayOn": True})
    assert L.hold_overlay({"displayOn": True}, holds, 100.0) == ({"displayOn": True}, {}, [])


def test_a_new_command_replaces_the_hold_on_the_same_setting():
    holds = held("fanSpeed", 4, {"fanLevel": 2, "mode": "auto", "onOffState": True})
    holds = held("fanSpeed", 1, {"fanLevel": 2, "mode": "auto", "onOffState": True}, 5.0, holds)
    assert holds["fanLevel"]["value"] == 1 and holds["fanLevel"]["request"] == "set the fan speed to 1"


def test_hold_overlay_does_not_change_what_it_was_given():
    holds = held("display", False, {"displayOn": True})
    facts = {"displayOn": True}
    before = {k: dict(v) for k, v in holds.items()}
    L.hold_overlay(facts, holds, 50.0)
    assert facts == {"displayOn": True} and holds == before


def test_request_words_follow_could_not():
    assert L.command_request("power", False) == "switch it off"
    assert L.command_request("mode", "auto") == "put it in automatic mode"
    assert L.command_request("timer", 0) == "clear the sleep timer"


@pytest.mark.parametrize("minutes, words", [
    (1, "1 minute"), (2, "2 minutes"), (60, "1 hour"), (90, "1 hour 30 minutes"), (120, "2 hours"),
])
def test_duration_words(minutes, words):
    assert L.duration_words(minutes) == words


def test_timer_words():
    assert L.command_words("timer", 0) == "sleep timer cleared"
    assert L.command_words("timer", 61) == "set to switch off in 1 hour 1 minute"


# ── settings ──

@pytest.mark.parametrize("raw, seconds, warns", [
    ("60", 60, False), (120, 120, False), ("10", 30, True), ("", 60, True), ("abc", 60, True),
    (None, 60, True), ("45.7", 45, False),
])
def test_poll_seconds(raw, seconds, warns):
    got, warning = L.poll_seconds(raw)
    assert got == seconds and bool(warning) == warns


@pytest.mark.parametrize("raw, code", [("gb", "GB"), ("US", "US"), ("", "GB"), (None, "GB"),
                                       ("GBR", "GB"), ("1A", "GB"), (" de ", "DE")])
def test_country_code(raw, code):
    assert L.country_code(raw) == code


def test_redact_email_never_shows_the_local_part():
    assert L.redact_email("clive.smith@example.com") == "c***@example.com"
    assert L.redact_email("") == ""
    assert L.redact_email("nonsense") == "***"


def test_merge_sql_logger_ignore():
    assert L.merge_sql_logger_ignore("") == "lastUpdate"
    assert L.merge_sql_logger_ignore("foo") == "foo, lastUpdate"
    assert L.merge_sql_logger_ignore("lastupdate") is None
    assert L.merge_sql_logger_ignore("*") is None


# ── credentials ──

def test_read_secrets_one_key_at_a_time(tmp_path):
    path = tmp_path / "IndigoSecrets.py"
    path.write_text('VESYNC_USERNAME = "a@b.c"\nOTHER = 1\n', encoding="utf-8")
    assert L.read_secrets(str(path)) == {"VESYNC_USERNAME": "a@b.c", "VESYNC_PASSWORD": "", "VESYNC_COUNTRY": ""}


def test_read_secrets_sees_a_same_length_change_at_once(tmp_path):
    path = tmp_path / "IndigoSecrets.py"
    path.write_text('VESYNC_PASSWORD = "aaaa"\n', encoding="utf-8")
    assert L.read_secrets(str(path))["VESYNC_PASSWORD"] == "aaaa"
    path.write_text('VESYNC_PASSWORD = "bbbb"\n', encoding="utf-8")
    assert L.read_secrets(str(path))["VESYNC_PASSWORD"] == "bbbb"


def test_read_secrets_missing_or_broken_file(tmp_path):
    assert L.read_secrets(str(tmp_path / "nope.py"))["VESYNC_USERNAME"] == ""
    bad = tmp_path / "IndigoSecrets.py"
    bad.write_text("this is not python(", encoding="utf-8")
    assert L.read_secrets(str(bad))["VESYNC_PASSWORD"] == ""


# ── log lines ──

def _facts(**kw):
    base = {"onOffState": True, "mode": "auto", "filterLife": 50, "deviceOnline": True}
    base.update(kw)
    return base


def test_no_lines_on_the_first_poll():
    assert L.change_lines("Bedroom", None, _facts()) == []


def test_power_mode_and_filter_lines():
    lines = L.change_lines("Bedroom", _facts(onOffState=False), _facts(mode="sleep", filterLife=9))
    texts = [t for _, t in lines]
    assert "Bedroom is now on." in texts
    assert "Bedroom is now in sleep mode." in texts
    assert any(level == "warning" and "9% of its life left" in t for level, t in lines)


def test_offline_says_so_and_nothing_else():
    lines = L.change_lines("Bedroom", _facts(), _facts(deviceOnline=False, onOffState=False))
    assert lines == [("warning", "Bedroom has dropped off VeSync, so its readings are not current.")]


def test_new_filter_is_noticed():
    lines = L.change_lines("Bedroom", _facts(filterLife=3), _facts(filterLife=100))
    assert lines == [("info", "Bedroom: the filter reads 100% now, so a new one has gone in.")]


def test_every_line_is_a_plain_sentence():
    pairs = [(_facts(onOffState=False, deviceOnline=False), _facts(mode="manual", filterLife=5)),
             (_facts(filterLife=5), _facts(filterLife=95)), (_facts(), _facts(deviceOnline=False))]
    for before, after in pairs:
        for _, text in L.change_lines("Bedroom", before, after):
            assert text.endswith(".") and "|" not in text and "=" not in text
            text.encode("ascii")


def test_device_summary_reads_like_a_sentence():
    line = L.device_summary(real_purifier())
    assert line == ("Bedroom Purifier (Core400S) is on, in automatic mode, PM2.5 4 ug/m3 (excellent), "
                    "filter 87%.")
    off = L.device_summary(real_purifier(list_entry={"connectionStatus": "offline"}, status=False))
    assert off == "Bedroom Purifier (Core400S) is on the account but not connected to VeSync."


def test_ui_values():
    assert L.ui_value("pm25", 12) == "12 ug/m3"
    assert L.ui_value("filterLife", 40) == "40%"
    assert L.ui_value("airQuality", 3) == "moderate"
    assert L.ui_value("mode", "auto") == "automatic"
    assert L.ui_value("childLock", True) is None


@pytest.mark.parametrize("before, after, returned", [
    ({"on": True, "states": {"sysUptime": 14227}}, {"on": True, "states": {"sysUptime": 3}}, True),
    ({"on": True, "states": {"sysUptime": 100}}, {"on": True, "states": {"sysUptime": 130}}, False),
    ({"on": True, "states": {"deviceOnline": False}}, {"on": True, "states": {"deviceOnline": True}}, True),
    ({"on": False, "states": {}}, {"on": True, "states": {}}, True),
    ({"on": True, "states": {}}, {"on": False, "states": {}}, False),
    ({"on": True, "states": {"sysUptime": "x"}}, {"on": True, "states": {"sysUptime": 1}}, False),
    ({}, {}, False),
])
def test_plug_power_returned(before, after, returned):
    assert L.plug_power_returned(before, after) is returned


@pytest.mark.parametrize("was, now, dropped", [
    (4.1, 1.7, True), (3.2, 2.4, True), (4.1, 3.9, False), (1.7, 1.6, False), (1.7, 4.0, False),
    (None, 1.7, False), ("x", 1.7, False),
])
def test_plug_dropped_to_standby(was, now, dropped):
    before = {"states": {"powerWatts": was}}
    after = {"states": {"powerWatts": now}}
    assert L.plug_dropped_to_standby(before, after) is dropped
