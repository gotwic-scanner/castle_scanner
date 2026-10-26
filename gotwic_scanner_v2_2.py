#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.4
"""GOTWiC Scanner v2.2 (console update; v1.4 scan baseline): locked V448 movement, V451 decoder, X25/Y13 sweep.
Requires live_truce_scanner_v6.py, live_truce_scanner_v5.py and their decoder dependencies.
Install: py -3.13 -m pip install websocket-client lz4 pymysql
"""
import argparse, base64, collections, datetime as dt, json, time, urllib.request, os, sys, ctypes, csv, signal
from pathlib import Path
import websocket
from live_truce_scanner_v6 import LiveTracker

GREEN = "\x1b[92m"
RED = "\x1b[91m"
RESET = "\x1b[0m"


def enable_vt():
    """Enable ANSI colors on Windows consoles (as in original scanner v15)."""
    if os.name != 'nt':
        return sys.stdout.isatty()
    try:
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return False


class ColorTracker(LiveTracker):
    """Original scanner v15's discovery/status console logic; v6 decoder unchanged."""
    def __init__(self, *args, color=True, show='ALL', player=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.color = color
        self.show = show
        self.player_filter = player.casefold() if player else None

    def consume(self, entry):
        before = {id(it): (it.get('status'), it.get('map_id')) for it in self.items}
        self._suppress_status_messages = True
        try:
            changed = super().consume(entry)
        finally:
            self._suppress_status_messages = False
        if changed:
            for item in self.items:
                old = before.get(id(item))
                status = item.get('status')
                if self.show == 'OPEN' and status != 'UNPROTECTED':
                    continue
                if self.player_filter and self.player_filter not in (item.get('name') or '').casefold():
                    continue
                if old is not None and old[0] == status and old[1] == item.get('map_id'):
                    continue
                mid = item.get('map_id')
                coords = f"X={mid%720} Y={mid//720}" if mid is not None else 'X=? Y=?'
                self.say(f"{item.get('name') or '?'} {coords}: {status}")
        return changed

    def say(self, message):
        if getattr(self, '_suppress_status_messages', False):
            return
        if self.color:
            color = GREEN if ': UNPROTECTED' in message else RED if ': PROTECTED' in message else ''
            if color:
                message = color + message + RESET
        print(message, flush=True)


PREFLIGHT="""(()=>{const t=window.gameInstance?.Module?.asm?.__indirect_function_table;
if(!t)return {ok:false,error:'WASM table unavailable in Unity context'};
const f=t.get(62958);
if(typeof f!=='function')return {ok:false,error:'Table slot 62958 not callable'};
if(f.name!=='145867')return {ok:false,error:'Function identity mismatch',name:f.name};
return {ok:true,functionName:f.name}})()"""
NAV="""(()=>{const t=window.gameInstance?.Module?.asm?.__indirect_function_table;
if(!t)return {ok:false,error:'WASM table unavailable'};
const f=t.get(62958);
if(typeof f!=='function'||f.name!=='145867')return {ok:false,error:'Function identity mismatch',name:f?.name};
try{const v=f(__MAPID__,150,200,250,300);
return {ok:true,status:'returned',returnValue:String(v)}}
catch(e){return {ok:false,error:String(e),stack:String(e.stack||'').slice(0,600)}}})()"""

# v1.0.4: event-driven 20007 castle power, enabled only by -v / --verbose.
POWER_TIMEOUT = 4.0
POWER_CASTLE_SETTLE = 0.1
POWER_WATER_SETTLE = 0.1
POWER_DISMISS_WAIT = 0.05
POWER_WATER_MAP_ID = 325 * 720 + 125

def _power_varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data): raise ValueError('truncated varint')
        byte = data[offset]; offset += 1
        value |= (byte & 127) << shift
        if byte < 128: return value, offset
    raise ValueError('varint overflow')

def _power_fields(data):
    result = {}; offset = 0
    while offset < len(data):
        tag, offset = _power_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number < 1: raise ValueError('invalid protobuf field')
        if wire == 0: value, offset = _power_varint(data, offset)
        elif wire == 2:
            length, offset = _power_varint(data, offset)
            if length > len(data)-offset: raise ValueError('truncated protobuf bytes')
            value = data[offset:offset+length]; offset += length
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if size > len(data)-offset: raise ValueError('truncated fixed protobuf field')
            value = int.from_bytes(data[offset:offset+size], 'little'); offset += size
        else: raise ValueError('unsupported protobuf wire type')
        result[number] = value
    return result

