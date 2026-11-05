#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.9.13
"""GOTWiC Scanner desktop GUI. Runs the original scanner as a separate process."""
import csv
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
from web_upload import upload_csv, UPLOAD_ENDPOINT
from discord_webhook import validate_webhook, send_kingdom_report, send_test
from gotwic_scanner_v2_2 import ignored_castle
from power_timing import PROFILES, DEFAULT_PROFILE
from csv_storage import CSV_DIR, migrate_root_csv

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QMenu,
    QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QHeaderView)

BASE = Path(__file__).resolve().parent
SCANNER = BASE / 'gotwic_scanner_v2_2.py'
COLUMNS = ['kingdom','x','y','map_id','name','player_id','status','status_source','last_status_utc','last_seen_utc','observations']

def read_snapshots(folder):
    scans = []
    for path in sorted(folder.glob('*.csv'), reverse=True):
        if not re.match(r'^\d+_\d{8}_\d{6}_\d{6}(?:_migrated_\d+)?\.csv$', path.name):
            continue
        try:
            with path.open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                if not set(COLUMNS).issubset(reader.fieldnames or []):
                    continue
                rows = list(reader)
            stamps = {r['last_seen_utc'] for r in rows}
            if len(stamps) > 1:
                continue
            scans.append((path, rows))
        except (OSError, UnicodeError, csv.Error):
            continue
    return scans

def movements(scans):
    """Compare consecutive snapshots of each kingdom; never infer moves from absence."""
    by_kingdom = {}
    for path, rows in reversed(scans):
        if rows:
            kingdom = rows[0]['kingdom']
        else:
            kingdom = path.name.split('_')[0]
        by_kingdom.setdefault(kingdom, []).append((path, rows))
    found = []
    for kingdom, snapshots in by_kingdom.items():
        previous = None
        for path, rows in snapshots:
            current = {r['player_id']: r for r in rows if r['player_id'].strip() and r['x'] and r['y']}
            if previous is not None:
                for pid, row in current.items():
                    old = previous.get(pid)
                    if old and (old['x'],old['y']) != (row['x'],row['y']):
                        found.append([kingdom, row['name'], pid, f"{old['x']},{old['y']}",
                                      f"{row['x']},{row['y']}", path.name])
            previous = current
    return list(reversed(found))


def parse_kingdom_selection(value):
    """Validate a single kingdom, inclusive ascending range, or 2–3 unique comma-separated kingdoms."""
    value = value.strip()
    if re.fullmatch(r'[0-9]+', value):
        number = int(value)
        if 1 <= number <= 999999:
            return [number]
        raise ValueError('Kingdom must be between 1 and 999999.')
    if ',' in value:
        parts = value.split(',')
        if not 2 <= len(parts) <= 3 or any(not re.fullmatch(r'\s*[0-9]+\s*', p) for p in parts):
            raise ValueError('Use 2 or 3 comma-separated kingdom numbers, e.g. 550, 542, 547.')
        kingdoms = [int(p.strip()) for p in parts]
        if any(not 1 <= k <= 999999 for k in kingdoms):
            raise ValueError('Every kingdom must be between 1 and 999999.')
        if len(set(kingdoms)) != len(kingdoms):
            raise ValueError('Duplicate kingdoms are not allowed.')
        return kingdoms
    match = re.fullmatch(r'([0-9]+)\s*-\s*([0-9]+)', value)
    if match:
        first, last = map(int, match.groups())
        if not (1 <= first < last <= 999999):
            raise ValueError('Ending kingdom must be greater than starting kingdom (1..999999).')
        return list(range(first, last + 1))
    raise ValueError('Enter a single kingdom, an ascending range (542 - 550), or up to 3 comma-separated kingdoms.')

class NumericItem(QTableWidgetItem):
    """Sort numeric table cells by value while displaying the original text."""
    def __lt__(self, other):
        try:
            left = float(self.data(Qt.ItemDataRole.UserRole))
            right = float(other.data(Qt.ItemDataRole.UserRole))
            return left < right
        except (TypeError, ValueError):
            return super().__lt__(other)


