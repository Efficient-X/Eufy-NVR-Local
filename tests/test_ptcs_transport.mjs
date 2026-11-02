import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
const require = createRequire(import.meta.url);
const {packetize, Reassembler, MAX_FRAME_BYTES, MAX_PARTIALS, MAX_BUFFER_BYTES, STALE_MS} = require('../bridge/ptcs_transport.js');
const {buildOpenLive, fixedControlStatus, link2channel, channel2link} = require('../bridge/sctp_oracle.js');

test('observed NVR notification flag delivers a synthetic device-list reply', () => {
  // Observed header facts only: v3, notification channel 2, 667-byte frame,
  // 695-byte wire packet, flags 0x4e9b. No household payload or identifiers.
  const payload = Buffer.from(JSON.stringify({cmd:9100, payload:{dev_list:
    Array.from({length:4}, (_,ch)=>({ch,name:`Synthetic ${ch}`,sn:`TEST${ch}`,status:1,sensor_num:2}))}}));
  const frame=Buffer.alloc(667); frame.write('XZYH'); frame.writeUInt16LE(1351,4);
  frame.writeUInt32LE(651,6); payload.copy(frame,16);
  const [packet]=packetize(frame,{channel:2,frameId:42,sequence:7,payloadBytes:667});
  packet.writeUInt16LE(packet.readUInt16LE(18)|0x0800,18);
  assert.equal(packet.length,695); assert.equal(packet.readUInt16LE(18),0x4e9b);
  const input=JSON.stringify({op:'recv',b64:packet.toString('base64')})+'\n';
  const result=spawnSync(globalThis.process.execPath,['bridge/sctp_oracle.js','serve'],{input,encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  const delivered=result.stdout.trim().split('\n').map(JSON.parse).find(e=>e.ev==='frame');
  assert(delivered,'NVR notification was silently discarded');
  assert.equal(delivered.channel,3); // Existing notification link contract.
  const body=Buffer.from(delivered.b64,'base64');
  assert.deepEqual(body,frame);
  assert.equal(JSON.parse(body.subarray(16,16+payload.length).toString()).payload.dev_list.length,4);
});

test('notification marker works on fragmented out-of-order frames', () => {
  const data=Buffer.alloc(2501,0xab), frames=[];
  const packets=packetize(data,{channel:2,frameId:43,sequence:8});
  for(const p of packets)p.writeUInt16LE(p.readUInt16LE(18)|0x0800,18);
  const receiver=new Reassembler((frame,ch)=>frames.push({frame,ch}));
  for(const p of packets.reverse())assert.equal(receiver.push(p),true);
  assert.deepEqual(frames,[{frame:data,ch:2}]); assert.equal(receiver.bufferedBytes,0);
});

test('notification support still rejects unrelated header bits and oversize payloads', () => {
  const receiver=new Reassembler(()=>assert.fail('Invalid frame delivered'));
  for(const flag of [0x1000,0x2000,0x8000]){
    const [p]=packetize(Buffer.from('abc'),{channel:2,frameId:44});
    p.writeUInt16LE(p.readUInt16LE(18)|flag,18);
    assert.equal(receiver.push(p),false);
  }
  const [bad]=packetize(Buffer.from('abc'),{channel:2,frameId:45});
  bad.writeUInt16LE(0x4c00|4,18); // Four bytes cannot fit a three-byte frame.
  assert.equal(receiver.push(bad),false); assert.equal(receiver.bufferedBytes,0);
});

test('wire bytes match the PTCS header layout and fixed packet padding', () => {
  const [packet] = packetize(Buffer.from('abc'), {frameId: 0x12345678, sequence: 0x1234, channel: 5, payloadBytes: 8});
  assert.equal(packet.toString('hex'), '505443530305341278563412030000000000034400000000000000006162630000000000');
});

test('rebuilds mixed channels out of order without delivering duplicates', () => {
  const delivered = [];
  const receiver = new Reassembler((body, channel) => delivered.push({body, channel}));
  const a = Buffer.alloc(128000, 0xaa), b = Buffer.alloc(3001, 0xbb);
  const ap = packetize(a, {channel: 1, frameId: 10, sequence: 3});
  const bp = packetize(b, {channel: 5, frameId: 11, sequence: 4});
  receiver.push(ap.at(-1)); receiver.push(ap.at(-1));
  for (const p of [...bp].reverse()) receiver.push(p);
  for (const p of ap.slice(0, -1).reverse()) receiver.push(p);
  assert.deepEqual(delivered, [{body:b,channel:5}, {body:a,channel:1}]);
  assert.equal(receiver.bufferedBytes, 0);
  assert.equal(channel2link(1), 5);
  assert.equal(link2channel(6), 5);
});

test('corrupt and conflicting headers never complete a frame', () => {
  const receiver = new Reassembler(() => assert.fail('Corrupt frame delivered'));
  for (const mutate of [p => p[4] = 2, p => p.writeUInt32LE(MAX_FRAME_BYTES + 1,12), p => p.writeUInt16LE(0xffff,18)]) {
    const [packet] = packetize(Buffer.from('abc'), {frameId: 1});
    mutate(packet); assert.equal(receiver.push(packet), false);
  }
  const [p] = packetize(Buffer.alloc(2000, 1), {frameId:2});
  assert.equal(receiver.push(p), true);
  const changed = Buffer.from(p); changed[28] = 2;
  assert.equal(receiver.push(changed), false);
  assert.equal(receiver.bufferedBytes, 0);
  const packets = packetize(Buffer.alloc(2000, 1), {frameId:3});
  receiver.push(packets[0]); packets[1].writeUInt16LE(2,6);
  assert.equal(receiver.push(packets[1]), false);
});

test('incomplete frames are bounded and expire', () => {
  let now = 0;
  const receiver = new Reassembler(() => assert.fail('Incomplete frame delivered'), () => now);
  for (let id = 0; id < MAX_PARTIALS * 2; id++) {
    const [p] = packetize(Buffer.alloc(2000, 1), {frameId:id}); receiver.push(p);
  }
  assert.equal(receiver.partials.size, MAX_PARTIALS);
  assert.equal(receiver.bufferedBytes, MAX_PARTIALS * 1000);
  now = STALE_MS + 1; receiver.expire();
  assert.equal(receiver.partials.size, 0); assert.equal(receiver.bufferedBytes, 0);
});

test('aggregate buffering cannot exceed its budget', () => {
  const receiver = new Reassembler(() => assert.fail('Incomplete frame delivered'));
  for (let id = 1; id <= 4; id++) {
    const packets = packetize(Buffer.alloc(12 * 1024 * 1024), {frameId:id});
    for (const p of packets.slice(0,-1)) receiver.push(p);
    assert(receiver.bufferedBytes <= MAX_BUFFER_BYTES);
  }
  receiver.clear(); assert.equal(receiver.bufferedBytes, 0);
});

test('adapter emits the existing JSON-lines contract with no vendor runtime', () => {
  const frame = buildOpenLive('synthetic-account', [0,1]);
  const packets = packetize(frame, {frameId:99});
  const input = [{op:'send', link:1, b64:frame.toString('base64')},
    ...packets.map(p => ({op:'recv', b64:p.toString('base64')})),
    {op:'buildOpenLive', userId:'synthetic-account', channels:[0,1]}].map(p => JSON.stringify(p)).join('\n') + '\n';
  const process = spawnSync(globalThis.process.execPath, ['bridge/sctp_oracle.js','serve'], {input,encoding:'utf8'});
  assert.equal(process.status, 0, process.stderr);
  const events = process.stdout.trim().split('\n').map(line => JSON.parse(line));
  assert.equal(events[0].ev, 'ready');
  assert(events.some(e => e.ev === 'tx' && e.src === 'send'));
  const output = events.find(e => e.ev === 'frame');
  assert.equal(output.channel,1); assert.equal(output.b64,frame.toString('base64'));
  assert.equal(events.find(e => e.ev === 'openLive').b64, frame.toString('base64'));
});

test('binary authorization errors remain distinguishable from video', () => {
  const frame = Buffer.alloc(24); frame.write('XZYH'); frame.writeUInt16LE(1350,4); frame.writeInt32LE(-104,16);
  assert.equal(fixedControlStatus(frame),-104);
  frame.writeUInt16LE(1032,4); assert.equal(fixedControlStatus(frame),null);
});
