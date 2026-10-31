# R6 Siege Settings Manager

A desktop app for Windows that reads, edits and copies Rainbow Six Siege settings files.

Siege keeps one `GameSettings.ini` per Ubisoft profile in `Documents\My Games\Rainbow Six - Siege\<profile ID>\`. The app lists every profile folder, lets you label and tag each one, and shows the settings with a control that fits each value.

## Features

- Settings grouped by category, with sliders, switches and segmented buttons. Search works across all categories.
- A button that opens a profile ID on R6 Tracker or stats.cc in your browser, so you can find the account name and use it as the label.
- Keys that have no control in the game menu, such as the sensitivity multipliers and the server region, are marked.
- A sensitivity calculator with effective sensitivity, cm/360 from your DPI, and a way to change the multiplier while keeping the same feel.
- An ADS helper that sets every scope at once.
- A ping test for the server regions, using the AWS GameLift ping beacons.
- Copy or merge settings between profiles, backups and presets. Each key has its own checkbox. Hardware-bound keys stay unchecked by default.
- Right-click any setting to copy that value to other profiles.
- A backup before every write, with compare and restore.
- A read-only lock, so the game cannot overwrite the file.
- A benchmark viewer for the game's built-in benchmark reports.
- No writes while the game runs, because Siege rewrites the file when it exits.

## Run

You need [uv](https://docs.astral.sh/uv/).

```bash
uv run r6_siege_settings_manager.py
```

To point the app at another settings folder, use `--root <folder>`.

## Build the exe

```bash
uv run --no-project --with pyinstaller --with customtkinter --with numpy pyinstaller --noconfirm --onefile --windowed --name "R6 Siege Settings Manager" --collect-data customtkinter r6_siege_settings_manager.py
```

The exe is written to `dist\`.

## Data

Labels, tags, presets and backups are stored in `%APPDATA%\R6SiegeSettingsManager`. To use another folder, set the `R6SM_DATA` environment variable.

## Limits

Ubisoft does not document most value encodings. The graphics labels come from the in-game menu order and community files. The cm/360 math uses a community-measured constant. Notes in the app mark these cases.
