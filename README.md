# v1.0.9.18 — selectable coordinate bounds

Power lookup validation now accepts X and Y coordinates from 0 through 700 inclusive, in both single and batch modes and in the GUI bulk-power queue. The internal 720 map-ID stride and existing scan grid/mask are unchanged. Retains v1.0.9.17 island points and silent website uploads.

---

# GOTWiC Scanner v1.0.9.17 — Island coverage and silent uploads

Based on the user-supplied stable v1.0.9.16 release. Automatic scanning now includes three explicit island navigation points (680,623), (670,629), (664,634) in addition to the existing grid mask. CSV website uploads continue only after successful power retrieval saves values, with one bulk upload at completion, and no upload-related GUI console or status notifications. The Enable CSV upload checkbox remains enabled by default. All other features are preserved.

---

# GOTWiC Scanner v1.0.9.16 — CSV Upload After Power Retrieval

Built on the complete v1.0.9.15 release, retaining v1.0.9.13 Discord,
v1.0.9.14 licensing, verified navigation, database, GUI and updater features.

- **No website CSV upload at scan completion**, including batch-kingdom scans.
- After a successful **individual** power retrieval, the updated CSV is saved
  atomically and then queued for website upload.
- After **bulk** power retrieval, wait for the entire bulk operation to finish;
  queue **one** upload of that snapshot, containing all successfully saved
  power values. No upload if zero values were saved.
- Failed power retrievals do not trigger an upload. A partially successful
  bulk retrieval uploads the values that were successfully saved.
- The existing website import protocol, sequential background upload worker,
  console reporting and local CSV retention remain unchanged.
- Settings still contains only **Enable CSV upload**, checked by default;
  the user's existing on/off preference is preserved.
- Power is persisted in the CSV's `status_source` field; no CSV schema changes.
- The website importer may apply its own duplicate-snapshot rules to repeat
  uploads of the same CSV after later power lookups.
- No changes to PHP, MySQL schema, WASM navigation, Discord report rules,
  licensing, or automatic updater behavior.

## Installation

Extract the complete release into the existing application directory and
launch `RUN_GUI.bat`. No website configuration changes are required.

---

## Historical release notes

# GOTWiC Scanner v1.0.9.15 — Automatic Website CSV Upload

Built on the complete v1.0.9.14 release, preserving v1.0.9.13 Discord filtering/heading,
v1.0.9.14 online license validation, and all existing scanner, SQL, GUI and updater features.

- Settings contains one website-upload control: **Enable CSV upload** (checked by default on fresh installations and upgrades). The choice persists when changed.
- Each successfully completed CSV scan is queued for automatic website import, including every completed kingdom in a batch. Failed/cancelled scans are not uploaded.
- Uses `https://scannerapp.serveminecraft.net/index.php?page=upload` with a GET for the hidden CSRF token and a multipart POST on the same `requests.Session()` (PHP session cookie preserved).
- POST fields: `action=upload`, `token=<retrieved>`, `async=1`, `csv_file[]=<CSV>`; success requires JSON `results[0].success == true` and matching original filename.
- Uploads are processed sequentially in a background worker; the GUI console reports success/failure. No upload retry button or upload-status field is shown in Settings.
- Local CSV snapshots are retained if website import fails. The GUI waits for queued uploads before closing.
- Adds `requests>=2.31,<3` to the startup dependency check.
- No website PHP, MySQL schema, scanner decoder, navigation, Discord eligibility, or licensing behavior changes.
- The standalone website import was verified separately (215 rows); live end-to-end testing of this integrated release remains to be done.

---

# v1.0.9.10 — Manual Power Discord Reporting

- Successful manual single-castle and bulk power retrievals also trigger Discord reporting when enabled.
- UNKNOWN castles are eligible in manual reports, including when the webhook mode is UNPROTECTED only; PROTECTED castles still require All statuses mode.
- Bulk results are sent after retrieval completes, grouped by kingdom, sorted by power descending, and never include failures.
- Manual reports use the configured minimum power threshold; if none qualify, no message is sent.
- New bulk Get Power — UNKNOWN button retrieves missing power for UNKNOWN-status castles.
- Existing automatic scan reporting remains unchanged.

