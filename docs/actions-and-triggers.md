---
title: Actions and triggers
nav_order: 4
---

# Actions and triggers

## Switching on and off

Use Indigo's own **Device Actions → Light/Appliance Controls → Turn On / Turn Off / Toggle**, as for any switch.

## The plugin's actions

Choose **Levoit Purifier** in an action's type menu, then one of these:

| Action | What it does |
|---|---|
| **Set Fan Speed** | Sets speed 1 to 4. This also switches the purifier on and puts it in manual mode. |
| **Set Mode** | Automatic, manual or sleep. |
| **Set Automatic Mode Setting** | Default, efficient or quiet. The room size held in the Levoit app is kept. |
| **Set Child Lock** | On, off or toggle. |
| **Set Display** | On, off or toggle. |
| **Set Switch-Off Timer** | The purifier switches itself off after this many minutes. 0 clears a timer already set. |
| **Reset Filter Life** | Sets the filter life back to 100%. Only after fitting a new filter. |
| **Refresh All Purifiers Now** | Reads every purifier straight away instead of waiting for the next check. |

Every action is checked before it is sent. A value the purifier cannot take, such as a fan speed of 5, is refused with a line in the Event Log saying so, and nothing is sent.

Once VeSync accepts a command the device shows it straight away. VeSync's own status can take a few minutes to catch up, and the device keeps showing what you asked for in the meantime; see [How it works](how-it-works.md). If VeSync refuses, the Event Log says what could not be done and why, and the states stay as they were.

## Triggers

Every state on the [devices page](devices.md) can start a trigger. Some that work well:

- **pm25 becomes greater than** 25: turn the purifier up, or on.
- **filterLife becomes less than** 10: send yourself a reminder.
- **deviceOnline becomes false**: tell you the purifier has dropped off.

## From Dashboards

The Dashboards plugin's Air page uses these same actions, so a fan speed set there is checked and logged in the same way.
