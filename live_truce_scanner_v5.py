#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.3
"""GTarcade live castle discovery and truce-status scanner v5 (LZ4 support).

Dependencies alongside this file: wide_network_capture.py,
  decode_bubble_events.py, extract_coordinates_from_protocol.py
Install dependencies: py -m pip install websocket-client lz4

Live:   py live_truce_scanner_v5.py --seconds 60 --output live_castles_v5.csv
Replay: py live_truce_scanner_v5.py --replay live_network.jsonl --output replay_v4.csv

9001 packets may contain LZ4-compressed protobuf payloads; decompress first.
9001 complete castle record field 10: 1 PROTECTED / 0 UNPROTECTED,
  visually checked for five named castles. Compact/incomplete records remain UNKNOWN.
9008 field 10: observed truce activation/removal on Baby Mei.
9002: position updates only; field 10 is NOT a protection flag.
"""
import argparse
import base64
import collections
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from decode_bubble_events import nested_fields, scalar, string, protobuf, varint
from extract_coordinates_from_protocol import objects

try:
    import lz4.block as lz4_block
except ImportError:
    lz4_block = None

COLUMNS = ['kingdom','x','y','map_id','name','player_id','status','status_source',
           'last_status_utc','last_seen_utc','observations']
MAX_MAP = 720 * 720


def frame(entry):
    if entry.get('kind') == 'Network.webSocketFrameReceived':
        response = entry.get('params', {}).get('response', {})
        if response.get('opcode') != 2:
            return None
        encoded = response.get('payload_base64') or response.get('payloadData')
    elif entry.get('direction') == 'received':
        encoded = entry.get('payload_base64')
    else:
        return None
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        return None
    if len(raw) < 13:
        return None
    mid = int.from_bytes(raw[4:8], 'big')
    # Game frame: [4-byte length][4-byte message ID][4-byte
    # uncompressed size, or zero][1-byte reserved][payload].
    # Nonzero size signals an LZ4 block beginning at byte 13.
    # Never search for records inside compressed bytes.
    if mid == 9001 and int.from_bytes(raw[8:12], 'big'):
        expected = int.from_bytes(raw[8:12], 'big')
        if expected > 16 * 1024 * 1024:
            raise ValueError(f'Unreasonable 9001 uncompressed length: {expected}')
        if lz4_block is None:
            raise RuntimeError('Compressed map data requires lz4. Install: py -m pip install lz4')
        try:
            payload = lz4_block.decompress(raw[13:], uncompressed_size=expected)
        except Exception as exc:
            raise ValueError(f'9001 LZ4 decompression failed: {exc}') from exc
        if len(payload) != expected:
            raise ValueError('9001 LZ4 length mismatch')
        raw = raw[:8] + b'\x00' * 5 + payload
    return mid, raw


def full_9001_records(raw):
    """Extract length-delimited, *fully validated* protobuf castle records.

    The 9001 message can also contain compressed or partial records that look like
    field 1/2/3 but cannot safely be decoded as a full protobuf. Those must
    not be assigned a status based on nearby arbitrary bytes.
    """
    seen = set()
    for i, byte in enumerate(raw):
        if byte != 0x08:
            continue
        try:
            map_id, pos = varint(raw, i + 1)
            if not 0 < map_id < MAX_MAP or pos >= len(raw) or raw[pos] != 0x10:
                continue
            player_id, pos = varint(raw, pos + 1)
            if pos >= len(raw) or raw[pos] != 0x1a:
                continue
            size, pos = varint(raw, pos + 1)
            if not 1 <= size <= 80 or pos + size > len(raw):
                continue
            name = raw[pos:pos+size].decode('utf-8')
            if not name.strip() or not name.isprintable():
                continue
        except (ValueError, UnicodeError, IndexError):
            continue
        # Find the enclosing protobuf field tag + length immediately preceding
        # the candidate. Only accept when length ends exactly on a valid record.
        for start in range(max(13, i - 7), i):
            try:
                key, after_tag = varint(raw, start)
                if (key & 7) != 2 or (key >> 3) not in (1, 2, 3):
                    continue
                length, after_length = varint(raw, after_tag)
                if after_length != i or length < pos + size - i or i + length > len(raw):
                    continue
                fields = protobuf(raw[i:i+length])
                if scalar(fields, 1) != map_id or scalar(fields, 2) != player_id or string(fields, 3) != name:
                    continue
                identity = (map_id, player_id, name)
                if identity not in seen:
                    seen.add(identity)
                    yield map_id, player_id, name, scalar(fields, 10)
                break
            except (ValueError, IndexError):
                continue


