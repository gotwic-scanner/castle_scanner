#!/usr/bin/env python3
"""Single-castle power lookup over Chrome CDP; prints one JSON result to stdout."""
import argparse
import base64
import json
import sys
import time
import urllib.request
import websocket
from gotwic_scanner_v2_2 import (NAV, PREFLIGHT, decode_power_20007, POWER_TIMEOUT,
    POWER_CASTLE_SETTLE, POWER_WATER_SETTLE, POWER_DISMISS_WAIT, POWER_WATER_MAP_ID)


def detect(x, y, name, port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=5) as response:
        pages = json.load(response)
    page = next((p for p in pages if p.get('type') == 'page' and
                 'gtarcade' in p.get('url', '').lower() and p.get('webSocketDebuggerUrl')), None)
    if not page:
        raise RuntimeError('No GTArcade game tab found on Chrome CDP')
    ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=5, suppress_origin=True)
    ws.settimeout(.01)
    sequence = 0
    replies = {}
    contexts = []
    profiles = []
    def send(method, params=None):
        nonlocal sequence
        sequence += 1
        ws.send(json.dumps({'id':sequence,'method':method,'params':params or {}}))
        return sequence
    def poll():
        try:
            msg = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            return
        if 'id' in msg:
            replies[msg['id']] = msg
            return
        if msg.get('method') == 'Runtime.executionContextCreated':
            contexts.append(msg.get('params', {}).get('context', {}))
        if msg.get('method') == 'Network.webSocketFrameReceived':
            response = msg.get('params', {}).get('response', {})
            if response.get('opcode') != 2:
                return
            try:
                raw = base64.b64decode(response.get('payloadData', ''), validate=True)
                if len(raw) >= 13 and int.from_bytes(raw[4:8], 'big') == 20007:
                    decoded = decode_power_20007(raw)
                    if decoded:
                        profiles.append((time.monotonic(), decoded))
            except (ValueError, KeyError, TypeError, UnicodeError):
                pass
    def reply(rid, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            if rid in replies:
                result = replies.pop(rid)
                if 'error' in result:
                    raise RuntimeError(str(result['error']))
                return result
            poll()
        raise TimeoutError('Chrome CDP command timed out')
    def evaluate(expression, cid):
        result = reply(send('Runtime.evaluate', {'expression':expression,'contextId':cid,
                       'returnByValue':True,'silent':True,'timeout':4000}))['result']
        if result.get('exceptionDetails'):
            raise RuntimeError(str(result['exceptionDetails']))
        return result.get('result',{}).get('value')
    try:
        reply(send('Runtime.enable'))
        reply(send('Network.enable',{'maxTotalBufferSize':16000000}))
        until=time.monotonic()+.3
        while time.monotonic()<until:
            poll()
        valid=[c for c in contexts if c.get('auxData',{}).get('isDefault') and
               'logins-gotpc.gtarcade.com' in c.get('origin','')]
        if not valid:
            raise RuntimeError('Game WASM execution context unavailable')
        cid=valid[0]['id']
        if not evaluate(PREFLIGHT,cid).get('ok'):
            raise RuntimeError('Game WASM preflight failed')
        viewport=evaluate('(()=>({w:innerWidth,h:innerHeight}))()',cid)
        px,py=round(viewport['w']*.8),round(viewport['h']*.75)
        start=time.monotonic()
        try:
            result=evaluate(NAV.replace('__MAPID__',str(y*720+x)),cid)
            if not result or not result.get('ok'):
                raise RuntimeError(f'Castle navigation failed: {result}')
            time.sleep(POWER_CASTLE_SETTLE)
            deadline=start+POWER_TIMEOUT
            while time.monotonic()<deadline:
                for seen, profile in profiles:
                    if (seen>=start and profile.get('player')==name and
                        type(profile.get('power')) is int and profile['power']>=0):
                        power=profile['power']
                        return {'success':True,'x':x,'y':y,'player':name,'power':power,
                                'power_millions':f'{power/1000000:.3f}'}
                poll()
            raise TimeoutError('No matching castle profile received within four seconds')
        finally:
            try:
                evaluate(NAV.replace('__MAPID__',str(POWER_WATER_MAP_ID)),cid)
                time.sleep(POWER_WATER_SETTLE)
                for event,extra in [('mouseMoved',{}),('mousePressed',{'button':'left','clickCount':1}),
                                    ('mouseReleased',{'button':'left','clickCount':1})]:
                    reply(send('Input.dispatchMouseEvent',{'type':event,'x':px,'y':py,**extra}))
                time.sleep(POWER_DISMISS_WAIT)
            except Exception:
                pass
    finally:
        ws.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--x',type=int,required=True)
    parser.add_argument('--y',type=int,required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--port',type=int,default=9222)
    args=parser.parse_args()
    if not (0<=args.x<=650 and 0<=args.y<=710):
        parser.error('Coordinates outside selectable map')
    try:
        result=detect(args.x,args.y,args.name,args.port)
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'success':False,'error':str(exc)}))
        return 1
    return 0

if __name__=='__main__':
    sys.exit(main())
