/**
 * 纯点击取消预约：
 * 打开官方首页 → 找到当前预约卡片 → 点「取消预定」→ 弹窗点「确定」→ 验证
 * 使用浏览器原生 UA（不伪造）+ 反自动化特征处理
 */
const fs = require('fs');
const { launchBrowser, newStealthContext } = require('./browser-utils');

async function cancelReservation({ sessionFile }) {
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
    await page.goto('https://web.traceint.com/web/index.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(6000);
    let text = await page.evaluate(() => document.body.innerText);
    if (!/取消预定/.test(text)) {
      if (/您还没有选座哦|暂无预约/.test(text)) return { ok: true, message: '当前没有预约' };
      return { ok: false, message: '页面上没有找到「取消预定」按钮（可能没有预约或页面异常）' };
    }
    await page.getByText('取消预定', { exact: true }).first().click({ timeout: 8000 });
    await page.waitForTimeout(1500);
    let confirmed = false;
    try {
      const btn = page.locator('uni-modal').getByText('确定', { exact: true }).first();
      if (await btn.count()) { await btn.click({ timeout: 5000 }); confirmed = true; }
    } catch (e) { }
    if (!confirmed) {
      try { await page.locator('.uni-modal__btn_primary').first().click({ timeout: 5000 }); confirmed = true; } catch (e) { }
    }
    if (!confirmed) return { ok: false, message: '没有找到确认弹窗' };
    await page.waitForTimeout(3500);
    text = await page.evaluate(() => document.body.innerText);
    const done = /您还没有选座哦|暂无预约/.test(text) || !/取消预定/.test(text);
    return { ok: done, message: done ? '已取消预约' : '取消操作已执行，页面状态未确认，请刷新查看' };
  } finally {
    await browser.close().catch(() => { });
  }
}

module.exports = { cancelReservation };
