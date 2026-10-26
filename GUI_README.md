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
