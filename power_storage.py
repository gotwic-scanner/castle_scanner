"""Persist manually detected power to exactly one current snapshot record."""
import csv
import datetime as dt
import os
from pathlib import Path
import tempfile


def _match(row, target):
    return (str(row.get('kingdom')) == str(target['kingdom']) and
            str(row.get('x')) == str(target['x']) and
            str(row.get('y')) == str(target['y']) and
            str(row.get('player_id') or '') == str(target['player_id']) and
            str(row.get('name') or '') == str(target['name']) and
            str(row.get('last_seen_utc')) == str(target['stamp']))


def save_power(target, power_millions):
    value = float(power_millions)
    if not (0 <= value < 1e15):
        raise ValueError('Invalid castle power')
    value_text = f'{value:.3f}'
    csv_path = Path(target['csv'])
    with csv_path.open('r',encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        columns=reader.fieldnames
        if not columns or 'status_source' not in columns:
            raise ValueError('Snapshot has no status_source column')
        rows=list(reader)
    matches=[row for row in rows if _match(row,target)]
    if len(matches)!=1:
        raise ValueError(f'Expected one matching snapshot record, found {len(matches)}')
    # If SQL is enabled, update SQL first; do not silently fall back to CSV on failure.
    if target.get('sql'):
        import pymysql
        conn=pymysql.connect(host=os.getenv('GOTWIC_DB_HOST','127.0.0.1'),
             port=int(os.getenv('GOTWIC_DB_PORT','3306')),
             user=os.getenv('GOTWIC_DB_USER','root'),password=os.getenv('GOTWIC_DB_PASSWORD',''),
             database=os.getenv('GOTWIC_DB_NAME') or 'scanner_results',
             charset='utf8mb4',autocommit=False,connect_timeout=10)
        try:
            with conn.cursor() as cursor:
                # Snapshot timestamps are exported with millisecond precision.
                stamp=dt.datetime.fromisoformat(target['stamp'].replace('Z','+00:00'))
                if stamp.tzinfo: stamp=stamp.astimezone(dt.timezone.utc).replace(tzinfo=None)
                sql='''UPDATE castles SET status_source=%s
                       WHERE kingdom=%s AND x=%s AND y=%s
                       AND name=%s AND last_seen_utc=%s'''
                params=[value_text,target['kingdom'],target['x'],target['y'],target['name'],stamp]
                if target['player_id']:
                    sql+=' AND player_id=%s'
                    params.append(target['player_id'])
                else:
                    sql+=' AND (player_id IS NULL OR player_id=%s)'
                    params.append('')
                cursor.execute(sql,params)
                if cursor.rowcount != 1:
                    raise ValueError(f'SQL update affected {cursor.rowcount} records; expected exactly one')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    matches[0]['status_source']=value_text
    temp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8-sig',newline='',
                 dir=str(csv_path.parent),prefix=csv_path.name+'.',suffix='.tmp',delete=False) as handle:
            temp=Path(handle.name)
            writer=csv.DictWriter(handle,fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp,csv_path)
    finally:
        if temp and temp.exists(): temp.unlink()
    return value
