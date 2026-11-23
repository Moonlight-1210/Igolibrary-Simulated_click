/**
 * 登录态管理：
 * - 通过微信授权链接里的 code 换取 cookie（平台登录接口，登录必需）
 * - 保存/读取本地会话
 *
 * 注意：账号/预约/场馆等平台数据不由本模块直接查询，
 * 由 browser-data.js 通过真实浏览器打开官方网页获取。
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'data');
const SESSION_FILE = path.join(DATA_DIR, 'session.json');
const UA = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36 NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63070626)';

function ensureDir() { fs.mkdirSync(DATA_DIR, { recursive: true }); }

/** 从用户粘贴的文本/链接里提取微信授权 code */
function extractCode(text) {
  if (!text) return null;
  const m = String(text).match(/[?&]code=([A-Za-z0-9_-]+)/);
  if (m) return m[1];
  const m2 = String(text).trim().match(/^[A-Za-z0-9_-]{20,}$/);
  return m2 ? m2[0] : null;
}

function decodeJwt(token) {
  try {
    const payload = token.split('.')[1];
    const json = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
    return JSON.parse(json);
  } catch (e) { return null; }
}

/** 用授权 code 换 cookie（登录接口，登录必需） */
async function exchangeCode(code) {
  const target = 'https://web.traceint.com/web/index.html';
  const url = `http://wechat.v2.traceint.com/index.php/urlNew/auth.html?r=${encodeURIComponent(target)}&code=${encodeURIComponent(code)}&state=1`;
  const res = await fetch(url, { headers: { 'User-Agent': UA }, redirect: 'manual' });
  const rawCookies = typeof res.headers.getSetCookie === 'function' ? res.headers.getSetCookie() : [];
  let auth = null, serverId = null;
  for (const c of rawCookies) {
    const m = c.match(/^([^=]+)=([^;]*)/);
    if (!m) continue;
    if (m[1] === 'Authorization') auth = m[2];
    if (m[1] === 'SERVERID') serverId = m[2];
  }
  if (!auth) {
    const text = await res.text().catch(() => '');
    throw new Error('登录失败：没有拿到 cookie（code 可能已过期，请重新从微信复制授权链接）' + (text ? '：' + text.slice(0, 100) : ''));
  }
  return { cookie: `Authorization=${auth}; SERVERID=${serverId || ''}`, auth, serverId };
}

/**
 * 登录：从粘贴的链接/文本里取 code，换取 cookie 并保存。
 * 账号昵称等信息由服务器随后用真实浏览器打开官方网页获取（browser-data.js）。
 */
async function loginWithText(text) {
  const code = extractCode(text);
  if (!code) throw new Error('没有找到 code：请粘贴微信里「我去图书馆」的完整授权链接');
  const { cookie } = await exchangeCode(code);
  const jwt = decodeJwt(cookie.split('Authorization=')[1].split(';')[0]);
  const session = {
    cookie,
    userId: jwt && jwt.userId ? jwt.userId : null,
    nick: '',
    school: '',
    expireAt: jwt && jwt.expireAt ? jwt.expireAt : null,
    savedAt: new Date().toISOString(),
  };
  ensureDir();
  fs.writeFileSync(SESSION_FILE, JSON.stringify(session, null, 2), 'utf8');
  return session;
}

function loadSession() {
  try { return JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8')); } catch (e) { return null; }
}
function saveSession(session) { ensureDir(); fs.writeFileSync(SESSION_FILE, JSON.stringify(session, null, 2), 'utf8'); }
function clearSession() { try { fs.unlinkSync(SESSION_FILE); } catch (e) { } }

module.exports = { extractCode, loginWithText, loadSession, saveSession, clearSession, decodeJwt, UA };
