#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    levoit_logic.py
# Description: Every decision the Levoit Purifier plugin makes, free of Indigo
#              and of pyvesync, so the tests drive it directly: reading a
#              purifier's state into Indigo states, checking commands,
#              settings and credentials, and the words the log uses.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026 13:30 BST
# Version:     1.0.0

import os
from datetime import datetime

# Core 400S, from pyvesync 3.4.2's device map (VeSyncAirBypass). Other
# purifiers on the same class share these; a purifier whose own lists differ
# is checked against its own lists by vesync_client before anything is sent.
MODES = ("auto", "manual", "sleep")
FAN_LEVELS = (1, 2, 3, 4)
AUTO_PREFERENCES = ("default", "efficient", "quiet")

MODE_WORDS = {"auto": "automatic", "manual": "manual", "sleep": "sleep"}
AUTO_PREFERENCE_WORDS = {"default": "default", "efficient": "efficient", "quiet": "quiet"}

# pyvesync's AirQualityLevel: 1 excellent .. 4 poor, -1 or None unknown.
AIR_QUALITY_TEXT = {1: "excellent", 2: "good", 3: "moderate", 4: "poor"}

# The commands a person (an action, a dashboard) can send, and what each takes.
COMMANDS = ("power", "fanSpeed", "mode", "autoPreference", "childLock", "display",
            "timer", "resetFilter")

POLL_DEFAULT_SECONDS = 60
POLL_MIN_SECONDS = 30            # VeSync rate-limits accounts that poll harder
TIMER_MAX_MINUTES = 24 * 60
FILTER_LOW_PERCENT = 10

DEFAULT_COUNTRY = "GB"
SECRETS_KEYS = ("VESYNC_USERNAME", "VESYNC_PASSWORD", "VESYNC_COUNTRY")

# Rewritten on every poll, so its history is noise to the SQL Logger.
SQL_LOGGER_CHURN_STATES = ("lastUpdate",)


# ── settings and credentials ─────────────────────────────────────────────

def read_secrets(path):
    """Read the VeSync keys straight from IndigoSecrets.py on disk.

    Compiled from the SOURCE each time, one key at a time, so a file holding
    only some of them keeps the rest, and a change to the file is picked up at
    the next Configure save or restart. An ordinary import is cached for the
    life of the plugin host, and importlib's loader goes through the bytecode
    cache, which keys on mtime and size only.
    """
    out = {k: "" for k in SECRETS_KEYS}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        namespace = {"__file__": path, "__name__": "levoit_plugin_secrets"}
        exec(compile(source, path, "exec"), namespace)
    except Exception:
        return out
    for key in out:
        try:
            out[key] = str(namespace.get(key, "") or "").strip()
        except Exception:
            out[key] = ""
    return out


def country_code(value):
    """Two upper-case letters, or GB. VeSync wants ISO 3166 alpha-2."""
    text = str(value or "").strip().upper()
    return text if len(text) == 2 and text.isalpha() else DEFAULT_COUNTRY


def poll_seconds(value):
    """(seconds, warning or None). Blank or nonsense falls back to the default,
    and anything under the minimum is raised to it."""
    try:
        seconds = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return POLL_DEFAULT_SECONDS, (f"the check interval is not a number, so {POLL_DEFAULT_SECONDS} "
                                      f"seconds is used")
    if seconds < POLL_MIN_SECONDS:
        return POLL_MIN_SECONDS, (f"VeSync limits how often an account can ask, so the check interval "
                                  f"is {POLL_MIN_SECONDS} seconds rather than {seconds}")
    return seconds, None


def redact_email(email):
    """c***@example.com - enough to tell accounts apart, not enough to read."""
    text = str(email or "").strip()
    if "@" not in text:
        return "***" if text else ""
    local, domain = text.split("@", 1)
    return f"{local[:1]}***@{domain}"


def merge_sql_logger_ignore(existing, extra=SQL_LOGGER_CHURN_STATES):
    """Return the new sqlLoggerIgnoreStates value, or None when nothing changes."""
    current = [t.strip() for t in str(existing or "").split(",") if t.strip()]
    if len(current) == 1 and current[0] == "*":
        return None
    have = {t.lower() for t in current}
    missing = [t for t in extra if t.lower() not in have]
    if not missing:
        return None
    return ", ".join(current + missing)


