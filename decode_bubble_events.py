#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.3
"""Decode GTarcade 9002/9008/20816 castle events from wide CDP JSONL.
No third-party packages required.

py decode_bubble_events.py babymei_wide.jsonl --name "Baby Mei" --output babymei_events.csv
"""
import argparse, base64, csv, json, collections

INTERESTING = {9002, 9008}  # 20816 uses a different record schema; count but do not misdecode

def varint(data, pos):
    result = 0
    for shift in range(0, 70, 7):
        if pos >= len(data): raise ValueError('truncated varint')
        b = data[pos]; pos += 1
        result |= (b & 127) << shift
        if b < 128: return result, pos
    raise ValueError('varint overflow')

def protobuf(data):
    pos, fields = 0, collections.defaultdict(list)
    while pos < len(data):
        key, pos = varint(data, pos)
        number, wire = key >> 3, key & 7
        if number < 1 or wire not in (0, 1, 2, 5): raise ValueError('invalid protobuf tag')
        if wire == 0: value, pos = varint(data, pos)
        elif wire == 2:
            size, pos = varint(data, pos)
            if size > len(data)-pos: raise ValueError('truncated length field')
            value = data[pos:pos+size]; pos += size
        else:
            size = 8 if wire == 1 else 4
            if pos+size > len(data): raise ValueError('truncated fixed field')
            value = int.from_bytes(data[pos:pos+size], 'little'); pos += size
        fields[number].append((wire, value))
    return fields

def nested_fields(raw, mid):
    # Game envelope: 13 bytes, followed by protobuf field whose number is message ID.
    outer = protobuf(raw[13:])
    for wire, message in outer.get(mid, []):
        if wire != 2: continue
        payload = protobuf(message)
        # 9008: field 2 is castle/player record; 9002: field 1 is record.
        for f in (2, 1):
            for kind, blob in payload.get(f, []):
                if kind != 2: continue
                try:
                    record = protobuf(blob)
                    if 3 in record and 1 in record: yield record
                except ValueError: continue

def scalar(fields, key):
    vals = fields.get(key, [])
    return vals[0][1] if vals and vals[0][0] == 0 else None

def string(fields, key):
    vals = fields.get(key, [])
    if not vals or vals[0][0] != 2: return ''
    try: return vals[0][1].decode('utf-8')
    except UnicodeError: return ''

def frame_payload(entry):
    if entry.get('kind') not in ('Network.webSocketFrameReceived', 'Network.webSocketFrameSent'): return None
    response = entry.get('params', {}).get('response', {})
    if response.get('opcode') != 2: return None
    encoded = response.get('payload_base64') or response.get('payloadData')
    if not encoded: return None
    try: return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error): return None

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', nargs='?', default='babymei_wide.jsonl')
    ap.add_argument('--name', default='Baby Mei', help='case-insensitive player name filter; empty for all')
    ap.add_argument('--output', default='babymei_events.csv')
    args = ap.parse_args()
    rows, seen = [], set()
    message_counts = collections.Counter()
    with open(args.input, encoding='utf-8') as source:
        for line in source:
            if not line.strip(): continue
            entry = json.loads(line)
            if entry.get('kind') != 'Network.webSocketFrameReceived': continue
            raw = frame_payload(entry)
            if not raw or len(raw) < 14: continue
            mid = int.from_bytes(raw[4:8], 'big')
            message_counts[mid] += 1
            if mid not in INTERESTING: continue
            # The wide capture attached twice to the same target: dedupe mirrored frames.
            key = (entry.get('timestamp_utc'), mid, raw)
            if key in seen: continue
            seen.add(key)
            try: records = list(nested_fields(raw, mid))
            except ValueError: continue
            for fields in records:
                name = string(fields, 3)
                if args.name and args.name.casefold() not in name.casefold(): continue
                map_id = scalar(fields, 1)
                rows.append(dict(time_s=entry.get('elapsed_seconds'), timestamp_utc=entry.get('timestamp_utc'),
                    message_id=mid, name=name, map_id=map_id,
                    x=map_id%720 if map_id is not None else '',
                    y=map_id//720 if map_id is not None else '',
                    player_id=scalar(fields, 2), field_8=scalar(fields, 8),
                    field_9=scalar(fields, 9), field_10=scalar(fields, 10),
                    field_12=scalar(fields, 12),
                    fields_json=json.dumps({str(k): [v if w==0 else v.hex() if w==2 else v for w,v in values]
                                            for k,values in fields.items()},ensure_ascii=False)))
    rows.sort(key=lambda r:(float(r['time_s'] or 0),r['message_id']))
    columns = ['time_s','timestamp_utc','message_id','name','map_id','x','y','player_id',
               'field_8','field_9','field_10','field_12','fields_json']
    with open(args.output,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(rows)
    print('Incoming message counts (includes mirrored target sessions):',dict(message_counts))
    print('Decoded',len(rows),'matching records ->',args.output)
    for r in rows:
        print(f"{float(r['time_s']):7.3f}s  msg={r['message_id']:5}  {r['name']:<20} "
              f"map={r['map_id']} X={r['x']} Y={r['y']}  field10={r['field_10']}")
    print('\nField 10 is a candidate status indicator, NOT yet a validated truce flag.')

if __name__=='__main__':main()
