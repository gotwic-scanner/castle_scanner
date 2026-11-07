"""Upload completed GOTWiC CSV scans to the existing website importer.

The server's established flow uses a CSRF token tied to a PHP session:
GET the upload form, then POST a single CSV using that same requests.Session.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

import requests

UPLOAD_ENDPOINT = 'https://scannerapp.serveminecraft.net/index.php?page=upload'
TIMEOUT = 60
MAX_CSV_SIZE = 20 * 1024 * 1024


class _TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'input':
            return
        fields = {str(k).lower(): v for k, v in attrs}
        if fields.get('name') == 'token' and fields.get('value'):
            self.token = fields['value']


def upload_csv(path, endpoint=UPLOAD_ENDPOINT, timeout=TIMEOUT):
    """Import one CSV and return its confirmed server result.

    Raises if the upload page, PHP session, POST or database import fails.
    A 200 HTTP response alone is *not* considered an import success.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError('CSV file does not exist')
    if not isinstance(endpoint, str) or not endpoint.startswith('https://'):
        raise ValueError('Upload endpoint must use HTTPS')
    if path.stat().st_size > MAX_CSV_SIZE:
        raise ValueError('CSV exceeds the 20 MiB upload limit')

    with requests.Session() as session:
        page = session.get(endpoint, timeout=15)
        page.raise_for_status()
        parser = _TokenParser()
        parser.feed(page.text)
        if not parser.token:
            raise RuntimeError('Website upload token not found')

        with path.open('rb') as csv_file:
            response = session.post(
                endpoint,
                data={'action': 'upload', 'token': parser.token, 'async': '1'},
                files={'csv_file[]': (path.name, csv_file, 'text/csv')},
                timeout=timeout,
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError('Website did not return JSON import results') from exc

    if not isinstance(payload, dict):
        raise RuntimeError('Invalid website import response')
    results = payload.get('results')
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise RuntimeError('Website returned no single-file import result')
    result = results[0]
    if result.get('success') is not True:
        message = str(result.get('message') or result.get('error') or 'Database import failed')
        raise RuntimeError(f'Website rejected CSV import: {message[:400]}')
    if result.get('filename') != path.name:
        raise RuntimeError('Website confirmed a different CSV filename')
    return result