def as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


# ── commands ─────────────────────────────────────────────────────────────

def check_command(command, value, current=None):
    """Normalise a command's value, or raise ValueError saying what is wrong.

    `current` is the purifier's present state for the yes/no commands, so
    "toggle" can be turned into a definite on or off before it is sent.
    """
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}")
    if command in ("power", "childLock", "display"):
        text = str(value if value is not None else "").strip().lower()
        if text == "toggle":
            return not bool(current)
        if isinstance(value, bool):
            return value
        if text in ("on", "true", "1", "yes"):
            return True
        if text in ("off", "false", "0", "no"):
            return False
        raise ValueError("needs on, off or toggle")
    if command == "fanSpeed":
        try:
            level = int(float(str(value).strip()))
        except (TypeError, ValueError):
            raise ValueError("the fan speed must be 1, 2, 3 or 4") from None
        if level not in FAN_LEVELS:
            raise ValueError("the fan speed must be 1, 2, 3 or 4")
        return level
    if command == "mode":
        mode = str(value or "").strip().lower()
        if mode not in MODES:
            raise ValueError("the mode must be auto, manual or sleep")
        return mode
    if command == "autoPreference":
        pref = str(value or "").strip().lower()
        if pref not in AUTO_PREFERENCES:
            raise ValueError("the automatic setting must be default, efficient or quiet")
        return pref
    if command == "timer":
        try:
            minutes = int(float(str(value).strip()))
        except (TypeError, ValueError):
            raise ValueError("the sleep timer needs a number of minutes, or 0 to clear it") from None
        if not 0 <= minutes <= TIMER_MAX_MINUTES:
            raise ValueError(f"the sleep timer must be 0 to {TIMER_MAX_MINUTES} minutes")
        return minutes
    return None                                    # resetFilter takes nothing


def command_words(command, value):
    """What a command did, as part of a sentence: 'fan speed set to 3'."""
    on_off = "on" if value else "off"
    if command == "power":
        return f"switched {on_off}"
    if command == "fanSpeed":
        return f"fan speed set to {value}"
    if command == "mode":
        return f"set to {MODE_WORDS.get(value, value)} mode"
    if command == "autoPreference":
        return f"automatic mode set to {AUTO_PREFERENCE_WORDS.get(value, value)}"
    if command == "childLock":
        return f"child lock {on_off}"
    if command == "display":
        return f"display {on_off}"
    if command == "timer":
        if not value:
            return "sleep timer cleared"
        return f"set to switch off in {duration_words(value)}"
    if command == "resetFilter":
        return "filter life reset to 100%"
    return command


def command_request(command, value):
    """What a command asks for, to follow 'could not': 'set the fan speed to 3'."""
    on_off = "on" if value else "off"
    if command == "power":
        return f"switch it {on_off}"
    if command == "fanSpeed":
        return f"set the fan speed to {value}"
    if command == "mode":
        return f"put it in {MODE_WORDS.get(value, value)} mode"
    if command == "autoPreference":
        return f"set automatic mode to {AUTO_PREFERENCE_WORDS.get(value, value)}"
    if command == "childLock":
        return f"turn the child lock {on_off}"
    if command == "display":
        return f"turn the display {on_off}"
    if command == "timer":
        if not value:
            return "clear the sleep timer"
        return f"set it to switch off in {duration_words(value)}"
    if command == "resetFilter":
        return "reset the filter life"
    return command


def command_expects(command, value):
    """The states a command should leave behind, or {} where the states
    cannot show it (timer, filter reset).

    VESYNC'S STATUS LAGS A COMMAND BY MINUTES (measured 24-10-2026 on a real
    Core 400S): the purifier's display changed at once, but getPurifierStatus
    went on reporting the old setting for 1.5 to 4 minutes. Read straight
    back, every command looked ignored. So after VeSync accepts a command the
    plugin holds these values (hold_overlay) until the status agrees.
    """
    if command == "power":
        return {"onOffState": bool(value)}
    if command == "fanSpeed":                     # pyvesync: a speed also means manual and on
        return {"fanLevel": int(value), "mode": "manual", "onOffState": True}
    if command == "mode":
        return {"mode": value, "onOffState": True}
    if command == "autoPreference":
        return {"autoPreference": value}
    if command == "childLock":
        return {"childLock": bool(value)}
    if command == "display":
        return {"displayOn": bool(value)}
    return {}


