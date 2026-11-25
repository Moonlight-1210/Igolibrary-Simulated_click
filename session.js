/**
 * 登录态管理：
 * - 通过微信授权链接里的 code 换取 cookie（登录在真实浏览器内完成）
 * - 保存/读取本地会话
 *
 * 注意：账号/预约/场馆等平台数据由 browser-data.js 通过真实浏览器打开官方网页获取。
 */
const fs = require('fs');
const path = require('path');
const { exchangeCodeViaBrowser } = require('./browser-login');

const DATA_DIR = path.join(__dirname, 'data');
const SESSION_FILE = path.join(DATA_DIR, 'session.json');

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

/** 登录：从粘贴的链接/文本里取 code，在真实浏览器中换取 cookie 并保存 */
async function loginWithText(text) {
  const code = extractCode(text);
  if (!code) throw new Error('没有找到 code：请粘贴微信里「我去图书馆」的完整授权链接');
  const { cookie } = await exchangeCodeViaBrowser(code);
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

module.exports = { extractCode, loginWithText, loadSession, saveSession, clearSession, decodeJwt };
