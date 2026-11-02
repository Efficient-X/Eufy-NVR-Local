"""Synthetic wire -> real Node adapter -> actual Python discovery functions.

AST extraction avoids importing the stream script's account/network entrypoint.
No real NVR payload, identity, address, authentication or media is used.
"""
import ast
import base64
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bridge'))
import gen_go2rtc


@pytest.mark.parametrize('ports',[4,8])
@pytest.mark.parametrize('fragmented',[False,True])
def test_notification_marker_reaches_atomic_manifest_and_full_dual_lens_layout(tmp_path,ports,fragmented):
    node=shutil.which('node')
    if not node:pytest.skip('Node is required for the real JSON-lines adapter')
    payload=json.dumps({'cmd':9100,'payload':{'dev_list':[
        {'ch':i,'name':f'Synthetic {i}','sn':f'TEST-{i}','status':1,'sensor_num':2}
        for i in range(ports)]}},separators=(',',':')).encode()
    frame=bytearray(16+len(payload));frame[:4]=b'XZYH'
    struct.pack_into('<H',frame,4,1351);frame[16:]=payload
    width=137 if fragmented else 1000
    packets=[]
    for index,start in enumerate(range(0,len(frame),width)):
        body=frame[start:start+width]
        last=start+width>=len(frame)
        header=bytearray(28);header[:4]=b'PTCS';header[4:6]=bytes([3,2])
        struct.pack_into('<HIIHH',header,6,7,42,len(frame),index,
                         0x4000|0x0800|(0x0400 if last else 0)|len(body))
        packets.append(bytes(header+body))
    # Reordering and a duplicated fragment exercise bounded reassembly too.
    wire=list(reversed(packets))
    if fragmented:wire.insert(1,wire[0])
    stdin=''.join(json.dumps({'op':'recv','b64':base64.b64encode(p).decode()})+'\n' for p in wire)
    process=subprocess.run([node,'bridge/sctp_oracle.js','serve'],input=stdin,text=True,
                           capture_output=True,cwd=ROOT,timeout=10,check=True)
    delivered=[json.loads(line) for line in process.stdout.splitlines() if json.loads(line).get('ev')=='frame']
    assert len(delivered)==1
    tree=ast.parse((ROOT/'bridge/eufy_stream.py').read_text())
    functions=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in {'on_frame','handle_devlist'}]
    assert {n.name for n in functions}=={'on_frame','handle_devlist'}
    manifest=tmp_path/'cameras.json'
    scope={'state':{'frames_seen':0,'discovered':False,'nvr_ip':None},'DISCOVER':True,
           'framelog':io.StringIO(),'json':json,'os':os,'CAMERAS_JSON':str(manifest),
           'STATION_SN':'SYNTHETIC-STATION','log':lambda *args:None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[])),
                 'actual-eufy-stream-discovery-functions','exec'),scope)
    event=delivered[0]
    scope['on_frame'](event['channel'],base64.b64decode(event['b64']))
    assert scope['state']['discovered'] and scope['state']['frames_seen']==1
    assert len(json.loads(manifest.read_text())['cameras'])==ports
    assert not Path(str(manifest)+'.tmp').exists()
    config=tmp_path/'go2rtc.yaml'
    gen_go2rtc.generate(manifest,config,tmp_path/'names.json',username='synthetic',
                       password='synthetic-test-only',shared_grid=True,grid_lens='both')
    layout_path=Path(yaml.safe_load(config.read_text())['api']['static_dir'])/'eufy-grid-layout.json'
    layout=json.loads(layout_path.read_text())
    assert len(layout['tiles'])==ports*2
    assert layout['grid_columns']==(3 if ports==4 else 4)
    assert 'TEST-' not in layout_path.read_text()