# v1.0.9.7 — Discord Webhook Reporting

- Settings: enable/disable automatic Discord reporting (disabled by default).
- Configure webhook URL, report mode (UNPROTECTED by default, or both protection statuses), and Test Webhook.
- Sends each completed kingdom report asynchronously; supports large reports and Discord HTTP 429 rate limiting.
- Existing Night'sWatch and coordinate exclusions are respected. Web upload and SQL remain disabled.
- Webhook URL is stored in local application settings and is not printed to the console. Treat this URL as a secret.

# v1.0.9.6 changes

- Ignore Night'sWatch castles and castles with X < 5 or Y < 5 in live results, statistics and power lookup queues. Raw CSV records remain intact.
- Skip excluded castles during scan-time and bulk power lookups.
- After each kingdom, best-effort CDP cleanup: disable event domains, release temporary buffers, request JavaScript garbage collection, and clear browser resource cache without clearing cookies or reloading the game.
- SQL and web upload remain disabled.

## v1.0.9.5 changes

- Scan Statistics now displays each scanned kingdom separately with Untruced, Truced and Total counts, plus an overall TOTAL row. Night'sWatch exclusions still apply.
- SQL import and website upload are temporarily disabled (no startup SQL probe or automatic transfers); CSV saving and retention continue normally.

## v1.0.9.4
- Exclude castles with names starting with `Night'sWatch` from the Current Scan list and numeric scan statistics, matching the existing power-check exemption. CSV and SQL records remain unchanged.
- Omit `WASM_RECOVERY_GUIDELINE.md` from the distributed application and release manifests.

## v1.0.9.3
- Compact Power (M) column and numeric live scan statistics for untruced, truced, and total castles (all results, independent of filters).
- Double-clicking a castle switches to its recorded kingdom before navigating and retrieving power; leaves castle dialog open.

## v1.0.9.2
- Batch kingdoms accepts comma-separated selections (2 or 3 unique kingdoms) in entered order, e.g. `550, 542, 547`.
- Rejects malformed, duplicate, out-of-range, or more than three entries before scanning; ascending ranges and single kingdom selection remain supported.
- Existing per-kingdom switching, saving, and incremental GUI updates remain unchanged.

## v1.0.9.1
- Switch to the selected kingdom once before each scan using the verified WASM kingdom selector.
- Optional inclusive batch kingdom range (start - end), ascending and validated.
- Save and display each kingdom after completion; retain completed kingdoms in the live table.
- Stop cancels remaining kingdoms; failed kingdom aborts the batch.

# GOTWiC Scanner v1.0.9.0

- CSV snapshots are now stored in the `csv/` subfolder. At GUI startup, root-level `*.csv` files are moved into that folder; name conflicts receive `_migrated_N` suffixes without overwriting either file.
- Castle Explorer, history, CSV retention and deletion use the `csv/` directory by default.
- Double-click a castle in Current Scan or Castle Explorer to retrieve its power while leaving the game at that castle with the information dialog open. The normal Get Power buttons and bulk lookups still return to water and dismiss the dialog.
- Includes an original medieval heraldic launcher icon. Windows cannot assign a custom icon directly to `.bat` files; `RUN_GUI.bat` creates `GOTWiC Scanner.lnk` using `CREATE_SHORTCUT.ps1` on first launch. Use the shortcut for the icon.

## v1.0.8.9 — Streamlined scanning and Castle Explorer history

- Start Scan now automatically proceeds after game preflight; the second game-ready confirmation button is removed. Command-line scans without `--auto-start` still prompt normally.
- Castle Explorer has Latest Scan (default) and All Saved Scans views. Historical observations remain distinct and existing filters apply to either view. Power lookup resolves the selected record to its own CSV snapshot.
- Starting a new scan clears the Current Scan player-name search field; other filters are unchanged.