class Tracker:
    def __init__(self, kingdom, output, quiet=False):
        self.kingdom, self.output, self.quiet = kingdom, Path(output), quiet
        self.by_player, self.by_map, self.items = {}, {}, []
        self.seen_frames = set()
        self.counts = collections.Counter()
        self.status_counts = collections.Counter()

    def say(self, text):
        if not self.quiet:
            print(text, flush=True)

    def get_item(self, player_id, map_id, name):
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
        if old_map != map_id and self.by_map.get(old_map) is item:
            del self.by_map[old_map]
        item['map_id'] = map_id
        if name:
            item['name'] = name
        if player_id is not None:
            item['player_id'] = player_id
            self.by_player[player_id] = item
        self.by_map[map_id] = item
        return item

    def update_status(self, item, value, source, stamp, elapsed):
        if value not in (0, 1):
            return
        new = 'PROTECTED' if value == 1 else 'UNPROTECTED'
        if item['status'] != new:
            map_id = item['map_id']
            self.say(f"[{elapsed}s] {item['name']} X={map_id%720} Y={map_id//720}: "
                     f"{new} ({source}={value})")
        item['status'] = new
        item['status_source'] = source
        item['last_status_utc'] = stamp
        self.status_counts[source] += 1

    def consume(self, entry):
        parsed = frame(entry)
        if not parsed:
            return False
        mid, raw = parsed
        # Mirror sessions can report the same frame more than once.
        dedup = (entry.get('timestamp_utc'), mid, hashlib.blake2s(raw, digest_size=12).digest())
        if dedup in self.seen_frames:
            return False
        self.seen_frames.add(dedup)
        self.counts[mid] += 1
        stamp, elapsed = entry.get('timestamp_utc', ''), entry.get('elapsed_seconds', '?')
        changed = False
        if mid == 9001:
            # First, decode full records with validated field 10.
            full = list(full_9001_records(raw))
            for map_id, player_id, name, status in full:
                item = self.get_item(player_id, map_id, name)
                item['last_seen_utc'] = stamp
                item['observations'] += 1
                self.update_status(item, status, '9001.field10', stamp, elapsed)
                changed = True
            full_keys = {(m, p, n) for m, p, n, _ in full}
            # Still discover compact records, but leave status UNKNOWN.
            for map_id, player_id, name in objects(raw):
                if (map_id, player_id, name) in full_keys:
                    continue
                item = self.get_item(player_id, map_id, name)
                item['last_seen_utc'] = stamp
                item['observations'] += 1
                changed = True
        elif mid in (9008, 9002):
            try:
                records = list(nested_fields(raw, mid))
            except ValueError:
                records = []
            for fields in records:
                map_id, player_id, name = scalar(fields, 1), scalar(fields, 2), string(fields, 3)
                if map_id is None or not 0 < map_id < MAX_MAP or not name:
                    continue
                item = self.get_item(player_id, map_id, name)
                item['last_seen_utc'] = stamp
                item['observations'] += 1
                if mid == 9008:
                    self.update_status(item, scalar(fields, 10), '9008.field10', stamp, elapsed)
                changed = True
        return changed

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temp = self.output.with_suffix(self.output.suffix + '.tmp')
        with temp.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            for item in sorted(self.items, key=lambda it: (it['map_id'], it['name'])):
                m = item['map_id']
                writer.writerow(dict(kingdom=self.kingdom, x=m%720, y=m//720,
                    map_id=m, name=item['name'], player_id=item['player_id'] or '',
                    status=item['status'], status_source=item['status_source'],
                    last_status_utc=item['last_status_utc'], last_seen_utc=item['last_seen_utc'],
                    observations=item['observations']))
        os.replace(temp, self.output)


def follow(path, tracker, process=None):
    with open(path, encoding='utf-8') as f:
        while True:
            line = f.readline()
            if line:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tracker.consume(entry):
                    tracker.save()
                continue
            if process is None or process.poll() is not None:
                break
            time.sleep(.15)
    tracker.save()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--seconds', type=int, default=60)
    p.add_argument('--port', type=int, default=9222)
    p.add_argument('--output', default='live_castles_v5.csv')
    p.add_argument('--capture', default='live_network_v5.jsonl')
    p.add_argument('--replay', help='Read a saved capture instead of attaching to Chrome')
    p.add_argument('--kingdom', default='K542')
    p.add_argument('--quiet', action='store_true')
    args = p.parse_args()
    if lz4_block is None:
        p.error('lz4 is required for compressed map packets: py -m pip install lz4')
    tracker = Tracker(args.kingdom, args.output, args.quiet)
    if args.replay:
        follow(args.replay, tracker)
    else:
        capture_script = Path(__file__).with_name('wide_network_capture.py')
        if not capture_script.exists():
            p.error('wide_network_capture.py must be in the same folder')
        capture = Path(args.capture)
        if capture.resolve() == Path(args.output).resolve():
            p.error('capture and output must differ')
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_text('', encoding='utf-8')
        command = [sys.executable, str(capture_script), '--seconds', str(args.seconds),
                   '--port', str(args.port), '--output', str(capture)]
        print('Starting wide Chrome network capture... (Ctrl+C to stop)', flush=True)
        proc = subprocess.Popen(command)
        try:
            follow(capture, tracker, proc)
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            tracker.save()
    counts = collections.Counter(x['status'] for x in tracker.items)
    print(f'Saved {len(tracker.items)} objects to {tracker.output}')
    print('Statuses:', dict(counts))
    print('Status observations by source:', dict(tracker.status_counts))
    print('Incoming message counts:', dict(tracker.counts))

if __name__ == '__main__':
    main()
