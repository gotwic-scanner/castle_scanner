"""Optional web CSV upload. Standard library only; no dependency on the scanner runtime."""
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import uuid

UPLOAD_ENDPOINT = 'https://gotwic-scanner.byethost4.com/upload.php'
TIMEOUT = 30
MAX_RESPONSE = 65536


def upload_csv(path, endpoint, timeout=TIMEOUT):
    path = Path(path)
    if not path.is_file():
        raise ValueError('CSV file does not exist')
    if not endpoint.startswith('https://'):
        raise ValueError('Upload endpoint must use HTTPS')
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('CSV exceeds the 20 MiB upload limit')
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    boundary = 'gotwic-' + uuid.uuid4().hex
    name = path.name.replace('"', '_').replace('\r', '_').replace('\n', '_')
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            'Content-Type: text/csv\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(endpoint, data=body, headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'X-Content-SHA256': digest,
        'Accept': 'application/json',
        'User-Agent': 'GOTWiC-Scanner/1.0.8.2',
    }, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError('Upload response too large')
            result = json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(MAX_RESPONSE).decode('utf-8'))
            message = payload.get('error', 'Server rejected upload')
        except (ValueError, UnicodeError):
            message = 'Server rejected upload'
        raise RuntimeError(f'HTTP {exc.code}: {message}') from None
    if not isinstance(result, dict) or result.get('success') is not True:
        raise RuntimeError('Server did not confirm successful import')
    if result.get('sha256') != digest:
        raise RuntimeError('Server confirmation hash mismatch')
    return result
