/**
 * 登录态管理：
 * - 通过微信授权链接里的 code 换取 cookie（平台 auth.html 接口）
 * - 保存/读取本地会话
 * - 用 GraphQL 查询账号信息与当前预约
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'data');
const SESSION_FILE = path.join(DATA_DIR, 'session.json');
const ENDPOINT = 'https://wechat.v2.traceint.com/index.php/graphql/';
const UA = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36 NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63070626)';
const APP_VERSION = '2.2.9';

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

/** 用 code 换 cookie（返回 cookie 字符串） */
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

async function graphql(cookie, operationName, query, variables) {
  const body = JSON.stringify({ operationName, query, variables: variables || {} });
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'User-Agent': UA, 'Content-Type': 'application/json',
      'Origin': 'https://web.traceint.com', 'Referer': 'https://web.traceint.com/web/',
      'App-Version': APP_VERSION, 'Accept': '*/*', 'Cookie': cookie,
    },
    body,
  });
  const text = await res.text();
  try { return JSON.parse(text); } catch (e) { throw new Error('接口返回异常: ' + text.slice(0, 200)); }
}

const QUERY_ACCOUNT = 'query account { userAuth { currentUser { user_id user_nick user_deny { deny_deadline } sch { sch_id sch_name } } } }';
const QUERY_RESERVE = 'query reserve { userAuth { reserve { reserve { token status user_id seat_name lib_name date exp_date } } } }';
const QUERY_LIBS = 'query list { userAuth { reserve { libs(libType: -1) { lib_id lib_name is_open lib_rt { seats_total seats_used seats_booking seats_has } } } } }';

/** 验证 cookie 并取账号信息 */
async function fetchAccount(cookie) {
  const j = await graphql(cookie, 'account', QUERY_ACCOUNT);
  if (j.errors) throw new Error(j.errors[0].msg || '查询账号失败');
  const u = j.data && j.data.userAuth && j.data.userAuth.currentUser;
  if (!u) throw new Error('未登录或登录态已失效');
  return { userId: u.user_id, nick: u.user_nick, school: u.sch ? u.sch.sch_name : '', deny: u.user_deny ? u.user_deny.deny_deadline : null };
}

/** 查询当前预约 */
async function fetchReserve(cookie) {
  const j = await graphql(cookie, 'reserve', QUERY_RESERVE);
  if (j.errors) return null;
  const r = j.data && j.data.userAuth && j.data.userAuth.reserve && j.data.userAuth.reserve.reserve;
  if (!r) return null;
  return {
    seat: r.seat_name, lib: r.lib_name, status: r.status,
    date: r.date, expDate: r.exp_date,
  };
}

/** 查询场馆 */
async function fetchLibs(cookie) {
  const j = await graphql(cookie, 'list', QUERY_LIBS);
  if (j.errors) return [];
  const libs = (j.data && j.data.userAuth && j.data.userAuth.reserve && j.data.userAuth.reserve.libs) || [];
  return libs.map(l => ({ libId: l.lib_id, name: l.lib_name, open: l.is_open, total: l.lib_rt && l.lib_rt.seats_total, used: l.lib_rt && l.lib_rt.seats_used, booking: l.lib_rt && l.lib_rt.seats_booking, has: l.lib_rt && l.lib_rt.seats_has }));
}

/** 登录：从粘贴的链接/文本里取 code，换 cookie 并验证，保存会话 */
async function loginWithText(text) {
  const code = extractCode(text);
  if (!code) throw new Error('没有找到 code：请粘贴微信里「我去图书馆」的完整授权链接');
  const { cookie } = await exchangeCode(code);
  const jwt = decodeJwt(cookie.split('Authorization=')[1].split(';')[0]);
  const account = await fetchAccount(cookie);
  const session = {
    cookie,
    userId: account.userId,
    nick: account.nick,
    school: account.school,
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

module.exports = { extractCode, loginWithText, loadSession, saveSession, clearSession, fetchAccount, fetchReserve, fetchLibs, decodeJwt, UA, APP_VERSION };