## v1.0.8.8 republished — unrestricted GUI startup

Removed the one-time activation password prompt, activation-state check, and three-attempt lockout. The GUI starts without activation; old activation.json files are ignored. Version remains 1.0.8.8, so users already on this version must manually replace their files.

## v1.0.8.8 — Persistent Power Check Timing setting

Settings > Power Check Timing offers Original (300/100/4000/100/50 ms), Recommended (150/50/2500/50/25 ms), and Aggressive (100/25/1500/25/10 ms) profiles for initialization, castle settling, response timeout, water settling, and dismissal. Recommended is the default; the selection persists via QSettings and applies to single, bulk and scan-time power checks. Bulk checks retain one persistent CDP connection.

## v1.0.8.7 changes

- Bulk Get Power reuses a single Chrome CDP session for the entire batch.
- Power timings: 150 ms initialization, 50 ms castle settle, 2.5 s timeout, 50 ms water settle, 25 ms dismissal.
- Existing single-castle lookup, exact player matching, persistence, and bulk skip-existing-power behavior retained.

## v1.0.8.6 changes

- Added bulk Get Power for PROTECTED and UNPROTECTED castles from the latest completed scan; skips existing power values, runs sequentially, saves each result, and reports progress/failures.
- Automatically checks MySQL connectivity on startup and selects SQL import if reachable; otherwise disables SQL import and continues in CSV-only mode.
- Startup activation was removed in the republished v1.0.8.8 release.

## v1.0.8.5 — WASM navigation, buffer and live player filter

- Updated verified WASM 2.82.0_aff1eee501 navigation: table slot 63014, function 117459 (five arguments).
- Default scan response buffer increased to 24 (range 1–32).
- Current Scan Player input now filters the live results table immediately, case-insensitively and by partial name. Clearing it restores all matching status/power rows. Display-only; saved scan output is unaffected.

## v1.0.8.4 — CSV cleanup

Settings now includes **Delete all CSV files**. Clicking it immediately deletes every `*.csv` file in the currently selected CSV snapshot folder without a confirmation prompt. Snapshot tables refresh afterward. Files in other directories and non-CSV files are untouched. Deletion errors are shown in the scanner console.

## v1.0.7 — WASM navigation recovery

Updated verified Unity WASM navigation from slot 62958 / function 145867 to slot 62960 / function 145870 for game build `2.81.0_39b7b26851`. The existing five navigation arguments and castle decoder remain unchanged. Full scanner regression testing is still required.

## v1.0.6.8 — mandatory detected updates

When the GitHub version manifest announces a newer version, the update prompt is mandatory: declining the update exits the application. Download or staging failure after an update is detected also exits instead of launching the outdated GUI. If the version check itself fails before any newer version is confirmed, the installed GUI can still launch (offline availability). The republished v1.0.8.8 release no longer performs activation checks.

## v1.0.6.7 — historical one-time activation (removed in republished v1.0.8.8)

Previously, on the first GUI launch, users entered the activation password once. A successful entry is saved under `%LOCALAPPDATA%\GOTWiC-Scanner\activation.json` and future launches, including after updates, skip the password prompt. Three incorrect entries cause a persistent local lockout. Cancelling does not consume an attempt. The lockout is application-level and is not tamper-resistant against someone with filesystem access. This activation mechanism is no longer used.

# GOTWiC Scanner GUI v1.0.6.6

## v1.0.6.6 changes

- Added Minimum Power filter to Scanner Console beneath Player: ALL (default), 1.000, 1.250, 1.500, 1.750, 2.000 billion.
- Applies instantly to live results in combination with truce status filter, including new power lookups. Display-only; does not alter CSV or SQL.
- Retains existing sorting and manual Get Power.

---

# GOTWiC Scanner GUI v1.0.6.5

## v1.0.6.4 change

