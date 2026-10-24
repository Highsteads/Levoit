---
title: How it works
nav_order: 7
---

# How it works

The plugin talks to VeSync through [pyvesync](https://github.com/webdjoe/pyvesync), the same library Home Assistant uses for Levoit. Indigo installs it from the plugin's `requirements.txt` the first time the plugin starts.

## One worker does all the talking

A single background worker signs in, reads the purifiers and sends every command, one at a time. Actions, menu items and the on/off buttons only hand it a job, so a slow answer from VeSync never holds up Indigo.

## Checking

Every minute (or whatever **Check every** says) the worker asks VeSync for the list of devices on the account and the latest readings from each purifier, and writes any state that has changed. It writes them in one go per purifier, and leaves out anything that has not changed, so the SQL Logger records real changes rather than a row a minute. `lastUpdate` changes at every check, so the plugin tells the SQL Logger to leave it out of the history.

## When VeSync cannot be reached

- One failed check is ignored. After three in a row the plugin marks every purifier offline, says so once in the Event Log, and keeps trying, waiting longer each time, up to 15 minutes.
- If VeSync turns down the email and password, the plugin says so once and waits half an hour before trying again, so a wrong password cannot get the account locked. Saving **Configure** tries again at once.
- If VeSync says the account is asking too often, the plugin waits five minutes.

## Commands

Each command is checked when you make it, then the worker sends it. The Event Log gets one line saying what was done, or what could not be done and why.

The purifier acts on a command straight away, but the status VeSync reports can take a few minutes to catch up: on a Core 400S, one to four minutes. So once VeSync accepts a command, the device shows what you asked for at once and keeps showing it while VeSync's status is behind. When the status agrees, the plugin goes back to showing whatever VeSync reports. If after six minutes it still has not caught up, the device shows what VeSync reports and the Event Log says so once. If the purifier itself shows the change, it is only VeSync's status that is behind.
