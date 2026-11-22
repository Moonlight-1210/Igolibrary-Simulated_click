/**
 * Cloudflare 快速隧道：把本机 HTTP 服务暴露成公网 https 地址（手机任何网络都能访问）
 * 依赖 bin/cloudflared.exe（IGoLibrary 自带同款）
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CLOUDFLARED = path.join(__dirname, 'bin', 'cloudflared.exe');
const URL_RE = /https:\/\/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.trycloudflare\.com/i;

let proc = null;
let url = null;
let starting = null;
let logs = [];

function getTunnelStatus() {
  return { running: !!proc, url, logs: logs.slice(-5) };
}

function stopTunnel() {
  if (proc) { try { proc.kill(); } catch (e) { } }
  proc = null; url = null; starting = null;
}

function startTunnel(port) {
  if (proc && url) return Promise.resolve({ url });
  if (starting) return starting;
  starting = new Promise((resolve, reject) => {
    if (!fs.existsSync(CLOUDFLARED)) {
      starting = null;
      return reject(new Error('找不到 cloudflared.exe（seat-app/bin/ 目录）'));
    }
    proc = spawn(CLOUDFLARED, ['tunnel', '--url', `http://127.0.0.1:${port}`, '--no-autoupdate'], {
      windowsHide: true,
      env: Object.assign({}, process.env, { NO_AUTO_UPDATE: '1' }),
    });
    let settled = false;
    const onData = (buf) => {
      const text = buf.toString();
      logs.push(text);
      if (logs.length > 300) logs.shift();
      const m = text.match(URL_RE);
      if (m && !settled) {
        settled = true;
        url = m[0];
        resolve({ url });
      }
    };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);
    proc.on('exit', (code) => {
      proc = null; url = null; starting = null;
      if (!settled) reject(new Error('cloudflared 启动失败（退出码 ' + code + '），可能网络无法访问 Cloudflare'));
    });
    setTimeout(() => {
      if (!settled) {
        stopTunnel();
        starting = null;
        reject(new Error('cloudflared 启动超时（60 秒内未获取到公网地址），请检查网络/代理'));
      }
    }, 60000);
  });
  return starting;
}

module.exports = { startTunnel, stopTunnel, getTunnelStatus };