def command_took(command, value, facts):
    """Does VeSync's status show the command yet? True / False, or None
    where the states cannot tell (timer, filter reset)."""
    expect = command_expects(command, value)
    if not expect:
        return None
    return all(facts.get(k) == v for k, v in expect.items())


def add_holds(holds, command, value, status, now, seconds):
    """The holds for one purifier after VeSync accepts a command.

    holds maps a state key to {"value", "until", "request", "proven"}. Each
    key is held on its own, so a later command (the display) does not drop an
    earlier one still waiting (the automatic setting) - measured 24-10-2026,
    when that let VeSync's stale status show Default for 80 s after Quiet.

    "proven" says whether VeSync's status agreeing would mean anything. If
    the status ALREADY reads the asked-for value when the command goes, it is
    stale - an off pressed just before may still land - so agreement proves
    nothing until the status has been seen to read something else.
    """
    out = dict(holds or {})
    request = command_request(command, value)
    for key, want in command_expects(command, value).items():
        out[key] = {"value": want, "until": now + seconds, "request": request,
                    "proven": status.get(key) != want}
    return out


def hold_overlay(facts, holds, now):
    """Apply a purifier's holds to the facts VeSync reports.

    Returns (facts to write, holds still running, requests that ran out
    without VeSync's status ever agreeing). A held key is released when the
    status agrees and that agreement is proven, or when its time is up.
    """
    if not holds:
        return facts, {}, []
    merged = dict(facts)
    kept = {}
    expired = []
    for key, h in holds.items():
        seen = facts.get(key)
        proven = h["proven"] or seen != h["value"]
        if seen == h["value"]:
            if proven or now >= h["until"]:
                continue                               # caught up (or nothing to wait for)
        elif now >= h["until"]:
            if h["request"] not in expired:
                expired.append(h["request"])           # gives way to what VeSync says
            continue
        merged[key] = h["value"]
        kept[key] = dict(h, proven=proven)
    return merged, kept, expired


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def plug_power_returned(before, after):
    """Has the plug a purifier runs from just had its power come back?

    before / after are {"on": bool, "states": {...}} for the plug. True when
    its uptime went DOWN (it restarted: the socket lost power - measured on a
    Shelly Plus Plug UK, whose sysUptime restarts from 0), when it came back
    online, or when its relay was switched on. A Core 400S comes back OFF
    after any of those (CliveS, 24-10-2026), so each is a reason to switch it
    back on.
    """
    b, a = before.get("states") or {}, after.get("states") or {}
    up_b, up_a = _num(b.get("sysUptime")), _num(a.get("sysUptime"))
    if up_b is not None and up_a is not None and up_a < up_b:
        return True
    if b.get("deviceOnline") is False and a.get("deviceOnline") is True:
        return True
    return before.get("on") is False and after.get("on") is True


STANDBY_WATTS = 2.5     # measured 24-10-2026 on a Core 400S's Shelly plug:
                        # standby 1.6-1.7 W, running 3.2 W and up


def plug_watts(states):
    return _num((states or {}).get("powerWatts"))


def plug_dropped_to_standby(before, after, standby=STANDBY_WATTS):
    """Did the purifier on this plug just go from running to standby?

    Measured 24-10-2026: unplugged and moved, the purifier came back OFF and
    its plug read 1.7 W against about 4 W running - within one 30-second plug
    reading. VeSync went on reporting it ON for over six minutes, and the
    plug's own restart counter only refreshes every five minutes, so this is
    the fast, honest signal.
    """
    was, now = plug_watts(before.get("states")), plug_watts(after.get("states"))
    return was is not None and now is not None and was >= standby > now


def duration_words(minutes):
    """90 -> '1 hour 30 minutes', 1 -> '1 minute'."""
    hours, mins = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins or not parts:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


# ── reading a purifier ───────────────────────────────────────────────────

