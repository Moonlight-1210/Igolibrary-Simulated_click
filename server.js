/**
 * 我去图书馆 · 网页点击抢座助手（简单版）
 * - 扫码/链接登录（微信授权链接换 cookie）
 * - 首页：账号 / 当前预约 / 抢座状态 / 日志
 * - 远程控制：局域网内手机浏览器访问同一地址即可操作
 *
 * 启动：node server.js（或双击 启动.bat）
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { exec } = require('child_process');
const session = require('./session');
const { SeatGrabber } = require('./grabber');
const { cancelReservation } = require('./cancel');
const tunnel = require('./tunnel');
const { fetchDataViaBrowser } = require('./browser-data');
const QRCode = require('qrcode');

let relayState = null; // 扫码登录：{ token, status: 'waiting'|'ok'|'err', message, createdAt, mobileUrl }

const PORT = Number(process.env.PORT || 7789);
const PUBLIC_DIR = path.join(__dirname, 'public');
const logs = [];
const MAX_LOGS = 800;
let logSeq = 0;
function pushLog(msg, level) {
  const item = { seq: ++logSeq, ts: Date.now(), time: new Date().toLocaleTimeString('zh-CN', { hour12: false }), level: level || 'info', msg: String(msg) };
  logs.push(item);
  if (logs.length > MAX_LOGS) logs.splice(0, logs.length - MAX_LOGS);
}

let grabber = null;
let lastSuccess = null;
let dataCache = null; // { account, reserve, libs, fetchedAt }

/** 用真实浏览器打开官方网页获取账号/预约/场馆数据（不直接调用平台接口） */
async function refreshData() {
  const s = session.loadSession();
  if (!s) { dataCache = null; return null; }
  const data = await fetchDataViaBrowser(path.join(__dirname, 'data', 'session.json'));
  // 回填账号信息到会话
  if (data.account) {
    s.userId = data.account.userId;
    s.nick = data.account.nick || s.nick || '';
    s.school = data.account.school || s.school || '';
    try { session.saveSession(s); } catch (e) { }
  }
  dataCache = { account: data.account, reserve: data.reserve, libs: data.libs, fetchedAt: Date.now() };
  return dataCache;
}

function lanUrls() {
  const out = [];
  const ifs = os.networkInterfaces();
  for (const name of Object.keys(ifs)) {
    for (const info of ifs[name] || []) {
      if (info.family === 'IPv4' && !info.internal) {
        const ip = info.address;
        // 排除虚拟网卡：Radmin VPN(26.x)、FlClash(198.18.x)
        if (ip.startsWith('26.') || ip.startsWith('198.18.')) continue;
        out.push(`http://${ip}:${PORT}`);
      }
    }
  }
  const score = (u) => {
    const h = new URL(u).hostname;
    if (h.startsWith('192.168.')) return 0;
    if (h.startsWith('10.')) return 1;
    if (h.startsWith('172.')) return 2;
    return 3;
  };
  return out.sort((a, b) => score(a) - score(b));
}

function sendJson(res, code, obj) {
  const data = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(data);
}

/** 生成扫码登录会话：自动开启公网通道（像 IGoLibrary 一样，手机任意网络可扫） */
async function qrLoginStart() {
  const token = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const urls = lanUrls();
  const lanHost = urls.length ? new URL(urls[0]).hostname : '127.0.0.1';
  // 先确保公网通道可用；失败则回退局域网
  let t = tunnel.getTunnelStatus();
  let mode = 'tunnel';
  if (!t.url) {
    try {
      pushLog('正在开启公网通道（手机任意网络可扫码）...');
      t = await tunnel.startTunnel(PORT);
    } catch (e) {
      mode = 'lan';
      pushLog('公网通道开启失败，回退局域网扫码：' + e.message);
    }
  }
  const base = (t.url || `http://${lanHost}:${PORT}`).replace(/\/$/, '');
  const mobileUrl = base + '/relay?token=' + token;
  const qrDataUrl = await QRCode.toDataURL(mobileUrl, { width: 260, margin: 1, color: { dark: '#111827', light: '#ffffff' } });
  relayState = { token, status: 'waiting', message: '', createdAt: Date.now(), mobileUrl };
  return {
    token,
    mode,
    qrDataUrl,
    mobileUrl,
    lanUrl: `http://${lanHost}:${PORT}/relay?token=${token}`,
    tunnelUrl: t.url ? t.url + '/relay?token=' + token : null,
  };
}

