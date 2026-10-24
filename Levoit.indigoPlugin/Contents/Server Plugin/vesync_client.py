#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    vesync_client.py
# Description: The one place that talks to VeSync, through pyvesync 3.x. pyvesync
#              is asyncio and aiohttp; Indigo plugins are threads. So this owns
#              ONE event loop and runs every call to completion on it, from the
#              plugin's worker thread only. Nothing else may call it.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026 13:30 BST
# Version:     1.0.0
#
# Checked against pyvesync 3.4.2 as installed (24-10-2026), not from memory:
# VeSync(username, password, country_code=...); await login() -> bool, raising
# VeSyncLoginError; await update() fetches the device list then every device;
# devices.air_purifiers; the Core 400S is VeSyncAirBypass, whose control calls
# are all coroutines returning bool. clear_timer() only works once get_timer()
# has read the timer's id, and the aiohttp session is created inside the first
# call, so it belongs to this loop.

import asyncio

from levoit_logic import DEFAULT_COUNTRY

CALL_TIMEOUT_SECONDS = 30


class VeSyncProblem(Exception):
    """Anything that stopped a VeSync call. The message is fit for the log."""


class NotInstalled(VeSyncProblem):
    pass


class AuthError(VeSyncProblem):
    pass


class RateLimited(VeSyncProblem):
    pass


class Unavailable(VeSyncProblem):
    pass


class NotFound(VeSyncProblem):
    pass


class CommandRefused(VeSyncProblem):
    pass


def _class_names(exc):
    return {k.__name__ for k in type(exc).__mro__}


def _translate(exc):
    """Map a pyvesync, aiohttp or timeout error to one of ours, by class name,
    so this module never has to import pyvesync to recognise its errors."""
    names = _class_names(exc)
    if "VeSyncLoginError" in names:
        return AuthError(f"VeSync turned down the sign-in ({exc})")
    if "VeSyncRateLimitError" in names:
        return RateLimited("VeSync says the account is asking too often")
    if "VeSyncTokenError" in names:
        return Unavailable("the VeSync sign-in expired")
    if isinstance(exc, asyncio.TimeoutError) or "TimeoutError" in names:
        return Unavailable("VeSync did not answer in time")
    if "VeSyncError" in names or "ClientError" in names or isinstance(exc, OSError):
        return Unavailable(f"cannot reach VeSync ({type(exc).__name__}: {exc})")
    return None


def _default_factory(username, password, country_code):
    try:
        from pyvesync import VeSync
    except ImportError as exc:
        raise NotInstalled(
            "the pyvesync library is not installed. Indigo installs it from requirements.txt when the "
            "plugin starts; restart the plugin, and if this stays, check the plugin's log for pip errors"
        ) from exc
    return VeSync(username, password, country_code=country_code)


class VeSyncClient:

    def __init__(self, username, password, country=DEFAULT_COUNTRY,
                 timeout=CALL_TIMEOUT_SECONDS, manager_factory=None):
        self.username = username
        self.password = password
        self.country = country
        self.timeout = timeout
        self.factory = manager_factory or _default_factory
        self.loop = None
        self.manager = None

    # ── running coroutines ──

    def _run(self, make_coro):
        if self.loop is None or self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
        try:
            return self.loop.run_until_complete(asyncio.wait_for(make_coro(), self.timeout))
        except VeSyncProblem:
            raise
        except Exception as exc:
            ours = _translate(exc)
            if ours is None:
                raise
            if "VeSyncTokenError" in _class_names(exc) or isinstance(ours, AuthError):
                self.manager = None                # sign in afresh next time
            raise ours from exc

    async def _signed_in(self):
        if self.manager is None:
            manager = self.factory(self.username, self.password, self.country)
            if not await manager.login():
                raise AuthError("VeSync turned down the sign-in")
            self.manager = manager
        return self.manager

    # ── what the plugin calls ──

    def refresh(self):
        """Sign in if needed, fetch every device, return the air purifiers."""
        async def go():
            manager = await self._signed_in()
            await manager.update()
            return list(manager.devices.air_purifiers)
        return self._run(go)

    def purifiers(self):
        """The purifiers from the last refresh, without asking VeSync."""
        if self.manager is None:
            return []
        try:
            return list(self.manager.devices.air_purifiers)
        except Exception:
            return []

    def send(self, cid, command, value):
        """Carry out one command on the purifier with this cid, then read it
        back. Returns the refreshed pyvesync device; raises VeSyncProblem."""
        async def go():
            manager = await self._signed_in()
            device = next((d for d in manager.devices.air_purifiers if d.cid == cid), None)
            if device is None:
                await manager.update()
                device = next((d for d in manager.devices.air_purifiers if d.cid == cid), None)
            if device is None:
                raise NotFound("VeSync does not list that purifier on this account any more")
            ok = await self._apply(device, command, value)
            if not ok:
                raise CommandRefused("VeSync did not accept the command")
            await device.update()
            return device
        return self._run(go)

    @staticmethod
    def _offered(device, attr):
        try:
            return {str(getattr(v, "value", v)).lower() for v in getattr(device, attr)}
        except Exception:
            return None

    async def _apply(self, device, command, value):
        state = device.state
        if command == "power":
            return await device.toggle_switch(bool(value))
        if command == "fanSpeed":
            levels = getattr(device, "fan_levels", None)
            if levels and int(value) not in list(levels):
                raise CommandRefused(f"this purifier's fan speeds are {', '.join(map(str, levels))}")
            return await device.set_fan_speed(int(value))
        if command == "mode":
            offered = self._offered(device, "modes")
            if offered and value not in offered:
                raise CommandRefused(f"this purifier has no {value} mode")
            return await device.set_mode(value)
        if command == "autoPreference":
            offered = self._offered(device, "auto_preferences")
            if offered is not None and value not in offered:
                raise CommandRefused(f"this purifier has no {value} setting for automatic mode")
            room = getattr(state, "auto_room_size", None) or 800
            return await device.set_auto_preference(value, room_size=int(room))
        if command == "childLock":
            return await device.toggle_child_lock(bool(value))
        if command == "display":
            return await device.toggle_display(bool(value))
        if command == "timer":
            if value:
                return await device.set_timer(int(value) * 60)
            await device.get_timer()               # clear_timer needs the timer's id
            if getattr(state, "timer", None) is None:
                return True                        # nothing set, so nothing to clear
            return await device.clear_timer()
        if command == "resetFilter":
            return await device.reset_filter()
        raise CommandRefused(f"unknown command {command!r}")

    def close(self):
        """Close the aiohttp session and the loop. Safe to call twice."""
        manager, self.manager = self.manager, None
        if self.loop is None or self.loop.is_closed():
            return
        try:
            if manager is not None:
                self.loop.run_until_complete(
                    asyncio.wait_for(manager.__aexit__(None, None, None), 10))
        except Exception:
            pass
        finally:
            self.loop.close()