def decode_power_20007(raw):
    if len(raw) < 13 or int.from_bytes(raw[4:8], 'big') != 20007: return None
    length = int.from_bytes(raw[8:12], 'big'); payload = raw[13:]
    if length:
        import lz4.block
        payload = lz4.block.decompress(payload, uncompressed_size=length)
    player = _power_fields(_power_fields(_power_fields(payload)[20007])[1])
    name = player.get(16)
    return {'player':name.decode('utf-8','replace') if isinstance(name,bytes) else None,
            'power':player.get(3)}

def axis(start,end,step):
    return list(range(start,end+1,step)) + ([] if (end-start)%step==0 else [end])

def parse_utc(value):
    if not value: return None
    if isinstance(value,dt.datetime): return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    try:
        parsed=dt.datetime.fromisoformat(str(value).strip().replace('Z','+00:00'))
        return parsed.astimezone(dt.timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError,TypeError): return None

def snapshot_rows(tracker,kingdom,stamp):
    rows=[]
    for item in tracker.items:
        mid=item.get('map_id')
        if not isinstance(mid,int) or not 0<=mid<720*720: continue
        rows.append((kingdom,mid%720,mid//720,mid,item.get('name'),item.get('player_id'),
                     item.get('status'),0,parse_utc(item.get('last_status_utc')),
                     stamp,int(item.get('observations') or 0)))
    return rows

# CSV column order and NULL semantics mirror the existing MySQL castles INSERT.
CASTLE_COLUMNS = ('kingdom','x','y','map_id','name','player_id','status',
                  'status_source','last_status_utc','last_seen_utc','observations')