- Scanner Console startup banner displays APP_VERSION rather than the scanner engine internal version.

# GOTWiC Scanner GUI v1.0.6.3 — live status filtering

- Scanner Console live castle results have an **ALL / PROTECTED / UNPROTECTED** display filter.
- Switching the filter instantly refreshes visible castles during or after a scan, without changing scan output, SQL, CSV, or the underlying castle list.
- Truce Status and Power (M) sorting remain available, as does manual Get Power for a selected visible castle.
- Castle Explorer retains its existing status filter (including UNKNOWN) and search.
- Includes all v1.0.6.2 Unicode and v1.0.6.1 snapshot fixes, NPC power exclusion, and GitHub updater.

---

# GOTWiC Scanner GUI v1.0.6.2 — Unicode power lookup hotfix

- Manual castle power subprocess is launched with `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1`.
- Standalone `castle_power_detector.py` configures stdout/stderr as UTF-8 to support Cyrillic and other Unicode castle names in JSON output.
- GUI reads detector output as UTF-8; exact player-name validation and UTF-8 CSV persistence remain unchanged.
- Retains v1.0.6.1 snapshot synchronization, NPC exclusion, SQL support, and GitHub updater.

---

# GOTWiC Scanner GUI v1.0.6.1 — snapshot synchronization hotfix

- Use the exact CSV path reported by a completed GUI scan, including when the scanner writes outside the GUI's configured CSV folder.
- Refresh Castle Explorer from that snapshot; display its kingdom and `last_seen_utc` timestamp.
- Resolve manual Get Power requests from the live table against that same snapshot, never an unrelated older scan.
- If the current CSV is missing or invalid, report that condition instead of silently falling back to old results.
- All v1.0.6 features, including Night'sWatch NPC power exclusion and GitHub updates, remain unchanged.

---

# GOTWiC Scanner GUI v1.0.6

## v1.0.6 changes

- Automatic verbose power lookups skip castles with names starting exactly with `Night'sWatch`, regardless of truce status. These castles remain in scan output with power 0.
- Manual Get Power remains available for all castles, including NPC castles.
- GitHub changed-file updater and all v1.0.5 features are retained.

---

# GOTWiC Scanner GUI v1.0.5

## v1.0.5 changes

- The Auto scan mask selector has been removed from the GUI.
- Every automatic kingdom scan uses `gotwic_kingdom542_production_mask.json`, regardless of kingdom number.
- All existing GUI, castle power, SQL, CSV and update features are retained.

---

# GOTWiC Scanner GUI v1.0.2

## What's new in v1.0.2

- `RUN_GUI.bat` automatically starts Google Chrome with remote debugging enabled on port `9222`.
- Chrome uses a dedicated profile at `%LOCALAPPDATA%\GOTWiC-Scanner-Chrome`.
- Includes the complete scanner GUI package, supporting Python modules, mask, and update manifest.

## Requirements

- Windows
- Python 3.13
- Google Chrome installed at `C:\Program Files\Google\Chrome\Application\chrome.exe`
- Python dependencies installed from `requirements_gui.txt`

Install dependencies from this folder:

```bat
py -3.13 -m pip install -r requirements_gui.txt
```

## Start the application

Double-click `RUN_GUI.bat`. It will:

1. Set the working directory to the folder containing the batch file.
2. Start Chrome with `--remote-debugging-port=9222` and the dedicated user-data directory.
3. Launch `gotwic_gui.py`.

If Chrome is installed somewhere else, edit the Chrome executable path in `RUN_GUI.bat`.

## Chrome profile note

The dedicated profile is separate from your usual Chrome profile. Sign in to the game in this Chrome window and open the game map before starting a scan. Keep this Chrome window open while scanning.

## Update server

The package contains `version.json`, which currently advertises GUI version `1.0.2` and payload `gotwic_gui.py`.

Update base URL:

`https://gotwic-scanner.byethost4.com/game/updates/`

