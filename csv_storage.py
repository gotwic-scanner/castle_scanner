"""Shared CSV storage and safe one-time migration from application root."""
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
CSV_DIR = BASE / 'csv'

def migrate_root_csv():
    """Move root-level CSV files without overwriting existing snapshots."""
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    moved, errors = [], []
    for source in BASE.glob('*.csv'):
        destination = CSV_DIR / source.name
        if destination.exists():
            stem, suffix = source.stem, source.suffix
            index = 1
            while destination.exists():
                destination = CSV_DIR / f'{stem}_migrated_{index}{suffix}'
                index += 1
        try:
            shutil.move(str(source), str(destination))
            moved.append((source.name, destination.name))
        except OSError as exc:
            errors.append(f'{source.name}: {exc}')
    return moved, errors
