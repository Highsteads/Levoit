# Levoit Purifier for Indigo

**Watch and control Levoit air purifiers from Indigo, through your VeSync account.**

**Version:** 1.0.0 | **Author:** CliveS & Claude | **Needs:** Indigo 2025.2 or later and a VeSync account

**[Read the full guide](https://highsteads.github.io/Levoit/)** — setting up, what everything means, and what to do when something goes wrong.

---

## What it does

This plugin lets [Indigo](https://www.indigodomo.com) show and control Levoit air purifiers. It signs in to your VeSync account, the same one the Levoit app uses, and reaches each purifier through VeSync's servers, so the Mac that runs Indigo needs to reach the internet.

- **Shows each purifier as an Indigo device** — on or off, PM2.5, air quality, fan speed, mode and how much life the filter has left.
- **Switches it on and off** like any other Indigo device, from schedules, triggers, control pages and Dashboards.
- **Sets everything the Levoit app sets** — fan speed, mode, the automatic-mode setting, child lock, display, a switch-off timer, and resetting the filter after you fit a new one.
- **Checks every minute** (you can choose how often, down to 30 seconds). A command shows in Indigo at once and is held there while VeSync's own status catches up, which can take a few minutes.
- **Tells you in the Event Log** when a purifier drops off VeSync, and when its filter needs changing.

It is built on [pyvesync](https://github.com/webdjoe/pyvesync), which Indigo installs for you when the plugin first starts.

## Which purifiers it works with

It was written for the **Core 400S**. Any air purifier pyvesync supports should appear and switch on and off, and the actions check each purifier's own fan speeds, modes and settings before sending anything, so a command a model cannot take is refused with the reason rather than sent.

## Installing

1. Go to the [Releases page](https://github.com/Highsteads/Levoit/releases/latest) and download `Levoit.indigoPlugin.zip`
2. Unzip the downloaded file — you will get `Levoit.indigoPlugin`
3. Double-click `Levoit.indigoPlugin` — Indigo will install it automatically

## Setting it up

1. Put your VeSync email address and password in `IndigoSecrets.py` as `VESYNC_USERNAME` and `VESYNC_PASSWORD`, or open **Plugins → Levoit Purifier → Configure** and fill them in there. Set the account's country if it is not the UK.
2. Choose **Plugins → Levoit Purifier → Test Connection**. The Event Log should say the test passed and list your purifiers.
3. Create a **New Device**, choose **Levoit Purifier** and **Levoit Air Purifier**, pick your purifier from the list and click **Save**.

The [full guide](https://highsteads.github.io/Levoit/) goes through each step, explains every reading and setting, and covers what to do if something does not work.

## What's new

**v1.0.0** — First release.

Every version is listed in the [version history](https://highsteads.github.io/Levoit/changelog.html).

## Authors & licence

Vibed into existence by **CliveS**, who knew what he wanted, argued until he got it, and tested it on a real house. Typed at inhuman speed by **Claude** (Anthropic), who mostly did as it was told.

© 2026 CliveS · [MIT licence](LICENSE) — copy it, fork it, bend it, break it, fix it, ship it. If it breaks, you get to keep both pieces.