class TruceItem(QTableWidgetItem):
    def __lt__(self, other):
        order = {'UNPROTECTED':0, 'UNKNOWN':1, 'PROTECTED':2}
        return order.get(self.text().upper(),3) < order.get(other.text().upper(),3)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'GOTWiC Scanner — Desktop Manager — v{APP_VERSION}')
        self.resize(1150, 780)
        self.proc = None
        self.batch_kingdoms = []
        self.batch_completed = 0
        self.scan_config = None
        self.current_scan_kingdom = None
        self.messages = queue.Queue()
        self.scans = []
        self.latest = []
        self.active_scan_csv = None  # Exact CSV emitted by the latest completed GUI scan.
        self.pending_scan_csv = None
        self.live_results = []
        self.upload_running = False
        self.upload_last_csv = None
        self.power_proc = None
        self.power_target = None
        self.bulk_power_queue = []
        self.bulk_power_total = 0
        self.bulk_power_done = 0
        self.bulk_power_failed = 0
        self.bulk_power_status = None
        self.bulk_worker = None
        self.manual_power_reports = []
        self.bulk_messages = queue.Queue()
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.build_scanner()
        self.build_explorer()
        self.build_history()
        self.build_settings()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(150)
        moved, errors = migrate_root_csv()
        if moved:
            self.console.append(f'CSV migration: moved {len(moved)} file(s) into {CSV_DIR}')
        for error in errors:
            self.console.append('CSV migration warning: ' + error)
        self.cleanup_expired_csv()
        self.refresh_data()
        self.check_sql_on_startup()

    def check_sql_on_startup(self):
        """Check actual database access without blocking the GUI startup."""
        self.enable_sql.setChecked(False)
        self.enable_sql.setEnabled(False)
        self.enable_sql.setToolTip('Checking MySQL connection...')
        def worker():
            try:
                import pymysql
                conn = pymysql.connect(
                    host=os.getenv('GOTWIC_DB_HOST', '127.0.0.1'),
                    port=int(os.getenv('GOTWIC_DB_PORT', '3306')),
                    user=os.getenv('GOTWIC_DB_USER', 'root'),
                    password=os.getenv('GOTWIC_DB_PASSWORD', ''),
                    database=os.getenv('GOTWIC_DB_NAME') or 'scanner_results',
                    connect_timeout=3, read_timeout=3, write_timeout=3)
                try:
                    with conn.cursor() as cursor:
                        cursor.execute('SELECT 1')
                        cursor.fetchone()
                finally:
                    conn.close()
                self.messages.put(('sql_check', (True, 'SQL connected — import enabled')))
            except Exception as exc:
                self.messages.put(('sql_check', (False, f'SQL unavailable — CSV only ({exc})')))
        threading.Thread(target=worker, daemon=True).start()

    def build_scanner(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        controls = QGroupBox('Scan configuration'); form = QGridLayout(controls)
        self.kingdom = QSpinBox(); self.kingdom.setRange(1,999999)
        last_kingdom = QSettings('GOTWiC', 'Scanner').value('last_successful_kingdom', 1, type=int)
        self.kingdom.setValue(last_kingdom if 1 <= last_kingdom <= 999999 else 1)
        self.kingdom_range = QLineEdit(); self.kingdom_range.setPlaceholderText('e.g. 542 - 550 or 550, 542, 547 (max 3)')
        self.mode = QComboBox(); self.mode.addItems(['auto','manual'])
        self.show_filter = QComboBox(); self.show_filter.addItems(['ALL','OPEN'])
        self.player = QLineEdit(); self.player.setPlaceholderText('Filter current scan by player name'); self.player.textChanged.connect(self.refresh_live_table)
        self.minimum_power = QComboBox()
        self.minimum_power.addItem('ALL (no minimum)', 0.0)
        for millions in (1000, 1250, 1500, 1750, 2000):
            self.minimum_power.addItem(f'{millions / 1000:.3f} billion', float(millions))
        self.minimum_power.currentIndexChanged.connect(self.refresh_live_table)
        self.buffer = QSpinBox(); self.buffer.setRange(1,48); self.buffer.setValue(24)
        self.verbose_power = QCheckBox('Castle power (verbose) — slower scan')
        self.enable_sql = QCheckBox('SQL import (automatic after scan)')
        self.enable_sql.setChecked(False)
        self.enable_sql.setEnabled(False)
        self.enable_sql.setToolTip('SQL import activates automatically when the database is reachable.')
        form.addWidget(QLabel('Kingdom'),0,0); form.addWidget(self.kingdom,0,1)
        form.addWidget(QLabel('Batch kingdoms'),0,2); form.addWidget(self.kingdom_range,0,3)
        form.addWidget(QLabel('Mode'),0,4); form.addWidget(self.mode,0,5)
        form.addWidget(QLabel('Show'),1,0); form.addWidget(self.show_filter,1,1)
        form.addWidget(QLabel('Player'),1,2); form.addWidget(self.player,1,3)
        form.addWidget(QLabel('Buffer'),2,0); form.addWidget(self.buffer,2,1)
        form.addWidget(QLabel('Minimum Power'),2,2); form.addWidget(self.minimum_power,2,3)
        form.addWidget(self.verbose_power,3,0,1,2)
        form.addWidget(self.enable_sql,3,2,1,2)
        layout.addWidget(controls)
        buttons = QHBoxLayout()
        self.start_btn = QPushButton('Start scan'); self.start_btn.clicked.connect(self.start_scan)
        self.stop_btn = QPushButton('Finish manual scan / Stop'); self.stop_btn.clicked.connect(self.stop_scan); self.stop_btn.setEnabled(False)
        buttons.addWidget(self.start_btn); buttons.addWidget(self.stop_btn)
        layout.addLayout(buttons)
        self.status = QLabel('Idle — open Chrome with remote debugging and load the game map.')
        layout.addWidget(self.status)
        live_header = QHBoxLayout()
        live_header.addWidget(QLabel('Live castle results — protected: light red; unprotected: light green'))
        live_header.addStretch()
        live_header.addWidget(QLabel('Display'))
        self.live_status_filter = QComboBox()
        self.live_status_filter.addItems(['ALL', 'PROTECTED', 'UNPROTECTED', 'Fervor'])
        self.live_status_filter.currentTextChanged.connect(self.refresh_live_table)
        live_header.addWidget(self.live_status_filter)
        self.unknown_toggle = QPushButton('Fervor')
        self.unknown_toggle.setToolTip('Show castles with underlying UNKNOWN status (Fervor)')
        self.unknown_toggle.setCheckable(True)
        self.unknown_toggle.clicked.connect(self.toggle_unknown_filter)
        self.live_status_filter.currentIndexChanged.connect(self.sync_unknown_toggle)
        live_header.addWidget(self.unknown_toggle)
        layout.addLayout(live_header)
        self.live_table = self.make_table(['Kingdom', 'Castle', 'X', 'Y', 'Truce Status', 'Power (M)'])
        self.live_table.sortItems(5, Qt.SortOrder.DescendingOrder)
        self.live_table.horizontalHeader().setStretchLastSection(False)
        self.live_table.setColumnWidth(0, 95)
        self.live_table.setColumnWidth(1, 200)
        self.live_table.setColumnWidth(2, 90)
        self.live_table.setColumnWidth(3, 90)
        self.live_table.setColumnWidth(4, 145)
        self.live_table.setColumnWidth(5, 115)
        self.live_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        results_row = QHBoxLayout()
        results_row.addWidget(self.live_table, 1)
        stats_box = QGroupBox('Scan Statistics')
        stats_box.setMinimumWidth(315)
        stats_layout = QVBoxLayout(stats_box)
        self.stats_table = QTableWidget(0, 4)
        self.stats_table.setHorizontalHeaderLabels(['Kingdom', 'Untruced', 'Truced', 'Total'])
        self.stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stats_table.setAlternatingRowColors(True)
        stats_layout.addWidget(self.stats_table)
        results_row.addWidget(stats_box)
        layout.addLayout(results_row, 3)
        self.live_table.cellDoubleClicked.connect(self.request_power_at_live_row)
        self.live_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.live_table.customContextMenuRequested.connect(self.show_live_context_menu)
        self.live_power_btn = QPushButton('Get Power for selected castle (including truced)')
        self.live_power_btn.clicked.connect(self.request_power_from_live)
        layout.addWidget(self.live_power_btn)
        bulk_row = QHBoxLayout()
        self.bulk_protected_btn = QPushButton('Get Power — PROTECTED (missing only)')
        self.bulk_unprotected_btn = QPushButton('Get Power — UNPROTECTED (missing only)')
        self.bulk_unknown_btn = QPushButton('Get Power — Fervor (missing only)')
        self.bulk_protected_btn.clicked.connect(lambda: self.start_bulk_power('PROTECTED'))
        self.bulk_unprotected_btn.clicked.connect(lambda: self.start_bulk_power('UNPROTECTED'))
        self.bulk_unknown_btn.clicked.connect(lambda: self.start_bulk_power('UNKNOWN'))
        bulk_row.addWidget(self.bulk_protected_btn)
        bulk_row.addWidget(self.bulk_unprotected_btn)
        bulk_row.addWidget(self.bulk_unknown_btn)
        layout.addLayout(bulk_row)
        self.console = QTextEdit(); self.console.setReadOnly(True)
        self.console.setMaximumHeight(155)
        layout.addWidget(QLabel('Scanner messages'))
        layout.addWidget(self.console, 1)
        self.tabs.addTab(tab,'Scanner & Console')

    def build_explorer(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        bar = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText('Filter by player, ID, status or coordinate'); self.search.textChanged.connect(self.filter_castles)
        self.status_filter = QComboBox(); self.status_filter.addItems(['ALL','UNPROTECTED','PROTECTED','Fervor']); self.status_filter.currentTextChanged.connect(self.filter_castles)
        self.explorer_history = QComboBox()
        self.explorer_history.addItems(['Latest Scan', 'All Saved Scans'])
        self.explorer_history.currentIndexChanged.connect(self.filter_castles)
        bar.addWidget(QLabel('Scan History')); bar.addWidget(self.explorer_history)
        bar.addWidget(self.search); bar.addWidget(self.status_filter); layout.addLayout(bar)
        self.summary = QLabel('No snapshots loaded'); layout.addWidget(self.summary)
        self.castles_table = self.make_table(['Castle', 'X', 'Y', 'Truce Status', 'Power (M)', 'last_seen_utc']); layout.addWidget(self.castles_table)
        self.castles_table.sortItems(4, Qt.SortOrder.DescendingOrder)
        self.castles_table.cellDoubleClicked.connect(self.request_power_at_row)
        self.castles_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.castles_table.customContextMenuRequested.connect(self.show_castle_context_menu)
        self.get_power_btn = QPushButton('Get Power for selected castle (including truced)')
        self.get_power_btn.clicked.connect(self.request_power)
        layout.addWidget(self.get_power_btn)
        self.tabs.addTab(tab,'Castle Explorer')

    def build_history(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        self.scan_table = self.make_table(['File','Kingdom','Castles','Unprotected','Protected','Unknown'])
        self.move_table = self.make_table(['Kingdom','Player','Player ID','Previous X,Y','New X,Y','Detected in scan'])
        layout.addWidget(QLabel('CSV scan snapshots')); layout.addWidget(self.scan_table)
        layout.addWidget(QLabel('Movement between consecutive snapshots (matching player IDs)')); layout.addWidget(self.move_table)
        self.tabs.addTab(tab,'Scan & Movement History')

    def build_settings(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.port = QSpinBox(); self.port.setRange(1,65535); self.port.setValue(9222)
        self.folder = QLineEdit(str(CSV_DIR)); self.folder.setReadOnly(True)
        choose = QPushButton('Choose CSV folder'); choose.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout(); folder_row.addWidget(self.folder); folder_row.addWidget(choose)
        form.addRow('Chrome debugging port',self.port); form.addRow('CSV snapshot folder',folder_row)
        layout.addLayout(form)
        reload_btn = QPushButton('Reload CSV snapshots'); reload_btn.clicked.connect(self.refresh_data)
        layout.addWidget(reload_btn)
        delete_csv_btn = QPushButton('Delete all CSV files')
        delete_csv_btn.clicked.connect(self.delete_all_csv_files)
        layout.addWidget(delete_csv_btn)
        self.power_timing_profile = QComboBox()
        for key in PROFILES:
            self.power_timing_profile.addItem(key.capitalize(), key)
        form.addRow('Power Check Timing', self.power_timing_profile)
        layout.addWidget(QLabel('Applies to individual, bulk and scan-time power lookups. Changes are saved automatically.'))
        self.csv_retention_days = QSpinBox()
        self.csv_retention_days.setRange(0, 36500)
        self.csv_retention_days.setSpecialValueText('0 — keep indefinitely')
        form.addRow('Auto CSV cleanup (days)', self.csv_retention_days)
        layout.addWidget(QLabel('0 = keep CSV files indefinitely; cleanup runs at startup and after scans.'))
        layout.addWidget(QLabel('SQL import is enabled when MySQL is reachable; CSV storage is always active.'))
        web_group = QGroupBox('Website CSV upload (optional)')
        web_form = QFormLayout(web_group)
        self.web_enabled = QCheckBox('Automatically upload CSV after scan (temporarily disabled)')
        self.web_retry = QPushButton('Upload / retry latest CSV')
        self.web_retry.clicked.connect(self.retry_web_upload)
        self.web_status = QLabel('Web upload temporarily disabled')
        web_form.addRow(self.web_enabled)
        web_form.addRow(self.web_retry)
        web_form.addRow('Upload status', self.web_status)
        layout.addWidget(web_group)
        self.web_settings = QSettings('GOTWiC', 'Scanner')
        self.buffer.setValue(max(1, min(48, self.web_settings.value('scan_buffer', 24, type=int))))
        self.buffer.valueChanged.connect(lambda value: self.web_settings.setValue('scan_buffer', value))
        saved_profile = str(self.web_settings.value('power_timing_profile', DEFAULT_PROFILE))
        index = self.power_timing_profile.findData(saved_profile)
        self.power_timing_profile.setCurrentIndex(index if index >= 0 else self.power_timing_profile.findData(DEFAULT_PROFILE))
        self.power_timing_profile.currentIndexChanged.connect(
            lambda _index: self.web_settings.setValue('power_timing_profile', self.power_timing_profile.currentData()))
        self.csv_retention_days.setValue(self.web_settings.value('csv_retention_days', 14, type=int))
        self.csv_retention_days.valueChanged.connect(lambda days: self.web_settings.setValue('csv_retention_days', days))
        self.web_enabled.setChecked(False)
        self.web_enabled.setEnabled(False)
        self.web_retry.setEnabled(False)
        self.web_retry.setToolTip('Web upload is disabled in v1.0.9.5')

        discord_group = QGroupBox('Discord Webhook Reporting')
        discord_form = QFormLayout(discord_group)
        self.discord_enabled = QCheckBox('Enable automatic Discord reporting after each kingdom')
        self.discord_url = QLineEdit()
        self.discord_url.setPlaceholderText('https://discord.com/api/webhooks/...')
        self.discord_url.setEchoMode(QLineEdit.EchoMode.Password)
        self.discord_mode = QComboBox()
        self.discord_mode.addItem('UNPROTECTED only', 'unprotected')
        self.discord_mode.addItem('PROTECTED and UNPROTECTED', 'all')
        self.discord_min_power = QComboBox()
        for millions, label in ((500, '500 Million'), (1000, '1 Billion'),
                                (1500, '1.5 Billion'), (2000, '2 Billion')):
            self.discord_min_power.addItem(label, millions)
        self.discord_test = QPushButton('Test Webhook')
        self.discord_test.clicked.connect(self.test_discord_webhook)
        self.discord_status = QLabel('Disabled')
        discord_form.addRow(self.discord_enabled)
        discord_form.addRow('Webhook URL', self.discord_url)
        discord_form.addRow('Report mode', self.discord_mode)
        discord_form.addRow('Minimum castle power', self.discord_min_power)
        discord_form.addRow(self.discord_test)
        discord_form.addRow('Status', self.discord_status)
        layout.addWidget(discord_group)
        self.discord_enabled.setChecked(self.web_settings.value('discord_enabled', False, type=bool))
        self.discord_url.setText(str(self.web_settings.value('discord_url', '') or ''))
        saved_discord_mode = str(self.web_settings.value('discord_mode', 'unprotected'))
        mode_index = self.discord_mode.findData(saved_discord_mode)
        self.discord_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        saved_discord_power = self.web_settings.value('discord_min_power_millions', 500, type=int)
        power_index = self.discord_min_power.findData(saved_discord_power)
        self.discord_min_power.setCurrentIndex(power_index if power_index >= 0 else 0)
        self.discord_min_power.currentIndexChanged.connect(
            lambda _: self.web_settings.setValue('discord_min_power_millions', self.discord_min_power.currentData()))
        self.discord_enabled.toggled.connect(lambda v: self.web_settings.setValue('discord_enabled', v))
        self.discord_url.textChanged.connect(lambda v: self.web_settings.setValue('discord_url', v.strip()))
        self.discord_mode.currentIndexChanged.connect(
            lambda _: self.web_settings.setValue('discord_mode', self.discord_mode.currentData()))


        layout.addStretch()
        self.tabs.addTab(tab,'Settings')

    def test_discord_webhook(self):
        try:
            url = validate_webhook(self.discord_url.text())
        except ValueError as exc:
            self.discord_status.setText(str(exc))
            return
        self.discord_test.setEnabled(False)
        self.discord_status.setText('Testing...')
        def worker():
            try:
                send_test(url)
                self.messages.put(('discord_test', (True, 'Webhook test succeeded')))
            except Exception:
                self.messages.put(('discord_test', (False, 'Webhook test failed; check URL and connectivity')))
        threading.Thread(target=worker, daemon=True).start()

    def queue_discord_report(self, kingdom, castles, manual=False):
        if not self.discord_enabled.isChecked():
            return
        try:
            url = validate_webhook(self.discord_url.text())
        except ValueError:
            self.console.append('Discord: invalid webhook configuration; report skipped')
            return
        mode = self.discord_mode.currentData()
        min_power = int(self.discord_min_power.currentData() or 500)
        snapshot = list(castles)
        self.console.append(f'Discord: queuing kingdom {kingdom} {"manual power" if manual else "scan"} report')
        def worker():
            try:
                count, messages = send_kingdom_report(url, kingdom, snapshot, mode, min_power_millions=min_power, allow_unknown=manual, manual=manual)
                self.messages.put(('discord_report', (True, kingdom, count, messages)))
            except Exception:
                self.messages.put(('discord_report', (False, kingdom, 0, 0)))
        threading.Thread(target=worker, daemon=True).start()

    def save_web_settings(self):
        self.web_settings.setValue('web_enabled', self.web_enabled.isChecked())

    def retry_web_upload(self):
        return  # Web upload temporarily disabled.
        path = self.active_scan_csv or self.upload_last_csv
        if path is None:
            self.web_status.setText('No completed CSV available')
            return
        self.start_web_upload(path, manual=True)

    def start_web_upload(self, path, manual=False):
        return  # Web upload temporarily disabled.
        if self.upload_running:
            self.web_status.setText('Upload already in progress')
            return
        if not manual and not self.web_enabled.isChecked():
            return
        url = UPLOAD_ENDPOINT
        self.upload_running = True
        self.upload_last_csv = Path(path)
        self.web_retry.setEnabled(False)
        self.web_status.setText('Uploading CSV...')
        def worker():
            try:
                result = upload_csv(path, url)
                self.messages.put(('upload_ok', result))
            except Exception as exc:
                self.messages.put(('upload_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def make_table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        t.setAlternatingRowColors(True)
        t.setShowGrid(True)
        t.setWordWrap(False)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(27)
        t.setSortingEnabled(True)
        t.sortItems(0, Qt.SortOrder.AscendingOrder)
        header = t.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(85)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        t.setStyleSheet(
            "QTableWidget { gridline-color: #d6dbe1; font-size: 10pt; }"
            "QHeaderView::section { padding: 6px; font-weight: 600; }"
        )
        return t

    @staticmethod
    def fill_table(table, rows, row_statuses=None):
        header = table.horizontalHeader()
        sort_col = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            status = row_statuses[i] if row_statuses and i < len(row_statuses) else None
            if status is None and len(row) > 6 and str(row[6]).upper() in (
                'PROTECTED', 'UNPROTECTED', 'UNKNOWN', 'FERVOR'
            ):
                status = str(row[6]).upper()
            if status == 'PROTECTED':
                text_color = QColor('#ff7777')  # light red text
            elif status == 'UNPROTECTED':
                text_color = QColor('#78e08f')  # light green text
            elif status in ('UNKNOWN', 'FERVOR'):
                text_color = QColor('#ffd166')  # light amber text
            else:
                text_color = None
            for j, value in enumerate(row):
                column_name = table.horizontalHeaderItem(j).text()
                item_type = (TruceItem if column_name == 'Truce Status' else
                             NumericItem if column_name in ('Power (M)', 'X', 'Y') else
                             QTableWidgetItem)
                item = item_type(str(value if value is not None else ''))
                if column_name in ('Power (M)', 'X', 'Y'):
                    try: item.setData(Qt.ItemDataRole.UserRole, float(value) if value is not None and str(value).strip() else -1.0)
                    except (ValueError, TypeError): item.setData(Qt.ItemDataRole.UserRole, -1.0)
                if str(table.horizontalHeaderItem(j).text()) == 'Truce Status':
                    item.setData(Qt.ItemDataRole.UserRole, {'UNPROTECTED':0,'UNKNOWN':1,'FERVOR':1,'PROTECTED':2}.get(str(value).upper(),3))
                if text_color is not None:
                    item.setForeground(QBrush(text_color))
                table.setItem(i, j, item)
        table.setSortingEnabled(True)
        if sort_col >= 0: table.sortItems(sort_col, sort_order)
        table.resizeRowsToContents()

    def toggle_unknown_filter(self, checked):
        self.live_status_filter.setCurrentIndex(3 if checked else 0)

    def sync_unknown_toggle(self, *_):
        self.unknown_toggle.setChecked(self.live_status_filter.currentIndex() == 3)

    def refresh_live_table(self, *_):
        status_filter = self.live_status_filter.currentText().split(" ", 1)[0].upper()
        if status_filter == "FERVOR":
            status_filter = "UNKNOWN".upper()
        if status_filter == "FERVOR":
            status_filter = "UNKNOWN"
        minimum_millions = float(self.minimum_power.currentData() or 0)
        player_term = self.player.text().strip().casefold()
        def meets_minimum(record):
            if minimum_millions <= 0:
                return True
            try:
                return float(record.get('power') or 0) >= minimum_millions
            except (ValueError, TypeError):
                return False
        # Match the scanner's name-based power exemption: Night'sWatch castles
        # remain in the underlying scan results, but not in the live UI or stats.
        eligible = [r for r in self.live_results
                    if not ignored_castle(r.get('name'), r.get('x'), r.get('y'))]
        visible = [r for r in eligible
                   if (status_filter == 'ALL' or r['status'].upper() == status_filter)
                   and meets_minimum(r)
                   and (not player_term or player_term in str(r.get('name') or '').casefold())]
        rows = [[r.get('kingdom', ''), r['name'], r['x'], r['y'], ('Fervor' if str(r['status']).upper() == 'UNKNOWN' else r['status']), r.get('power', 0)] for r in visible]
        statuses = [r['status'] for r in visible]
        self.fill_table(self.live_table, rows, statuses)
        # Statistics reflect all eligible observations, independent of table filters.
        # Each kingdom gets its own row; the final row aggregates the entire batch.
        grouped = {}
        for record in eligible:
            kingdom = str(record.get('kingdom', '')).strip() or '?'
            counts = grouped.setdefault(kingdom, [0, 0, 0, 0])
            status = str(record.get('status', '')).upper()
            if status == 'UNPROTECTED':
                counts[0] += 1
            elif status == 'PROTECTED':
                counts[1] += 1
            elif status == 'UNKNOWN':
                counts[3] += 1
            counts[2] += 1  # UNKNOWN castles still contribute to total.
        ordered = sorted(grouped, key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else k))
        totals = [sum(grouped[k][i] for k in ordered) for i in range(4)]
        self.stats_table.setRowCount(len(ordered) + 1)
        for row, kingdom in enumerate(ordered + ['TOTAL']):
            values = grouped[kingdom] if kingdom != 'TOTAL' else totals
            display = [kingdom, f'{values[0]:,} ({values[3]:,})', f'{values[1]:,}', f'{values[2]:,}']
            for col, value in enumerate(display):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if col == 0 else Qt.AlignmentFlag.AlignRight))
                if col == 1:
                    item.setForeground(QBrush(QColor('#78e08f')))
                elif col == 2:
                    item.setForeground(QBrush(QColor('#ff8e8e')))
                if kingdom == 'TOTAL':
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.stats_table.setItem(row, col, item)
        self.stats_table.resizeRowsToContents()

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'CSV folder',self.folder.text())
        if folder:
            self.folder.setText(folder); self.refresh_data()

    def cleanup_expired_csv(self):
        """Remove CSVs older than the configured retention using creation time on Windows."""
        days = self.web_settings.value('csv_retention_days', 14, type=int)
        if days <= 0:
            return
        import time
        cutoff = time.time() - days * 86400
        folder = Path(self.folder.text())
        deleted = 0
        failures = []
        for path in folder.glob('*.csv'):
            try:
                if path.stat().st_ctime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError as exc:
                failures.append(f'{path.name}: {exc}')
        if deleted or failures:
            for attr in ('active_scan_csv', 'pending_scan_csv', 'upload_last_csv'):
                candidate = getattr(self, attr)
                if candidate is not None and not candidate.is_file():
                    setattr(self, attr, None)
            self.console.append(f'Automatic CSV cleanup ({days} days): deleted {deleted} file(s)')
            if failures:
                self.console.append('Automatic CSV cleanup errors: ' + '; '.join(failures))

    def delete_all_csv_files(self):
        """Immediately remove every .csv file in the selected snapshot folder."""
        folder = Path(self.folder.text())
        deleted = 0
        failures = []
        for path in folder.glob('*.csv'):
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f'{path.name}: {exc}')
        # Forget deleted snapshots and any pending upload reference to a missing file.
        if self.active_scan_csv is not None and not self.active_scan_csv.is_file():
            self.active_scan_csv = None
        if self.pending_scan_csv is not None and not self.pending_scan_csv.is_file():
            self.pending_scan_csv = None
        if self.upload_last_csv is not None and not self.upload_last_csv.is_file():
            self.upload_last_csv = None
        self.refresh_data()
        self.console.append(f'CSV cleanup: deleted {deleted} file(s) from {folder}')
        if failures:
            self.console.append('CSV cleanup errors: ' + '; '.join(failures))
        self.status.setText(f'Deleted {deleted} CSV file(s)' + (f'; {len(failures)} failed' if failures else ''))

    def refresh_data(self):
        self.scans=read_snapshots(Path(self.folder.text()))
        # The scanner can write CSV files outside the GUI's configured folder.
        # Never silently substitute an older snapshot for the current run.
        if self.active_scan_csv is not None:
            path = self.active_scan_csv
            try:
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    reader = csv.DictReader(stream)
                    if not set(COLUMNS).issubset(reader.fieldnames or []):
                        raise ValueError('CSV columns do not match the scanner format')
                    rows = list(reader)
                if len({r['last_seen_utc'] for r in rows}) > 1:
                    raise ValueError('CSV contains multiple scan timestamps')
                self.scans = [(p, rs) for p, rs in self.scans if p.resolve() != path.resolve()]
                self.scans.insert(0, (path, rows))
            except (OSError, UnicodeError, csv.Error, ValueError) as exc:
                self.scans = []
                self.status.setText(f'Latest scan snapshot unavailable: {exc}')
        self.latest=self.scans[0][1] if self.scans else []
        self.filter_castles()
        summary=[]
        for path,rows in self.scans:
            count=lambda status:sum(r['status']==status for r in rows)
            summary.append([path.name,rows[0]['kingdom'] if rows else path.name.split('_')[0],len(rows),count('UNPROTECTED'),count('PROTECTED'),count('UNKNOWN')])
        self.fill_table(self.scan_table,summary)
        self.fill_table(self.move_table,movements(self.scans))

    def explorer_rows(self):
        """Yield (snapshot path, record) pairs without collapsing historical observations."""
        snapshots = self.scans if self.explorer_history.currentIndex() == 1 else self.scans[:1]
        return [(path, record) for path, records in snapshots for record in records]

    def filter_castles(self,*_):
        term = self.search.text().casefold().strip()
        status = self.status_filter.currentText()
        if status == "Fervor":
            status = "UNKNOWN"
        source = self.explorer_rows()
        filtered = [(path, r) for path, r in source
                    if (status == 'ALL' or r['status'] == status)
                    and (not term or any(term in str(v).casefold() for v in r.values()))]
        self.explorer_visible = filtered
        self.fill_table(self.castles_table,
                        [[r.get('name', ''), r.get('x', ''), r.get('y', ''),
                          ('Fervor' if str(r.get('status', 'UNKNOWN')).upper() == 'UNKNOWN' else r.get('status', 'UNKNOWN')), r.get('status_source', ''),
                          r.get('last_seen_utc', '')] for _, r in filtered],
                        [str(r.get('status', 'UNKNOWN')).upper() for _, r in filtered])
        all_rows = [r for _, r in source]
        mode = self.explorer_history.currentText()
        self.summary.setText(
            f'{mode}: {len(all_rows)} castle observations | Displayed: {len(filtered)} | '
            f'Unprotected: {sum(r.get("status") == "UNPROTECTED" for r in all_rows)} | '
            f'Snapshots: {len(self.scans) if self.explorer_history.currentIndex() == 1 else min(len(self.scans), 1)}')

    def start_scan(self):
        if self.proc is not None or self.power_proc is not None or self.bulk_power_status is not None:
            return
        if not SCANNER.exists():
            QMessageBox.critical(self, 'Missing scanner', str(SCANNER)); return
        maskfile = BASE / 'gotwic_kingdom542_production_mask.json'
        if self.mode.currentText() == 'auto' and not maskfile.exists():
            QMessageBox.warning(self, 'Missing mask', str(maskfile)); return
        range_text = self.kingdom_range.text().strip()
        if range_text:
            try:
                kingdoms = parse_kingdom_selection(range_text)
            except ValueError as exc:
                QMessageBox.warning(self, 'Invalid kingdom selection', str(exc)); return
            if self.mode.currentText() != 'auto':
                QMessageBox.warning(self, 'Batch mode', 'Multiple kingdom scanning requires automatic scan mode.'); return
            self.batch_kingdoms = kingdoms
        else:
            self.batch_kingdoms = [self.kingdom.value()]
        self.batch_completed = 0
        self.scan_config = {
            'mode': self.mode.currentText(), 'port': self.port.value(),
            'show': self.show_filter.currentText(), 'buffer': self.buffer.value(),
            'mask': str(maskfile), 'sql': self.enable_sql.isChecked(),
            'verbose': self.verbose_power.isChecked(),
            'timing': self.power_timing_profile.currentData()
        }
        self.pending_scan_csv = None
        self.active_scan_csv = None
        self.player.clear()
        self.console.clear()
        self.live_results = []
        self.refresh_live_table()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._start_next_kingdom()

    def _start_next_kingdom(self):
        if not self.batch_kingdoms:
            self.scan_config = None
            self.proc = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status.setText(f'Scan finished — {self.batch_completed} kingdom(s) completed')
            return
        kingdom = self.batch_kingdoms.pop(0)
        self.current_scan_kingdom = kingdom
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        report = BASE / f'gui_scan_{stamp}_report.json'
        cfg = self.scan_config
        cmd = [sys.executable, '-u', str(SCANNER), '--kingdom', str(kingdom),
               '--mode', cfg['mode'], '--port', str(cfg['port']), '--show', cfg['show'],
               '--buf', str(cfg['buffer']), '--report', str(report), '--mask', cfg['mask'],
               '--auto-start', '--power-timing-profile', cfg['timing']]
        if not cfg['sql']: cmd.append('--no-db')
        if cfg['verbose']: cmd.append('--verbose')
        self.pending_scan_csv = None
        self.console.append(f'Kingdom {kingdom}: selecting kingdom, then scanning...')
        self.status.setText(f'Scanning kingdom {kingdom} — {len(self.batch_kingdoms)} remaining')
        try:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            self.proc = subprocess.Popen(cmd, cwd=str(BASE), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True,
                encoding='utf-8', errors='replace', creationflags=flags, env=env)
        except OSError as exc:
            self.batch_kingdoms = []
            self.proc = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            QMessageBox.critical(self, 'Launch failed', str(exc))
            return
        self.report_path = report
        self.ready_sent = True
        def read_output(proc):
            try:
                for line in proc.stdout:
                    self.messages.put(('line', line.rstrip()))
            finally:
                self.messages.put(('exit', proc.wait()))
        threading.Thread(target=read_output, args=(self.proc,), daemon=True).start()

    def confirm_ready(self):
        if self.proc and self.proc.poll() is None and not self.ready_sent:
            try:
                self.proc.stdin.write('\n');self.proc.stdin.flush()
                self.ready_sent=True
                self.status.setText('Scanning — '+self.mode.currentText())
            except (OSError,ValueError) as exc:self.console.append(f'Cannot confirm: {exc}')

    def stop_scan(self):
        if not self.proc or self.proc.poll() is not None:return
        if not self.ready_sent:
            self.console.append('Cancelling before scan start (no CSV will be created).')
            self.proc.terminate()
            self.stop_btn.setEnabled(False)
            return
        if self.mode.currentText()=='manual' and self.ready_sent:
            self.console.append('Finishing manual scan (requesting Ctrl+C)...')
        else:
            self.console.append('Requesting scanner stop...')
        try:
            if os.name=='nt':self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:self.proc.send_signal(signal.SIGINT)
        except (OSError,ValueError) as exc:self.console.append(f'Stop failed: {exc}')
        self.batch_kingdoms = []  # Stop also cancels all remaining kingdoms.
        self.stop_btn.setEnabled(False)

    def tick(self):
        self.poll_power()
        self.poll_bulk_power()
        for _ in range(300):
            try:kind,value=self.messages.get_nowait()
            except queue.Empty:break
            if kind == 'discord_test':
                self.discord_test.setEnabled(True)
                self.discord_status.setText(value[1])
                continue
            if kind == 'discord_report':
                ok, kingdom, count, messages = value
                note = (f'Discord: kingdom {kingdom} sent ({count} castles, {messages} messages)'
                        if ok else f'Discord: kingdom {kingdom} report failed; scanning unaffected')
                self.discord_status.setText(note)
                self.console.append(note)
                continue
            if kind == 'sql_check':
                connected, note = value
                self.enable_sql.setChecked(connected)
                self.enable_sql.setEnabled(connected)
                self.enable_sql.setToolTip(note)
                self.console.append(note)
                continue
            if kind=='upload_ok':
                self.upload_running = False
                self.web_retry.setEnabled(True)
                self.web_status.setText('Already imported' if value.get('duplicate') else f"Uploaded {value.get('inserted_rows', 0)} rows")
                continue
            if kind=='upload_error':
                self.upload_running = False
                self.web_retry.setEnabled(True)
                self.web_status.setText('Upload failed — retry available')
                self.console.append('Web upload: ' + value)
                continue
            if kind=='line':
                clean_line = re.sub(r'\x1b\[[0-9;]*m', '', value).strip()
                # Scanner status lines are rendered as structured, color-coded table rows.
                castle_match = re.match(
                    r'^(.*?)\s+X=(\d+)\s+Y=(\d+):\s*(PROTECTED|UNPROTECTED|UNKNOWN)\s*$',
                    clean_line, re.IGNORECASE
                )
                if castle_match:
                    name, x, y, status = castle_match.groups()
                    status = status.upper()
                    if ignored_castle(name, x, y):
                        continue
                    self.live_results.append({'kingdom': self.current_scan_kingdom, 'name': name.strip(), 'x': int(x), 'y': int(y), 'status': status})
                    self.refresh_live_table()
                    continue
                lowered = clean_line.casefold()
                # Do not show raw tracebacks, encoding errors, or scanner error details in the GUI.
                if any(token in lowered for token in (
                    'traceback', 'exception', 'error:', 'report errors', 'warning:',
                    'failed with', 'connectionerror', 'timeouterror', 'modulenotfounderror',
                    'filenotfounderror', 'charmap', "codec can't encode", 'stop: runtimeerror',
                    'could not encode', 'unicodeencodeerror'
                )):
                    continue
                if clean_line.startswith('CSV:'):
                    raw_path = clean_line.partition(':')[2].strip().strip('"')
                    if raw_path:
                        self.pending_scan_csv = Path(raw_path)
                if clean_line:
                    if clean_line.startswith('GOTWiC Scanner v2.2 |'):
                        clean_line = f'GOTWiC Scanner v{APP_VERSION} |' + clean_line.split('|', 1)[1]
                    self.console.append(clean_line)
            else:
                self.proc = None
                completed_csv = self.pending_scan_csv
                successful = value == 0
                report = None
                if self.report_path.exists():
                    try:
                        report = json.loads(self.report_path.read_text(encoding='utf-8'))
                        successful = successful and not report.get('errors') and (report.get('csv_export') or {}).get('status') == 'success'
                    except (OSError, ValueError) as exc:
                        successful = False
                        self.console.append(f'Could not load kingdom report: {exc}')
                else:
                    successful = False
                if successful and completed_csv is not None and completed_csv.is_file():
                    self.active_scan_csv = completed_csv
                    self.batch_completed += 1
                    # Persist only a genuinely completed kingdom, including the last batch member.
                    self.web_settings.setValue('last_successful_kingdom', int(self.current_scan_kingdom))
                    self.kingdom.setValue(int(self.current_scan_kingdom))
                    self.cleanup_expired_csv()
                    self.refresh_data()  # Each completed kingdom appears immediately in Explorer.
                    lookup = {(str(c.get('name')), int(c.get('x')), int(c.get('y'))): c.get('power_millions', 0)
                              for c in report.get('castles', [])}
                    for castle in self.live_results:
                        if castle.get('kingdom') == self.current_scan_kingdom:
                            castle['power'] = lookup.get((castle['name'], castle['x'], castle['y']), 0)
                    self.refresh_live_table()
                    self.console.append(f'Kingdom {self.current_scan_kingdom} complete — {len(self.batch_kingdoms)} remaining')
                    self.queue_discord_report(self.current_scan_kingdom, report.get('castles', []))
                    if self.batch_kingdoms:
                        self._start_next_kingdom()
                        continue
                else:
                    self.console.append(f'Kingdom {self.current_scan_kingdom} did not complete successfully; batch stopped.')
                    self.batch_kingdoms = []
                self.scan_config = None
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                self.status.setText(f'Scan finished — {self.batch_completed} kingdom(s) completed')

    def request_power_at_live_row(self, row, column):
        self.live_table.selectRow(row)
        self.request_power_from_live(keep_open=True)

    def show_live_context_menu(self, position):
        item = self.live_table.itemAt(position)
        if item is None:
            return
        self.live_table.selectRow(item.row())
        menu = QMenu(self.live_table)
        action = menu.addAction('Get Power for selected castle')
        action.setEnabled(self.proc is None and self.power_proc is None)
        if menu.exec(self.live_table.viewport().mapToGlobal(position)) == action:
            self.request_power_from_live()

    def request_power_from_live(self, keep_open=False):
        if self.proc is not None or self.power_proc is not None or self.bulk_power_status is not None:
            QMessageBox.warning(self, 'Busy', 'Finish the active scan or power lookup first.')
            return
        row = self.live_table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'Select castle', 'Select a castle in Live castle results first.')
            return
        try:
            kingdom = int(self.live_table.item(row, 0).text())
            name = self.live_table.item(row, 1).text()
            x = int(float(self.live_table.item(row, 2).text()))
            y = int(float(self.live_table.item(row, 3).text()))
        except (AttributeError, ValueError, TypeError):
            QMessageBox.warning(self, 'Invalid castle', 'Selected castle has invalid coordinates.')
            return
        # The live table has no player ID or snapshot ID. Resolve against the
        # latest persisted snapshot and reject missing/ambiguous matches.
        snapshot = next(((path, rows) for path, rows in self.scans if rows and str(rows[0].get('kingdom')) == str(kingdom)), None)
        matches = [r for r in (snapshot[1] if snapshot else []) if r.get('name') == name
                   and str(r.get('x')) == str(x) and str(r.get('y')) == str(y)
                   and str(r.get('kingdom')) == str(kingdom)]
        if len(matches) != 1 or snapshot is None:
            QMessageBox.warning(self, 'Snapshot unavailable',
                'Could not uniquely match this castle to the latest saved scan. '
                'Finish the scan and reload its snapshot before requesting power.')
            return
        record = matches[0]
        target = {'kingdom':int(record['kingdom']), 'x':x, 'y':y,
                  'name':name, 'player_id':record.get('player_id',''),
                  'stamp':record['last_seen_utc'], 'csv':str(snapshot[0]),
                  'sql':False}
        self.launch_power_lookup(target, keep_open=keep_open)

    def request_power_at_row(self, row, column):
        self.castles_table.selectRow(row)
        self.request_power(keep_open=True)

    def show_castle_context_menu(self, position):
        item = self.castles_table.itemAt(position)
        if item is None:
            return
        self.castles_table.selectRow(item.row())
        menu = QMenu(self.castles_table)
        action = menu.addAction('Get Power for selected castle')
        action.setEnabled(self.proc is None and self.power_proc is None)
        if menu.exec(self.castles_table.viewport().mapToGlobal(position)) == action:
            self.request_power()

    def request_power(self, keep_open=False):
        if self.proc is not None or self.power_proc is not None or self.bulk_power_status is not None:
            QMessageBox.warning(self, 'Busy', 'Finish the active scan or power lookup first.')
            return
        row = self.castles_table.currentRow()
        if row < 0 or not self.scans:
            QMessageBox.information(self, 'Select castle', 'Select a castle in Castle Explorer first.')
            return
        def cell(column):
            return self.castles_table.item(row, column).text()
        try:
            x, y = int(float(cell(1))), int(float(cell(2)))
        except (TypeError, ValueError):
            QMessageBox.warning(self, 'Invalid castle', 'Selected castle has invalid coordinates.')
            return
        name, stamp = cell(0), cell(5)
        # Sorting changes visible row indices; identify the original snapshot by
        # the displayed record and its scan timestamp, not the latest CSV path.
        matches = [(path, r) for path, r in self.explorer_rows()
                   if str(r.get('x')) == str(x) and str(r.get('y')) == str(y)
                   and str(r.get('name', '')) == name
                   and str(r.get('last_seen_utc')) == stamp]
        if len(matches) != 1:
            QMessageBox.warning(self, 'Ambiguous selection', 'Could not uniquely identify selected snapshot row.')
            return
        snapshot, record = matches[0]
        kingdom = int(record['kingdom'])
        player_id = record.get('player_id', '')
        target = {'kingdom':kingdom,'x':x,'y':y,'name':name,'player_id':player_id,
                  'stamp':stamp,'csv':str(snapshot),'sql':False}
        self.launch_power_lookup(target, keep_open=keep_open)

    @staticmethod
    def has_power(record):
        """Power is persisted in status_source; zero/missing is not a known value."""
        try:
            return float(record.get('status_source') or 0) > 0
        except (TypeError, ValueError):
            return False

    def start_bulk_power(self, status):
        if self.proc is not None or self.power_proc is not None or self.bulk_power_status is not None:
            QMessageBox.warning(self, 'Busy', 'Finish the active scan or power lookup first.')
            return
        if not self.scans:
            QMessageBox.information(self, 'No scan', 'No completed scan snapshot is available.')
            return
        snapshot, rows = self.scans[0]
        if not snapshot.is_file():
            QMessageBox.warning(self, 'Snapshot unavailable', 'The latest scan CSV is missing.')
            return
        eligible = [r for r in rows if (str(r.get('status', '')).upper() in ('UNPROTECTED', 'UNKNOWN') if status == 'UNPROTECTED' else str(r.get('status', '')).upper() == status)
                    and not self.has_power(r)
                    and not ignored_castle(r.get('name'), r.get('x'), r.get('y'))]
        targets = []
        for r in eligible:
            try:
                x, y = int(r['x']), int(r['y'])
                kingdom = int(r['kingdom'])
                if not (0 <= x <= 650 and 0 <= y <= 710):
                    raise ValueError('Out-of-range coordinates')
                targets.append({'kingdom': kingdom, 'x': x, 'y': y,
                    'name': str(r['name']), 'player_id': r.get('player_id', ''),
                    'stamp': r['last_seen_utc'], 'csv': str(snapshot),
                    'sql': False})
            except (ValueError, KeyError, TypeError):
                self.console.append(f"Bulk power: skipped invalid record {r.get('name', '?')}")
        if not targets:
            self.status.setText(f'Bulk {status}: no castles missing power in latest scan')
            return
        self.manual_power_reports = []
        self.bulk_power_status = status
        self.bulk_power_queue = targets
        self.bulk_power_total = len(targets)
        self.bulk_power_done = 0
        self.bulk_power_failed = 0
        self.bulk_protected_btn.setEnabled(False)
        self.bulk_unprotected_btn.setEnabled(False)
        self.bulk_unknown_btn.setEnabled(False)
        self.console.append(f'Bulk {status}: checking {len(targets)} castles missing power from {snapshot.name}')
        self.launch_bulk_worker()

    def launch_bulk_worker(self):
        cmd = [sys.executable, str(BASE/'castle_power_detector.py'), '--batch', '--port', str(self.port.value()),
               '--power-timing-profile', self.power_timing_profile.currentData()]
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        try:
            worker = subprocess.Popen(cmd, cwd=str(BASE), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                      bufsize=1, env=env)
            self.bulk_worker = worker
            targets = list(self.bulk_power_queue)
            self.bulk_power_queue = targets
            def read_results():
                try:
                    for line in worker.stdout:
                        try:
                            self.bulk_messages.put(json.loads(line))
                        except json.JSONDecodeError:
                            self.bulk_messages.put({'success':False,'error':'Invalid worker response'})
                finally:
                    self.bulk_messages.put({'_finished':True})
            threading.Thread(target=read_results, daemon=True).start()
            try:
                for target in targets:
                    worker.stdin.write(json.dumps({k:target[k] for k in ('x','y','name')}, ensure_ascii=False)+'\n')
                worker.stdin.flush()
                worker.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                self.console.append(f'Bulk worker input failed: {exc}')
        except OSError as exc:
            self.console.append(f'Bulk worker startup failed: {exc}')
            self.bulk_worker = None
            self.bulk_power_failed = self.bulk_power_total
            self.bulk_power_done = self.bulk_power_total
            self.finish_bulk_power()

    def finish_bulk_power(self):
        self.flush_manual_power_reports()
        status = self.bulk_power_status
        self.bulk_power_status = None
        self.bulk_worker = None
        self.bulk_protected_btn.setEnabled(True)
        self.bulk_unprotected_btn.setEnabled(True)
        self.bulk_unknown_btn.setEnabled(True)
        message = (f'Bulk {status} complete: {self.bulk_power_done - self.bulk_power_failed} saved, '
                   f'{self.bulk_power_failed} failed, {self.bulk_power_total} attempted')
        self.console.append(message)
        self.status.setText(message)

    def poll_bulk_power(self):
        if self.bulk_worker is None:
            return
        while True:
            try:
                result = self.bulk_messages.get_nowait()
            except queue.Empty:
                break
            if result.get('_finished'):
                if self.bulk_power_done < self.bulk_power_total:
                    remaining = self.bulk_power_total - self.bulk_power_done
                    self.bulk_power_failed += remaining
                    self.bulk_power_done += remaining
                    self.console.append(f'Bulk worker ended early: {remaining} unprocessed castles')
                self.finish_bulk_power()
                break
            if result.get('fatal'):
                self.console.append(f"Bulk CDP initialization failed: {result['fatal']}")
                continue
            if self.bulk_power_done >= len(self.bulk_power_queue):
                continue
            target = self.bulk_power_queue[self.bulk_power_done]
            self.power_target = target
            self.handle_power_result(result)
            self.power_target = None
            self.bulk_power_done += 1
            self.status.setText(f'Bulk {self.bulk_power_status}: {self.bulk_power_done}/{self.bulk_power_total}')

    def flush_manual_power_reports(self):
        # Only successfully retrieved castles are reported, once per operation.
        by_kingdom = {}
        for castle in self.manual_power_reports:
            by_kingdom.setdefault(castle['kingdom'], []).append(castle)
        self.manual_power_reports = []
        for kingdom, castles in by_kingdom.items():
            self.queue_discord_report(kingdom, castles, manual=True)

    def next_bulk_power(self):
        if self.bulk_power_status is None:
            return
        if not self.bulk_power_queue:
            self.flush_manual_power_reports()
            status = self.bulk_power_status
            self.bulk_power_status = None
            self.bulk_protected_btn.setEnabled(True)
            self.bulk_unprotected_btn.setEnabled(True)
            self.bulk_unknown_btn.setEnabled(True)
            message = (f'Bulk {status} complete: {self.bulk_power_done - self.bulk_power_failed} saved, '
                       f'{self.bulk_power_failed} failed, {self.bulk_power_total} attempted')
            self.console.append(message)
            self.status.setText(message)
            return
        target = self.bulk_power_queue.pop(0)
        self.status.setText(f'Bulk {self.bulk_power_status}: '
                            f'{self.bulk_power_done + 1}/{self.bulk_power_total} — {target["name"]}')
        self.launch_power_lookup(target)
        if self.power_proc is None:
            self.bulk_power_done += 1
            self.bulk_power_failed += 1
            self.console.append(f'Bulk power: failed to launch for {target["name"]}')
            QTimer.singleShot(0, self.next_bulk_power)

    def launch_power_lookup(self, target, keep_open=False):
        x, y, name = target['x'], target['y'], target['name']
        cmd = [sys.executable, str(BASE/'castle_power_detector.py'), '--x',str(x),'--y',str(y),
               '--name',name,'--port',str(self.port.value()),
               '--power-timing-profile',self.power_timing_profile.currentData()]
        if keep_open:
            cmd.extend(['--keep-open', '--kingdom', str(target['kingdom'])])
        self.get_power_btn.setEnabled(False)
        self.live_power_btn.setEnabled(False)
        self.status.setText(f'Looking up power for {name} at X={x}, Y={y}...')
        try:
            power_env = os.environ.copy()
            power_env['PYTHONIOENCODING'] = 'utf-8'
            power_env['PYTHONUTF8'] = '1'
            self.power_proc = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                         env=power_env)
            self.power_target = target
        except OSError as exc:
            self.power_proc = None
            self.get_power_btn.setEnabled(True)
            self.live_power_btn.setEnabled(True)
            if self.bulk_power_status is None:
                QMessageBox.critical(self, 'Power lookup failed', str(exc))
            else:
                self.console.append(f'Bulk power launch failed: {exc}')

    def poll_power(self):
        if self.power_proc is None or self.power_proc.poll() is None:
            return
        proc = self.power_proc
        self.power_proc = None
        self.get_power_btn.setEnabled(True)
        self.live_power_btn.setEnabled(True)
        out, err = proc.communicate()
        try:
            result = json.loads(out)
        except (ValueError, TypeError):
            result = {'success':False,'error':err or out or 'Invalid power lookup output'}
        if proc.returncode and result.get('success'):
            result = {'success':False,'error':err or 'Power detector exited unsuccessfully'}
        self.handle_power_result(result)
        self.power_target = None

    def lookup_castle_status(self, target):
        # Match the exact CSV snapshot and castle identity; never infer status from GUI filters.
        try:
            with Path(target['csv']).open(encoding='utf-8-sig', newline='') as handle:
                for row in csv.DictReader(handle):
                    if (str(row.get('kingdom')) == str(target['kingdom'])
                            and str(row.get('x')) == str(target['x'])
                            and str(row.get('y')) == str(target['y'])
                            and str(row.get('name')) == str(target['name'])
                            and str(row.get('last_seen_utc')) == str(target['stamp'])):
                        return str(row.get('status') or 'UNKNOWN').upper()
        except (OSError, csv.Error, UnicodeError, KeyError):
            pass
        return 'UNKNOWN'

    def handle_power_result(self, result):
        try:
            if not result.get('success'):
                raise ValueError(result.get('error') or 'Power extraction failed')
            target = self.power_target
            if result.get('player') != target['name']:
                raise ValueError('Returned player name does not match the selected castle')
            from power_storage import save_power
            value = save_power(target, result['power_millions'])
            self.refresh_data()
            for castle in self.live_results:
                if (castle['name'] == target['name'] and castle['x'] == target['x']
                        and castle['y'] == target['y']):
                    castle['power'] = f'{value:.3f}'
            self.refresh_live_table()
            report_castle = {'kingdom': int(target['kingdom']), 'name': target['name'],
                             'x': target['x'], 'y': target['y'],
                             'status': self.lookup_castle_status(target),
                             'power_millions': float(value)}
            if self.bulk_power_status is not None:
                self.manual_power_reports.append(report_castle)
            else:
                self.queue_discord_report(target['kingdom'], [report_castle], manual=True)
            self.status.setText(f"Power saved: {target['name']} — {value:.3f} M")
            if self.bulk_power_status is not None:
                self.console.append(f'Bulk power {self.bulk_power_done + 1}/{self.bulk_power_total}: {target["name"]} — {value:.3f} M')
        except Exception as exc:
            self.status.setText('Power lookup failed — snapshot unchanged')
            if self.bulk_power_status is None:
                QMessageBox.warning(self, 'Power lookup', str(exc))
            else:
                self.bulk_power_failed += 1
                self.console.append(f'Bulk power failed ({self.bulk_power_done + 1}/{self.bulk_power_total}): {self.power_target["name"]} — {exc}')

    def closeEvent(self,event):
        if (self.proc is not None and self.proc.poll() is None) or self.power_proc is not None or self.bulk_power_status is not None:
            QMessageBox.warning(self,'Scanner active','Finish or stop the scanner before closing the GUI.')
            event.ignore();return
        event.accept()


