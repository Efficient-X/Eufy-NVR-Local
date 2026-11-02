// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 mega-yfue. Python/Node bridge adaptations by Efficient X (EFX).
// Adapted from mega-yfue/eufy-sdk src/transport/rtc/ptcs-framer.ts at
// 5f92bea559fa1a5b619a84a9966ccc90a541735e (Apache-2.0).
// EFX modifications: CommonJS/Node 18 interface, complete legacy channel map,
// bounded reassembly, strict headers and duplicate/conflicting packet handling.
// See licenses/eufy-sdk-APACHE-2.0.txt and licenses/eufy-sdk-PROVENANCE.md.
"use strict";

const HEADER = 28;
const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const MAX_BUFFER_BYTES = 32 * 1024 * 1024;
const MAX_PARTIALS = 64;
const MAX_CHUNKS = 32768;
const STALE_MS = 15000;

function packetize(frame, {channel = 0, frameId, payloadBytes = 1000, sequence = 0}) {
  if (!Buffer.isBuffer(frame) || frame.length > MAX_FRAME_BYTES) throw new RangeError("Frame size out of range");
  if (!Number.isInteger(payloadBytes) || payloadBytes < 1 || payloadBytes > 1023) throw new RangeError("Payload size out of range");
  const count = Math.max(1, Math.ceil(frame.length / payloadBytes));
  if (count > MAX_CHUNKS) throw new RangeError("Too many frame packets");
  const packets = [];
  for (let index = 0; index < count; index++) {
    const body = frame.subarray(index * payloadBytes, (index + 1) * payloadBytes);
    const packet = Buffer.alloc(HEADER + payloadBytes);
    packet.write("PTCS", 0, "ascii");
    packet[4] = 3;
    packet[5] = channel & 0xff;
    packet.writeUInt16LE(sequence & 0xffff, 6);
    packet.writeUInt32LE(frameId >>> 0, 8);
    packet.writeUInt32LE(frame.length, 12);
    packet.writeUInt16LE(index, 16);
    packet.writeUInt16LE(0x4000 | (index === count - 1 ? 0x0400 : 0) | body.length, 18);
    body.copy(packet, HEADER);
    packets.push(packet);
  }
  return packets;
}

function parseHeader(packet) {
  if (!Buffer.isBuffer(packet) || packet.length < HEADER || packet.toString("ascii", 0, 4) !== "PTCS" || packet[4] !== 3) return null;
  const flags = packet.readUInt16LE(18);
  // Actual NVR notifications also set 0x0800 (observed flags 0x4e9b).
  // It is independent of the last-chunk/length fields. The pinned upstream
  // parser extracts those fields without rejecting this marker. Preserve the
  // type nibble check and all size/reassembly bounds below; infer no meaning
  // for the marker beyond accepting this observed wire variant.
  if ((flags & 0xf000) !== 0x4000) return null;
  const payloadLength = flags & 0x03ff;
  const frameLength = packet.readUInt32LE(12);
  const index = packet.readUInt16LE(16);
  if (packet.length < HEADER + payloadLength || frameLength > MAX_FRAME_BYTES || index >= MAX_CHUNKS) return null;
  if (payloadLength > frameLength || (frameLength > 0 && payloadLength === 0)) return null;
  return {channel: packet[5], sequence: packet.readUInt16LE(6), frameId: packet.readUInt32LE(8),
    frameLength, index, last: (flags & 0x0400) !== 0, payloadLength};
}

class Reassembler {
  constructor(onFrame, now = Date.now) {
    this.onFrame = onFrame;
    this.now = now;
    this.partials = new Map();
    this.bufferedBytes = 0;
  }
  drop(key) {
    const frame = this.partials.get(key);
    if (frame) this.bufferedBytes -= frame.bytes;
    this.partials.delete(key);
  }
  expire() {
    const now = this.now();
    for (const [key, frame] of this.partials) if (now - frame.touched > STALE_MS) this.drop(key);
  }
  push(packet) {
    const h = parseHeader(packet);
    if (!h) return false;
    this.expire();
    const key = `${h.channel}:${h.frameId}`;
    let frame = this.partials.get(key);
    if (!frame) {
      if (this.partials.size >= MAX_PARTIALS) this.drop(this.partials.keys().next().value);
      frame = {length: h.frameLength, sequence: h.sequence, chunks: new Map(), bytes: 0, touched: this.now()};
      this.partials.set(key, frame);
    }
    const body = packet.subarray(HEADER, HEADER + h.payloadLength);
    const previous = frame.chunks.get(h.index);
    if (frame.length !== h.frameLength || frame.sequence !== h.sequence ||
        (previous && !previous.equals(body)) ||
        (frame.lastIndex !== undefined && (h.index > frame.lastIndex || (h.last && h.index !== frame.lastIndex)))) {
      this.drop(key);
      return false;
    }
    if (h.last) {
      if ([...frame.chunks.keys()].some(index => index > h.index)) { this.drop(key); return false; }
      frame.lastIndex = h.index;
    }
    if (!previous) {
      if (frame.bytes + body.length > frame.length || this.bufferedBytes + body.length > MAX_BUFFER_BYTES) {
        this.drop(key);
        return false;
      }
      frame.chunks.set(h.index, Buffer.from(body));
      frame.bytes += body.length;
      this.bufferedBytes += body.length;
    }
    frame.touched = this.now();
    if (frame.lastIndex === undefined || frame.chunks.size !== frame.lastIndex + 1) return true;
    if (frame.bytes !== frame.length) { this.drop(key); return false; }
    const parts = [];
    for (let index = 0; index <= frame.lastIndex; index++) {
      const chunk = frame.chunks.get(index);
      if (!chunk) return true;
      parts.push(chunk);
    }
    this.drop(key);
    this.onFrame(Buffer.concat(parts, frame.length), h.channel);
    return true;
  }
  clear() { this.partials.clear(); this.bufferedBytes = 0; }
}

module.exports = {packetize, parseHeader, Reassembler, HEADER, STALE_MS,
  MAX_FRAME_BYTES, MAX_BUFFER_BYTES, MAX_PARTIALS, MAX_CHUNKS};
