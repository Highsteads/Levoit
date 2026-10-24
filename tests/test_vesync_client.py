#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_vesync_client.py
# Description: vesync_client: sign-in, refresh, every command's mapping onto
#              pyvesync, read-back after a command, error translation, timeouts
#              and closing - against a fake async manager, plus a check that
#              every pyvesync method the client calls exists on the REAL
#              VeSyncAirBypass class and is a coroutine.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0

import asyncio
import inspect
import re
from types import SimpleNamespace

import pytest

from conftest import SRC, real_purifier
import vesync_client as vc


# Error classes named as pyvesync's are; the client recognises them by name.
class VeSyncError(Exception):
    pass


class VeSyncLoginError(VeSyncError):
    pass


class VeSyncTokenError(VeSyncError):
    pass


class VeSyncRateLimitError(VeSyncError):
    pass


class VeSyncServerError(VeSyncError):
    pass


class FakeDevice:
    def __init__(self, cid="cid1", name="Bedroom Purifier"):
        self.cid, self.device_name, self.device_type = cid, name, "Core400S"
        self.fan_levels = [1, 2, 3, 4]
        self.modes = ["sleep", "manual", "auto"]
        self.auto_preferences = ["default", "efficient", "quiet"]
        self.state = SimpleNamespace(auto_room_size=538, timer=None, device_status="on",
                                     connection_status="online")
        self.calls = []
        self.answer = True
        self.updates = 0
        self.existing_timer = None

    def _record(self, name, *args, **kw):
        self.calls.append((name, args, kw))
        return self.answer

    async def toggle_switch(self, toggle):
        return self._record("toggle_switch", toggle)

    async def set_fan_speed(self, speed):
        return self._record("set_fan_speed", speed)

    async def set_mode(self, mode):
        return self._record("set_mode", mode)

    async def set_auto_preference(self, preference, room_size=800):
        return self._record("set_auto_preference", preference, room_size=room_size)

    async def toggle_child_lock(self, toggle):
        return self._record("toggle_child_lock", toggle)

    async def toggle_display(self, mode):
        return self._record("toggle_display", mode)

    async def set_timer(self, duration):
        return self._record("set_timer", duration)

    async def get_timer(self):
        self.calls.append(("get_timer", (), {}))
        self.state.timer = self.existing_timer

    async def clear_timer(self):
        return self._record("clear_timer")

    async def reset_filter(self):
        return self._record("reset_filter")

    async def update(self):
        self.updates += 1


class FakeManager:
    instances = []

    def __init__(self, username, password, country_code):
        self.args = (username, password, country_code)
        self.devices = SimpleNamespace(air_purifiers=[FakeDevice()])
        self.login_result = True
        self.login_error = None
        self.update_error = None
        self.logins = self.updates = 0
        self.closed = False
        self.update_delay = 0
        FakeManager.instances.append(self)

    async def login(self):
        self.logins += 1
        if self.login_error:
            raise self.login_error
        return self.login_result

    async def update(self):
        self.updates += 1
        if self.update_delay:
            await asyncio.sleep(self.update_delay)
        if self.update_error:
            raise self.update_error

    async def __aexit__(self, *exc):
        self.closed = True


@pytest.fixture
def client():
    FakeManager.instances = []
    c = vc.VeSyncClient("someone@example.com", "pw", "GB", manager_factory=FakeManager)
    yield c
    c.close()


def manager():
    return FakeManager.instances[-1]


# ── sign-in and refresh ──

def test_refresh_signs_in_once_and_returns_purifiers(client):
    purifiers = client.refresh()
    assert [p.cid for p in purifiers] == ["cid1"]
    assert manager().args == ("someone@example.com", "pw", "GB")
    client.refresh()
    assert len(FakeManager.instances) == 1 and manager().logins == 1 and manager().updates == 2


def test_login_returning_false_is_an_auth_error(client):
    def factory(*a):
        m = FakeManager(*a)
        m.login_result = False
        return m
    client.factory = factory
    with pytest.raises(vc.AuthError):
        client.refresh()
    assert client.manager is None


def test_login_error_is_an_auth_error_and_signs_in_afresh_next_time(client):
    def factory(*a):
        m = FakeManager(*a)
        if len(FakeManager.instances) == 1:
            m.login_error = VeSyncLoginError("bad password")
        return m
    client.factory = factory
    with pytest.raises(vc.AuthError, match="bad password"):
        client.refresh()
    assert client.refresh()[0].cid == "cid1"
    assert len(FakeManager.instances) == 2


@pytest.mark.parametrize("error, ours", [
    (VeSyncRateLimitError("slow down"), vc.RateLimited),
    (VeSyncServerError("500"), vc.Unavailable),
    (OSError("no route to host"), vc.Unavailable),
    (VeSyncTokenError("expired"), vc.Unavailable),
])
def test_errors_are_translated(client, error, ours):
    client.refresh()
    manager().update_error = error
    with pytest.raises(ours):
        client.refresh()


def test_token_error_drops_the_session_so_the_next_call_signs_in_again(client):
    client.refresh()
    manager().update_error = VeSyncTokenError("expired")
    with pytest.raises(vc.Unavailable):
        client.refresh()
    assert client.manager is None
    client.refresh()
    assert len(FakeManager.instances) == 2


def test_a_programming_error_is_not_dressed_up_as_an_outage(client):
    client.refresh()
    manager().update_error = KeyError("bug")
    with pytest.raises(KeyError):
        client.refresh()