For the current updater, upload `version.json` and `gotwic_gui.py` to that directory. The launcher and README are distributed with the ZIP and are not delivered by the current single-file updater. Future multi-file updater releases should publish all required `.py` files together.

## Troubleshooting

- If the GUI reports that Chrome cannot be reached, confirm Chrome started and is listening on port `9222`.
- If Python or a module is missing, install dependencies using the command above.
- If a scan does not start, open the game map in the Chrome window launched by `RUN_GUI.bat`, then follow the GUI's ready/start prompt.

## Files

- `gotwic_gui.py` — desktop GUI and startup updater
- `gotwic_scanner_v2_2.py` — scanner engine
- `live_truce_scanner_v5.py`, `live_truce_scanner_v6.py` — supporting scanner modules
- `decode_bubble_events.py`, `extract_coordinates_from_protocol.py` — decoder/helper modules
- `gotwic_kingdom542_production_mask.json` — production mask
- `requirements_gui.txt` — Python dependencies
- `updater.py` — separate updater helper
- `version.json` — update manifest
- `RUN_GUI.bat` — Chrome + GUI launcher


## Release/update standard (v1.0.2)

The release unit is the **complete application package**, not only Python source files. Every release archive must contain all runtime files: all required `.py` modules, configuration and mask files, launcher scripts, dependency lists, documentation, the updater, and manifests. The server manifest points to the complete package ZIP, and `release_manifest.json` inventories files and SHA-256 hashes.

Deployment requires publishing `version.json`, `release_manifest.json`, and the complete package ZIP to the update directory. The existing v1.0.2 client updater currently uses a single-file download flow; complete-package updating requires a compatible updater implementation before automatic full-package updates will work.


## v1.0.2 hotfix — console encoding and live results

- Castle status lines are parsed into a live table in the Scanner & Console tab.
- PROTECTED rows are light red; UNPROTECTED rows are light green; UNKNOWN rows are light amber.
- The child scanner process is launched with `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` to prevent Windows console encoding failures on non-Latin player/castle names.
- Raw scanner tracebacks and encoding/error details are suppressed from the GUI console.


## v1.0.2 readability correction

Castle status is now indicated by **text color**, not colored row backgrounds:
- PROTECTED: light red text (`#ff7777`)
- UNPROTECTED: light green text (`#78e08f`)
- UNKNOWN: light amber text (`#ffd166`)

The application version remains 1.0.2.


## v1.0.4 — changed-files-only update system

The server `version.json` inventories **all** application files with SHA-256 and size.
At update time, the GUI compares each installed file against the manifest and downloads
only files whose content differs. All changed files are staged and validated before
any installed file is replaced. The helper waits for the GUI to close, installs the
changed files, attempts rollback on failure, and relaunches the GUI.

Upload the manifest and all listed individual files to the updates directory.
The downloadable ZIP remains a complete manual installation package.

**Migration note:** v1.0.2's old updater only knows how to replace `gotwic_gui.py`.
Install the v1.0.4 complete ZIP once to activate the new multi-file updater. Future
releases can then use changed-file updates. This avoids relying on the old updater
to install its own replacement helper.

## Castle power (v1.0.4)

Default scans write `0` to CSV `status_source`. Select **Castle power (verbose)** in the GUI, or run the scanner with `-v` / `--verbose`, to retrieve castle power from 20007 responses for **UNPROTECTED castles only**. PROTECTED and UNKNOWN castles are skipped and remain `0`. The CSV `status_source` field then contains power divided by 1,000,000 with three decimal places (e.g. `199.844`); unavailable power remains `0`. The lookup uses the v7 navigation/dismissal baseline and can increase scan time. No extra CSV column is created.

## GUI manual power lookup (v1.0.4)
In Castle Explorer select a row from the latest CSV snapshot and click **Get Power for selected castle**. This works for protected and unprotected castles. It uses the existing Chrome CDP session and the validated 20007 response, then verifies the exact player name. The game tab must be open and idle; manual power lookup is blocked while a scan runs. A successful lookup updates only the selected row of the latest snapshot, in the CSV and, when SQL import is enabled, the matching SQL row. Historical snapshots are not modified. Failed lookups do not overwrite the existing power value.

