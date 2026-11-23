/**
 * 浏览器数据获取：
 * 用真实 Chrome 打开官方网页，由页面自己发出请求，程序只监听并解析页面响应。
 * （不直接调用平台接口；平台看到的是官方网页正常加载）
 */
const { chromium } = require('playwright-core');
const fs = require('fs');

const UA = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36 NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63070626)';
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

  const browser = await chromium.launch({
    channel: 'chrome', headless: true,
    args: ['--no-proxy-server', '--ignore-certificate-errors', '--disable-blink-features=AutomationControlled'],
  });
  try {
    const context = await browser.newContext({
      userAgent: UA, locale: 'zh-CN', ignoreHTTPSErrors: true,
      viewport: { width: 430, height: 932 }, deviceScaleFactor: 2,
    });
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

        // index 查询：账号信息 + 当前预约
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
        // list 查询：场馆列表
        if (post.includes('"operationName":"list"') && ua.reserve && ua.reserve.libs && !collected.libs) {
          collected.libs = normalizeLibs(ua.reserve.libs);
        }
      } catch (e) { }
    });

    await page.goto(WEB, { waitUntil: 'domcontentloaded', timeout: 30000 });

    // 等待页面自动请求返回（最多 20 秒）
    let waited = 0;
    while (!collected.account && waited < 20000) { await sleep(300); waited += 300; }

    // 还没拿到账号？点击"我的"让页面重新请求
    if (!collected.account) {
      try {
        const mine = page.getByText('我的', { exact: true }).first();
        if (await mine.count()) await mine.click({ timeout: 5000 });
      } catch (e) { }
      waited = 0;
      while (!collected.account && waited < 10000) { await sleep(300); waited += 300; }
    }

    // 进入选座页，让页面请求场馆列表（list）
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
    await browser.close();
  }
}

module.exports = { fetchDataViaBrowser };
