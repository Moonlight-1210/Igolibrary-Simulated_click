/**
 * 浏览器数据获取：
 * 用真实 Chrome 打开官方网页，由页面自己发出请求，程序只监听并解析页面响应。
 * 使用浏览器原生 UA（不伪造），并做基础反自动化特征处理。
 */
const fs = require('fs');
const { launchBrowser, newStealthContext } = require('./browser-utils');

const WEB = 'https://web.traceint.com/web/index.html';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function normalizeLibs(libs) {
  return (libs || []).map(l => ({
    libId: l.lib_id,
    name: l.lib_name,
    open: l.is_open,
    total: l.lib_rt ? l.lib_rt.seats_total : undefined,
    used: l.lib_rt ? l.lib_rt.seats_used : undefined,
    booking: l.lib_rt ? l.lib_rt.seats_booking : undefined,
    has: l.lib_rt ? l.lib_rt.seats_has : undefined,
  }));
}

function normalizeReserve(r) {
  if (!r) return null;
  return {
    seat: r.seat_name,
    lib: r.lib_name,
    status: r.status,
    date: r.date,
    expDate: r.exp_date,
  };
}

/**
 * 打开官方网页（注入登录态），从页面自身请求的响应里取出账号/预约/场馆数据
 * @returns {Promise<{account:object, reserve:object|null, libs:Array}>}
 */
async function fetchDataViaBrowser(sessionFile) {
  const session = JSON.parse(fs.readFileSync(sessionFile, 'utf8'));
  const cookies = session.cookie.split('; ').map(s => {
    const i = s.indexOf('=');
    return { name: s.slice(0, i), value: s.slice(i + 1), domain: '.traceint.com', path: '/', secure: true };
  });

  const browser = await launchBrowser();
  try {
    const context = await newStealthContext(browser);
    await context.addCookies(cookies);
    const page = await context.newPage();

    const collected = { account: null, reserve: null, libs: null };
    page.on('response', async (resp) => {
      try {
        if (!/index\.php\/graphql/.test(resp.url())) return;
        const post = resp.request().postData() || '';
        const j = await resp.json();
        const ua = j && j.data && j.data.userAuth;
        if (!ua) return;

        if (post.includes('"operationName":"index"') || ua.currentUser) {
          if (ua.currentUser && !collected.account) {
            collected.account = {
              userId: ua.currentUser.user_id,
              nick: ua.currentUser.user_nick,
              school: ua.currentUser.sch ? ua.currentUser.sch.sch_name : '',
              deny: ua.currentUser.user_deny ? ua.currentUser.user_deny.deny_deadline : null,
            };
          }
          if (ua.reserve && Object.prototype.hasOwnProperty.call(ua.reserve, 'reserve') && collected.reserve === null) {
            collected.reserve = normalizeReserve(ua.reserve.reserve);
          }
        }
        if (post.includes('"operationName":"list"') && ua.reserve && ua.reserve.libs && !collected.libs) {
          collected.libs = normalizeLibs(ua.reserve.libs);
        }
      } catch (e) { }
    });

    await page.goto(WEB, { waitUntil: 'domcontentloaded', timeout: 30000 });
    let waited = 0;
    while (!collected.account && waited < 20000) { await sleep(300); waited += 300; }

    if (!collected.account) {
      try {
        const mine = page.getByText('我的', { exact: true }).first();
        if (await mine.count()) await mine.click({ timeout: 5000 });
      } catch (e) { }
      waited = 0;
      while (!collected.account && waited < 10000) { await sleep(300); waited += 300; }
    }

    if (!collected.libs) {
      try {
        const go = page.getByText('去选座', { exact: true }).first();
        if (await go.count()) {
          await go.click({ timeout: 5000 });
        } else {
          const tab = page.getByText('选座', { exact: true }).first();
          if (await tab.count()) await tab.click({ timeout: 5000 });
        }
      } catch (e) { }
      waited = 0;
      while (!collected.libs && waited < 15000) { await sleep(300); waited += 300; }
    }

    if (!collected.account) {
      throw new Error('无法从官方网页获取账号数据（登录态可能已过期，请重新登录）');
    }
    return {
      account: collected.account,
      reserve: collected.reserve,
      libs: collected.libs || [],
    };
  } finally {
    await browser.close().catch(() => { });
  }
}

module.exports = { fetchDataViaBrowser };
