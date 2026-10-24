#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    plugin.py
# Description: Levoit air purifiers (Core 400S and others pyvesync knows) in
#              Indigo, through the VeSync cloud: one relay device per purifier
#              with its air quality, PM2.5, fan, mode and filter, and actions
#              for everything the VeSync app can set.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026 13:30 BST
# Version:     1.0.0
#
# v1.0.0 (24-10-2026, Claude Opus 5.5): first release.
#
# HOW IT IS BUILT
# vesync_client.py talks to VeSync and levoit_logic.py makes every decision;
# neither knows about Indigo, so the tests drive them directly. This file only
# moves facts between them and Indigo.
#
# Threading: pyvesync is asyncio. One worker (runConcurrentThread) owns the
# VeSync client and its event loop and does ALL the network calls - the polls
# and every command. Actions, menu items and the on/off buttons only put a
# command on a queue, so a slow VeSync server can never hold up Indigo's
# callbacks, and no two threads ever share the event loop.

try:
    import indigo
except ImportError:
    pass

import logging
import os
import queue
import sys as _sys
import time

_sys.path.insert(0, os.getcwd())
try:
    from plugin_utils import log_startup_banner
except ImportError:
    log_startup_banner = None

import levoit_logic as L
import vesync_client as vc


PLUGIN_ID = "com.clives.indigoplugin.levoit"
DEVICE_TYPE = "levoitPurifier"
NO_PURIFIER = "none"                 # a menu value of "" is dropped by Indigo

TICK_SECONDS = 1.0
OFFLINE_AFTER_FAILURES = 3           # one blip is not an outage
MAX_BACKOFF_SECONDS = 900
AUTH_RETRY_SECONDS = 1800            # a wrong password retried hard can lock the account
RATE_LIMIT_SECONDS = 300
NOT_INSTALLED_RETRY_SECONDS = 600
# VeSync's status lagged a command by 1.5 to 4 minutes on a real Core 400S
# (24-10-2026, five commands); a command is held this long before the plugin
# gives up waiting for the status to agree.
HOLD_SECONDS = 360
DUPLICATE_SECONDS = 3                # an identical command this soon after the last is a double tap
# Switching a purifier back on after its power returns: it needs time to boot
# and rejoin Wi-Fi and VeSync before it will take a command, so the first try
# waits, failures are retried, and the plugin gives up after RESTORE_GIVE_UP.
# Measured 24-10-2026: unplugged for a few seconds, the purifier took the
# first try at +45 s, so it was ready well before; CliveS saw 30 s from plugging
# in and wanted it quicker. A purifier not yet back on Wi-Fi just refuses, so
# early tries cost nothing but a VeSync call: quick at first, slower later.
RESTORE_FIRST_TRY_SECONDS = 15
RESTORE_RETRY_SECONDS = 10
RESTORE_SLOW_RETRY_SECONDS = 30
RESTORE_QUICK_FOR_SECONDS = 120
RESTORE_GIVE_UP_SECONDS = 600
RESTORE_AFTER_STANDBY_SECONDS = 10   # it is already powered; give it a moment to rejoin Wi-Fi
RESTORE_VERIFY_SECONDS = 60          # then look at its plug: still in standby means it did not take
RESTORE_ATTEMPTS = 3

SECRETS_FILE = "IndigoSecrets.py"