# Multi-file, changed-only updater. Release manifest lists every distributable file.
APP_VERSION = '1.0.9.13'
UPDATE_BASE_URL = "https://raw.githubusercontent.com/gotwic-scanner/castle_scanner/main/"
UPDATE_CHECK_TIMEOUT_SECONDS = 5
UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 15

def _version_tuple(value):
    try:
        return tuple(int(part) for part in str(value).strip().split("."))
    except (TypeError, ValueError):
        return ()

def _safe_update_name(name):
    return (isinstance(name, str) and bool(name) and name not in (".", "..")
            and "/" not in name and "\\" not in name
            and Path(name).name == name)

def _update_file_hash(path):
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()

def check_for_gui_update():
    import hashlib
    import shutil
    import tempfile
    import time
    import urllib.request

    try:
        req = urllib.request.Request(UPDATE_BASE_URL + "version.json",
                                     headers={"User-Agent": "GOTWiC-Scanner-Updater"})
        with urllib.request.urlopen(req, timeout=UPDATE_CHECK_TIMEOUT_SECONDS) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError("Update manifest exceeds 128 KiB")
        if raw.lstrip().startswith((b"<", b"<!")):
            raise ValueError("Update server returned HTML instead of version.json")
        manifest = json.loads(raw.decode("utf-8"))
        remote = manifest.get("version")
        if not _version_tuple(remote) or _version_tuple(remote) <= _version_tuple(APP_VERSION):
            return False
        entries = manifest.get("files")
        if not isinstance(entries, list) or not entries or len(entries) > 100:
            QMessageBox.critical(None, "Update unavailable",
                "The server does not provide a valid multi-file manifest. The application will close.")
            return True
        seen = set()
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("Invalid file record")
            name = item.get("path")
            digest = item.get("sha256")
            if (not _safe_update_name(name) or name in seen or
                not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest) or
                not isinstance(item.get("size"), int) or item["size"] < 0 or item["size"] > 30_000_000):
                raise ValueError("Invalid update file metadata")
            seen.add(name)
    except Exception as exc:
        QMessageBox.warning(None, "GOTWiC Scanner update check failed",
            f"Could not check for updates at GitHub:\n{exc}\n\n"
            "Select OK to continue with the installed version.")
        return False

    root = Path(__file__).resolve().parent
    changed = [item for item in entries
               if not (root/item["path"]).is_file()
               or _update_file_hash(root/item["path"]) != item["sha256"].lower()]
    answer = QMessageBox.question(None, "GOTWiC Scanner update",
        f"Version {remote} is available (installed: {APP_VERSION}).\n"
        f"{len(changed)} changed file(s) need downloading.\n\nThis update is required. Declining will close the application. Install now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes)
    if answer != QMessageBox.StandardButton.Yes:
        QMessageBox.information(None, "Update required", "The update was declined. The application will now close.")
        return True

    stage = Path(tempfile.mkdtemp(prefix="gotwic_update_"))
    started = time.monotonic()
    try:
        for item in changed:
            name = item["path"]
            remaining = UPDATE_DOWNLOAD_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("The 15-second overall download timeout was exceeded")
            req = urllib.request.Request(UPDATE_BASE_URL + name,
                                        headers={"User-Agent": "GOTWiC-Scanner-Updater"})
            with urllib.request.urlopen(req, timeout=remaining) as response, (stage/name).open("wb") as target:
                while True:
                    remaining = UPDATE_DOWNLOAD_TIMEOUT_SECONDS - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("The 15-second overall download timeout was exceeded")
                    block = response.read(min(65536, item["size"] + 1 - target.tell()))
                    if not block:
                        break
                    target.write(block)
                    if target.tell() > item["size"]:
                        raise ValueError("Downloaded file exceeds declared size: " + name)
            if (stage/name).stat().st_size != item["size"] or _update_file_hash(stage/name) != item["sha256"].lower():
                raise ValueError("Downloaded file failed verification: " + name)
            if name.endswith(".py"):
                compile((stage/name).read_text(encoding="utf-8"), name, "exec")
        if time.monotonic() - started > UPDATE_DOWNLOAD_TIMEOUT_SECONDS:
            raise TimeoutError("The 15-second overall download timeout was exceeded")
        (stage/"plan.json").write_text(json.dumps({"files": changed}), encoding="utf-8")
        # Copy the helper outside the installation folder, so updater.py itself may be replaced.
        helper = root/"updater.py"
        if not helper.is_file():
            raise OSError("updater.py is missing")
        helper_copy = stage/"run_updater.py"
        shutil.copy2(helper, helper_copy)
        subprocess.Popen([sys.executable, str(helper_copy), "--pid", str(os.getpid()),
                          "--root", str(root), "--stage", str(stage)],
                         cwd=str(root), close_fds=True)
        return True
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        QMessageBox.critical(None, "Update failed",
            f"Could not download or stage the complete update:\n{exc}\n\n"
            "Select OK to close the application. The update is required before continuing.")
        return True


if __name__=='__main__':
    app=QApplication(sys.argv)
    if check_for_gui_update():
        sys.exit(0)
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('-v','--verbose',action='store_true')
    cli_args,_=parser.parse_known_args(sys.argv[1:])
    window=MainWindow()
    if cli_args.verbose:window.verbose_power.setChecked(True)
    window.show()
    sys.exit(app.exec())
