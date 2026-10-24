---
title: Your devices
nav_order: 3
---

# Your devices

Each purifier is one Indigo device of the model **Levoit Air Purifier**. It is a switch device, so **Turn On**, **Turn Off** and **Toggle** work on it from anywhere in Indigo, and its **State** column shows on or off.

## What each state means

| State | What it tells you |
|---|---|
| **onOffState** | Whether the purifier is running. |
| **mode** | `auto` (follows the air quality), `manual` (keeps its fan speed) or `sleep` (quietest). `unknown` until VeSync has said. |
| **fanLevel** | The fan speed, 1 (lowest) to 4 (highest). 0 when VeSync has not given one. |
| **pm25** | Fine particles in the air, in micrograms per cubic metre. Lower is better. |
| **airQuality** | The purifier's own verdict as a number: 1 excellent, 2 good, 3 moderate, 4 poor, 0 unknown. |
| **airQualityText** | The same verdict in words. |
| **filterLife** | How much of the filter's life is left, as a percentage. |
| **childLock** | True when the purifier's buttons are locked. |
| **displayOn** | True when the purifier's display is lit. |
| **autoPreference** | How hard automatic mode works: `default`, `efficient` or `quiet`. |
| **deviceOnline** | True while VeSync can reach the purifier. When this is false the other readings are the last ones seen, not current ones. |
| **lastUpdate** | When the plugin last read the purifier, as `2026-10-24 13:30:05`. |

## When a purifier is not reachable

The device shows an error in red, and `deviceOnline` turns false:

- **offline** — VeSync says the purifier is not connected (switched off at the wall, or off Wi-Fi), or the plugin has not been able to reach VeSync three checks running.
- **not listed** — the purifier is no longer on the VeSync account.
- **login refused** — VeSync turned down the email address and password.

The error clears by itself at the first good reading.

## After the power has been off

A Core 400S comes back switched off when its power returns: after being unplugged, moved to another room, or a power cut. With **Switch on when power returns** ticked in the device's settings (it is, to start with), the plugin switches it back on.

- **With a plug chosen** under **Plug it is on**, the plugin watches the plug's power reading, which arrives every 30 seconds. When it falls from running (3 W or more) to standby (about 1.7 W), the purifier has come back off, and about 10 seconds later the plugin switches it on. A minute after that it checks the plug again, and asks once more if the purifier is still in standby, three times in all. The plug losing power itself counts too. Either way the plugin tries first after 10 to 15 seconds, then every 10 seconds while the purifier rejoins Wi-Fi, which on a Core 400S is ready within half a minute.
- **Without a plug**, it notices the purifier drop off VeSync and come back. That also happens when the Wi-Fi drops, so it only switches it on again if it was on before. VeSync can take minutes to notice, and misses a short unplug altogether, so a plug is much the better way.

**To switch it off and keep it off**, switch it off from Indigo or the Dashboards Air page; the plugin remembers, even through a restart, until it is switched on from Indigo again. Switching it off with the button on the purifier, or in the Levoit app, looks the same as a power cut to the plugin, so it is switched back on. In sleep mode the purifier draws nearly as little as in standby, so the power reading is not used then.

## The filter

When `filterLife` drops below 10% the Event Log says it is time to order a new filter. After fitting one, run the **Reset Filter Life** action so the count starts again from 100%.
