/**
 * 浏览器公共工具：启动真实 Chrome（不伪造 UA）+ 去自动化特征注入
 */
const { chromium } = require('playwright-core');

/** 启动本机真实 Chrome（使用浏览器原生 UA，不覆盖） */
async function launchBrowser() {
  return chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--no-proxy-server', '--ignore-certificate-errors', '--disable-blink-features=AutomationControlled'],
  });
}

/** 注入常见反自动化检测规避（webdriver/plugins/languages/WebGL 等） */
async function applyStealth(context) {
  await context.addInitScript(() => {
    try {
      Object.defineProperty(navigator, 'webdriver', { get: () => false });
      window.chrome = window.chrome || { runtime: {} };
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
      const origGetParameter = WebGLRenderingContext.prototype.getParameter;
      WebGLRenderingContext.prototype.getParameter = function (p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return origGetParameter.call(this, p);
      };
    } catch (e) { }
  });
}

/** 创建带 stealth 的浏览器上下文（不设置 userAgent：使用真实 Chrome UA） */
async function newStealthContext(browser) {
  const context = await browser.newContext({
    locale: 'zh-CN',
    ignoreHTTPSErrors: true,
    viewport: { width: 430, height: 932 },
    deviceScaleFactor: 2,
  });
  await applyStealth(context);
  return context;
}

module.exports = { launchBrowser, applyStealth, newStealthContext };
