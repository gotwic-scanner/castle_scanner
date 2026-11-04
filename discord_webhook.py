#!/usr/bin/env python3
"""Optional outbound Discord webhook reporting for completed kingdom scans."""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_CONTENT = 1900

def validate_webhook(url):
    parsed = urlsplit(url.strip())
    if parsed.scheme != 'https' or parsed.hostname not in ('discord.com', 'discordapp.com'):
        raise ValueError('Enter a valid HTTPS Discord webhook URL.')
    parts = parsed.path.strip('/').split('/')
    if len(parts) < 4 or parts[:2] != ['api', 'webhooks'] or not parts[2].isdigit() or not parts[3]:
        raise ValueError('Invalid Discord webhook URL format.')
    return url.strip()

def _post(url, message, timeout=10):
    payload = json.dumps({'content': message, 'allowed_mentions': {'parse': []}}, ensure_ascii=False).encode('utf-8')
    for attempt in range(4):
        request = urllib.request.Request(url, data=payload, headers={
            'Content-Type': 'application/json', 'User-Agent': 'GOTWiC-Scanner/1.0.9.9'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise RuntimeError(f'Discord HTTP {exc.code}') from None
            try:
                details = json.loads(exc.read().decode('utf-8'))
                delay = float(details.get('retry_after', 1))
            except (ValueError, TypeError):
                delay = 1
            time.sleep(min(max(delay, .25), 30))

def _format_castle(c):
    name = str(c.get('name') or c.get('player') or 'Unknown').replace('`', "'").replace('@', '@\u200b').replace('\n', ' ')
    name = name[:100]
    power = c.get('power_millions')
    if power is None and isinstance(c.get('power'), (int, float)):
        power = float(c['power']) / 1000000
    try:
        power_text = f' | {float(power):,.3f}M' if power is not None and float(power) > 0 else ''
    except (ValueError, TypeError):
        power_text = ''
    return f"{name} | X:{c.get('x', '?')} Y:{c.get('y', '?')}{power_text}"

def send_kingdom_report(url, kingdom, castles, mode='unprotected', min_power_millions=500):
    url = validate_webhook(url)
    from gotwic_scanner_v2_2 import ignored_castle
    if min_power_millions not in (500, 1000, 1500, 2000):
        raise ValueError('Unsupported Discord minimum power threshold')
    selected = []
    for c in castles:
        if not isinstance(c, dict) or ignored_castle(c.get('name', ''), c.get('x', 0), c.get('y', 0)):
            continue
        status = str(c.get('status') or '').upper()
        if mode == 'unprotected' and status != 'UNPROTECTED':
            continue
        if mode == 'all' and status not in ('PROTECTED', 'UNPROTECTED'):
            continue
        # Prefer raw integer power, falling back to the report's millions value.
        # Castles with unknown, invalid, or zero power must not be posted.
        try:
            raw_power = c.get('power')
            if raw_power is not None and str(raw_power).strip() != '':
                power_millions = float(raw_power) / 1_000_000
            else:
                power_millions = float(c.get('power_millions'))
        except (ValueError, TypeError, OverflowError):
            continue
        if not (power_millions >= min_power_millions):
            continue
        selected.append((power_millions, c))
    if not selected:
        return 0, 0
    selected.sort(key=lambda entry: (-entry[0], str(entry[1].get('name') or entry[1].get('player') or '').casefold()))
    heading = f'**GOTWiC Scanner — Kingdom {kingdom}**\n{len(selected)} castle(s) with power ≥ {min_power_millions:,}M — {"Unprotected only" if mode == "unprotected" else "Protected and unprotected"}'
    lines = [_format_castle(c) for _, c in selected]
    messages = []
    current = heading
    for line in lines:
        if len(current) + len(line) + 1 > MAX_CONTENT:
            messages.append(current)
            current = f'**Kingdom {kingdom} (continued)**'
        current += '\n' + line[:MAX_CONTENT-60]
    messages.append(current)
    for i, message in enumerate(messages):
        _post(url, message)
        if i + 1 < len(messages):
            time.sleep(.4)
    return len(selected), len(messages)

def send_test(url):
    _post(validate_webhook(url), 'GOTWiC Scanner: Discord webhook connection test successful.')
