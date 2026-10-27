// Download the pinned go2rtc binary; use the OS package manager for FFmpeg.
const https = require("https");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { execSync, spawnSync } = require("child_process");

const HERE = __dirname;
const BIN = path.join(HERE, "bin");
fs.mkdirSync(BIN, { recursive: true });
const isWin = process.platform === "win32";
const arch = process.arch === "arm64" ? "arm64" : "amd64";
// Verify the official go2rtc binary before making it available to the bridge.
const GO2RTC_VERSION = "v1.9.14";
const SHA256 = {
  "go2rtc_linux_amd64": "32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6",
  "go2rtc_linux_arm64": "359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50",
  "go2rtc_win64.zip": "dd4167d75cb04abe618855b7c71f8658bd009f60c1a71835d134d2c11c939907",
  "go2rtc_win_arm64.zip": "814be0f6d8669025c7bccdd1f026ffaf613abae5352239f4ec84de543b94594a",
  "go2rtc_mac_amd64.zip": "9b0b9a27a4dc3a5b8b93376e7e8fc2787c6af624a512842622be84aec0171c7a",
  "go2rtc_mac_arm64.zip": "919b78adc759d6b3883d1e1b2ac915ac0985bb903ff1897b4d228527bd64690c",
};

function dl(url, dest, expected) {
  const temporary = `${dest}.part-${process.pid}`;
  return new Promise((res, rej) => {
    const fail = (error) => {
      try { fs.rmSync(temporary, { force: true }); } catch {}
      rej(error);
    };
    const request = (current) => https.get(current, { headers: { "User-Agent": "fetch_deps" } }, (r) => {
      if (r.statusCode >= 300 && r.statusCode < 400 && r.headers.location) {
        r.resume();
        request(new URL(r.headers.location, current).toString());
        return;
      }
      if (r.statusCode !== 200) {
        r.resume();
        fail(new Error(`HTTP ${r.statusCode} for ${current}`));
        return;
      }
      const digest = crypto.createHash("sha256");
      const f = fs.createWriteStream(temporary, { mode: 0o600 });
      r.on("data", (chunk) => digest.update(chunk));
      r.on("error", fail);
      f.on("error", fail);
      r.pipe(f);
      f.on("finish", () => f.close(() => {
        const actual = digest.digest("hex");
        if (actual !== expected) {
          fail(new Error(`SHA-256 mismatch for ${path.basename(dest)}: ${actual}`));
          return;
        }
        try {
          fs.rmSync(dest, { force: true });
          fs.renameSync(temporary, dest);
          res(fs.statSync(dest).size);
        } catch (error) {
          fail(error);
        }
      }));
    }).on("error", fail);
    request(url);
  });
}
function have(cmd) { try { execSync((isWin ? "where " : "command -v ") + cmd, { stdio: "ignore" }); return true; } catch { return false; } }
function unzip(zip, outdir) {
  if (isWin) execSync(`powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath '${zip}' -DestinationPath '${outdir}'"`);
  else execSync(`unzip -o "${zip}" -d "${outdir}"`);
}

(async () => {
  // 2) go2rtc
  process.stdout.write("go2rtc ... ");
  try {
    if (isWin) {
      const asset = arch === "arm64" ? "go2rtc_win_arm64.zip" : "go2rtc_win64.zip";
      const zip = path.join(BIN, asset);
      await dl(`https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/${asset}`, zip, SHA256[asset]);
      unzip(zip, BIN); fs.rmSync(zip, { force: true });
      console.log("ok (bin/go2rtc.exe)");
    } else if (process.platform === "darwin") {
      const asset = `go2rtc_mac_${arch}.zip`;
      const zip = path.join(BIN, asset);
      await dl(`https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/${asset}`, zip, SHA256[asset]);
      unzip(zip, BIN); fs.rmSync(zip, { force: true });
      fs.chmodSync(path.join(BIN, "go2rtc"), 0o755);
      console.log("ok (bin/go2rtc)");
    } else {
      const bin = path.join(BIN, "go2rtc");
      const asset = `go2rtc_linux_${arch}`;
      await dl(`https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/${asset}`, bin, SHA256[asset]);
      fs.chmodSync(bin, 0o755);
      console.log("ok (bin/go2rtc)");
    }
  } catch (e) { console.log("FAILED:", e.message, "\n  Get it from https://github.com/AlexxIT/go2rtc/releases and put it in bin/"); }

  // 3) ffmpeg (best-effort)
  if (have("ffmpeg")) { console.log("ffmpeg ... already on PATH"); }
  else console.log("ffmpeg ... not found — install a trusted package via your package manager");

  console.log("\nDone. Next: `node get_auth.js` (one-time login), then start_bridge" + (isWin ? ".cmd" : ".sh"));
})().catch((error) => {
  console.error("fetch_deps FATAL:", error.message);
  process.exitCode = 1;
});
