---
title: Settings
nav_order: 5
---

# Settings

**Plugins → Levoit Purifier → Configure**

| Setting | What it does |
|---|---|
| **Email address** | The VeSync account's email. Only used when `IndigoSecrets.py` has no `VESYNC_USERNAME`. |
| **Password** | The VeSync account's password. Only used when `IndigoSecrets.py` has no `VESYNC_PASSWORD`. |
| **Account country** | Two letters, as the account was set up: GB, US, DE and so on. GB when blank. `VESYNC_COUNTRY` in `IndigoSecrets.py` wins over this box. |
| **Check every (seconds)** | How often the plugin reads every purifier. 60 to start with; 30 at the least, because VeSync limits how often an account can ask. A smaller number is raised to 30, with a line in the Event Log saying so. |
| **Logging level** | How much goes to the Event Log. **Debug** also records each failed check while VeSync is unreachable. |

Clicking **Save** signs in afresh with whatever the settings and `IndigoSecrets.py` now say, and checks every purifier at once.

## In each device

| Setting | What it does |
|---|---|
| **Purifier** | Which purifier on the account this device shows. The list comes from the plugin's last check of the account. Leave it on the first choice and the device links itself to the first purifier not already in Indigo. |
| **Switch on when power returns** | Switches the purifier back on after its power has been off, once it has reconnected. Ticked to start with. See [Your devices](devices.md). |
| **Plug it is on** | Optional: the smart plug the purifier runs from, chosen from the devices that measure power. The plugin then sees a power cut straight away, and the Dashboards Air page shows the purifier's power and running cost. |
| **Standby below (W)** | With a plug chosen, a reading falling from above this to below it means the purifier has gone to standby, so it is switched back on. 2.5 W suits a Core 400S, which reads about 1.7 W in standby. |
