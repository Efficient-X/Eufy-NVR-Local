// JSON-lines adapter for the Apache-2.0 PTCS transport. No Eufy JS/WASM runs here.
const {packetize, Reassembler, MAX_FRAME_BYTES} = require("./ptcs_transport.js");
const LINK = { Unknow: 0, Cmd: 1, File: 2, Notify: 3, PlayBack: 4, Live: 5, SendLive: 6, Inner: 99 };
const CH = { COMMAND: 0, MEDIA: 1, NOTIFY: 2, DOWNLOAD: 3, PLAYBACK: 4, LIVE: 5, MAX: 6 };
function link2channel(link) {
  switch (link) {
    case LINK.Cmd: return CH.COMMAND;
    case LINK.File: return CH.DOWNLOAD;
    case LINK.Notify: return CH.NOTIFY;
    case LINK.PlayBack: return CH.PLAYBACK;
    case LINK.Live:
    case LINK.SendLive: return CH.LIVE;
    default: return CH.MAX;
  }
}
function channel2link(ch) {
  switch (ch) {
    case CH.COMMAND: return LINK.Cmd;
    case CH.MEDIA: return LINK.Live;
    case CH.NOTIFY: return LINK.Notify;
    case CH.DOWNLOAD: return LINK.File;
    case CH.PLAYBACK: return LINK.PlayBack;
    case CH.LIVE: return LINK.Live;
    case CH.MAX: return LINK.Inner;
    default: return LINK.Unknow;
  }
}
function buildOpenLive(userId, channelArray) {
  const payload = Buffer.from(JSON.stringify({ account_id: userId, cmd: 1103, payload: { channel_info: { array_size: channelArray.length, channel_array: channelArray } } }), "utf8");
  const header = Buffer.alloc(16);
  header.write("XZYH", 0, "ascii");
  header.writeUInt16LE(1350, 4);
  header.writeUInt32LE(payload.length, 6);
  header[12] = 255;
  header[15] = 2;
  return Buffer.concat([header, payload]);
}

// Some Eufy control failures are not JSON. They are a signed int32 status followed by zeros.
function fixedControlStatus(frame) {
  if (!Buffer.isBuffer(frame) || frame.length < 20 || frame.toString("ascii", 0, 4) !== "XZYH") return null;
  if (frame.readUInt16LE(4) === 1032) return null;
  const payload = frame.subarray(16);
  if (payload.length < 4) return null;
  for (let i = 4; i < payload.length; i++) if (payload[i] !== 0) return null;
  let binaryPrefix = false;
  for (let i = 0; i < 4; i++) if (payload[i] < 0x20 || payload[i] > 0x7e) binaryPrefix = true;
  return binaryPrefix ? payload.readInt32LE(0) : null;
}

const b64 = (buf) => Buffer.from(buf).toString("base64");
const unb64 = (s) => Buffer.from(s, "base64");

async function selftest() {
  const frames = [];
  const receiver = new Reassembler((buf, channel) => frames.push({buf, channel}));
  const cmd = buildOpenLive("SYNTHETIC_TEST_USER", [0]);
  for (const packet of packetize(cmd, {frameId: 1})) receiver.push(packet);
  const pass = frames.length === 1 && frames[0].channel === 0 && frames[0].buf.equals(cmd);
  console.log(pass ? "SELFTEST PASS: Apache PTCS framing roundtrips in Node." : "SELFTEST FAIL: framing roundtrip mismatch.");
  process.exitCode = pass ? 0 : 2;
}

async function serve() {
  const send = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
  let sequence = 0;
  let lastFrameId = 0;
  const pushFrame = (link, bytes) => {
    let frameId = Date.now() >>> 0;
    if (frameId <= lastFrameId) frameId = (lastFrameId + 1) >>> 0;
    lastFrameId = frameId;
    for (const packet of packetize(bytes, {channel: link2channel(link), frameId, sequence})) {
      send({ev: "tx", src: "send", b64: b64(packet)});
    }
    sequence = (sequence + 1) & 0xffff;
  };
  const receiveFrame = (frame, channel) => {
      const link = channel2link(channel);
      const status = fixedControlStatus(frame);
      if (status !== null) {
        send({ ev: "control_status", channel: link, status });
        console.error(`[oracle] fixed control status ${status}`);
        if (status === -104) {
          console.error("EUFY_AUTHORIZATION_ERROR_-104: use the eufy account that owns/administers this NVR; shared/member accounts cannot open NVR command sessions.");
          setTimeout(() => { try { process.kill(process.ppid, "SIGTERM"); } catch (e) {} }, 10);
        }
      }
      send({ ev: "frame", channel: link, b64: b64(frame) });
  };
  const receiver = new Reassembler(receiveFrame);
  const sweep = setInterval(() => receiver.expire(), 1000);
  sweep.unref();
  let input = "";
  process.stdin.on("data", (chunk) => {
    input += chunk.toString("utf8");
    if (input.length > 24 * 1024 * 1024) {
      console.error("PTCS input limit exceeded");
      process.exit(1);
    }
    let nl;
    while ((nl = input.indexOf("\n")) >= 0) {
      const line = input.slice(0, nl); input = input.slice(nl + 1);
      if (!line.trim()) continue;
      let msg;
      try { msg = JSON.parse(line); } catch (e) { continue; }
      try {
        if (msg.op === "send") pushFrame(msg.link || LINK.Cmd, unb64(msg.b64));
        else if (msg.op === "recv") {
          const packet = unb64(msg.b64);
          if (packet.length > MAX_FRAME_BYTES) throw new RangeError("Input size out of range");
          if (packet.length >= 16 && packet.toString("ascii", 0, 4) === "XZYH") receiveFrame(packet, CH.COMMAND);
          else receiver.push(packet);
        }
        else if (msg.op === "buildOpenLive") send({ ev: "openLive", b64: b64(buildOpenLive(msg.userId, msg.channels || [0])) });
      } catch (e) { send({ ev: "error", op: "protocol", msg: "Invalid protocol operation" }); }
    }
  });
  process.stdin.on("end", () => process.exit(0));
  send({ ev: "ready" });
}

module.exports = {buildOpenLive, fixedControlStatus, link2channel, channel2link};
if (require.main === module) {
  const mode = process.argv[2] || "selftest";
  (mode === "serve" ? serve() : selftest()).catch(() => {
    console.error("PTCS adapter failed");
    process.exit(1);
  });
}
