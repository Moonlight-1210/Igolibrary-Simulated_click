# v2：PC 微信内置浏览器后台抢座（GUI + UIA + PrintWindow）

> 在电脑版微信内置浏览器独立小窗内运行，零鼠标占用、不抢焦点、可被其他窗口完全遮挡。
> 版本对比、完整操作说明及风险提示请一并查看仓库根目录 [README.md](../README.md)。

## 环境准备

- Windows 10 / 11
- 电脑版微信 4.x（`Weixin.exe`）
- Python 3.10+（自带 Tkinter）

安装依赖（仅需 `Pillow` 与 `numpy`）：

```bash
pip install -r requirements.txt
```

## 快速上手（4 步）

1. 打开并登录 **电脑版微信**。
2. 在微信内打开「我去图书馆」链接 → 点「去选座」→ 进入目标场馆的 **座位图页面**（能看到座位图和底部「确认选座」）。
3. 点击微信浏览器右上角的 **`[↗]`** 弹出为独立小窗：
   - 弹出后可拖到屏幕角落或压在其他窗口下面；
   - **切勿最小化到任务栏**（最小化会暂停窗口画面渲染），也不要让电脑锁屏或休眠。
4. 双击 **`start_gui.bat`** 打开桌面图形界面（或使用命令行启动脚本）：
   - 确认顶部显示 `已连接 · XXX座`；
   - 选择 **仅监控提醒 (Monitor)** 或 **自动抢座 (Live)**，点 **「▶ 开始运行」**。

## 首次安全测试（推荐）

首次使用可先双击 **`run_test_click.bat`**：仅测试 3 次「刷新」按钮，绝不点击任何座位或「确认选座」，用于确认后台截图（`PrintWindow`）、UIA 读取和零鼠标消息点击在你的系统上正常生效。

## 常用参数与热键

- **全局热键**：运行中随时按 **`F9` 暂停**、**`F10` 继续**。
- **常用命令行参数**（`wechat_grab_v2.py`）：
  - `--mode monitor|live`：仅监控提醒 / 自动抢座
  - `--interval-min 0.8 --interval-max 1.5`：自定义随机刷新间隔（秒）
  - `--fast`：冲刺模式（`0.4~0.8s` 随机间隔，建议仅高峰短时使用）
  - `--safe`：稳健模式（`2.0~3.5s` 随机间隔）
  - `--prefer 001,002`：优先抢指定座位号（留空则从空座中随机选择）
  - `--minutes 120`：运行时长（`0` 表示不限时）
  - `--no-beep`：关闭提示音

## 目录文件说明

- `wechat_grab_gui.py`：Tkinter 桌面图形界面（实时预览、空座标注、日志与热键控制）
- `wechat_grab_v2.py`：后台抢座主程序（CLI 模式）
- `win_backend.py`：Win32 窗口定位、`PrintWindow` 后台截图、消息点击与座位状态识别
- `uia_worker.ps1`：常驻 Windows UIA 助手（读取页面 DOM、座位号与弹窗文案）
- `test_bg_click.py`：5 秒无害刷新点击验证脚本
- `start_gui.bat` / `start_monitor.bat` / `start_live.bat` / `start_fast.bat` / `run_test_click.bat`：一键启动脚本
