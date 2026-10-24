---
title: Getting started
nav_order: 2
---

# Getting started

## Before you begin

- The purifier must already be set up in the **Levoit** app (VeSync) and showing there.
- You need the email address and password you sign in to that app with.
- The Mac that runs Indigo needs to reach the internet.

## 1. Install the plugin

1. Go to the [Releases page](https://github.com/Highsteads/Levoit/releases/latest) and download `Levoit.indigoPlugin.zip`.
2. Unzip it. You will get `Levoit.indigoPlugin`.
3. Double-click `Levoit.indigoPlugin`. Indigo installs and starts it.

The first start takes a little longer than usual, because Indigo downloads the pyvesync library the plugin is built on.

## 2. Give it your VeSync account

Either way works; the file is read first.

**In `IndigoSecrets.py`** (in `/Library/Application Support/Perceptive Automation/`), add:

```python
VESYNC_USERNAME = "you@example.com"
VESYNC_PASSWORD = "your password"
VESYNC_COUNTRY  = "GB"      # optional; GB if left out
```

Then restart the plugin, or open **Configure** and click **Save**, so it reads the file again.

**Or in Indigo**: open **Plugins → Levoit Purifier → Configure**, fill in **Email address**, **Password** and **Account country**, and click **Save**.

The country is the one the account was set up in: **GB** for the UK, **US** for the United States, and so on. A wrong country is the usual reason a correct password is turned down.

## 3. Test the connection

Choose **Plugins → Levoit Purifier → Test Connection**. A few seconds later the Event Log says either

> Connection test PASSED - signed in as c\*\*\*@example.com (GB); 1 air purifier on the account.

followed by a line about each purifier, or `Connection test FAILED` with the reason.

## 4. Add the purifier to Indigo

1. Click **New** under Devices.
2. Choose **Levoit Purifier** as the type and **Levoit Air Purifier** as the model.
3. Pick your purifier from the **Purifier** list and click **Save**.

If the list says none have been seen yet, the plugin has not been able to read the account; the Event Log says why. Leaving the list on its first choice is fine too: the device links itself to the first purifier on the account that is not already in Indigo.

Within a minute the device fills in its readings. Its **State** column shows on or off, like any switch; the readings are in the device's states.