def test_a_slow_answer_times_out(client):
    client.timeout = 0.05
    client.refresh()
    manager().update_delay = 1
    with pytest.raises(vc.Unavailable, match="in time"):
        client.refresh()


def test_purifiers_without_asking(client):
    assert client.purifiers() == []
    client.refresh()
    assert [p.cid for p in client.purifiers()] == ["cid1"]
    assert manager().updates == 1


# ── commands ──

@pytest.mark.parametrize("command, value, call", [
    ("power", True, ("toggle_switch", (True,), {})),
    ("power", False, ("toggle_switch", (False,), {})),
    ("fanSpeed", 3, ("set_fan_speed", (3,), {})),
    ("mode", "sleep", ("set_mode", ("sleep",), {})),
    ("autoPreference", "quiet", ("set_auto_preference", ("quiet",), {"room_size": 538})),
    ("childLock", True, ("toggle_child_lock", (True,), {})),
    ("display", False, ("toggle_display", (False,), {})),
    ("timer", 90, ("set_timer", (5400,), {})),
    ("resetFilter", None, ("reset_filter", (), {})),
])
def test_each_command_calls_pyvesync_then_reads_back(client, command, value, call):
    device = client.send("cid1", command, value)
    assert device.calls == [call]
    assert device.updates == 1


def test_clearing_a_timer_reads_it_first(client):
    client.refresh()
    dev = manager().devices.air_purifiers[0]
    dev.existing_timer = SimpleNamespace(id=7)
    client.send("cid1", "timer", 0)
    assert [c[0] for c in dev.calls] == ["get_timer", "clear_timer"]


def test_clearing_when_no_timer_is_set_succeeds_without_a_call(client):
    device = client.send("cid1", "timer", 0)
    assert [c[0] for c in device.calls] == ["get_timer"]


def test_a_refused_command_is_reported_and_not_read_back(client):
    client.refresh()
    dev = manager().devices.air_purifiers[0]
    dev.answer = False
    with pytest.raises(vc.CommandRefused):
        client.send("cid1", "fanSpeed", 2)
    assert dev.updates == 0


def test_unknown_purifier(client):
    with pytest.raises(vc.NotFound):
        client.send("nope", "power", True)


def test_a_new_purifier_is_found_after_a_fresh_list(client):
    client.refresh()
    manager().devices.air_purifiers.append(FakeDevice("cid2", "Lounge"))
    assert client.send("cid2", "power", True).cid == "cid2"


@pytest.mark.parametrize("command, value", [("fanSpeed", 5), ("mode", "turbo"), ("autoPreference", "loud")])
def test_the_purifier_own_lists_are_checked_before_sending(client, command, value):
    client.refresh()
    dev = manager().devices.air_purifiers[0]
    with pytest.raises(vc.CommandRefused):
        client.send("cid1", command, value)
    assert dev.calls == []


def test_close_closes_the_session_and_loop_and_can_run_twice(client):
    client.refresh()
    m = manager()
    client.close()
    assert m.closed and client.loop.is_closed() and client.manager is None
    client.close()


def test_missing_library_says_how_to_fix_it(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def refuse(name, *a, **kw):
        if name == "pyvesync":
            raise ImportError("blocked")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(ImportError):
        import pyvesync  # noqa: F401  - prove the block works before trusting it
    c = vc.VeSyncClient("a@b.c", "pw")
    with pytest.raises(vc.NotInstalled, match="requirements.txt"):
        c.refresh()
    c.close()


# ── against the real library ──

def test_every_pyvesync_call_exists_on_the_real_core_400s_and_is_a_coroutine():
    device = real_purifier()
    source = open(f"{SRC}/vesync_client.py", encoding="utf-8").read()
    called = set(re.findall(r"await device\.(\w+)\(", source))
    assert called >= {"toggle_switch", "set_fan_speed", "set_mode", "set_auto_preference",
                      "toggle_child_lock", "toggle_display", "set_timer", "get_timer",
                      "clear_timer", "reset_filter", "update"}
    for name in called:
        assert inspect.iscoroutinefunction(getattr(device, name)), name


def test_real_manager_calls_exist_and_are_coroutines():
    pyvesync = pytest.importorskip("pyvesync")
    for name in ("login", "update", "__aexit__"):
        assert inspect.iscoroutinefunction(getattr(pyvesync.VeSync, name)), name
    params = inspect.signature(pyvesync.VeSync).parameters
    assert list(params)[:3] == ["username", "password", "country_code"]


def test_real_device_offers_the_lists_the_client_checks():
    device = real_purifier()
    assert vc.VeSyncClient._offered(device, "modes") == {"auto", "manual", "sleep"}
    assert vc.VeSyncClient._offered(device, "auto_preferences") == {"default", "efficient", "quiet"}
    assert list(device.fan_levels) == [1, 2, 3, 4]


def test_real_error_classes_are_recognised_by_name():
    errors = pytest.importorskip("pyvesync.utils.errors")
    assert isinstance(vc._translate(errors.VeSyncLoginError("x")), vc.AuthError)
    assert isinstance(vc._translate(errors.VeSyncRateLimitError()), vc.RateLimited)   # takes no message
    assert isinstance(vc._translate(errors.VeSyncTokenError("x")), vc.Unavailable)
    assert isinstance(vc._translate(errors.VeSyncServerError("x")), vc.Unavailable)
    aiohttp = pytest.importorskip("aiohttp")
    assert isinstance(vc._translate(aiohttp.ClientConnectionError("x")), vc.Unavailable)
