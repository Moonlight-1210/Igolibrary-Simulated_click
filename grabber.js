/**
 * 网页点击抢座核心（真实 Chrome + 官方网页 + 真实点击）
 * - 浏览器使用原生 UA（不伪造），并做基础反自动化特征处理
 * - 流程模拟真人：首页 → 选座页 → 座位图 → 循环刷新
 * 事件：log / status / success / stopped
 */
const { EventEmitter } = require('events');
const path = require('path');
const fs = require('fs');
const { launchBrowser, newStealthContext } = require('./browser-utils');

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const rand = (a, b) => a + Math.random() * (b - a);

class SeatGrabber extends EventEmitter {
  constructor(opts) {
    super();
    this.opts = Object.assign({
      libId: 0, minSec: 1, maxSec: 1.5, mode: 'live', minutes: 60,
      sessionFile: path.join(__dirname, 'data', 'session.json'),
    }, opts || {});
    this.running = false;
    this.stopRequested = false;
    this.browser = null;
    this.round = 0;
    this.lastState = null; // {seats_total, used, booking, leave, available}
    this.lastSuccess = null;
  }

  log(msg) { this.emit('log', msg); }

  async start() {
    if (this.running) return { ok: false, message: '任务已在运行' };
    this.running = true;
    this.stopRequested = false;
    this.round = 0;
    let session;
    try { session = JSON.parse(fs.readFileSync(this.opts.sessionFile, 'utf8')); } catch (e) { throw new Error('没有登录态，请先登录'); }
    const cookies = session.cookie.split('; ').map(s => { const i = s.indexOf('='); return { name: s.slice(0, i), value: s.slice(i + 1), domain: '.traceint.com', path: '/', secure: true }; });

    this.browser = await launchBrowser();
    const context = await newStealthContext(this.browser);
    await context.addCookies(cookies);
    this.context = context;
    const page = await context.newPage();
    this.page = page;

    let lastLayout = null, stopFlag = false, currentSeat = '';
    const seatCooldown = new Map();
    const seatMapUrl = `https://web.traceint.com/web/index.html#/pages/reserve/seatMap?libId=${this.opts.libId}&recordUser=null`;

    page.on('response', async (resp) => {
      try {
        if (!/index\.php\/graphql/.test(resp.url())) return;
        const post = resp.request().postData() || '';
        if (post.includes('libLayout')) {
          const j = await resp.json();
          const errMsg = j && j.errors && j.errors[0] ? j.errors[0].msg : '';
          if (/访问行为异常|请求已拦截/.test(errMsg)) { this.log('⚠️ 查询被风控拦截：' + errMsg); stopFlag = true; return; }
          const layout = j && j.data && j.data.userAuth && j.data.userAuth.reserve && j.data.userAuth.reserve.libs && j.data.userAuth.reserve.libs[0] && j.data.userAuth.reserve.libs[0].lib_layout;
          lastLayout = layout || null;
        }
        if (post.includes('reserueSeat')) {
          const text = await resp.text();
          try {
            const j = JSON.parse(text);
            if (j.errors && j.errors.length) {
              const msg = j.errors[0].msg || '';
              if (/访问行为异常|请求已拦截/.test(msg)) { this.log('⚠️ 预约被风控拦截，任务自动停止：' + msg); stopFlag = true; }
              else this.log('预约未成功：' + msg);
              if (currentSeat) seatCooldown.set(currentSeat, Date.now() + 300000);
            } else if (j.data && j.data.userAuth && j.data.userAuth.reserve && j.data.userAuth.reserve.reserueSeat === true) {
              this.log('✅ 预约成功！');
              stopFlag = true;
              this.lastSuccess = { seat: currentSeat, libId: this.opts.libId, at: Date.now() };
              this.emit('success', this.lastSuccess);
            }
          } catch (e) { }
        }
      } catch (e) { }
    });

    // 模拟真人进入流程：打开首页 → 点「去选座」→ 进入座位图
    await page.goto('https://web.traceint.com/web/index.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(rand(2000, 4000));
    let entered = false;
    try {
      const go = page.getByText('去选座', { exact: true }).first();
      if (await go.count()) { await go.click({ timeout: 8000 }); entered = true; }
    } catch (e) { }
    if (!entered) {
      try {
        const tab = page.getByText('选座', { exact: true }).first();
        if (await tab.count()) { await tab.click({ timeout: 8000 }); entered = true; }
      } catch (e) { }
    }
    await page.waitForTimeout(rand(1500, 3000));
    await page.goto(seatMapUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => { });
    await page.waitForTimeout(rand(3000, 5000));
    this.log(`已进入座位图（流程：首页 → 选座 → 座位图；场馆 ${this.opts.libId}，模式 ${this.opts.mode}，间隔 ${this.opts.minSec}~${this.opts.maxSec} 秒）`);
    this.emit('status', { running: true });

    const startedAt = Date.now();
    while (!this.stopRequested && Date.now() - startedAt < this.opts.minutes * 60 * 1000 && !stopFlag) {
      this.round++;
      const before = lastLayout;
      try {
        await page.getByText('刷新', { exact: true }).first().click({ timeout: 8000 });
      } catch (e) {
        this.log('点击刷新失败，重新加载页面');
        await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => { });
        await sleep(2000);
        continue;
      }
      const waitStart = Date.now();
      while (lastLayout === before && Date.now() - waitStart < 10000 && !stopFlag) await sleep(200);
      if (stopFlag) break;
      if (!lastLayout) { await sleep(rand(this.opts.minSec, this.opts.maxSec) * 1000); continue; }

      const seats = lastLayout.seats || [];
      const now = Date.now();
      const avail = seats.filter(s => s && s.type === 1 && (s.seat_status === 0 || s.seat_status === 1) && s.status === false)
        .filter(s => !((seatCooldown.get(s.name || s.key) || 0) > now));
      const used = seats.filter(s => s.type === 1 && s.seat_status === 3).length;
      const booking = seats.filter(s => s.type === 1 && s.seat_status === 2).length;
      const leave = seats.filter(s => s.type === 1 && s.seat_status === 4).length;
      this.lastState = { seats_total: lastLayout.seats_total, used, booking, leave, available: avail.length };

      if (this.round % 5 === 1 || avail.length > 0) {
        this.log(`第 ${this.round} 轮：总座 ${lastLayout.seats_total} | 有人 ${used} | 已约 ${booking} | 暂离 ${leave} | 可选 ${avail.length}`);
      }
      if (avail.length > 0 && this.opts.mode === 'live') {
        const idx = seats.indexOf(avail[0]);
        const targetName = avail[0].name || avail[0].key;
        currentSeat = targetName;
        this.log(`🎯 发现空座 ${targetName}，立即点击预约`);
        await sleep(rand(20, 80));
        const seatEl = page.locator(`.seatClass:has(img[seatid="${idx}"])`).first();
        await seatEl.scrollIntoViewIfNeeded().catch(() => { });
        await seatEl.click({ timeout: 5000, force: true }).catch(e => this.log('点击座位异常: ' + e.message.split('\n')[0]));
        await sleep(rand(80, 200));
        await sleep(rand(50, 150));
        try {
          await page.getByText('确认选座', { exact: true }).first().click({ timeout: 8000 });
          this.log('已点击「确认选座」，等待结果');
        } catch (e) { this.log('点击确认失败: ' + e.message.split('\n')[0]); }
        await sleep(2500);
        if (!stopFlag) {
          await page.goto(seatMapUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => { });
          await sleep(800);
        }
      }

      await sleep(rand(this.opts.minSec, this.opts.maxSec) * 1000);
    }

    this.running = false;
    this.log(`任务结束：共 ${this.round} 轮${stopFlag ? '（已停止）' : ''}`);
    this.emit('stopped', { round: this.round, success: this.lastSuccess });
    await this.cleanup();
    return { ok: true };
  }

  stop() {
    this.stopRequested = true;
    this.log('收到停止指令');
  }

  async cleanup() {
    try { if (this.context) await this.context.close(); } catch (e) { }
    try { if (this.browser) await this.browser.close(); } catch (e) { }
    this.browser = null; this.context = null; this.page = null;
  }

  getStatus() {
    return { running: this.running, round: this.round, lastState: this.lastState, lastSuccess: this.lastSuccess, opts: this.opts };
  }
}

module.exports = { SeatGrabber };
