#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_config_contract.py
# Description: Ties the XML to the code: every setting is read, every callback
#              exists, the action choices are the values the logic accepts,
#              and the menus never offer a value Indigo drops.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0
#
# Indigo never reads a plugin's dialog XML back to the plugin, so a setting
# that nothing reads, or a menu pointing at a missing method, is invisible to
# every other test.

import ast
import os
import re
import xml.etree.ElementTree as ET

from conftest import SRC
import levoit_logic as L

PLUGIN_PY = os.path.join(SRC, "plugin.py")


def xml(name):
    return ET.parse(os.path.join(SRC, name)).getroot()


def code():
    return "\n".join(open(os.path.join(SRC, f), encoding="utf-8").read()
                     for f in ("plugin.py", "levoit_logic.py", "vesync_client.py"))


def plugin_methods():
    tree = ast.parse(open(PLUGIN_PY, encoding="utf-8").read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Plugin")
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}


def options(root, field_id):
    field = next(f for f in root.iter("Field") if f.get("id") == field_id)
    return {o.get("value") for o in field.iter("Option")}


def test_every_plugin_setting_is_read():
    text = code()
    for field in xml("PluginConfig.xml").iter("Field"):
        if field.get("type") in ("label", "separator"):
            continue
        assert re.search(rf'["\']{field.get("id")}["\']', text), f"{field.get('id')} is never read"


def test_every_device_setting_is_read():
    text = code()
    for field in xml("Devices.xml").iter("Field"):
        if field.get("type") in ("label", "separator"):
            continue
        assert re.search(rf'["\']{field.get("id")}["\']', text), f"{field.get('id')} is never read"


def test_every_action_field_is_read():
    text = code()
    for field in xml("Actions.xml").iter("Field"):
        if field.get("type") in ("label", "separator"):
            continue
        assert re.search(rf'props\.get\(["\']{field.get("id")}["\']', text), f"{field.get('id')} is never read"


def test_every_callback_exists():
    methods = plugin_methods()
    names = [e.text for name in ("Actions.xml", "MenuItems.xml", "PluginConfig.xml", "Devices.xml")
             for e in xml(name).iter("CallbackMethod")]
    names += [e.get("method") for name in ("PluginConfig.xml", "Devices.xml")
              for e in xml(name).iter("List") if e.get("method")]
    assert names
    for name in names:
        assert name in methods, f"{name} is named in the XML but not defined"


def test_action_choices_are_what_the_logic_accepts():
    actions = xml("Actions.xml")
    assert options(actions, "speed") == {str(n) for n in L.FAN_LEVELS}
    assert options(actions, "mode") == set(L.MODES)
    assert options(actions, "preference") == set(L.AUTO_PREFERENCES)
    for value in options(actions, "value"):
        L.check_command("childLock", value, current=False)


def test_device_actions_are_limited_to_purifiers():
    for action in xml("Actions.xml").iter("Action"):
        if action.get("id") != "refreshNow":
            assert action.get("deviceFilter") == "self.levoitPurifier", action.get("id")


def test_no_ui_path_on_actions():
    # Dashboards calls these same actions; none needs a hidden copy.
    for action in xml("Actions.xml").iter("Action"):
        assert action.get("uiPath") is None, action.get("id")


def test_no_menu_option_uses_an_empty_value():
    for name in ("PluginConfig.xml", "Actions.xml", "Devices.xml"):
        for option in xml(name).iter("Option"):
            assert option.get("value") != "", f"{name}: an Option with value '' is dropped by Indigo"


def test_state_ids_are_camel_case_ascii_and_not_reserved():
    for state in xml("Devices.xml").iter("State"):
        sid = state.get("id")
        assert re.fullmatch(r"[a-z][A-Za-z0-9]*", sid), sid
        assert sid not in ("onOffState", "sensorValue", "batteryLevel", "brightnessLevel"), sid


def test_yes_no_states_are_boolean():
    types = {s.get("id"): s.find("ValueType").text for s in xml("Devices.xml").iter("State")}
    for sid in ("childLock", "displayOn", "deviceOnline"):
        assert types[sid] == "Boolean", sid
    for sid in ("fanLevel", "pm25", "airQuality", "filterLife"):
        assert types[sid] == "Integer", sid


def test_the_device_is_a_relay():
    device = next(xml("Devices.xml").iter("Device"))
    assert (device.get("type"), device.get("id")) == ("relay", "levoitPurifier")


def test_churn_states_are_declared():
    declared = {s.get("id") for s in xml("Devices.xml").iter("State")}
    assert set(L.SQL_LOGGER_CHURN_STATES) <= declared


def test_requirements_pin_pyvesync_3_and_not_requests():
    lines = [ln.strip() for ln in open(os.path.join(SRC, "requirements.txt"), encoding="utf-8")
             if ln.strip() and not ln.startswith("#")]
    assert lines == ["pyvesync>=3.4,<4"]