def export_csv(rows, kingdom, stamp):
    """Write the complete scan snapshot in the script directory, even with --no-db.

    Use a UTC timestamp with microseconds so separate scans never overwrite each other.
    Empty nullable values import as SQL NULL when the import tool is configured to
    interpret empty fields as NULL.
    """
    filename = f'{kingdom}_{stamp.strftime("%Y%m%d_%H%M%S_%f")}.csv'
    target = Path(__file__).resolve().parent / filename
    temporary = target.with_suffix('.csv.tmp')
    try:
        with temporary.open('w', encoding='utf-8-sig', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(CASTLE_COLUMNS)
            for row in rows:
                writer.writerow([value.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                                 if isinstance(value, dt.datetime)
                                 else ('' if value is None else value)
                                 for value in row])
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return str(target)

def import_mysql(args,rows,stamp):
    import pymysql
    if not args.db_name: raise ValueError('MySQL database name required (--db-name or GOTWIC_DB_NAME)')
    connection=pymysql.connect(host=args.db_host,port=args.db_port,user=args.db_user,
       password=args.db_password,database=args.db_name,charset='utf8mb4',autocommit=False,
       connect_timeout=10,read_timeout=30,write_timeout=30)
    try:
        with connection.cursor() as cur:
            cur.executemany("""INSERT INTO castles
                (kingdom,x,y,map_id,name,player_id,status,status_source,last_status_utc,last_seen_utc,observations)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",rows)
            cur.execute("""INSERT INTO import_history
                (filename,imported_at_utc,row_count,status,details)
                VALUES (%s,%s,%s,%s,%s)""",('automated',dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
                  len(rows),'success',f'GOTWiC Scanner v2.2; scan_utc={stamp.isoformat()}'))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally: connection.close()

def main():
    if os.name == 'nt' and hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=9222)
    p.add_argument('--kingdom',type=int,required=True,help='Required Kingdom ID for database snapshots; does not switch game Kingdom')
    p.add_argument('--show',choices=('ALL','OPEN'),type=str.upper,default='ALL',help='Console: ALL castles (default), or OPEN for UNPROTECTED only')
    p.add_argument('--player',default=None,help='Console: show only player names containing this text (case-insensitive); does not filter database or report')
    p.add_argument('--mode',choices=('auto','manual'),default='auto')
    p.add_argument('--x-min',type=int,default=0);p.add_argument('--x-max',type=int,default=719)
    p.add_argument('--y-min',type=int,default=0);p.add_argument('--y-max',type=int,default=719)
    p.add_argument('--settle',type=float,default=3.0)
    p.add_argument('--response-timeout',type=float,default=1.5,help='Seconds to await a 9001 response after navigation')
    p.add_argument('--retries',type=int,default=2,help='Additional attempts to recover a missing batch response')
    p.add_argument('--buffer','--buf',dest='buffer',type=int,default=4,help='Maximum outstanding map responses (default 4; range 1..32)')
    p.add_argument('--report',default='gotwic_scanner_v2_1_report.json')
    p.add_argument('--db-host',default=os.getenv('GOTWIC_DB_HOST','127.0.0.1'))
    p.add_argument('--db-port',type=int,default=int(os.getenv('GOTWIC_DB_PORT','3306')))
    p.add_argument('--db-user',default=os.getenv('GOTWIC_DB_USER','root'))
    p.add_argument('--db-password',default=os.getenv('GOTWIC_DB_PASSWORD',''))
    p.add_argument('--db-name',default=os.getenv('GOTWIC_DB_NAME') or 'scanner_results')
    p.add_argument('--mask',default='gotwic_kingdom542_production_mask.json',help='JSON list of allowed X/Y navigation points; use --mask none for unmasked scanning')
    p.add_argument('-v','--verbose',action='store_true',help='Retrieve power for UNPROTECTED castles only; protected/unknown stay 0')
    p.add_argument('--no-db',action='store_true',help='Diagnostic scan without database import')
    a=p.parse_args()
    if a.kingdom<1:p.error('--kingdom must be a positive integer')
    if not (0<=a.x_min<=a.x_max<=719 and 0<=a.y_min<=a.y_max<=719):p.error('Coordinate bounds must be within 0..719')
    if a.settle<0:p.error('--settle must be nonnegative')
    if a.response_timeout<=0 or a.retries<0:p.error('--response-timeout must be positive and --retries nonnegative')
    if not 1<=a.buffer<=32:p.error('--buffer must be 1..32')
    if not a.no_db and not a.db_name:p.error('Provide --db-name or GOTWIC_DB_NAME, or use --no-db')
    mask_points=None
    if a.mode=='auto' and a.mask.lower()!='none':
        try:
            mask_doc=json.loads((Path(a.mask) if Path(a.mask).is_file() else Path(__file__).resolve().parent / a.mask).read_text(encoding='utf-8'))
            if mask_doc.get('step_x')!=25 or mask_doc.get('step_y')!=13 or mask_doc.get('map_size')!=720:
                p.error('Mask map/grid metadata does not match scanner')
            raw_points=mask_doc['points']
            mask_points={tuple(v) for v in raw_points}
            if len(mask_points)!=len(raw_points) or not mask_points or any(len(v)!=2 or not all(isinstance(z,int) and 0<=z<=719 for z in v) for v in mask_points):
                p.error('Invalid, empty or duplicate mask points')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            p.error(f'Unable to load scan mask: {exc}')
    stamp=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    report={'version':'2.2','scan_utc':stamp.isoformat()+'Z','kingdom':a.kingdom,'mode':a.mode,
            'step_x':25,'step_y':13,'delay_seconds':0,'errors':[],'import':None,'csv_export':None,'castles':[]}
    tracker=ColorTracker('K','gotwic_v1_unused.csv','gotwic_v1_unused_open.csv',quiet=True,show=a.show,player=a.player,color=enable_vt())
    active={'castle_frames':0,'decoder_errors':[],'power_profiles':[],'power_decode_errors':[],'power_visit':None}
    missing_points=[]; retry_attempts=0; response_waits=[]; batch_stats={'completed':0,'short':0,'max_outstanding':0}
    with urllib.request.urlopen(f'http://127.0.0.1:{a.port}/json',timeout=5) as r:pages=json.load(r)
    page=next((q for q in pages if q.get('type')=='page' and 'gtarcade' in q.get('url','').lower() and q.get('webSocketDebuggerUrl')),None)
    if not page:raise RuntimeError('GTArcade CDP page not found')
    ws=websocket.create_connection(page['webSocketDebuggerUrl'],timeout=5,suppress_origin=True)
    ws.settimeout(.01)
    seq=0;replies={};contexts=[]
    def send(method,params=None):
        nonlocal seq
        seq+=1;ws.send(json.dumps({'id':seq,'method':method,'params':params or {}}));return seq
    def poll():
        try:m=json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:return
        if m.get('id') is not None:replies[m['id']]=m;return
        method=m.get('method');params=m.get('params',{})
        if method=='Runtime.executionContextCreated':contexts.append(params.get('context',{}))
        elif method=='Network.webSocketFrameReceived':
            response=params.get('response',{})
            if response.get('opcode')!=2:return
            encoded=response.get('payloadData','')
            try:
                raw=base64.b64decode(encoded,validate=True)
                if len(raw)<13:return
                message_id=int.from_bytes(raw[4:8],'big')
            except Exception:return
            if a.verbose and message_id==20007:
                try:
                    profile=decode_power_20007(raw)
                    if profile:
                        active['power_profiles'].append({'at':time.monotonic(),
                            'visit':active['power_visit'],**profile})
                except Exception as exc:
                    if len(active['power_decode_errors'])<20:active['power_decode_errors'].append(str(exc))
                return
            if message_id!=9001:return
            active['castle_frames']+=1
            entry={'kind':'Network.webSocketFrameReceived','params':{'response':{'opcode':2,'payload_base64':encoded}}}
            try:tracker.consume(entry)
            except Exception as e:
                if len(active['decoder_errors'])<30:active['decoder_errors'].append(str(e))
    def collect(duration):
        end=time.monotonic()+duration
        while time.monotonic()<end:poll()
    def wait_reply(rid,timeout=5):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if rid in replies:return replies.pop(rid)
            poll()
        raise TimeoutError(f'CDP request {rid} timed out')
    def evaluate(expr,cid):
        response=wait_reply(send('Runtime.evaluate',{'expression':expr,'contextId':cid,
                    'returnByValue':True,'silent':True,'timeout':4000}),5)
        if 'error' in response or 'exceptionDetails' in response.get('result',{}):
            raise RuntimeError(str(response.get('error') or response['result']['exceptionDetails']))
        return response.get('result',{}).get('result',{}).get('value')
    origin=time.monotonic()
    total=0
    try:
        wait_reply(send('Runtime.enable'))
        wait_reply(send('Network.enable',{'maxTotalBufferSize':16000000}))
        collect(.2)
        valid=[c for c in contexts if c.get('auxData',{}).get('isDefault') and 'logins-gotpc.gtarcade.com' in c.get('origin','')]
        if not valid:raise RuntimeError('Unity main-world execution context not found')
        cid=valid[0]['id'];report['context_id']=cid
        pre=evaluate(PREFLIGHT,cid);report['preflight']=pre
        if not isinstance(pre,dict) or not pre.get('ok'):raise RuntimeError('Preflight failed: '+str(pre))
        print('GOTWiC Scanner v2.2 | Kingdom',a.kingdom,'| mode',a.mode,'| show',a.show,'| player',repr(a.player) if a.player else 'ALL',flush=True)
        input('Verify Kingdom and map are open; press Enter to start: ')
        origin=time.monotonic();jumps=0
        if a.mode=='auto':
            xs=axis(a.x_min,a.x_max,25);ys=axis(a.y_min,a.y_max,13)
            points=[(x,y) for row,y in enumerate(ys) for x in (xs if row%2==0 else list(reversed(xs)))]
            original_total=len(points)
            if mask_points is not None:
                unknown=mask_points.difference(points)
                if unknown:raise RuntimeError(f'Mask contains {len(unknown)} points outside the requested grid')
                points=[point for point in points if point in mask_points]
                if not points:raise RuntimeError('Mask excludes every navigation point')
            total=len(points)
            report['mask']={'file':a.mask if mask_points is not None else None,'original_points':original_total,'retained_points':total,'excluded_points':original_total-total}
            print(f'Sweep points: {total}/{original_total} | masked: {original_total-total}',flush=True)
            # A 9001 frame has no verified request ID. Account for responses by
            # batch totals, never pretend to attribute an individual frame to X/Y.
            # Keep at most --buffer navigation responses outstanding.
            for offset in range(0,len(points),a.buffer):
                batch=points[offset:offset+a.buffer]
                frame_start=active['castle_frames']
                started=time.monotonic()
                for x,y in batch:
                    result=evaluate(NAV.replace('__MAPID__',str(y*720+x)),cid)
                    if not isinstance(result,dict) or not result.get('ok') or result.get('returnValue')!='1':
                        raise RuntimeError(f'Navigation failed at X={x} Y={y}: {result}')
                    jumps+=1
                # Responses may have arrived during Runtime.evaluate calls.
                received=active['castle_frames']-frame_start
                batch_stats['max_outstanding']=max(batch_stats['max_outstanding'],max(0,len(batch)-received))
                deadline=time.monotonic()+a.response_timeout
                while active['castle_frames']-frame_start<len(batch) and time.monotonic()<deadline:
                    poll()
                received=active['castle_frames']-frame_start
                if received<len(batch):
                    batch_stats['short']+=1
                    deficit=len(batch)-received
                    print(f'  Batch {offset//a.buffer+1}: {deficit} missing 9001 frame(s); recovery',flush=True)
                    # No request correlation exists, so retries are batch-level.
                    # Revisit batch coordinates, one by one, until the deficit
                    # is recovered. Extra responses do not prove spatial coverage.
                    for attempt in range(a.retries):
                        if active['castle_frames']-frame_start>=len(batch):break
                        for x,y in batch:
                            if active['castle_frames']-frame_start>=len(batch):break
                            retry_attempts+=1
                            before=active['castle_frames']
                            result=evaluate(NAV.replace('__MAPID__',str(y*720+x)),cid)
                            if not isinstance(result,dict) or not result.get('ok') or result.get('returnValue')!='1':
                                raise RuntimeError(f'Retry navigation failed at X={x} Y={y}: {result}')
                            end_retry=time.monotonic()+a.response_timeout
                            while active['castle_frames']==before and time.monotonic()<end_retry:poll()
                    received=active['castle_frames']-frame_start
                    if received<len(batch):
                        missing_points.append({'batch_start':offset,'points':[{'x':x,'y':y} for x,y in batch],
                                               'missing_responses':len(batch)-received})
                batch_stats['completed']+=1
                response_waits.append(round(time.monotonic()-started,4))
            collect(a.settle)
        else:
            print('Manual mode: move the map freely. Press Ctrl+C to finish and import.',flush=True)
            try:
                while True:collect(.25)
            except KeyboardInterrupt:pass
            collect(a.settle)
        if active['decoder_errors']:raise RuntimeError(f'Decoder errors: {active["decoder_errors"][:3]}')
        rows=snapshot_rows(tracker,a.kingdom,stamp)
        power_results={'enabled':a.verbose,'eligible':0,'skipped':0,'attempted':0,'matched':0,'unavailable':0,
                       'timeout_seconds':POWER_TIMEOUT,'errors':[]}
        if a.verbose and rows:
            power_results['eligible']=sum(row[6]=='UNPROTECTED' and not row[4].startswith("Night'sWatch") for row in rows)
            power_results['skipped']=len(rows)-power_results['eligible']
            print(f'Power lookup: {power_results["eligible"]} UNPROTECTED castles ({power_results["skipped"]} skipped) | v7 timing baseline',flush=True)
            viewport=evaluate('(()=>({w:innerWidth,h:innerHeight}))()',cid)
            dismiss_x=round(viewport['w']*.80);dismiss_y=round(viewport['h']*.75)
            def dismiss_click():
                for kind,extra in [('mouseMoved',{}),('mousePressed',{'button':'left','clickCount':1}),
                                   ('mouseReleased',{'button':'left','clickCount':1})]:
                    wait_reply(send('Input.dispatchMouseEvent',
                        {'type':kind,'x':dismiss_x,'y':dismiss_y,**extra}))
            powered=[]
            for index,row in enumerate(rows):
                name=row[4]; mid=row[3]; power=0
                if row[6]!='UNPROTECTED' or name.startswith("Night'sWatch"):
                    powered.append(row)
                    continue
                power_results['attempted']+=1
                active['power_visit']=index
                try:
                    if not name:
                        raise ValueError('castle has no player name for exact matching')
                    nav_start=time.monotonic()
                    result=evaluate(NAV.replace('__MAPID__',str(mid)),cid)
                    if not isinstance(result,dict) or not result.get('ok'):
                        raise RuntimeError(f'power navigation failed: {result}')
                    time.sleep(POWER_CASTLE_SETTLE)
                    deadline=nav_start+POWER_TIMEOUT
                    while time.monotonic()<deadline:
                        matching=next((profile for profile in active['power_profiles']
                            if profile['visit']==index and profile['at']>=nav_start
                            and profile['player']==name and type(profile['power']) is int
                            and profile['power']>=0),None)
                        if matching:
                            power=f'{matching["power"]/1_000_000:.3f}'
                            power_results['matched']+=1
                            break
                        poll()
                    if power==0:
                        raise TimeoutError('no matching 20007 profile within four seconds')
                except Exception as exc:
                    power_results['unavailable']+=1
                    if len(power_results['errors'])<30:
                        power_results['errors'].append({'x':row[1],'y':row[2],
                                                         'player':name,'error':str(exc)})
                finally:
                    # Always attempt the verified water + outside-click dismissal.
                    try:
                        result=evaluate(NAV.replace('__MAPID__',str(POWER_WATER_MAP_ID)),cid)
                        if not isinstance(result,dict) or not result.get('ok'):
                            raise RuntimeError(f'water navigation failed: {result}')
                        time.sleep(POWER_WATER_SETTLE)
                        dismiss_click()
                        time.sleep(POWER_DISMISS_WAIT)
                    except Exception as exc:
                        if len(power_results['errors'])<30:
                            power_results['errors'].append({'x':row[1],'y':row[2],
                                                             'player':name,'dismiss_error':str(exc)})
                powered.append(row[:7]+(power,)+row[8:])
                if (index+1)%25==0 or index+1==len(rows):
                    print(f'  Power: {power_results["attempted"]}/{power_results["eligible"]} eligible | matched {power_results["matched"]}',flush=True)
            rows=powered
            active['power_visit']=None
        report['power_lookup']=power_results
        report['castles']=[{'x':r[1],'y':r[2],'map_id':r[3],'name':r[4],'player_id':r[5],
                            'status':r[6],'power_millions':r[7],'observations':r[10]} for r in rows]
        report['summary']={'navigation_successes':jumps,'navigation_planned':total if a.mode=='auto' else None,
                           'missing_response_points':len(missing_points),'retry_attempts':retry_attempts,
                           'response_timeout_seconds':a.response_timeout,'max_retries':a.retries,
                           'buffer_size':a.buffer,'batch_stats':batch_stats,
                           'average_batch_duration_seconds':round(sum(response_waits)/len(response_waits),4) if response_waits else None,
                           'castle_9001_frames':active['castle_frames'],
                           'decoded_castles':len(rows),'elapsed_seconds':round(time.monotonic()-origin,3)}
        report['csv_export']={'status':'success','filename':export_csv(rows,a.kingdom,stamp),'row_count':len(rows)}
        report['missing_points']=missing_points
        if (missing_points or active['castle_frames']<jumps) and not a.no_db:
            report['import']={'status':'skipped_incomplete','row_count':0}
            raise RuntimeError(f'Incomplete sweep: {len(missing_points)} unresolved batches; refusing partial DB snapshot')
        if a.no_db:
            report['import']={'status':'skipped','row_count':0}
        else:
            import_mysql(a,rows,stamp)
            report['import']={'status':'success','row_count':len(rows)}
    except (Exception, KeyboardInterrupt) as e:
        report['errors'].append(f'{type(e).__name__}: {e}')
        print('STOP:',type(e).__name__,e,flush=True)
    finally:
        ws.close()
        Path(a.report).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        # Final summary: complete decoded scan, independent of console display filters.
        # SQL state counts inserted castle records only (not import_history).
        summary_rows = report.get('castles', [])
        unprotected = sum(r.get('status') == 'UNPROTECTED' for r in summary_rows)
        protected = sum(r.get('status') == 'PROTECTED' for r in summary_rows)
        elapsed = time.monotonic() - origin
        import_result = report.get('import') or {}
        inserted = import_result.get('row_count', 0) if import_result.get('status') == 'success' else 0
        print(f'Total Castles : {len(summary_rows)} | Unprotected : {unprotected} | Protected : {protected} | run-time : {elapsed:.2f}s', flush=True)
        print(f'SQL state : {inserted}', flush=True)
        if (report.get('csv_export') or {}).get('status') == 'success':
            print('CSV:',report['csv_export']['filename'],flush=True)
        print('Report:',a.report,flush=True)
if __name__=='__main__':main()
