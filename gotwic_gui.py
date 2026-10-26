#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.6
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

from PySide6.QtCore import Qt, QTimer
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
        if not re.match(r'^\d+_\d{8}_\d{6}_\d{6}\.csv$', path.name):
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

class TruceItem(QTableWidgetItem):
    def __lt__(self, other):
        order = {'UNPROTECTED':0, 'UNKNOWN':1, 'PROTECTED':2}
        return order.get(self.text().upper(),3) < order.get(other.text().upper(),3)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('GOTWiC Scanner — Desktop Manager')
        self.resize(1150, 780)
        self.proc = None
        self.messages = queue.Queue()
        self.scans = []
        self.latest = []
        self.live_results = []
        self.power_proc = None
        self.power_target = None
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.build_scanner()
        self.build_explorer()
        self.build_history()
        self.build_settings()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(150)
        self.refresh_data()

    def build_scanner(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        controls = QGroupBox('Scan configuration'); form = QGridLayout(controls)
        self.kingdom = QSpinBox(); self.kingdom.setRange(1,999999); self.kingdom.setValue(542)
        self.mode = QComboBox(); self.mode.addItems(['auto','manual'])
        self.show_filter = QComboBox(); self.show_filter.addItems(['ALL','OPEN'])
        self.player = QLineEdit(); self.player.setPlaceholderText('Optional console-only player filter')
        self.buffer = QSpinBox(); self.buffer.setRange(1,32); self.buffer.setValue(4)
        self.verbose_power = QCheckBox('Castle power (verbose) — slower scan')
        self.enable_sql = QCheckBox('Enable SQL import (omit --no-db)')
        form.addWidget(QLabel('Kingdom'),0,0); form.addWidget(self.kingdom,0,1)
        form.addWidget(QLabel('Mode'),0,2); form.addWidget(self.mode,0,3)
        form.addWidget(QLabel('Show'),1,0); form.addWidget(self.show_filter,1,1)
        form.addWidget(QLabel('Player'),1,2); form.addWidget(self.player,1,3)
        form.addWidget(QLabel('Buffer'),2,0); form.addWidget(self.buffer,2,1)
        form.addWidget(self.verbose_power,3,0,1,2)
        form.addWidget(self.enable_sql,3,2,1,2)
        layout.addWidget(controls)
        buttons = QHBoxLayout()
        self.start_btn = QPushButton('Start scan'); self.start_btn.clicked.connect(self.start_scan)
        self.ready_btn = QPushButton('Confirm game ready / Start scanning'); self.ready_btn.clicked.connect(self.confirm_ready); self.ready_btn.setEnabled(False)
        self.stop_btn = QPushButton('Finish manual scan / Stop'); self.stop_btn.clicked.connect(self.stop_scan); self.stop_btn.setEnabled(False)
        buttons.addWidget(self.start_btn); buttons.addWidget(self.ready_btn); buttons.addWidget(self.stop_btn)
        layout.addLayout(buttons)
        self.status = QLabel('Idle — open Chrome with remote debugging and load the game map.')
        layout.addWidget(self.status)
        layout.addWidget(QLabel('Live castle results — protected: light red; unprotected: light green'))
        self.live_table = self.make_table(['Castle', 'X', 'Y', 'Truce Status', 'Power (M)'])
        layout.addWidget(self.live_table, 3)
        self.live_table.cellDoubleClicked.connect(self.request_power_at_live_row)
        self.live_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.live_table.customContextMenuRequested.connect(self.show_live_context_menu)
        self.live_power_btn = QPushButton('Get Power for selected castle (including truced)')
        self.live_power_btn.clicked.connect(self.request_power_from_live)
        layout.addWidget(self.live_power_btn)
        self.console = QTextEdit(); self.console.setReadOnly(True)
        self.console.setMaximumHeight(155)
        layout.addWidget(QLabel('Scanner messages'))
        layout.addWidget(self.console, 1)
        self.tabs.addTab(tab,'Scanner & Console')

    def build_explorer(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        bar = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText('Filter by player, ID, status or coordinate'); self.search.textChanged.connect(self.filter_castles)
        self.status_filter = QComboBox(); self.status_filter.addItems(['ALL','UNPROTECTED','PROTECTED','UNKNOWN']); self.status_filter.currentTextChanged.connect(self.filter_castles)
        bar.addWidget(self.search); bar.addWidget(self.status_filter); layout.addLayout(bar)
        self.summary = QLabel('No snapshots loaded'); layout.addWidget(self.summary)
        self.castles_table = self.make_table(['Castle', 'X', 'Y', 'Truce Status', 'Power (M)', 'last_seen_utc']); layout.addWidget(self.castles_table)
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
        self.folder = QLineEdit(str(BASE)); self.folder.setReadOnly(True)
        choose = QPushButton('Choose CSV folder'); choose.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout(); folder_row.addWidget(self.folder); folder_row.addWidget(choose)
        form.addRow('Chrome debugging port',self.port); form.addRow('CSV snapshot folder',folder_row)
        layout.addLayout(form)
        reload_btn = QPushButton('Reload CSV snapshots'); reload_btn.clicked.connect(self.refresh_data)
        layout.addWidget(reload_btn)
        layout.addWidget(QLabel('SQL import uses scanner database environment variables (GOTWIC_DB_*). Enable SQL import in Scan configuration.'))
        layout.addStretch()
        self.tabs.addTab(tab,'Settings')

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
                'PROTECTED', 'UNPROTECTED', 'UNKNOWN'
            ):
                status = str(row[6]).upper()
            if status == 'PROTECTED':
                text_color = QColor('#ff7777')  # light red text
            elif status == 'UNPROTECTED':
                text_color = QColor('#78e08f')  # light green text
            elif status == 'UNKNOWN':
                text_color = QColor('#ffd166')  # light amber text
            else:
                text_color = None
            for j, value in enumerate(row):
                item = (TruceItem if str(table.horizontalHeaderItem(j).text()) == 'Truce Status' else QTableWidgetItem)(str(value if value is not None else ''))
                if str(table.horizontalHeaderItem(j).text()) in ('Power (M)', 'X', 'Y'):
                    try: item.setData(Qt.ItemDataRole.EditRole, float(value or 0))
                    except (ValueError, TypeError): pass
                if str(table.horizontalHeaderItem(j).text()) == 'Truce Status':
                    item.setData(Qt.ItemDataRole.UserRole, {'UNPROTECTED':0,'UNKNOWN':1,'PROTECTED':2}.get(str(value).upper(),3))
                if text_color is not None:
                    item.setForeground(QBrush(text_color))
                table.setItem(i, j, item)
        table.setSortingEnabled(True)
        if sort_col >= 0: table.sortItems(sort_col, sort_order)
        table.resizeRowsToContents()

    def refresh_live_table(self):
        rows = [[r['name'], r['x'], r['y'], r['status'], r.get('power', 0)] for r in self.live_results]
        statuses = [r['status'] for r in self.live_results]
        self.fill_table(self.live_table, rows, statuses)

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'CSV folder',self.folder.text())
        if folder:
            self.folder.setText(folder); self.refresh_data()

    def refresh_data(self):
        self.scans=read_snapshots(Path(self.folder.text()))
        self.latest=self.scans[0][1] if self.scans else []
        self.filter_castles()
        summary=[]
        for path,rows in self.scans:
            count=lambda status:sum(r['status']==status for r in rows)
            summary.append([path.name,rows[0]['kingdom'] if rows else path.name.split('_')[0],len(rows),count('UNPROTECTED'),count('PROTECTED'),count('UNKNOWN')])
        self.fill_table(self.scan_table,summary)
        self.fill_table(self.move_table,movements(self.scans))

    def filter_castles(self,*_):
        term=self.search.text().casefold().strip(); status=self.status_filter.currentText()
        filtered=[r for r in self.latest if (status=='ALL' or r['status']==status) and (not term or any(term in str(v).casefold() for v in r.values()))]
        self.fill_table(self.castles_table, [[r.get('name', ''), r.get('x', ''), r.get('y', ''), r.get('status', 'UNKNOWN'), r.get('status_source', 0), r.get('last_seen_utc', '')] for r in filtered],
                        [str(r.get('status', 'UNKNOWN')).upper() for r in filtered])
        self.summary.setText(f'Latest snapshot: {len(self.latest)} castles | Displayed: {len(filtered)} | Unprotected: {sum(r["status"]=="UNPROTECTED" for r in self.latest)}')

    def start_scan(self):
        if self.proc is not None or self.power_proc is not None: return
        if not SCANNER.exists():
            QMessageBox.critical(self,'Missing scanner',str(SCANNER)); return
        maskfile=BASE/'gotwic_kingdom542_production_mask.json'
        if self.mode.currentText()=='auto' and not maskfile.exists():
            QMessageBox.warning(self,'Missing mask',str(maskfile)); return
        stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        report=BASE/f'gui_scan_{stamp}_report.json'
        cmd=[sys.executable,'-u',str(SCANNER),'--kingdom',str(self.kingdom.value()),
             '--mode',self.mode.currentText(),'--port',str(self.port.value()),
             '--show',self.show_filter.currentText(),'--buf',str(self.buffer.value()),'--report',str(report),
             '--mask',str(maskfile)]
        if not self.enable_sql.isChecked():cmd+=['--no-db']
        if self.player.text().strip():cmd+=['--player',self.player.text().strip()]
        if self.verbose_power.isChecked():cmd+=['--verbose']
        self.console.clear(); self.console.append('Starting scanner...')
        try:
            flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
            child_env = os.environ.copy()
            child_env['PYTHONIOENCODING'] = 'utf-8'
            child_env['PYTHONUTF8'] = '1'
            self.live_results = []
            self.refresh_live_table()
            self.proc=subprocess.Popen(cmd,cwd=str(BASE),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT,bufsize=1,text=True,encoding='utf-8',errors='replace',
                 creationflags=flags,env=child_env)
        except OSError as exc:
            self.proc=None; QMessageBox.critical(self,'Launch failed',str(exc));return
        self.report_path=report; self.ready_sent=False
        self.start_btn.setEnabled(False);self.ready_btn.setEnabled(True);self.stop_btn.setEnabled(True)
        self.status.setText('Scanner starting — wait for game-ready prompt.')
        def read_output(proc):
            try:
                for line in proc.stdout:self.messages.put(('line',line.rstrip()))
            finally:
                self.messages.put(('exit',proc.wait()))
        threading.Thread(target=read_output,args=(self.proc,),daemon=True).start()

    def confirm_ready(self):
        if self.proc and self.proc.poll() is None and not self.ready_sent:
            try:
                self.proc.stdin.write('\n');self.proc.stdin.flush()
                self.ready_sent=True;self.ready_btn.setEnabled(False)
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
        self.stop_btn.setEnabled(False)

    def tick(self):
        self.poll_power()
        for _ in range(300):
            try:kind,value=self.messages.get_nowait()
            except queue.Empty:break
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
                    self.live_results.append({'name': name.strip(), 'x': int(x), 'y': int(y), 'status': status})
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
                if clean_line:
                    self.console.append(clean_line)
                if 'press Enter to start' in lowered:self.status.setText('Ready — verify Kingdom/map and click Confirm')
            else:
                self.status.setText('Scan finished')
                self.proc=None;self.start_btn.setEnabled(True);self.ready_btn.setEnabled(False);self.stop_btn.setEnabled(False)
                self.refresh_data()
                if self.report_path.exists():
                    try:
                        report=json.loads(self.report_path.read_text(encoding='utf-8'))
                        lookup={(str(c.get('name')),int(c.get('x')),int(c.get('y'))):c.get('power_millions',0) for c in report.get('castles',[])}
                        for castle in self.live_results:
                            castle['power']=lookup.get((castle['name'],castle['x'],castle['y']),0)
                        self.refresh_live_table()
                        # Scanner report errors are intentionally not echoed into the GUI.
                    except (OSError,ValueError):pass

    def request_power_at_live_row(self, row, column):
        self.live_table.selectRow(row)
        self.request_power_from_live()

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

    def request_power_from_live(self):
        if self.proc is not None or self.power_proc is not None:
            QMessageBox.warning(self, 'Busy', 'Finish the active scan or power lookup first.')
            return
        row = self.live_table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'Select castle', 'Select a castle in Live castle results first.')
            return
        try:
            name = self.live_table.item(row, 0).text()
            x = int(float(self.live_table.item(row, 1).text()))
            y = int(float(self.live_table.item(row, 2).text()))
        except (AttributeError, ValueError, TypeError):
            QMessageBox.warning(self, 'Invalid castle', 'Selected castle has invalid coordinates.')
            return
        # The live table has no player ID or snapshot ID. Resolve against the
        # latest persisted snapshot and reject missing/ambiguous matches.
        matches = [r for r in self.latest if r.get('name') == name
                   and str(r.get('x')) == str(x) and str(r.get('y')) == str(y)
                   and str(r.get('kingdom')) == str(self.kingdom.value())]
        if len(matches) != 1 or not self.scans:
            QMessageBox.warning(self, 'Snapshot unavailable',
                'Could not uniquely match this castle to the latest saved scan. '
                'Finish the scan and reload its snapshot before requesting power.')
            return
        record = matches[0]
        target = {'kingdom':int(record['kingdom']), 'x':x, 'y':y,
                  'name':name, 'player_id':record.get('player_id',''),
                  'stamp':record['last_seen_utc'], 'csv':str(self.scans[0][0]),
                  'sql':self.enable_sql.isChecked()}
        self.launch_power_lookup(target)

    def request_power_at_row(self, row, column):
        self.castles_table.selectRow(row)
        self.request_power()

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

    def request_power(self):
        if self.proc is not None or self.power_proc is not None:
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
        matches = [r for r in self.latest if str(r.get('x')) == str(x) and str(r.get('y')) == str(y)
                   and str(r.get('name', '')) == name and str(r.get('last_seen_utc')) == stamp]
        if len(matches) != 1:
            QMessageBox.warning(self, 'Ambiguous selection', 'Could not uniquely identify selected snapshot row.')
            return
        kingdom = int(matches[0]['kingdom'])
        player_id = matches[0].get('player_id', '')
        target = {'kingdom':kingdom,'x':x,'y':y,'name':name,'player_id':player_id,
                  'stamp':stamp,'csv':str(self.scans[0][0]),'sql':self.enable_sql.isChecked()}
        self.launch_power_lookup(target)

    def launch_power_lookup(self, target):
        x, y, name = target['x'], target['y'], target['name']
        cmd = [sys.executable, str(BASE/'castle_power_detector.py'), '--x',str(x),'--y',str(y),
               '--name',name,'--port',str(self.port.value())]
        self.get_power_btn.setEnabled(False)
        self.live_power_btn.setEnabled(False)
        self.status.setText(f'Looking up power for {name} at X={x}, Y={y}...')
        try:
            self.power_proc = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            self.power_target = target
        except OSError as exc:
            self.power_proc = None
            self.get_power_btn.setEnabled(True)
            self.live_power_btn.setEnabled(True)
            QMessageBox.critical(self, 'Power lookup failed', str(exc))

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
            if proc.returncode or not result.get('success'):
                raise ValueError(result.get('error') or err or 'Power extraction failed')
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
            self.status.setText(f"Power saved: {target['name']} — {value:.3f} M")
        except Exception as exc:
            self.status.setText('Power lookup failed — snapshot unchanged')
            QMessageBox.warning(self, 'Power lookup', str(exc))
        finally:
            self.power_target = None

    def closeEvent(self,event):
        if (self.proc is not None and self.proc.poll() is None) or self.power_proc is not None:
            QMessageBox.warning(self,'Scanner active','Finish or stop the scanner before closing the GUI.')
            event.ignore();return
        event.accept()


# Multi-file, changed-only updater. Release manifest lists every distributable file.
APP_VERSION = "1.0.6"
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
                "The server does not provide a valid multi-file manifest. Select OK to start the installed version.")
            return False
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
        f"{len(changed)} changed file(s) need downloading.\n\nInstall now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes)
    if answer != QMessageBox.StandardButton.Yes:
        return False

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
            "Select OK to continue with the installed version.")
        return False


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
