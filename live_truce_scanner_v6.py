#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.3
"""Live castle/truce scanner v6.

Requires files alongside this script: live_truce_scanner_v5.py,
wide_network_capture.py, decode_bubble_events.py,
extract_coordinates_from_protocol.py.

Python 3.13: py -3.13 -m pip install lz4 websocket-client
Replay: py -3.13 live_truce_scanner_v6.py --replay live_network_v4.jsonl
Live indefinitely: py -3.13 live_truce_scanner_v6.py --seconds 0
Live one minute: py -3.13 live_truce_scanner_v6.py --seconds 60

All CSVs are snapshots, not historical logs. UNKNOWN is never assumed unprotected.
"""
import argparse
import collections
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from live_truce_scanner_v5 import Tracker, frame, full_9001_records, objects, nested_fields, scalar, string, MAX_MAP

FIELDS = ['kingdom','x','y','map_id','name','player_id','status','status_source',
          'last_status_utc','last_seen_utc','observations']

class LiveTracker(Tracker):
    def __init__(self, kingdom, output, unprotected, quiet=False):
        super().__init__(kingdom, output, quiet)
        self.unprotected = Path(unprotected)
        self.dirty = False
        self.status_events = 0
        self.movement_events = 0

    def get_item(self, player_id, map_id, name):
        # Identity is the stable player ID. A map tile may be reoccupied.
        item = self.by_player.get(player_id) if player_id is not None else None
        if item is None:
            candidate = self.by_map.get(map_id)
            if candidate and (player_id is None or candidate['player_id'] in (None, player_id)):
                item = candidate
        if item is None:
            item = dict(map_id=map_id, name=name, player_id=player_id,
                        status='UNKNOWN', status_source='', last_status_utc='',
                        last_seen_utc='', observations=0)
            self.items.append(item)
        old_map = item['map_id']
        if old_map != map_id:
            if self.by_map.get(old_map) is item:
                del self.by_map[old_map]
            self.movement_events += 1
        occupant = self.by_map.get(map_id)
        if occupant is not None and occupant is not item:
            # Tile reassignment must not merge unrelated players.
            if occupant['map_id'] == map_id:
                occupant['map_id'] = None
            del self.by_map[map_id]
        item['map_id'] = map_id
        if name:
            item['name'] = name
        if player_id is not None:
            prior = item.get('player_id')
            if prior is not None and prior != player_id and self.by_player.get(prior) is item:
                del self.by_player[prior]
            item['player_id'] = player_id
            self.by_player[player_id] = item
        self.by_map[map_id] = item
        return item

    def update_status(self, item, value, source, stamp, elapsed):
        if value not in (0, 1):
            return
        before = item['status']
        super().update_status(item, value, source, stamp, elapsed)
        if before != item['status']:
            self.status_events += 1

    def consume(self, entry):
        changed = super().consume(entry)
        if changed:
            self.dirty = True
        return changed

    def rows(self):
        for item in sorted(self.items, key=lambda it: (it['map_id'] is None,
                                                        it['map_id'] if it['map_id'] is not None else -1,
                                                        it['name'])):
            m = item['map_id']
            yield dict(kingdom=self.kingdom,
                       x=m%720 if m is not None else '',
                       y=m//720 if m is not None else '',
                       map_id=m if m is not None else '',
                       name=item['name'],
                       player_id=item['player_id'] if item['player_id'] is not None else '',
                       status=item['status'], status_source=item['status_source'],
                       last_status_utc=item['last_status_utc'],
                       last_seen_utc=item['last_seen_utc'], observations=item['observations'])

    @staticmethod
    def write_csv(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.tmp')
        with temp.open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)

    def save(self):
        rows = list(self.rows())
        self.write_csv(self.output, rows)
        self.write_csv(self.unprotected, [r for r in rows if r['status'] == 'UNPROTECTED' and r['map_id'] != ''])
        self.dirty = False


def follow(path, tracker, proc=None, interval=1.0):
    last_save = time.monotonic()
    with open(path, encoding='utf-8') as file:
        while True:
            line = file.readline()
            if line:
                try:
                    tracker.consume(json.loads(line))
                except json.JSONDecodeError:
                    pass
                if tracker.dirty and time.monotonic() - last_save >= interval:
                    tracker.save()
                    last_save = time.monotonic()
                continue
            if proc is None or proc.poll() is not None:
                break
            if tracker.dirty and time.monotonic() - last_save >= interval:
                tracker.save()
                last_save = time.monotonic()
            time.sleep(.15)
    tracker.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seconds', type=int, default=0, help='0 means until Ctrl+C')
    parser.add_argument('--port', type=int, default=9222)
    parser.add_argument('--output', default='live_castles_v6.csv')
    parser.add_argument('--unprotected', default='unprotected_castles_v6.csv')
    parser.add_argument('--capture', default='live_network_v6.jsonl')
    parser.add_argument('--replay')
    parser.add_argument('--kingdom', default='K542')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error('--seconds must be >= 0')
    paths = [Path(args.output).resolve(), Path(args.unprotected).resolve()]
    if len(set(paths)) != 2:
        parser.error('--output and --unprotected must differ')
    tracker = LiveTracker(args.kingdom, args.output, args.unprotected, args.quiet)
    proc = None
    if args.replay:
        follow(args.replay, tracker)
    else:
        helper = Path(__file__).with_name('wide_network_capture_v6.py')
        if not helper.exists():
            parser.error('wide_network_capture_v6.py must be alongside this script')
        capture = Path(args.capture)
        if capture.resolve() in paths:
            parser.error('capture path must differ from CSV outputs')
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_text('', encoding='utf-8')
        cmd = [sys.executable, str(helper), '--seconds', str(args.seconds),
               '--port', str(args.port), '--output', str(capture)]
        print('Capturing until Ctrl+C' if args.seconds == 0 else f'Capturing {args.seconds} seconds', flush=True)
        proc = subprocess.Popen(cmd)
        try:
            follow(capture, tracker, proc)
        except KeyboardInterrupt:
            print('\nStopping capture; saving CSVs...', flush=True)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            tracker.save()
    print(f'All: {tracker.output} | Unprotected: {tracker.unprotected}')
    print('Statuses:', dict(collections.Counter(x['status'] for x in tracker.items)))
    print('Status observations:', dict(tracker.status_counts))
    print('Message counts:', dict(tracker.counts))

if __name__ == '__main__':
    main()
