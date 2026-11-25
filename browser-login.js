/**
 * 浏览器内登录：用真实 Chrome 打开微信授权接口完成换 cookie
 * （登录与后续使用处于同一浏览器环境，避免 Node fetch 与浏览器环境不一致）
 */
const { launchBrowser, newStealthContext } = require('./browser-utils');

async function exchangeCodeViaBrowser(code) {
  const target = 'https://web.traceint.com/web/index.html';
  const url = `http://wechat.v2.traceint.com/index.php/urlNew/auth.html?r=${encodeURIComponent(target)}&code=${encodeURIComponent(code)}&state=1`;
  const browser = await launchBrowser();
  try {
    const context = await newStealthContext(browser);
    const page = await context.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => { });
    await page.waitForTimeout(2500);
    const cookies = await context.cookies();
    const auth = cookies.find(c => c.name === 'Authorization');
    const serverId = cookies.find(c => c.name === 'SERVERID');
    if (!auth) {
      throw new Error('登录失败：浏览器没有拿到 cookie（code 可能已过期，请重新从微信复制授权链接）');
    }
    return { cookie: `Authorization=${auth.value}; SERVERID=${serverId ? serverId.value : ''}` };
  } finally {
    await browser.close().catch(() => { });
  }
}

module.exports = { exchangeCodeViaBrowser };