class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        try:
            from plugin_utils import install_timestamp_filter
            install_timestamp_filter(self)
        except Exception:
            pass

        self.client = None
        self.rebuild = True                # the worker (re)builds the client
        self.commands = queue.Queue()
        self.facts = {}                    # dev.id -> latest L.decode() of its purifier
        self.holds = {}                    # dev.id -> a command waiting for VeSync's status to agree
        self.clock = time.monotonic        # replaceable, so the tests can move time on
        self.last_sent = {}                # dev.id -> (command, value, clock) of the last command sent
        self.plug_of = {}                  # plug device id -> {purifier dev ids} it powers
        self.restores = {}                 # dev.id -> {"next", "until", "why"}: switch it back on
        self.on_at_outage = {}             # dev.id -> was it on when it dropped off VeSync
        self.account = []                  # [(cid, name, model)] from the last refresh
        self.next_poll = 0.0
        self.failures = 0
        self.auth_failed = False
        self.rate_limited = False
        self.missing_creds_logged = False
        self.not_installed_logged = False
        self.warned = set()                # one-off warnings already given
        self.poll_interval = L.POLL_DEFAULT_SECONDS

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def startup(self):
        self._apply_log_level(self.pluginPrefs.get("logLevel"))
        self._load_interval()
        user, password, _ = self._credentials()
        creds = "sign-in details found" if user and password else "no VeSync sign-in details yet"
        self.logger.info(f"Levoit Purifier ready: {creds}, checking every {self.poll_interval} seconds.")
        # To see a purifier's plug get its power back (deviceUpdated).
        indigo.devices.subscribeToChanges()

    def shutdown(self):
        # The worker closes the VeSync client as it stops (runConcurrentThread's
        # finally). Indigo stops that thread before calling shutdown().
        pass

    def validatePrefsConfigUi(self, valuesDict):
        errors = indigo.Dict()
        try:
            int(float(str(valuesDict.get("pollSeconds", "")).strip()))
        except (TypeError, ValueError):
            errors["pollSeconds"] = f"A number of seconds, {L.POLL_MIN_SECONDS} or more."
        country = str(valuesDict.get("country", "") or "").strip()
        if country and L.country_code(country) != country.upper():
            errors["country"] = "Two letters, such as GB or US."
        if len(errors):
            return False, valuesDict, errors
        valuesDict["country"] = country.upper()
        return True, valuesDict

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if userCancelled:
            return
        self._apply_log_level(valuesDict.get("logLevel"))
        self._load_interval(valuesDict)
        self.rebuild = True
        self.next_poll = 0.0
        self.logger.info(f"Settings saved; checking every {self.poll_interval} seconds.")

    def _apply_log_level(self, value):
        level = logging.INFO
        try:
            from plugin_utils import log_level
            level = log_level(value)
        except Exception:
            pass
        handler = getattr(self, "indigo_log_handler", None)
        if handler is not None:
            handler.setLevel(level)
        # pyvesync logs through its own logger. Send it to this plugin's log
        # file only: its routine complaints (one device not answering, an
        # account with no devices) would otherwise reach the Event Log as red
        # lines every minute. Redaction is on by default in pyvesync.
        lib = logging.getLogger("pyvesync")
        lib.propagate = False
        lib.setLevel(logging.DEBUG if level <= logging.DEBUG else logging.INFO)
        file_handler = getattr(self, "plugin_file_handler", None)
        if file_handler is not None and file_handler not in lib.handlers:
            lib.addHandler(file_handler)

    def _load_interval(self, prefs=None):
        prefs = self.pluginPrefs if prefs is None else prefs
        self.poll_interval, warning = L.poll_seconds(prefs.get("pollSeconds", L.POLL_DEFAULT_SECONDS))
        if warning:
            self.logger.warning(warning[:1].upper() + warning[1:] + ".")

    # ------------------------------------------------------------------
    # Credentials: IndigoSecrets.py first, then Configure
    # ------------------------------------------------------------------

    def _secrets_path(self):
        return os.path.join(os.path.dirname(indigo.server.getInstallFolderPath()), SECRETS_FILE)

    def _credentials(self, prefs=None):
        prefs = self.pluginPrefs if prefs is None else prefs
        secrets = L.read_secrets(self._secrets_path())
        user = secrets["VESYNC_USERNAME"] or str(prefs.get("username", "") or "").strip()
        password = secrets["VESYNC_PASSWORD"] or str(prefs.get("password", "") or "")
        country = L.country_code(secrets["VESYNC_COUNTRY"] or prefs.get("country", ""))
        return user, password, country

    def _rebuild_client(self, keep_status=False):
        """Worker thread only: close the old client and make a new one from
        the credentials as they are now. keep_status keeps the outage flags,
        so a retry does not repeat what has already been said."""
        self.rebuild = False
        if self.client is not None:
            self.client.close()
            self.client = None
        if not keep_status:
            self.auth_failed = False
            self.rate_limited = False
            self.failures = 0
        user, password, country = self._credentials()
        if not user or not password:
            if not self.missing_creds_logged:
                missing = " and ".join(n for n, v in (("VESYNC_USERNAME", user), ("VESYNC_PASSWORD", password))
                                       if not v)
                self.logger.error(
                    f"No {missing} found. Put them in IndigoSecrets.py, or fill in Plugins -> Levoit "
                    f"Purifier -> Configure. The plugin will not contact VeSync until it has both.")
                self.missing_creds_logged = True
            return
        self.missing_creds_logged = False
        self.client = vc.VeSyncClient(user, password, country)
        self.logger.debug(f"Using the VeSync account {L.redact_email(user)} ({country}).")

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def deviceStartComm(self, dev):
        # Picks up states added to Devices.xml since the device was made.
        dev.stateListOrDisplayStateIdChanged()
        self._keep_churn_out_of_sql_logger(dev)
        self._map_plug(dev)
        self.next_poll = 0.0

    def deviceStopComm(self, dev):
        self.facts.pop(dev.id, None)
        self.holds.pop(dev.id, None)
        self.last_sent.pop(dev.id, None)
        self.restores.pop(dev.id, None)
        self.on_at_outage.pop(dev.id, None)
        self._unmap_plug(dev.id)

    def didDeviceCommPropertyChange(self, origDev, newDev):
        return self._cid_of(origDev) != self._cid_of(newDev)

    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        # A changed plug or restore setting needs no restart, only the map.
        if not userCancelled and devId in indigo.devices:
            self._map_plug(indigo.devices[devId], valuesDict)

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        values = indigo.Dict(pluginProps)
        # A dynamic menu's defaultValue does not apply to an existing device.
        if not str(values.get("cid", "") or "").strip():
            values["cid"] = NO_PURIFIER
        if not str(values.get("powerPlug", "") or "").strip():
            values["powerPlug"] = NO_PURIFIER
        if "restoreOn" not in values:
            values["restoreOn"] = True
        return values, indigo.Dict()

    def plugMenu(self, filter="", valuesDict=None, typeId="", targetId=0):
        """Devices that measure power, for "the plug it is on"."""
        items = [(NO_PURIFIER, "- no plug -")]
        found = []
        for d in indigo.devices:
            if d.pluginId == self.pluginId:
                continue
            if "powerWatts" in d.states or getattr(d, "energyCurLevel", None) is not None:
                found.append((str(d.id), d.name))
        return items + sorted(found, key=lambda x: x[1].lower())

    @staticmethod
    def _plug_id_of(dev, props=None):
        props = dev.pluginProps if props is None else props
        try:
            return int(str(props.get("powerPlug", "") or "0"))
        except ValueError:
            return 0

    def _map_plug(self, dev, props=None):
        self._unmap_plug(dev.id)
        plug = self._plug_id_of(dev, props)
        if plug:
            self.plug_of.setdefault(plug, set()).add(dev.id)

    def _unmap_plug(self, dev_id):
        for ids in self.plug_of.values():
            ids.discard(dev_id)

    def _restore_wanted(self, dev):
        """Keep it on, unless that is switched off in its settings or someone
        switched it off from Indigo (or a page that goes through Indigo), which
        is remembered in its props so it survives a restart."""
        props = dev.pluginProps
        return (L.as_bool(props.get("restoreOn", True), True)
                and not L.as_bool(props.get("keptOff", False), False))

    @staticmethod
    def _standby_watts(dev):
        try:
            return float(str(dev.pluginProps.get("standbyWatts", L.STANDBY_WATTS)).strip())
        except ValueError:
            return L.STANDBY_WATTS

    def _remember_power_choice(self, dev, on):
        """Record an on/off chosen from Indigo, so 'keep it on' respects an off."""
        props = dict(dev.ownerProps)
        want = "" if on else "true"
        if str(props.get("keptOff", "") or "") == want:
            return
        props["keptOff"] = want
        try:
            dev.replacePluginPropsOnServer(props)
        except Exception as e:
            self.logger.debug(f"{dev.name}: could not record the on/off choice ({e})")

    def deviceUpdated(self, origDev, newDev):
        super().deviceUpdated(origDev, newDev)
        if newDev.pluginId == self.pluginId:       # never react to our own writes
            return
        ids = self.plug_of.get(newDev.id)
        if not ids:
            return
        before = {"on": getattr(origDev, "onState", None), "states": dict(origDev.states)}
        after = {"on": getattr(newDev, "onState", None), "states": dict(newDev.states)}
        returned = L.plug_power_returned(before, after)
        for dev_id in list(ids):
            try:
                dev = indigo.devices[dev_id]
            except Exception:
                continue
            if not (dev.enabled and self._restore_wanted(dev)):
                continue
            if returned:
                self._schedule_restore(dev, f"its plug \"{newDev.name}\" has power again")
            elif (L.plug_dropped_to_standby(before, after, self._standby_watts(dev))
                  and (self.facts.get(dev_id) or {}).get("mode") != "sleep"):
                # Sleep mode may draw nearly as little as standby, so the
                # power reading says nothing then.
                self._schedule_restore(dev, f"its plug \"{newDev.name}\" shows it has gone to standby",
                                       first_try=RESTORE_AFTER_STANDBY_SECONDS)

    def _schedule_restore(self, dev, why, first_try=RESTORE_FIRST_TRY_SECONDS):
        """Callback-safe: only records the job; the worker does the sending."""
        if dev.id in self.restores:
            return
        now = self.clock()
        self.restores[dev.id] = {"next": now + first_try, "until": now + RESTORE_GIVE_UP_SECONDS, "why": why,
                                 "since": now}
        self.logger.info(f"{dev.name}: {why}, so it will be switched back on once it has reconnected.")

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        cid = str(valuesDict.get("cid", "") or "").strip() or NO_PURIFIER
        valuesDict["cid"] = cid
        known = {c: (name, model) for c, name, model in self.account}
        valuesDict["address"] = known[cid][1] if cid in known else ""
        valuesDict["SupportsStatusRequest"] = True
        try:
            watts = float(str(valuesDict.get("standbyWatts", L.STANDBY_WATTS)).strip())
        except ValueError:
            watts = -1
        if not 0 < watts < 50:
            errors = indigo.Dict()
            errors["standbyWatts"] = "A number of watts, such as 2.5."
            return False, valuesDict, errors
        return True, valuesDict

    def purifierMenu(self, filter="", valuesDict=None, typeId="", targetId=0):
        """The purifiers on the account, from the last check (never a live call)."""
        if not self.account:
            return [(NO_PURIFIER, "- none seen yet; see the Event Log, then reopen this -")]
        items = [(NO_PURIFIER, "- the first purifier not already in Indigo -")]
        items += [(cid, f"{name} ({model})") for cid, name, model in self.account]
        return items

    @staticmethod
    def _cid_of(dev):
        try:
            cid = str(dev.pluginProps.get("cid", "") or "").strip()
        except Exception:
            return ""
        return "" if cid == NO_PURIFIER else cid

    def _devices(self):
        return [d for d in indigo.devices.iter("self") if d.deviceTypeId == DEVICE_TYPE and d.enabled]

    def _keep_churn_out_of_sql_logger(self, dev):
        try:
            shared = dev.sharedProps
            merged = L.merge_sql_logger_ignore(shared.get("sqlLoggerIgnoreStates", ""))
            if merged is None:
                return
            shared["sqlLoggerIgnoreStates"] = merged
            dev.replaceSharedPropsOnServer(shared)
        except Exception as e:
            self.logger.debug(f"{dev.name}: could not set the SQL Logger ignore list ({e})")

    # ------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------

    def runConcurrentThread(self):
        try:
            while True:
                try:
                    self._tick(time.monotonic())
                except self.StopThread:
                    raise
                except Exception as e:
                    # One bad tick must never end the thread.
                    self.logger.exception(f"Unexpected problem, carrying on: {e}")
                    self.next_poll = time.monotonic() + 60
                self.sleep(TICK_SECONDS)
        except self.StopThread:
            pass
        finally:
            if self.client is not None:
                self.client.close()
                self.client = None

    def _tick(self, now):
        if self.rebuild:
            self._rebuild_client()
        self._run_commands()
        self._run_restores()
        if self.client is not None and now >= self.next_poll:
            self._poll(now)

    def _run_restores(self):
        """Switch purifiers back on after their power was lost. Sent straight
        to VeSync (not through the double-tap guard). A purifier still booting
        refuses, so a failure is retried until RESTORE_GIVE_UP_SECONDS. VeSync
        can also ACCEPT the command while the purifier is still reconnecting,
        so with a plug the plugin looks at its power RESTORE_VERIFY_SECONDS
        later and asks again while it still reads standby, RESTORE_ATTEMPTS
        times in all."""
        now = self.clock()
        for dev_id, job in list(self.restores.items()):
            if now < job["next"]:
                continue
            try:
                dev = indigo.devices[dev_id]
            except Exception:
                self.restores.pop(dev_id, None)
                continue
            cid = self._cid_of(dev)
            if self.client is None or not cid or not self._restore_wanted(dev):
                self.restores.pop(dev_id, None)     # e.g. switched off from Indigo meanwhile
                continue
            sent = job.get("sent", 0)
            if sent:
                watts = self._plug_watts_of(dev)
                asleep = (self.facts.get(dev_id) or {}).get("mode") == "sleep"
                if watts is None or asleep or watts >= self._standby_watts(dev):
                    self.restores.pop(dev_id, None)
                    self.logger.debug(f"{dev.name}: its plug confirms it is running again.")
                    continue
                if sent >= RESTORE_ATTEMPTS:
                    self.restores.pop(dev_id, None)
                    self.logger.warning(f"{dev.name}: VeSync accepted the request to switch it back on "
                                        f"{sent} times, but its plug still shows it in standby. Switch it on at "
                                        f"the purifier or from Indigo.")
                    continue
                self.logger.info(f"{dev.name}: its plug still shows it in standby, so asking again.")
            try:
                device = self.client.send(cid, "power", True)
            except vc.VeSyncProblem as e:
                quick = now - job.get("since", now) < RESTORE_QUICK_FOR_SECONDS
                retry = RESTORE_RETRY_SECONDS if quick else RESTORE_SLOW_RETRY_SECONDS
                if now + retry > job["until"]:
                    self.restores.pop(dev_id, None)
                    self.logger.warning(f"{dev.name}: could not switch it back on ({e}). "
                                        f"Switch it on at the purifier or from Indigo.")
                else:
                    job["next"] = now + retry
                    self.logger.debug(f"{dev.name}: not ready to switch on yet ({e}); trying again shortly.")
                continue
            job["sent"] = sent + 1
            status = L.decode(device)
            self.holds[dev_id] = L.add_holds(self.holds.get(dev_id), "power", True, status, now, HOLD_SECONDS)
            if not sent:
                self.logger.info(f"{dev.name}: switched back on, because {job['why']}.")
            self._apply(dev, status, quiet=True)
            if self._plug_id_of(dev):
                job["next"] = now + RESTORE_VERIFY_SECONDS
            else:
                self.restores.pop(dev_id, None)

    def _plug_watts_of(self, dev):
        plug = self._plug_id_of(dev)
        try:
            return L.plug_watts(indigo.devices[plug].states) if plug else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll(self, now):
        if self.auth_failed:
            # Read the credentials again before retrying a refused sign-in, so
            # a fix made in IndigoSecrets.py meanwhile needs no restart. The
            # refusal is still only said once.
            self._rebuild_client(keep_status=True)
            if self.client is None:
                return
        try:
            purifiers = self.client.refresh()
        except vc.VeSyncProblem as e:
            self._poll_failed(now, e)
            return

        if self.auth_failed or self.rate_limited or self.failures >= OFFLINE_AFTER_FAILURES:
            self.logger.info("Back in touch with VeSync.")
        self.auth_failed = self.rate_limited = self.not_installed_logged = False
        self.failures = 0
        self.next_poll = now + self.poll_interval
        self._take_account(purifiers)

    def _take_account(self, purifiers):
        """Match what VeSync listed to the Indigo devices, and write states."""
        by_cid = {}
        self.account = []
        for p in purifiers:
            cid = str(getattr(p, "cid", "") or "")
            if not cid:
                continue
            by_cid[cid] = p
            self.account.append((cid, getattr(p, "device_name", "") or "Purifier",
                                 getattr(p, "device_type", "") or "unknown model"))

        devices = self._devices()
        claimed = {self._cid_of(d) for d in devices if self._cid_of(d)}
        for dev in devices:
            cid = self._cid_of(dev)
            if not cid:
                spare = [c for c in by_cid if c not in claimed]
                if not spare:
                    if f"nospare:{dev.id}" not in self.warned:
                        self.warned.add(f"nospare:{dev.id}")
                        self.logger.warning(f"{dev.name}: no purifier on the VeSync account is free to link "
                                            f"it to. Choose one in the device's settings.")
                    continue
                cid = spare[0]
                claimed.add(cid)
                props = dict(dev.ownerProps)
                props["cid"] = cid
                props["address"] = getattr(by_cid[cid], "device_type", "") or ""
                dev.replacePluginPropsOnServer(props)
                self.logger.info(f"{dev.name}: linked to \"{by_cid[cid].device_name}\" on the VeSync account.")
                # replacePluginPropsOnServer restarts the device; it is
                # written at the next check.
                continue
            device = by_cid.get(cid)
            if device is None:
                if f"missing:{cid}" not in self.warned:
                    self.warned.add(f"missing:{cid}")
                    self.logger.warning(f"{dev.name}: VeSync no longer lists this purifier on the account.")
                self._mark_unreachable(dev, "not listed")
                continue
            self.warned.discard(f"missing:{cid}")
            self._apply(dev, L.decode(device))

    def _poll_failed(self, now, e):
        if isinstance(e, vc.NotInstalled):
            if not self.not_installed_logged:
                self.logger.error(f"Cannot talk to VeSync: {e}.")
                self.not_installed_logged = True
            self.next_poll = now + NOT_INSTALLED_RETRY_SECONDS
            return
        if isinstance(e, vc.AuthError):
            if not self.auth_failed:
                user, _, country = self._credentials()
                if "@" not in user:
                    advice = ("The username is not an email address: VeSync signs in with the email address "
                              "the Levoit app uses, so put that in VESYNC_USERNAME (IndigoSecrets.py) or Configure.")
                else:
                    advice = (f"Check VESYNC_USERNAME and VESYNC_PASSWORD in IndigoSecrets.py, or Configure, "
                              f"and the account's country (it is {country} now).")
                self.logger.error(
                    f"{e}. {advice} The plugin will try again in {AUTH_RETRY_SECONDS // 60} minutes; "
                    f"Test Connection tries at once.")
                for dev in self._devices():
                    self._mark_unreachable(dev, "login refused")
            self.auth_failed = True
            self.next_poll = now + AUTH_RETRY_SECONDS
            return
        if isinstance(e, vc.RateLimited):
            if not self.rate_limited:
                self.logger.warning(f"{e}, so the plugin will wait {RATE_LIMIT_SECONDS // 60} minutes before "
                                    f"asking again.")
            self.rate_limited = True
            self.next_poll = now + RATE_LIMIT_SECONDS
            return
        self.failures += 1
        self.logger.debug(f"Check failed ({self.failures} in a row): {e}")
        if self.failures == OFFLINE_AFTER_FAILURES:
            self.logger.warning(f"Cannot reach VeSync ({e}). Will keep trying, more slowly.")
            for dev in self._devices():
                self._mark_unreachable(dev, "offline")
        self.next_poll = now + min(MAX_BACKOFF_SECONDS, self.poll_interval * (2 ** min(self.failures - 1, 4)))

    # ------------------------------------------------------------------
    # Writing states
    # ------------------------------------------------------------------

    def _apply(self, dev, facts, quiet=False):
        """Write one purifier's facts to its device, and log what changed.
        A command on hold is written over VeSync's stale status until that
        status agrees, or the hold runs out."""
        had = self.holds.get(dev.id) or {}
        facts, kept, expired = L.hold_overlay(facts, had, self.clock())
        if kept:
            self.holds[dev.id] = kept
        else:
            self.holds.pop(dev.id, None)
        if had and len(kept) < len(had):
            self.logger.debug(f"{dev.name}: VeSync's status has caught up with "
                              f"{', '.join(sorted(set(had) - set(kept)))}.")
        for request in expired:
            self.logger.warning(f"{dev.name}: VeSync accepted the request to {request}, but its status "
                                f"still does not show it {HOLD_SECONDS // 60} minutes later, so the device now "
                                f"shows what VeSync reports. If the purifier itself shows the change, VeSync's "
                                f"status is behind; otherwise try again.")
        before = self.facts.get(dev.id)
        self.facts[dev.id] = facts
        self._write(dev, facts)
        # Without a plug to watch, a power cut shows as the purifier dropping
        # off VeSync and coming back, off. A Wi-Fi blip looks the same, so it
        # is switched back on only if it was on when it dropped off.
        if before is not None:
            if before.get("deviceOnline") and not facts.get("deviceOnline"):
                self.on_at_outage[dev.id] = bool(before.get("onOffState"))
            elif (not before.get("deviceOnline") and facts.get("deviceOnline")
                  and self.on_at_outage.pop(dev.id, False) and self._restore_wanted(dev)):
                self._schedule_restore(dev, "it is back on VeSync after dropping off", first_try=0)
        error = "" if facts["deviceOnline"] else "offline"
        if indigo.devices[dev.id].errorState != error:
            dev.setErrorStateOnServer(error or None)
        if quiet:
            return
        for level, line in L.change_lines(dev.name, before, facts):
            getattr(self.logger, level)(line)

    def _mark_unreachable(self, dev, error):
        """A purifier we cannot see: offline, last readings kept as history."""
        self.facts.pop(dev.id, None)
        self._write(dev, {"deviceOnline": False})
        if indigo.devices[dev.id].errorState != error:
            dev.setErrorStateOnServer(error)

    def _write(self, dev, values):
        """One batched write of only the states that changed. clearErrorState
        is False on every write: Indigo clears a device's error on any state
        write by default, which would wipe "offline" with the next reading."""
        current = dev.states
        changed = []
        for key, value in values.items():
            if key in current and str(current[key]) == str(value):
                continue
            item = {"key": key, "value": value}
            shown = L.ui_value(key, value)
            if shown is not None:
                item["uiValue"] = shown
            changed.append(item)
        if changed:
            dev.updateStatesOnServer(changed, clearErrorState=False)

    # ------------------------------------------------------------------
    # Commands (run by the worker)
    # ------------------------------------------------------------------

    def _queue(self, dev, command, raw):
        """Check a command now, so a bad value is reported where it was made,
        then hand it to the worker."""
        current = None
        if command == "power":
            current = dev.states.get("onOffState")
        elif command == "childLock":
            current = dev.states.get("childLock")
        elif command == "display":
            current = dev.states.get("displayOn")
        try:
            value = L.check_command(command, raw, current=L.as_bool(current))
        except ValueError as e:
            self.logger.error(f"{dev.name}: {e}; nothing was sent.")
            return
        self.commands.put((command, dev.id, value))

    def _run_commands(self):
        while True:
            try:
                command, dev_id, value = self.commands.get_nowait()
            except queue.Empty:
                break
            if command == "testConnection":
                self._test_connection()
                continue
            if command == "listAccount":
                self._list_account()
                continue
            try:
                dev = indigo.devices[dev_id]
            except Exception:
                continue
            self._run_command(dev, command, value)

    def _run_command(self, dev, command, value):
        words = L.command_words(command, value)
        # A double tap sends the same thing twice within a second (seen
        # 24-10-2026 from the Air page: fan speed 4 four times in two seconds).
        # The second changes nothing, so it is not sent to VeSync again.
        now = self.clock()
        last = self.last_sent.get(dev.id)
        if last and last[:2] == (command, value) and now - last[2] < DUPLICATE_SECONDS:
            self.logger.debug(f"{dev.name}: the same command again within {DUPLICATE_SECONDS} s, not resent.")
            return
        if self.client is None:
            self.logger.error(f"{dev.name}: cannot send anything without VeSync sign-in details.")
            return
        cid = self._cid_of(dev)
        if not cid:
            self.logger.error(f"{dev.name}: this device is not linked to a purifier yet.")
            return
        try:
            device = self.client.send(cid, command, value)
        except vc.VeSyncProblem as e:
            self.logger.error(f"{dev.name}: could not {L.command_request(command, value)}, because {e}.")
            return
        self.last_sent[dev.id] = (command, value, now)
        if command == "power":
            self._remember_power_choice(dev, value)
        status = L.decode(device)
        # VeSync's status lags a command by minutes; hold what was asked for
        # until it catches up, so the read-back cannot undo it.
        self.holds[dev.id] = L.add_holds(self.holds.get(dev.id), command, value, status,
                                         self.clock(), HOLD_SECONDS)
        self.logger.info(f"{dev.name}: {words}.")
        self._apply(dev, status, quiet=True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def actionControlDevice(self, action, dev):
        act = action.deviceAction
        if act == indigo.kDeviceAction.TurnOn:
            self._queue(dev, "power", "on")
        elif act == indigo.kDeviceAction.TurnOff:
            self._queue(dev, "power", "off")
        elif act == indigo.kDeviceAction.Toggle:
            self._queue(dev, "power", "toggle")
        elif act == indigo.kDeviceAction.RequestStatus:
            self._request_status(dev)
        else:
            self.logger.warning(f"{dev.name}: a purifier only switches on and off from here; "
                                f"use the plugin's actions for the rest.")

    def actionControlUniversal(self, action, dev):
        if action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self._request_status(dev)

    def _request_status(self, dev):
        self.next_poll = 0.0
        self.logger.info(f"{dev.name}: asking VeSync for its status.")

    def actionSetFanSpeed(self, pluginAction, dev):
        self._queue(dev, "fanSpeed", pluginAction.props.get("speed"))

    def actionSetMode(self, pluginAction, dev):
        self._queue(dev, "mode", pluginAction.props.get("mode"))

    def actionSetAutoPreference(self, pluginAction, dev):
        self._queue(dev, "autoPreference", pluginAction.props.get("preference"))

    def actionSetChildLock(self, pluginAction, dev):
        self._queue(dev, "childLock", pluginAction.props.get("value"))

    def actionSetDisplay(self, pluginAction, dev):
        self._queue(dev, "display", pluginAction.props.get("value"))

    def actionSetSleepTimer(self, pluginAction, dev):
        self._queue(dev, "timer", pluginAction.props.get("minutes"))

    def actionResetFilter(self, pluginAction, dev):
        self._queue(dev, "resetFilter", None)

    def actionRefresh(self, pluginAction, dev=None):
        self.next_poll = 0.0

    def validateActionConfigUi(self, valuesDict, typeId, devId):
        if typeId == "setSleepTimer":
            try:
                L.check_command("timer", valuesDict.get("minutes"))
            except ValueError as e:
                errors = indigo.Dict()
                errors["minutes"] = str(e)[:1].upper() + str(e)[1:] + "."
                return False, valuesDict, errors
        return True, valuesDict

    # ------------------------------------------------------------------
    # Menu items
    # ------------------------------------------------------------------

    def showPluginInfo(self, valuesDict=None, typeId=None):
        if log_startup_banner:
            log_startup_banner(self.pluginId, self.pluginDisplayName, self.pluginVersion)
        else:
            indigo.server.log(f"{self.pluginDisplayName} v{self.pluginVersion}")

    def testConnection(self, valuesDict=None, typeId=None):
        if log_startup_banner:
            log_startup_banner(self.pluginId, self.pluginDisplayName, self.pluginVersion)
        self.logger.info("Testing the VeSync connection; the result follows in a few seconds.")
        self.commands.put(("testConnection", None, None))

    def listAccountDevices(self, valuesDict=None, typeId=None):
        self.commands.put(("listAccount", None, None))

    def refreshAll(self, valuesDict=None, typeId=None):
        self.next_poll = 0.0
        self.logger.info("Asking VeSync for every purifier's status now.")

    def _test_connection(self):
        """Worker thread: sign in and list, saying exactly what happened."""
        user, password, country = self._credentials()
        if not user or not password:
            self.logger.error("Connection test FAILED - no VeSync username or password.")
            return
        # Always sign in afresh with what IndigoSecrets.py and Configure say
        # NOW, so a corrected password is tested without a restart.
        self._rebuild_client()
        self.next_poll = 0.0
        try:
            purifiers = self.client.refresh()
        except vc.VeSyncProblem as e:
            self.logger.error(f"Connection test FAILED for {L.redact_email(user)} ({country}) - {e}.")
            return
        count = len(purifiers)
        self.logger.info(f"Connection test PASSED - signed in as {L.redact_email(user)} ({country}); "
                         f"{count} air purifier{'s' if count != 1 else ''} on the account.")
        for p in purifiers:
            self.logger.info(L.device_summary(p))
        self._take_account(purifiers)

    def _list_account(self):
        if self.client is None:
            self.logger.error("No VeSync sign-in details, so the account cannot be listed.")
            return
        try:
            purifiers = self.client.refresh()
        except vc.VeSyncProblem as e:
            self.logger.error(f"Could not list the VeSync account: {e}.")
            return
        if not purifiers:
            self.logger.info("VeSync lists no air purifiers on this account.")
        linked = {self._cid_of(d): d.name for d in self._devices()}
        for p in purifiers:
            where = f"in Indigo as \"{linked[p.cid]}\"" if p.cid in linked else "not in Indigo yet"
            self.logger.info(f"{L.device_summary(p)} It is {where}.")
        self._take_account(purifiers)