def _text(value):
    """A pyvesync StrEnum or str as a plain lower-case string ('' for None)."""
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().lower()


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def air_quality(level):
    """(1-4, words) from pyvesync's air_quality_level; (0, 'unknown') otherwise."""
    level = _int(level, 0)
    if level in AIR_QUALITY_TEXT:
        return level, AIR_QUALITY_TEXT[level]
    return 0, "unknown"


def decode(device, now=None):
    """The Indigo states for one pyvesync purifier, read from device.state.

    Plain values only (int, bool, str), so the plugin can compare and write
    them without knowing anything about pyvesync's enums.
    """
    state = device.state
    online = _text(getattr(state, "connection_status", None)) == "online"
    level, words = air_quality(getattr(state, "air_quality_level", None))
    fan = _int(getattr(state, "fan_level", 0))
    if fan not in FAN_LEVELS:
        fan = _int(getattr(state, "fan_set_level", 0))
    display = getattr(state, "display_status", None)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "onOffState":     _text(getattr(state, "device_status", None)) in ("on", "running"),
        "mode":           _text(getattr(state, "mode", None)) or "unknown",
        "fanLevel":       fan if fan in FAN_LEVELS else 0,
        "pm25":           _int(getattr(state, "pm25", 0)),
        "airQuality":     level,
        "airQualityText": words,
        "filterLife":     max(0, min(100, _int(getattr(state, "filter_life", 0)))),
        "childLock":      bool(getattr(state, "child_lock", False)),
        "displayOn":      _text(display) == "on",
        "autoPreference": _text(getattr(state, "auto_preference_type", None)) or "unknown",
        "deviceOnline":   online,
        "lastUpdate":     stamp,
    }


def ui_value(key, value):
    """The text Indigo shows beside a state, or None for its own default."""
    if key == "pm25":
        return f"{value} ug/m3"
    if key == "filterLife":
        return f"{value}%"
    if key == "airQuality":
        return AIR_QUALITY_TEXT.get(value, "unknown")
    if key == "mode":
        return MODE_WORDS.get(value, value)
    return None


def change_lines(name, before, after):
    """Plain-English log lines for what changed between two polls.

    Nothing on the first poll (before is None), and nothing for readings that
    drift on their own, such as PM2.5: those are states, not news.
    """
    if before is None:
        return []
    lines = []
    if before.get("deviceOnline") and not after.get("deviceOnline"):
        lines.append(("warning", f"{name} has dropped off VeSync, so its readings are not current."))
        return lines
    if after.get("deviceOnline") and not before.get("deviceOnline"):
        lines.append(("info", f"{name} is back on VeSync."))
    if before.get("onOffState") != after.get("onOffState"):
        lines.append(("info", f"{name} is now {'on' if after.get('onOffState') else 'off'}."))
    if after.get("onOffState") and before.get("mode") != after.get("mode") and after.get("mode") != "unknown":
        lines.append(("info", f"{name} is now in {MODE_WORDS.get(after['mode'], after['mode'])} mode."))
    was_low = before.get("filterLife", 100) < FILTER_LOW_PERCENT
    is_low = after.get("filterLife", 100) < FILTER_LOW_PERCENT
    if is_low and not was_low:
        lines.append(("warning", f"{name}: the filter has {after['filterLife']}% of its life left, "
                                 f"so it is time to order a new one."))
    elif was_low and not is_low:
        lines.append(("info", f"{name}: the filter reads {after['filterLife']}% now, so a new one has gone in."))
    return lines


def device_summary(device):
    """One plain-English line about a purifier on the account, for the log."""
    facts = decode(device)
    name = getattr(device, "device_name", "") or "A purifier"
    model = getattr(device, "device_type", "") or "unknown model"
    if not facts["deviceOnline"]:
        return f"{name} ({model}) is on the account but not connected to VeSync."
    power = "on" if facts["onOffState"] else "off"
    return (f"{name} ({model}) is {power}, in {MODE_WORDS.get(facts['mode'], facts['mode'])} mode, "
            f"PM2.5 {facts['pm25']} ug/m3 ({facts['airQualityText']}), filter {facts['filterLife']}%.")