/** 手机快传页（微信扫码打开）：展示微信授权二维码 + 粘贴授权链接提交 */
function renderRelayPage(token) {
  const tokenJson = JSON.stringify(token);
  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store">
<title>登录授权快传</title>
<style>
  :root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color-scheme:light}
  body{margin:0;background:#f5f7fb;color:#1d2129}
  main{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box}
  section{width:min(100%,430px);background:#fff;border:1px solid #e7eaee;border-radius:18px;padding:22px;box-shadow:0 10px 28px rgba(15,23,42,.08)}
  h1{margin:0 0 8px;font-size:21px}
  p{margin:0 0 14px;color:#4e5969;font-size:14px;line-height:1.65}
  .qr{text-align:center;margin:8px 0 14px}
  .qr img{width:220px;height:220px;border:1px solid #edf1f7;border-radius:12px}
  .hint{font-size:12px;color:#6b7785;margin:6px 0 0}
  button{width:100%;min-height:46px;border:0;border-radius:12px;font-size:16px;font-weight:700;margin-top:10px}
  button.primary{background:#0b6cf0;color:#fff}
  button.secondary{background:#e8f3ff;color:#0066d6}
  textarea{width:100%;min-height:120px;margin-top:10px;padding:12px;box-sizing:border-box;border:1px solid #d8dee8;border-radius:12px;font-size:15px;resize:vertical}
  #msg{min-height:22px;margin-top:12px;font-size:14px;line-height:1.5;color:#4e5969}
  .ok{color:#14804a}.bad{color:#c2410c}
  #success{display:none;text-align:center;padding:30px 10px}
  #success .c{font-size:64px;line-height:1;color:#16a34a;margin-bottom:12px}
</style></head><body><main><section>
  <div id="form">
    <h1>登录授权快传</h1>
    <p>1. 长按识别下方<b>微信授权二维码</b>，完成微信授权；<br>
       2. 跳转后，复制浏览器地址栏里包含 <b>code=</b> 的链接；<br>
       3. 回到本页粘贴并点「发送到电脑」。</p>
    <div class="qr"><img src="/wechat-auth-qrcode.png" alt="微信授权二维码"><p class="hint">长按图片识别；或截图后在微信「扫一扫」中识别</p></div>
    <button class="secondary" id="paste">点击粘贴链接</button>
    <textarea id="link" placeholder="在这里粘贴包含 code 的授权链接"></textarea>
    <button class="primary" id="send">发送到电脑</button>
    <div id="msg"></div>
  </div>
  <div id="success"><div class="c">✓</div><h1>登录成功</h1><p>电脑端已完成登录，可以关闭本页面了</p></div>
</section></main>
<script>
const token = ${tokenJson};
const $ = id => document.getElementById(id);
const link = $('link'), msg = $('msg'), send = $('send');
const setMsg = (t, k) => { msg.textContent = t; msg.className = k || ''; };
$('paste').addEventListener('click', async () => {
  try {
    if (!navigator.clipboard || !navigator.clipboard.readText) { setMsg('浏览器不支持直接读取剪贴板，请长按输入框手动粘贴', 'bad'); return; }
    link.value = await navigator.clipboard.readText();
    setMsg('已读取剪贴板，可以发送', 'ok');
  } catch (e) { setMsg('微信浏览器可能限制读取剪贴板，请长按输入框手动粘贴', 'bad'); }
});
send.addEventListener('click', async () => {
  const value = link.value.trim();
  if (!value) { setMsg('请先粘贴授权链接', 'bad'); return; }
  send.disabled = true;
  setMsg('正在发送到电脑...', '');
  try {
    const r = await fetch('/api/relay/submit?token=' + encodeURIComponent(token), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ link: value }),
    });
    const j = await r.json();
    if (j.ok) { $('form').style.display = 'none'; $('success').style.display = 'block'; }
    else { setMsg(j.message || '提交失败', 'bad'); send.disabled = false; }
  } catch (e) { setMsg('提交失败：' + e.message, 'bad'); send.disabled = false; }
});
</script></body></html>`;
}
function readBody(req) {
  return new Promise((resolve) => {
    let buf = '';
    req.on('data', c => { buf += c; if (buf.length > 1024 * 1024) req.destroy(); });
    req.on('end', () => { try { resolve(buf ? JSON.parse(buf) : {}); } catch (e) { resolve({}); } });
    req.on('error', () => resolve({}));
  });
}
function serveStatic(res, pathname) {
  let rel = pathname === '/' ? '/index.html' : pathname;
  const full = path.join(PUBLIC_DIR, path.normalize(rel).replace(/^(\.\.[/\\])+/, ''));
  if (!full.startsWith(PUBLIC_DIR) || !fs.existsSync(full) || !fs.statSync(full).isFile()) {
    res.writeHead(404); res.end('Not found'); return;
  }
  const ext = path.extname(full).toLowerCase();
  const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8' };
  res.writeHead(200, { 'Content-Type': types[ext] || 'application/octet-stream' });
  fs.createReadStream(full).pipe(res);
}

async function apiStatus() {
  const s = session.loadSession();
  let account = null, loggedIn = false, expireText = null;
  if (s) {
    loggedIn = true;
    account = { userId: s.userId, nick: s.nick, school: s.school };
    if (s.expireAt) {
      const left = s.expireAt * 1000 - Date.now();
      expireText = left > 0 ? `${Math.floor(left / 60000)} 分钟` : '已过期';
      if (left <= 0) loggedIn = false;
    }
    // 注意：这里不自动查询平台。数据只在登录成功、手动「刷新数据」、抢座成功后
    // 通过真实浏览器打开官方网页获取（见 refreshData）。
  }
  return {
    loggedIn, account,
    reserve: dataCache ? dataCache.reserve : null,
    libs: dataCache ? dataCache.libs : [],
    dataFetchedAt: dataCache ? dataCache.fetchedAt : null,
    expireText,
    grabber: grabber ? grabber.getStatus() : { running: false, round: 0, lastState: null, lastSuccess: lastSuccess },
    lanUrls: lanUrls(),
    tunnel: tunnel.getTunnelStatus(),
    success: lastSuccess,
    serverTime: Date.now(),
  };
}

async function handleApi(req, res, pathname) {
  try {
    if (pathname === '/api/status' && req.method === 'GET') return sendJson(res, 200, await apiStatus());
    if (pathname === '/api/login' && req.method === 'POST') {
      const b = await readBody(req);
      pushLog('正在用微信授权链接登录...');
      const s = await session.loginWithText(b.input || '');
      pushLog(`登录成功：${s.nick}（${s.school || '未知学校'}）`);
      await refreshData().catch(() => { });
      return sendJson(res, 200, { ok: true, account: { userId: s.userId, nick: s.nick, school: s.school } });
    }
    if (pathname === '/api/logout' && req.method === 'POST') {
      session.clearSession();
      pushLog('已退出登录');
      return sendJson(res, 200, { ok: true });
    }
    if (pathname === '/api/start' && req.method === 'POST') {
      const b = await readBody(req);
      if (grabber && grabber.running) return sendJson(res, 200, { ok: false, message: '任务已在运行' });
      const s = session.loadSession();
      if (!s) return sendJson(res, 200, { ok: false, message: '请先登录' });
      const libId = Number(b.libId || 0);
      if (!libId) return sendJson(res, 200, { ok: false, message: '请先选择场馆（登录后会自动加载你所在学校的场馆）' });
      grabber = new SeatGrabber({
        libId,
        minSec: Number(b.minSec || 1),
        maxSec: Number(b.maxSec || 1.5),
        mode: b.mode || 'live',
        minutes: Number(b.minutes || 60),
      });
      grabber.on('log', (m) => pushLog(m));
      grabber.on('success', (info) => {
        lastSuccess = info;
        pushLog('🎉 抢座成功：' + (info.seat || '') + '，请尽快到馆签到！', 'success');
        refreshData().catch(() => { });
      });
      grabber.on('stopped', () => pushLog('任务已停止'));
      grabber.start().catch(e => pushLog('任务异常：' + e.message, 'error'));
      pushLog('启动抢座任务');
      return sendJson(res, 200, { ok: true });
    }
    if (pathname === '/api/stop' && req.method === 'POST') {
      if (grabber) grabber.stop();
      return sendJson(res, 200, { ok: true });
    }
    if (pathname === '/api/cancel' && req.method === 'POST') {
      const s = session.loadSession();
      if (!s) return sendJson(res, 200, { ok: false, message: '请先登录' });
      pushLog('正在通过网页点击取消预约...');
      const result = await cancelReservation({ sessionFile: path.join(__dirname, 'data', 'session.json') });
      pushLog((result.ok ? '✅ ' : '⚠️ ') + result.message);
      await refreshData().catch(() => { });
      return sendJson(res, 200, result);
    }
    if (pathname === '/api/tunnel/start' && req.method === 'POST') {
      pushLog('正在开启公网访问（Cloudflare Tunnel）...');
      try {
        const { url } = await tunnel.startTunnel(PORT);
        pushLog('✅ 公网地址已就绪：' + url);
        return sendJson(res, 200, { ok: true, url });
      } catch (e) {
        pushLog('⚠️ 公网访问开启失败：' + e.message);
        return sendJson(res, 200, { ok: false, message: e.message });
      }
    }
    if (pathname === '/api/tunnel/stop' && req.method === 'POST') {
      tunnel.stopTunnel();
      pushLog('已关闭公网访问');
      return sendJson(res, 200, { ok: true });
    }
    if (pathname === '/api/qr-login/start' && req.method === 'GET') {
      const data = await qrLoginStart();
      pushLog('已生成扫码登录二维码，等待手机扫码...');
      return sendJson(res, 200, { ok: true, ...data });
    }
    if (pathname === '/api/qr-login/status' && req.method === 'GET') {
      const u = new URL(req.url, 'http://x');
      const token = u.searchParams.get('token') || '';
      if (!relayState || relayState.token !== token) return sendJson(res, 200, { ok: false, status: 'expired', message: '二维码已过期，请重新生成' });
      return sendJson(res, 200, { ok: true, status: relayState.status, message: relayState.message || '' });
    }
    if (pathname === '/api/relay/submit' && req.method === 'POST') {
      const u = new URL(req.url, 'http://x');
      const token = u.searchParams.get('token') || '';
      if (!relayState || relayState.token !== token) return sendJson(res, 200, { ok: false, message: '二维码已过期，请刷新电脑页面重新生成' });
      const b = await readBody(req);
      const link = (b.link || '').trim();
      if (!link) return sendJson(res, 200, { ok: false, message: '链接为空' });
      try {
        pushLog('手机端提交了授权链接，正在登录...');
        const s = await session.loginWithText(link);
        relayState.status = 'ok';
        relayState.message = '登录成功';
        pushLog(`扫码登录成功：${s.nick}`);
        await refreshData().catch(() => { });
        return sendJson(res, 200, { ok: true });
      } catch (e) {
        relayState.status = 'err';
        relayState.message = e.message;
        pushLog('扫码登录失败：' + e.message);
        return sendJson(res, 200, { ok: false, message: e.message });
      }
    }
    if (pathname === '/api/logs' && req.method === 'GET') {
      const u = new URL(req.url, 'http://x');
      const since = Number(u.searchParams.get('since') || 0);
      return sendJson(res, 200, { logs: logs.filter(l => l.seq > since) });
    }
    if (pathname === '/api/refresh' && req.method === 'POST') {
      const s = session.loadSession();
      if (!s) return sendJson(res, 200, { ok: false, message: '请先登录' });
      await refreshData();
      return sendJson(res, 200, { ok: true, data: dataCache });
    }
    sendJson(res, 404, { ok: false, message: 'not found' });
  } catch (e) {
    sendJson(res, 500, { ok: false, message: e.message });
  }
}

process.on('exit', () => { try { tunnel.stopTunnel(); } catch (e) { } });
process.on('SIGINT', () => { try { tunnel.stopTunnel(); } catch (e) { } process.exit(0); });

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://127.0.0.1');
  if (u.pathname === '/relay') {
    const token = u.searchParams.get('token') || '';
    if (!relayState || relayState.token !== token) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end('<!doctype html><meta charset=utf-8><title>已过期</title><body style="font-family:sans-serif;padding:40px;text-align:center"><h2>二维码已过期</h2><p>请回到电脑页面重新生成登录二维码</p></body>');
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(renderRelayPage(token));
    return;
  }
  if (u.pathname.startsWith('/api/')) return handleApi(req, res, u.pathname);
  return serveStatic(res, u.pathname);
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.log(`端口 ${PORT} 已被占用：程序可能已经在运行。`);
    console.log(`请直接用浏览器打开：http://127.0.0.1:${PORT}`);
    try { exec(`start "" "http://127.0.0.1:${PORT}"`); } catch (e) { }
    process.exit(0);
  }
  console.error('服务启动失败:', err.message);
  process.exit(1);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`座位助手已启动：http://127.0.0.1:${PORT}`);
  const urls = lanUrls();
  if (urls.length) console.log('手机（同一个 WiFi）访问：' + urls.join('  '));
  pushLog('服务已启动');
  try { exec(`start "" "http://127.0.0.1:${PORT}"`); } catch (e) { }
});
