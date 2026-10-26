# GOTWiC Scanner GUI package release: 1.0.3
"""Extract named map objects and coordinates from protocol_frames.jsonl.
Usage: py extract_coordinates_from_protocol.py protocol_frames.jsonl --output coordinates.csv
Coordinates follow map_id = Y*720 + X (verified against three references).
"""
import argparse, base64, csv, json
from collections import Counter

def varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError('truncated varint')
        b = data[pos]; pos += 1
        value |= (b & 127) << shift
        if not (b & 128):
            return value, pos
    raise ValueError('varint too long')

def objects(data):
    # Scan for field 1 varint, field 2 varint, field 3 UTF-8 name.
    # Works inside both ordinary protobuf and compact/prefixed 9001 packets.
    for i, byte in enumerate(data):
        if byte != 0x08:
            continue
        try:
            map_id, j = varint(data, i+1)
            if not (0 < map_id < 720*720) or j >= len(data) or data[j] != 0x10:
                continue
            secondary, j = varint(data, j+1)
            if j >= len(data) or data[j] != 0x1a:
                continue
            n, j = varint(data, j+1)
            if not (1 <= n <= 80) or j+n > len(data):
                continue
            name = data[j:j+n].decode('utf-8')
            if not name.isprintable() or not name.strip():
                continue
            yield map_id, secondary, name
        except (ValueError, UnicodeError):
            continue

def main():
    p = argparse.ArgumentParser()
    p.add_argument('input', nargs='?', default='protocol_frames.jsonl')
    p.add_argument('--output', default='coordinates.csv')
    args = p.parse_args()
    results = {}
    counts = Counter()
    with open(args.input, encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            counts[(entry.get('direction'), entry.get('message_id'))] += 1
            if entry.get('direction') != 'received' or entry.get('message_id') != 9001:
                continue
            raw = base64.b64decode(entry['payload_base64'])
            for map_id, secondary, name in objects(raw):
                key = (map_id, secondary, name)
                if key not in results:
                    results[key] = [0, entry.get('timestamp_utc','')]
                results[key][0] += 1
    with open(args.output, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['kingdom','x','y','map_id','name','secondary_id','observations','first_seen_utc'])
        for (map_id, secondary, name), (seen, ts) in sorted(results.items(), key=lambda t: (t[0][0]//720, t[0][0]%720)):
            w.writerow(['K542', map_id%720, map_id//720, map_id, name, secondary, seen, ts])
    print('Message counts:', dict(counts))
    print(f'Found {len(results)} unique named objects -> {args.output}')
    for (map_id, secondary, name), (seen, ts) in list(results.items())[:12]:
        print(f'{name:24.24s} X={map_id%720:3} Y={map_id//720:3} ID={map_id} seen={seen}')

if __name__ == '__main__':
    main()
