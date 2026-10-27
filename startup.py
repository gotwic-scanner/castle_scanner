#!/usr/bin/env python3
"""Standard-library-only startup and dependency bootstrap for GOTWiC Scanner."""
import importlib.metadata
import importlib
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / 'requirements_gui.txt'


def _version_tuple(value):
    parts = re.findall(r'\d+', str(value).split('+', 1)[0])
    return tuple(int(x) for x in parts[:4])


def _meets(version, spec):
    for op, required in re.findall(r'(>=|<=|==|!=|>|<)\s*([0-9][\w.]*)', spec):
        current, target = _version_tuple(version), _version_tuple(required)
        width = max(len(current), len(target))
        current += (0,) * (width - len(current))
        target += (0,) * (width - len(target))
        if not {'>=': current >= target, '<=': current <= target,
                '==': current == target, '!=': current != target,
                '>': current > target, '<': current < target}[op]:
            return False
    return True


def missing_requirements():
    missing = []
    for raw in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)(.*)', line)
        if not match:
            raise ValueError(f'Unsupported dependency specification: {line}')
        name, spec = match.groups()
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(line)
            continue
        if not _meets(installed, spec):
            missing.append(line)
    return missing


def error_dialog(message):
    # Windows native error dialog, with a console fallback.
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'GOTWiC Scanner dependencies', 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def main():
    try:
        missing = missing_requirements()
        if missing:
            print('Installing missing or incompatible dependencies: ' + ', '.join(missing), flush=True)
            command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', *missing]
            subprocess.run(command, check=True, cwd=str(ROOT))
            importlib.invalidate_caches() if hasattr(importlib, 'invalidate_caches') else None
            remaining = missing_requirements()
            if remaining:
                raise RuntimeError('Dependencies still missing after installation: ' + ', '.join(remaining))
        print('Dependency check passed.', flush=True)
    except Exception as exc:
        error_dialog(f'Dependency check or installation failed:\n{exc}\n\nThe application will close.')
        return 1
    chrome = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    if os.name == 'nt' and chrome.is_file():
        try:
            subprocess.Popen([str(chrome), '--remote-debugging-port=9222',
                '--user-data-dir=' + str(Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'GOTWiC-Scanner-Chrome')],
                cwd=str(ROOT))
        except OSError as exc:
            print(f'Chrome launch warning: {exc}', file=sys.stderr)
    return subprocess.call([sys.executable, str(ROOT / 'gotwic_gui.py'), *sys.argv[1:]], cwd=str(ROOT))


if __name__ == '__main__':
    sys.exit(main())