The **Enable SQL import** checkbox is OFF by default; checking it omits `--no-db` from the scanner launch. Configure the existing `GOTWIC_DB_HOST`, `GOTWIC_DB_PORT`, `GOTWIC_DB_USER`, `GOTWIC_DB_PASSWORD`, and `GOTWIC_DB_NAME` environment variables as needed. SQL mode requires `pymysql` (now in requirements_gui.txt). A missing SQL row causes manual update failure rather than a silent CSV-only update.

Both live scanner results and Castle Explorer show **Power (M)** immediately after truce status. Click the Truce Status or Power (M) header to sort; repeated clicks reverse the order. Power is sorted numerically. During scans the live table shows 0 until results are available in the final report; the table refreshes at scan completion.

Standalone detector usage (game tab must be open):

`py castle_power_detector.py --x 451 --y 506 --name "Baby Mei"`

The detector prints a JSON result and does not itself persist any changes; GUI persistence is handled by `power_storage.py`.

Castle Explorer: double-click a castle row to retrieve power, or right-click a row and select Get Power. The existing Get Power button remains available.

### Live results power lookup (v1.0.4 correction)

On the **Scanner & Console** tab, after the scan finishes, double-click a castle in **Live castle results**, right-click it and select **Get Power for selected castle**, or select a row and use the Get Power button below the live table. This works for protected and unprotected castles. The action resolves the selected castle against the latest saved CSV snapshot and updates only that record (and SQL when enabled). An active scan must finish before lookup. The same controls remain available in Castle Explorer.

Castle Explorer columns: Castle, X, Y, Truce Status, Power (M), last_seen_utc. Sortable and supports manual Get Power.

### GitHub updates
Updates use https://raw.githubusercontent.com/gotwic-scanner/castle_scanner/main/ . Publish version.json and all files it lists to the repository root.

## v1.0.6.5 — numeric power sorting

Live results and Castle Explorer now sort Power (M), X and Y numerically using dedicated numeric table items. Sorting is retained when tables refresh, filters change or power values are updated. Double-click and context-menu power lookups continue to resolve castle identity from displayed row data.


## v1.0.6.9: Automatic dependency bootstrap
Launch using RUN_GUI.bat. startup.py checks requirements_gui.txt before loading the GUI, installs missing or incompatible packages using the same Python 3.13 interpreter, and verifies installation. If installation fails, startup stops with an error. The check repeats on subsequent launches, installing only when needed. An internet connection and pip are needed for missing packages. Existing mandatory updates remain in place; one-time activation has been removed.

## v1.0.8.2.1 website CSV upload

Optional HTTPS CSV upload and manual retry are available in Settings. See `WEB_UPLOAD_SETUP.md` for server setup. No website credentials are included in this package.

CSV retention: Settings > Auto CSV cleanup (days), default 14; 0 disables. Cleanup on startup and after scan.

## v1.0.9.8 — Discord minimum power filter

Settings > Discord Webhook Reporting now includes a persistent **Minimum castle power** dropdown: 500 Million (default), 1 Billion, 1.5 Billion, or 2 Billion. Discord reports include only castles whose known power is at least the selected threshold. Castles without valid power are omitted. The threshold affects Discord reports only, not scan collection, local CSV storage, Castle Explorer, or scan statistics. Existing status and castle-name/coordinate exclusions still apply.


## v1.0.9.9 — Discord, sorting, buffer, UNKNOWN filter
- Discord: do not send a report if zero castles meet the minimum power requirement; order qualifying castles highest power first, then alphabetically for ties.
- Default Current Scan and Castle Explorer sort by Power (M), highest first. Unknown power appears last; users may manually change sorting.
- Configurable scanner buffer maximum increased to 48, default 24; selected value persists in settings.
- Current Scan Display dropdown adds UNKNOWN (Potential Fervor), with a nearby one-click UNKNOWN toggle. UNKNOWN is not proof of Fervor.


