---
title: When something goes wrong
nav_order: 8
---

# When something goes wrong

Start with **Plugins → Levoit Purifier → Test Connection**. The line it writes says what is wrong.

## "No VESYNC_USERNAME and VESYNC_PASSWORD found"

The plugin has no account details. Add them to `IndigoSecrets.py` or **Configure** — see [Getting started](getting-started.md).

## "VeSync turned down the sign-in"

- Check you can sign in to the Levoit app with the same email address and password.
- Check the **Account country**. An account set up in the UK needs GB; a wrong country is turned down even with the right password.
- After changing `IndigoSecrets.py`, restart the plugin or click **Save** in **Configure**, so it reads the file again.

## "the pyvesync library is not installed"

Indigo installs pyvesync when the plugin starts, and needs the internet to do it. Restart the plugin. If the message stays, look in the plugin's log in Indigo's `Logs/com.clives.indigoplugin.levoit/` folder for the reason pip gave.

## The device shows "offline"

- Check the purifier shows as online in the Levoit app. If it does not, it is off at the wall or off Wi-Fi.
- If the app shows it online but Indigo does not, the plugin cannot reach VeSync; the Event Log says "Cannot reach VeSync" with the reason. It keeps trying by itself.

## The Purifier list in the device settings is empty

The plugin fills that list from its last check of the account. Run **Test Connection**, wait for its answer, then close and reopen the device's settings.

## A command did nothing

The Event Log has a line for every command: what was done, or "could not ... because ...". A value the purifier cannot take is refused before it is sent, with "nothing was sent".
