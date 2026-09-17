# Lifecycle, startup, and quit

[← Model Harbor](../README.md)

Model Harbor is designed to stay available in the background while you work in Codex. This page explains how it starts, what closing the window does, and when a restart is actually required.

## Start automatically at login

In **Settings → General → Startup**, turn on **Launch at login** to start Harbor automatically after you log in to this Mac. The setting uses macOS `SMAppService` on macOS 13 and later. Harbor registers itself as a login item for the current user, so it starts after a reboot but does not run before you sign in.

If macOS shows a pending-approval state, open **System Settings → General → Login Items & Extensions** and approve Model Harbor. The toggle in Harbor reflects the actual system state; if approval is required, the button **Open Login Items settings** takes you there directly.

The menu-bar icon remains available even when the main window is closed, so you can always open Harbor from the menu bar.

## Close the window, do not quit

Closing the Model Harbor window does **not** stop the bridge. The local Responses bridge keeps running, provider connections stay active, and task models remain routed.

- **Close the window** to put Harbor back in the menu bar.
- **Quit Harbor** explicitly from the menu-bar menu, the Dock menu, or **Settings → Advanced**.

In the menu-bar popup, open **Settings** and use the footer’s **Open Model Harbor** action to reopen the main window. The same action is available from the Dock icon when Harbor is shown in the Dock.

## Presence: Dock and menu bar, or menu bar only

In **Settings → General → Window**, choose where Harbor appears:

- **Dock and menu bar**: Harbor has a Dock icon and a menu-bar icon. Use this if you prefer switching apps from the Dock.
- **Menu bar only**: Harbor stays out of the Dock and is reachable from the menu bar. Use this for a lighter utility.

The menu bar is always retained. The **On close** setting selects **Ask every time**, **Keep in Dock**, or **Menu bar only**. The close dialog also offers **Cancel** and **Remember this choice**. A close choice controls the closed-window state; reopening restores your selected **Show in** preference.

## Codex restart

Restart Codex after **saved-account direct connection changes**, such as switching a saved OpenAI account, or to load a changed provider catalog. Live model changes inside Harbor tasks do **not** require a restart.

In **Settings → Advanced → Codex restart**, choose how Codex is closed:

- **Graceful**: asks Codex to quit normally. This is the default and the safest choice.
- **Force**: asks macOS to force-terminate Codex, then waits for confirmed exit. This option warns you that unsaved work may be lost, and you must confirm the action explicitly.

The **Reopen Codex after closing** checkbox controls whether Harbor relaunches Codex after closing it. Both graceful and force actions require confirmation. Changing a preference never closes or restarts Codex by itself.

## Warm-up

Warm-up sends one short, low-effort inference request using the **current Codex account**. It may start a usage window; Harbor does not guarantee faster subsequent requests. It is optional and **off by default**.

In **Settings → General → Warm-up**, choose:

- **Off**: no automatic warm-up.
- **After startup**: attempt a warm-up during the first ten minutes after the bridge connects, at most once per local calendar day.
- **Daily**: attempt a warm-up when Harbor is open and idle within five minutes of the selected local time. Missed schedules are skipped.

A warm-up request uses your Codex subscription quota. It is a single, minimal request (for example, "Reply with OK.") and does not raise rate limits or affect account ceilings. You can also trigger a one-time warm-up with **Warm up now**.

Warm-up is not a health ping. It is a genuine inference request to the current Codex account, and it only runs when that account is available.

Automatic attempts, including failures, are recorded before dispatch and limited to one per local day across relaunches. Manual requests cannot overlap and must be at least one minute apart. Requests time out after 60 seconds and are not retried. Warm-up never refreshes credentials, switches accounts, opens 1Password, or uses Baseten/OpenRouter API credit. If the current Codex token has expired, sign in to Codex again. Turning on warm-up does not prevent provider throttling.

Launch at login starts Harbor quietly without its window. **Open window when launched manually** controls normal app launches.

## Verification status

The September 16, 2026 verification used unit tests with fake system and request services, plus isolated UI checks. It did not register a real macOS login item, send an upstream warm-up request, or terminate Codex. These checks do not establish that login startup or warm-up is active on this Mac.