## Release v1.0.9.11 (2026-11-05)

- WASM build `2.82.0_ab18aead67`: verified map-navigation table slot `62896`, function `117256` (five i32 parameters); verified kingdom selector slot `62893`, function `117283` (i64 + five i32 parameters). Both calls have function-name and arity checks.
- Scanner statistics: Untruced displays the independent UNKNOWN count in parentheses for every kingdom and TOTAL, e.g. `20 (3)`; UNKNOWN is not included in Untruced.
- Discord: removed the `(Manual Power)` and `— Unprotected + UNKNOWN` heading decorations without changing which castles qualify.
- MySQL/MariaDB: restore startup connectivity check and automatic post-scan imports when available. SQL failures leave completed CSV scans usable; history inserts retain the existing schema and timestamps.
- Desktop Manager title displays the application version. The Kingdom field remembers the last *successfully completed* kingdom (including batch scans); default is 1. Failed/cancelled scans do not update it.
- Website upload, hosting and licensing remain disabled/deferred. Existing GUI, CSV, decoder, power and updater components are retained.


## Release v1.0.9.12 (2026-11-05)

- UNKNOWN status is displayed as **Fervor** in the GUI, while retaining the original UNKNOWN status in CSV and protocol records for compatibility. This is a label, not proof of Fervor.
- Get Power UNPROTECTED includes UNKNOWN castles with missing power. The separate UNKNOWN button is relabeled Fervor.
- Verbose scan-time power checks include UNKNOWN castles.
- Discord reporting treats UNKNOWN as UNPROTECTED for eligibility, respecting minimum power.


## Release v1.0.9.12 (2026-11-05)

- UNKNOWN status is displayed as **Fervor** in the GUI, while retaining the original UNKNOWN status in CSV and protocol records for compatibility. This is a label, not proof of Fervor.
- Get Power UNPROTECTED includes UNKNOWN castles with missing power. The separate UNKNOWN button is relabeled Fervor.
- Verbose scan-time power checks include UNKNOWN castles.
- Discord reporting treats UNKNOWN as UNPROTECTED for eligibility, respecting minimum power.


## Release v1.0.9.12 (2026-11-05)

- UNKNOWN status is displayed as **Fervor** in the GUI, while retaining the original UNKNOWN status in CSV and protocol records for compatibility. This is a label, not proof of Fervor.
- Get Power UNPROTECTED includes UNKNOWN castles with missing power. The separate UNKNOWN button is relabeled Fervor.
- Verbose scan-time power checks include UNKNOWN castles.
- Discord reporting treats UNKNOWN as UNPROTECTED for eligibility, respecting minimum power.


## Release v1.0.9.13 (2026-11-05)

- Discord reports exclude castles with underlying status UNKNOWN or FERVOR in all report modes, including automatic scan and manual power retrieval reports.
- Discord report heading uses **Master Of Merits — Kingdom X**.
- GUI Fervor power retrieval remains unchanged.


## Release v1.0.9.14 (2026-11-06)

- Online-only HTTPS license validation after the application update check and before opening the GUI.
- First startup prompts for a GOTWiC license key; subsequent startups read the locally stored key from `%LOCALAPPDATA%\GOTWiC-Scanner\license.json`.
- Windows MachineGuid is hashed with SHA-256 and a GOTWiC-specific namespace before transmission as the 64-character device identifier. The raw MachineGuid is not sent.
- A successful first validation binds the license to that device on the server. Changing computers requires a new license.
- Invalid, revoked, expired and different-device licenses prevent startup. Network/server failures offer Retry or Exit, without offline grace.
- Existing scanner, GUI, database, Discord and multi-file updater behavior is retained.
- License administration remains on the server; the desktop application does not connect to the licensing database.
