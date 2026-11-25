"""Intuitive desktop GUI for WeChat background seat grabber (v2).

Reuses win_backend.py and wechat_grab_v2.py helpers. Designed to launch cleanly
via pythonw.exe (start_gui.bat) with zero console window and zero mouse capture.
"""
import os
import queue
import random
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from PIL import Image, ImageDraw, ImageTk

if getattr(sys, 'frozen', False):
    _HERE = os.path.dirname(sys.executable)
else:
    _HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Import win_backend before Tk() so SetProcessDPIAware() activates first.
import win_backend as wb
import wechat_grab_v2 as v2

BASE_DIR = wb.BASE_DIR

if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CONFIG_PATH = os.path.join(BASE_DIR, 'gui_config.json')
ERROR_LOG_PATH = os.path.join(BASE_DIR, 'gui_error.log')

DEFAULT_CONFIG = {
    'mode': 'monitor',
    'interval_min': 0.8,
    'interval_max': 1.5,
    'prefer': '',
    'minutes': 120.0,
    'click_method': 'render_doc',
    'beep': True,
    'preview': True,
    'topmost': False,
}

# Light-Slate color palette
BG_APP = '#F3F5F9'
BG_CARD = '#FFFFFF'
BG_SUBTLE = '#F8FAFC'
BG_PREVIEW = '#E9EEF5'
BORDER_CARD = '#DCE2EC'
TEXT_PRIMARY = '#0F172A'
TEXT_SECONDARY = '#475569'
TEXT_MUTED = '#64748B'
ACCENT_BLUE = '#2563EB'
ACCENT_BLUE_HOVER = '#1D4ED8'
ACCENT_GREEN = '#16A34A'
ACCENT_AMBER = '#D97706'
ACCENT_RED = '#DC2626'
ACCENT_SLATE = '#64748B'
FONT_FAMILY = 'Microsoft YaHei UI'


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            import json
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
        except Exception:
            pass
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    try:
        import json
        out = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def render_preview_image(arr, seats_rel=None, empties=None, max_w=680, max_h=420):
    """Build a smoothly scaled PIL RGB image with optional empty-seat highlights."""
    img = Image.fromarray(arr, mode='RGB')
    if seats_rel and empties:
        empty_set = set(empties)
        draw = ImageDraw.Draw(img)
        for s in seats_rel:
            if s.get('id') in empty_set:
                cx, cy = int(s['cx']), int(s['cy'])
                draw.rectangle([cx - 12, cy - 12, cx + 12, cy + 12], outline='#2563EB', width=3)
                draw.rectangle([cx - 14, cy - 14, cx + 14, cy + 14], outline='#16A34A', width=2)

    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    scale = min(max(64, max_w) / float(w), max(64, max_h) / float(h), 1.0)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    return img


def append_file_log(line):
    try:
        with open(v2.LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


class GrabWorker:
    """Thread-safe background grab/monitor engine communicating via a queue."""

    def __init__(self, ui_queue):
        self.ui_queue = ui_queue
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._thread = None
        self.beep_enabled = True
        self.preview_enabled = True

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self):
        return self._pause_event.is_set()

    def set_paused(self, flag):
        if flag:
            self._pause_event.set()
        else:
            self._pause_event.clear()

    def start(self, cfg):
        if self.is_running:
            return
        self._stop_event.clear()
        self._pause_event.clear()
        self.beep_enabled = bool(cfg.get('beep', True))
        self.preview_enabled = bool(cfg.get('preview', True))
        self._thread = threading.Thread(target=self._run, args=(dict(cfg),), daemon=True)
        self._thread.start()

    def request_stop(self):
        self._stop_event.set()
        self._pause_event.clear()

    def stop(self, join_timeout=2.5):
        self.request_stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def _emit(self, *args):
        self.ui_queue.put(args)

    def _log(self, msg, tag='info'):
        line = time.strftime('%H:%M:%S ') + msg
        append_file_log(line)
        self._emit('log', line, tag)

    def _sleep(self, sec, respect_pause=True):
        end = time.time() + max(0.0, sec)
        while time.time() < end:
            if self._stop_event.is_set():
                return False
            if respect_pause and self._pause_event.is_set():
                return False
            time.sleep(min(0.05, max(0.0, end - time.time())))
        return not self._stop_event.is_set()

    def _run(self, cfg):
        mode = cfg['mode']
        imin = float(cfg['interval_min'])
        imax = float(cfg['interval_max'])
        prefer_list = [p.strip() for p in cfg.get('prefer', '').split(',') if p.strip()]
        minutes = float(cfg['minutes'])
        click_method = cfg['click_method']

        wins = wb.find_wechat_windows()
        browser_hwnd = wins['browser_hwnd']
        render_hwnd = wins['render_hwnd']
        browser_rect = wins['browser_rect']

        if not browser_hwnd or not render_hwnd:
            self._emit('conn', '未检测到微信浏览器小窗', 'error')
            self._log('[错误] 找不到微信浏览器窗口或渲染子窗口，请先打开网页并点右上角弹出独立小窗。', 'error')
            self._emit('finished', 'stopped', '未启动')
            return

        occluded = wb.is_window_occluded(browser_hwnd)
        self._log(
            f'[启动] mode={mode} cadence={imin:.2f}-{imax:.2f}s click={click_method} '
            f'prefer={prefer_list or "任意"} browser_hwnd={browser_hwnd} occluded={occluded}',
            'info',
        )
        self._emit('status', 'running', '自动抢座中' if mode == 'live' else '监控中')

        uia = None
        cache = None
        last_warn_ts = 0.0
        prev_empty_count = 0
        consecutive_fails = 0
        round_no = 0
        start_ts = time.time()
        finish_key = 'stopped'
        finish_text = '已停止'

        try:
            uia = wb.UiaClient()
            while not self._stop_event.is_set() and (minutes <= 0 or (time.time() - start_ts) < minutes * 60.0):
                if self._pause_event.is_set():
                    time.sleep(0.12)
                    continue

                if (
                    not browser_hwnd
                    or not wb.user32.IsWindow(browser_hwnd)
                    or not wb.user32.IsWindowVisible(browser_hwnd)
                    or not render_hwnd
                    or not wb.user32.IsWindow(render_hwnd)
                ):
                    wins = wb.find_wechat_windows()
                    browser_hwnd = wins['browser_hwnd']
                    render_hwnd = wins['render_hwnd']
                    browser_rect = wins['browser_rect']
                    cache = None

                if not browser_hwnd or not render_hwnd:
                    self._emit('conn', '未检测到微信小窗', 'error')
                    if time.time() - last_warn_ts >= 10.0:
                        self._log('[等待] 微信浏览器窗口未打开，等待恢复...', 'warn')
                        last_warn_ts = time.time()
                    self._sleep(1.0)
                    continue

                root_hwnd = wb.user32.GetAncestor(browser_hwnd, wb.GA_ROOT) or browser_hwnd
                is_min = bool(wb.user32.IsIconic(root_hwnd))
                browser_rect = wb.get_rect(browser_hwnd)
                if is_min or browser_rect[0] < -10000:
                    self._emit('conn', '已最小化，请恢复', 'warn')
                    if time.time() - last_warn_ts >= 10.0:
                        self._log('[等待] 微信浏览器已最小化（可被其他窗口遮挡，但请勿最小化），等待恢复...', 'warn')
                        last_warn_ts = time.time()
                    self._sleep(1.0)
                    continue

                cur_size = (browser_rect[2] - browser_rect[0], browser_rect[3] - browser_rect[1])
                need_scan = (
                    cache is None
                    or cache['size'] != cur_size
                    or (round_no > 0 and round_no % 30 == 0)
                )
                if need_scan:
                    new_cache, err = v2.scan_and_build_cache(uia, render_hwnd, browser_rect)
                    if not new_cache or not new_cache['seats_rel'] or not new_cache['refresh_rel']:
                        texts, has_risk, _, _ = v2.inspect_texts_status(uia, render_hwnd)
                        if has_risk:
                            self._log(f'[熔断] 检测到风控/异常提示: {texts}', 'error')
                            v2.beep(self.beep_enabled, 'warn')
                            finish_key, finish_text = 'stopped_risk', '已熔断停止'
                            break
                        self._emit('conn', '未停在选座图页面', 'warn')
                        if time.time() - last_warn_ts >= 10.0:
                            self._log(
                                f'[等待] 当前未识别到选座页 (seats={len(new_cache["seats_rel"]) if new_cache else 0}, '
                                f'err={err}, texts={texts[:6]})',
                                'warn',
                            )
                            last_warn_ts = time.time()
                        self._sleep(1.5)
                        continue
                    cache = new_cache
                    occ_now = wb.is_window_occluded(browser_hwnd)
                    occ_label = '后台遮挡' if occ_now else '前台可见'
                    self._emit('conn', f'已连接 · {len(cache["seats_rel"])}座 ({occ_label})', 'ok')
                    if round_no == 0:
                        self._log(
                            f'[就绪] 座位={len(cache["seats_rel"])} 个, 刷新={cache["refresh_rel"]}, '
                            f'确认={cache["confirm_rel"]}',
                            'success',
                        )

                round_no += 1
                t_round = time.time()

                v2.click_named_button(
                    uia,
                    browser_hwnd,
                    render_hwnd,
                    browser_rect,
                    cache,
                    '刷新',
                    'refresh_rel',
                    click_method,
                    jitter=3,
                )

                arr, cur_rect, counts, empties = v2.wait_after_refresh(
                    browser_hwnd,
                    cache['seats_rel'],
                    cache['size'],
                )
                if self._stop_event.is_set():
                    break

                if wb.is_frame_blank(arr):
                    self._emit('conn', '截图空白/窗口被最小化', 'warn')
                    if time.time() - last_warn_ts >= 10.0:
                        self._log('[等待] 后台截图为空白（可能窗口刚最小化或重建），等待恢复...', 'warn')
                        last_warn_ts = time.time()
                    self._sleep(1.0)
                    continue

                if counts is None or (cur_rect[2] - cur_rect[0], cur_rect[3] - cur_rect[1]) != cache['size']:
                    cache = None
                    continue

                elapsed_ms = int((time.time() - t_round) * 1000)
                self._emit('stats', round_no, elapsed_ms, counts, empties)

                occ_now = wb.is_window_occluded(browser_hwnd)
                occ_label = '后台遮挡' if occ_now else '前台可见'
                self._emit('conn', f'已连接 · {len(cache["seats_rel"])}座 ({occ_label})', 'ok')

                if self.preview_enabled or empties:
                    info_txt = f'{arr.shape[1]}×{arr.shape[0]} · {occ_label} · 轮次#{round_no}'
                    self._emit('preview', arr, cache['seats_rel'], empties, info_txt)

                empty_preview = f' 编号={empties[:6]}' if empties else ''
                self._log(
                    f'[第{round_no}轮 {elapsed_ms}ms] '
                    f'占用={counts["occupied"]} 已选={counts["selected"]} '
                    f'暂离={counts["away"]} 监督={counts["supervise"]} 空座={counts["empty"]}{empty_preview}',
                    'empty' if empties else 'info',
                )

                if len(empties) >= 25 and prev_empty_count < 5:
                    texts, has_risk, has_success, on_seatmap = v2.inspect_texts_status(uia, render_hwnd)
                    if has_risk or has_success or not on_seatmap:
                        shot_p = v2.save_shot(arr, 'v2-spike')
                        self._log(f'[熔断] 页面状态变化或异常弹窗 ({shot_p}): {texts}', 'error' if has_risk else 'success')
                        v2.beep(self.beep_enabled, 'warn' if has_risk else 'success')
                        if has_risk:
                            finish_key, finish_text = 'stopped_risk', '已熔断停止'
                        else:
                            finish_key, finish_text = 'stopped_success', '选座成功'
                        break

                prev_empty_count = len(empties)

                if empties and mode == 'monitor':
                    v2.beep(self.beep_enabled, 'alert')
                    shot_p = v2.save_shot(arr, 'v2-empty')
                    self._log(f'  [发现空座] {empties} (截图 {shot_p})', 'empty')

                elif empties and mode == 'live':
                    if not self._sleep(0.08, respect_pause=False):
                        break
                    arr2, cur_rect2, _ = wb.capture_browser(browser_hwnd)
                    if not wb.is_frame_blank(arr2) and (cur_rect2[2] - cur_rect2[0], cur_rect2[3] - cur_rect2[1]) == cache['size']:
                        _, _, empties2 = wb.classify_all_seats(arr2, cur_rect2, cache['seats_rel'])
                        e2_set = set(empties2)
                        confirmed = [sid for sid in empties if sid in e2_set]
                        if not confirmed:
                            self._log(f'  -> [跳过] 二次帧确认无空座（刷新闪烁过滤，原={empties[:6]}）', 'warn')
                            self._sleep(random.uniform(imin, imax))
                            continue
                        empties = confirmed
                        arr = arr2
                        cur_rect = cur_rect2

                    target = v2.pick_target_seat(empties, prefer_list)
                    shot_found = v2.save_shot(arr, 'v2-found', target)
                    self._log(f'  -> [抢座] 目标={target} 空座共{len(empties)}个 (截图 {shot_found})', 'empty')

                    v2.click_seat(uia, browser_hwnd, render_hwnd, cur_rect, cache, target, click_method)

                    seat_entry = next((s for s in cache['seats_rel'] if s['id'] == target), None)
                    turned_green = False
                    poll_arr = None
                    deadline = time.time() + 1.2
                    while time.time() < deadline and seat_entry and not self._stop_event.is_set():
                        time.sleep(0.06)
                        poll_arr, _, _ = wb.capture_browser(browser_hwnd)
                        if not wb.is_frame_blank(poll_arr):
                            st = wb.classify_seat_patch(poll_arr, seat_entry['cx'], seat_entry['cy'])
                            if st == 'selected':
                                turned_green = True
                                break

                    if self._stop_event.is_set():
                        break

                    if not turned_green:
                        shot_ng = v2.save_shot(poll_arr if not wb.is_frame_blank(poll_arr) else arr, 'v2-notgreen', target)
                        texts, has_risk, has_success, on_seatmap = v2.inspect_texts_status(uia, render_hwnd)
                        self._log(f'  -> [未变绿] 座位 {target} 未变绿 (截图 {shot_ng}) texts={texts[:8]}', 'warn')
                        if has_risk:
                            self._log('[熔断] 检测到风控/异常提示，立即停止！', 'error')
                            v2.beep(self.beep_enabled, 'warn')
                            finish_key, finish_text = 'stopped_risk', '已熔断停止'
                            break
                        if has_success or not on_seatmap:
                            self._log('[成功/跳转] 页面已跳转或完成，停止。', 'success')
                            v2.beep(self.beep_enabled, 'success' if has_success else 'warn')
                            finish_key, finish_text = 'stopped_success', '选座成功'
                            break
                    else:
                        if not self._sleep(random.uniform(0.05, 0.12), respect_pause=False):
                            break
                        if not cache.get('confirm_rel'):
                            new_cache, _ = v2.scan_and_build_cache(uia, render_hwnd, cur_rect)
                            if new_cache:
                                cache = new_cache
                        v2.click_named_button(
                            uia,
                            browser_hwnd,
                            render_hwnd,
                            cur_rect,
                            cache,
                            '确认选座',
                            'confirm_rel',
                            click_method,
                            jitter=3,
                        )
                        self._log(f'  -> [已确认] 座位 {target} 已变绿并点击「确认选座」，等待结果...', 'empty')
                        if not self._sleep(1.8, respect_pause=False):
                            break

                        after_arr, _, _ = wb.capture_browser(browser_hwnd)
                        show_arr = after_arr if not wb.is_frame_blank(after_arr) else arr
                        shot_cf = v2.save_shot(show_arr, 'v2-confirm', target)
                        self._emit('preview', show_arr, cache['seats_rel'], [], f'确认结果 · {shot_cf}')
                        texts, has_risk, has_success, on_seatmap = v2.inspect_texts_status(uia, render_hwnd)
                        self._log(f'  -> [结果] 截图={shot_cf} on_seatmap={on_seatmap} texts={texts[:12]}', 'warn')

                        if has_risk:
                            self._log('[熔断] 检测到风控/异常弹窗，立即停止！', 'error')
                            v2.beep(self.beep_enabled, 'warn')
                            finish_key, finish_text = 'stopped_risk', '已熔断停止'
                            break
                        if has_success or not on_seatmap:
                            self._log('[成功/跳转] 已离开选座页或检测到成功提示，停止！', 'success')
                            v2.beep(self.beep_enabled, 'success')
                            finish_key, finish_text = 'stopped_success', '选座成功'
                            break

                        consecutive_fails += 1
                        if consecutive_fails >= 3:
                            self._pause_event.set()
                            self._emit('status', 'paused', '已暂停 (连续失败保护)')
                            self._log('[熔断] 连续 3 次选座未成功，已自动暂停保护账号（按 F10 或点击继续可恢复）。', 'error')
                            v2.beep(self.beep_enabled, 'warn')
                            continue
                        self._log(f'  -> [继续] 座位可能已被抢（连续未成功 {consecutive_fails}/3），冷却 1.5s 后继续', 'warn')
                        self._sleep(1.5)
                        continue

                self._sleep(random.uniform(imin, imax))
        except Exception as e:
            self._log(f'[异常] 后台线程出错: {e}', 'error')
            finish_key, finish_text = 'stopped_risk', '异常停止'
        finally:
            if uia is not None:
                uia.close()
            self._log('[结束] 后台监控已停止', 'info')
            self._emit('finished', finish_key, finish_text)


class GrabberGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('图书馆后台抢座助手 v2')
        self.root.geometry('1180x800')
        self.root.minsize(960, 660)
        self.root.configure(bg=BG_APP)

        self.cfg = load_config()
        self.ui_queue = queue.Queue()
        self.worker = GrabWorker(self.ui_queue)
        self._probing = False
        self._key_state = {v2.VK_F9: False, v2.VK_F10: False}
        self._last_preview_args = None
        self._photo_ref = None
        self._resize_job = None

        self._setup_styles()
        self._build_vars()
        self._build_ui()
        self._apply_config_to_vars()
        self._update_topmost()

        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._poll_ui_queue()
        self.root.after(200, self.on_probe_click)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TFrame', background=BG_APP)
        style.configure('Card.TFrame', background=BG_CARD)
        style.configure('TLabel', background=BG_CARD, foreground=TEXT_PRIMARY, font=(FONT_FAMILY, 10))
        style.configure('Muted.TLabel', background=BG_CARD, foreground=TEXT_MUTED, font=(FONT_FAMILY, 9))
        style.configure('CardTitle.TLabel', background=BG_CARD, foreground=TEXT_PRIMARY, font=(FONT_FAMILY, 10, 'bold'))
        style.configure('TRadiobutton', background=BG_CARD, foreground=TEXT_PRIMARY, font=(FONT_FAMILY, 10))
        style.configure('TCheckbutton', background=BG_CARD, foreground=TEXT_PRIMARY, font=(FONT_FAMILY, 9))
        style.configure('TCombobox', font=(FONT_FAMILY, 9))

    def _build_vars(self):
        self.var_mode = tk.StringVar(value='monitor')
        self.var_imin = tk.StringVar(value='0.8')
        self.var_imax = tk.StringVar(value='1.5')
        self.var_prefer = tk.StringVar(value='')
        self.var_minutes = tk.StringVar(value='120')
        self.var_method = tk.StringVar(value='render_doc')
        self.var_beep = tk.BooleanVar(value=True)
        self.var_preview = tk.BooleanVar(value=True)
        self.var_topmost = tk.BooleanVar(value=False)

    def _apply_config_to_vars(self):
        self.var_mode.set(self.cfg.get('mode', 'monitor'))
        self.var_imin.set(str(self.cfg.get('interval_min', 0.8)))
        self.var_imax.set(str(self.cfg.get('interval_max', 1.5)))
        self.var_prefer.set(str(self.cfg.get('prefer', '')))
        mins = self.cfg.get('minutes', 120.0)
        self.var_minutes.set(str(int(mins) if float(mins).is_integer() else mins))
        self.var_method.set(self.cfg.get('click_method', 'render_doc'))
        self.var_beep.set(bool(self.cfg.get('beep', True)))
        self.var_preview.set(bool(self.cfg.get('preview', True)))
        self.var_topmost.set(bool(self.cfg.get('topmost', False)))

    def _collect_config(self, validate=True):
        try:
            imin = float(self.var_imin.get().strip())
            imax = float(self.var_imax.get().strip())
            minutes = float(self.var_minutes.get().strip())
        except ValueError:
            if validate:
                messagebox.showerror('参数错误', '刷新间隔和运行时长必须为数字。')
            return None
        if imin < 0.2 or imax < 0.2:
            if validate:
                messagebox.showerror('参数错误', '刷新间隔建议不小于 0.2 秒。')
            return None
        if imin > imax:
            imin, imax = imax, imin
            self.var_imin.set(str(imin))
            self.var_imax.set(str(imax))
        if minutes < 0:
            minutes = 0.0
            self.var_minutes.set('0')
        return {
            'mode': self.var_mode.get(),
            'interval_min': imin,
            'interval_max': imax,
            'prefer': self.var_prefer.get().strip(),
            'minutes': minutes,
            'click_method': self.var_method.get(),
            'beep': bool(self.var_beep.get()),
            'preview': bool(self.var_preview.get()),
            'topmost': bool(self.var_topmost.get()),
        }

    def _make_card(self, parent, title=None):
        card = tk.Frame(
            parent,
            bg=BG_CARD,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
            padx=14,
            pady=11,
        )
        if title:
            lbl = tk.Label(card, text=title, bg=BG_CARD, fg=TEXT_PRIMARY, font=(FONT_FAMILY, 10, 'bold'))
            lbl.pack(anchor='w', pady=(0, 8))
        return card

    def _build_ui(self):
        # Top Header Bar
        header = tk.Frame(
            self.root,
            bg=BG_CARD,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
            padx=16,
            pady=10,
        )
        header.pack(fill='x', padx=14, pady=(12, 8))

        left_hdr = tk.Frame(header, bg=BG_CARD)
        left_hdr.pack(side='left', fill='y')
        tk.Label(
            left_hdr,
            text='图书馆后台抢座助手 v2',
            bg=BG_CARD,
            fg=TEXT_PRIMARY,
            font=(FONT_FAMILY, 13, 'bold'),
        ).pack(side='left', padx=(0, 12))

        self.lbl_status_pill = tk.Label(
            left_hdr,
            text='未启动',
            bg='#E2E8F0',
            fg=TEXT_SECONDARY,
            font=(FONT_FAMILY, 9, 'bold'),
            padx=10,
            pady=3,
        )
        self.lbl_status_pill.pack(side='left', padx=(0, 10))

        self.lbl_conn = tk.Label(
            left_hdr,
            text='微信小窗: 未检测',
            bg='#EFF6FF',
            fg=ACCENT_BLUE,
            font=(FONT_FAMILY, 9),
            padx=10,
            pady=3,
        )
        self.lbl_conn.pack(side='left')

        right_hdr = tk.Frame(header, bg=BG_CARD)
        right_hdr.pack(side='right')
        self.btn_probe = tk.Button(
            right_hdr,
            text='检测微信窗口',
            command=self.on_probe_click,
            bg='#EEF2FF',
            fg=ACCENT_BLUE,
            activebackground='#DBEAFE',
            relief='flat',
            font=(FONT_FAMILY, 9, 'bold'),
            padx=12,
            pady=5,
            cursor='hand2',
        )
        self.btn_probe.pack(side='left', padx=(0, 8))

        tk.Button(
            right_hdr,
            text='打开截图目录',
            command=self.on_open_shots,
            bg='#F1F5F9',
            fg=TEXT_PRIMARY,
            activebackground='#E2E8F0',
            relief='flat',
            font=(FONT_FAMILY, 9),
            padx=12,
            pady=5,
            cursor='hand2',
        ).pack(side='left')

        # Main 2-column content area
        body = tk.Frame(self.root, bg=BG_APP)
        body.pack(fill='both', expand=True, padx=14, pady=(0, 12))
        body.grid_columnconfigure(0, minsize=400, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left_col = tk.Frame(body, bg=BG_APP, width=400)
        left_col.grid(row=0, column=0, sticky='ns', padx=(0, 10))
        left_col.grid_propagate(False)

        right_col = tk.Frame(body, bg=BG_APP)
        right_col.grid(row=0, column=1, sticky='nsew')
        right_col.grid_rowconfigure(0, weight=3)
        right_col.grid_rowconfigure(1, weight=2)
        right_col.grid_columnconfigure(0, weight=1)

        # --- Left Column Cards ---
        # 1. Mode Card
        card_mode = self._make_card(left_col, '运行模式')
        card_mode.pack(fill='x', pady=(0, 8))

        self.rb_monitor = ttk.Radiobutton(
            card_mode,
            text='仅监控提醒 (Monitor)',
            variable=self.var_mode,
            value='monitor',
        )
        self.rb_monitor.pack(anchor='w')
        tk.Label(
            card_mode,
            text='刷新发现空座时响铃 + 截图，不点击预约（封禁期/蹲守测试推荐）',
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=(FONT_FAMILY, 8),
        ).pack(anchor='w', padx=(22, 0), pady=(0, 6))

        self.rb_live = ttk.Radiobutton(
            card_mode,
            text='自动抢座 (Live)',
            variable=self.var_mode,
            value='live',
        )
        self.rb_live.pack(anchor='w')
        tk.Label(
            card_mode,
            text='二次确认空座 → 后台点击座位 → 验绿后点击「确认选座」',
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=(FONT_FAMILY, 8),
        ).pack(anchor='w', padx=(22, 0))

        # 2. Cadence Card
        card_cadence = self._make_card(left_col, '刷新节奏（随机抖动防风控）')
        card_cadence.pack(fill='x', pady=(0, 8))

        preset_row = tk.Frame(card_cadence, bg=BG_CARD)
        preset_row.pack(fill='x', pady=(0, 8))
        self.preset_btns = []
        for label, a, b in [
            ('冲刺 0.4~0.8s', 0.4, 0.8),
            ('标准 0.8~1.5s', 0.8, 1.5),
            ('稳健 2.0~3.5s', 2.0, 3.5),
        ]:
            btn = tk.Button(
                preset_row,
                text=label,
                command=lambda x=a, y=b: self.on_preset(x, y),
                bg='#F1F5F9',
                fg=TEXT_PRIMARY,
                activebackground='#DBEAFE',
                relief='flat',
                font=(FONT_FAMILY, 8),
                padx=8,
                pady=4,
                cursor='hand2',
            )
            btn.pack(side='left', expand=True, fill='x', padx=2)
            self.preset_btns.append(btn)

        iv_row = tk.Frame(card_cadence, bg=BG_CARD)
        iv_row.pack(fill='x')
        tk.Label(iv_row, text='最小间隔(s):', bg=BG_CARD, fg=TEXT_SECONDARY, font=(FONT_FAMILY, 9)).pack(side='left')
        self.ent_imin = ttk.Entry(iv_row, textvariable=self.var_imin, width=7)
        self.ent_imin.pack(side='left', padx=(4, 14))
        tk.Label(iv_row, text='最大间隔(s):', bg=BG_CARD, fg=TEXT_SECONDARY, font=(FONT_FAMILY, 9)).pack(side='left')
        self.ent_imax = ttk.Entry(iv_row, textvariable=self.var_imax, width=7)
        self.ent_imax.pack(side='left', padx=(4, 0))

        # 3. Options & Preferences Card
        card_opts = self._make_card(left_col, '偏好与选项')
        card_opts.pack(fill='x', pady=(0, 8))

        tk.Label(card_opts, text='优先座位号（留空=任意空座，逗号分隔如 001,002）:', bg=BG_CARD, fg=TEXT_SECONDARY, font=(FONT_FAMILY, 9)).pack(anchor='w')
        self.ent_prefer = ttk.Entry(card_opts, textvariable=self.var_prefer)
        self.ent_prefer.pack(fill='x', pady=(3, 8))

        row_misc = tk.Frame(card_opts, bg=BG_CARD)
        row_misc.pack(fill='x', pady=(0, 8))
        tk.Label(row_misc, text='时长(分, 0=无限):', bg=BG_CARD, fg=TEXT_SECONDARY, font=(FONT_FAMILY, 9)).pack(side='left')
        self.ent_minutes = ttk.Entry(row_misc, textvariable=self.var_minutes, width=7)
        self.ent_minutes.pack(side='left', padx=(4, 10))
        tk.Label(row_misc, text='点击:', bg=BG_CARD, fg=TEXT_SECONDARY, font=(FONT_FAMILY, 9)).pack(side='left')
        self.cmb_method = ttk.Combobox(
            row_misc,
            textvariable=self.var_method,
            values=('render_doc', 'browser_client'),
            state='readonly',
            width=13,
        )
        self.cmb_method.pack(side='left', padx=(4, 0))

        chk_row = tk.Frame(card_opts, bg=BG_CARD)
        chk_row.pack(fill='x')
        ttk.Checkbutton(chk_row, text='声音提醒', variable=self.var_beep, command=self._sync_live_flags).pack(side='left', padx=(0, 10))
        ttk.Checkbutton(chk_row, text='实时后台预览', variable=self.var_preview, command=self._sync_live_flags).pack(side='left', padx=(0, 10))
        ttk.Checkbutton(chk_row, text='窗口置顶', variable=self.var_topmost, command=self._update_topmost).pack(side='left')

        # 4. Primary Action Row
        card_act = self._make_card(left_col)
        card_act.pack(fill='x', pady=(0, 8))

        btn_row = tk.Frame(card_act, bg=BG_CARD)
        btn_row.pack(fill='x')
        self.btn_start = tk.Button(
            btn_row,
            text='▶ 开始运行',
            command=self.on_start_stop,
            bg=ACCENT_BLUE,
            fg='#FFFFFF',
            activebackground=ACCENT_BLUE_HOVER,
            activeforeground='#FFFFFF',
            relief='flat',
            font=(FONT_FAMILY, 11, 'bold'),
            pady=8,
            cursor='hand2',
        )
        self.btn_start.pack(side='left', fill='x', expand=True, padx=(0, 6))

        self.btn_pause = tk.Button(
            btn_row,
            text='⏸ 暂停 (F9)',
            command=self.on_pause_resume,
            bg='#F1F5F9',
            fg=TEXT_PRIMARY,
            activebackground='#E2E8F0',
            relief='flat',
            font=(FONT_FAMILY, 10, 'bold'),
            padx=12,
            pady=8,
            state='disabled',
            cursor='hand2',
        )
        self.btn_pause.pack(side='left')

        tk.Label(
            card_act,
            text='全局热键: F9 暂停 / F10 继续 · 微信小窗可被遮挡但勿最小化',
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=(FONT_FAMILY, 8),
        ).pack(anchor='w', pady=(6, 0))

        # 5. Live Seat Stats Card
        card_stats = self._make_card(left_col, '实时座位统计')
        card_stats.pack(fill='both', expand=True)

        tiles_row = tk.Frame(card_stats, bg=BG_CARD)
        tiles_row.pack(fill='x', pady=(0, 8))
        self.stat_labels = {}
        tile_specs = [
            ('occupied', '占用', '#FEF2F2', ACCENT_RED),
            ('selected', '已选', '#F0FDF4', ACCENT_GREEN),
            ('empty', '空座', '#EFF6FF', ACCENT_BLUE),
            ('away', '暂离', '#F8FAFC', ACCENT_SLATE),
            ('supervise', '监督', '#FFFBEB', ACCENT_AMBER),
        ]
        for key, title, bg_c, fg_c in tile_specs:
            box = tk.Frame(tiles_row, bg=bg_c, padx=4, pady=6)
            box.pack(side='left', fill='x', expand=True, padx=2)
            val_lbl = tk.Label(box, text='0', bg=bg_c, fg=fg_c, font=(FONT_FAMILY, 14, 'bold'))
            val_lbl.pack()
            tk.Label(box, text=title, bg=bg_c, fg=TEXT_SECONDARY, font=(FONT_FAMILY, 8)).pack()
            self.stat_labels[key] = val_lbl

        self.lbl_stat_sub = tk.Label(
            card_stats,
            text='轮次: #0   耗时: 0ms   空座编号: 无',
            bg=BG_CARD,
            fg=TEXT_SECONDARY,
            font=(FONT_FAMILY, 9),
            anchor='w',
        )
        self.lbl_stat_sub.pack(fill='x')

        # --- Right Panel ---
        # Top: Live Preview Card
        card_prev = self._make_card(right_col)
        card_prev.grid(row=0, column=0, sticky='nsew', pady=(0, 8))

        prev_hdr = tk.Frame(card_prev, bg=BG_CARD)
        prev_hdr.pack(fill='x', pady=(0, 6))
        tk.Label(prev_hdr, text='实时后台画面预览 (PrintWindow)', bg=BG_CARD, fg=TEXT_PRIMARY, font=(FONT_FAMILY, 10, 'bold')).pack(side='left')
        self.lbl_prev_info = tk.Label(prev_hdr, text='等待检测...', bg=BG_CARD, fg=TEXT_MUTED, font=(FONT_FAMILY, 9))
        self.lbl_prev_info.pack(side='right')

        self.preview_box = tk.Frame(card_prev, bg=BG_PREVIEW)
        self.preview_box.pack(fill='both', expand=True)
        self.preview_box.bind('<Configure>', self._on_preview_resize)

        self.lbl_preview = tk.Label(
            self.preview_box,
            text='点击右上角「检测微信窗口」或「开始运行」后\n将在此显示被遮挡微信小窗的实时后台截图',
            bg=BG_PREVIEW,
            fg=TEXT_MUTED,
            font=(FONT_FAMILY, 10),
        )
        self.lbl_preview.place(relx=0.5, rely=0.5, anchor='center')

        # Bottom: Live Logs Card
        card_log = self._make_card(right_col)
        card_log.grid(row=1, column=0, sticky='nsew')

        log_hdr = tk.Frame(card_log, bg=BG_CARD)
        log_hdr.pack(fill='x', pady=(0, 6))
        tk.Label(log_hdr, text='实时运行日志', bg=BG_CARD, fg=TEXT_PRIMARY, font=(FONT_FAMILY, 10, 'bold')).pack(side='left')
        tk.Button(
            log_hdr,
            text='清空日志',
            command=self.on_clear_log,
            bg='#F1F5F9',
            fg=TEXT_SECONDARY,
            activebackground='#E2E8F0',
            relief='flat',
            font=(FONT_FAMILY, 8),
            padx=8,
            pady=2,
            cursor='hand2',
        ).pack(side='right')

        self.txt_log = scrolledtext.ScrolledText(
            card_log,
            wrap='word',
            state='disabled',
            bg=BG_SUBTLE,
            fg=TEXT_PRIMARY,
            font=('Consolas', 9),
            relief='flat',
            padx=8,
            pady=6,
        )
        self.txt_log.pack(fill='both', expand=True)
        self.txt_log.tag_configure('info', foreground='#1E293B')
        self.txt_log.tag_configure('empty', foreground=ACCENT_BLUE, font=('Consolas', 9, 'bold'))
        self.txt_log.tag_configure('success', foreground=ACCENT_GREEN, font=('Consolas', 9, 'bold'))
        self.txt_log.tag_configure('warn', foreground=ACCENT_AMBER, font=('Consolas', 9, 'bold'))
        self.txt_log.tag_configure('error', foreground=ACCENT_RED, font=('Consolas', 9, 'bold'))

        self.lockable_widgets = [
            self.rb_monitor,
            self.rb_live,
            self.ent_imin,
            self.ent_imax,
            self.ent_prefer,
            self.ent_minutes,
            self.cmb_method,
            *self.preset_btns,
        ]

    def _set_inputs_locked(self, locked):
        state = 'disabled' if locked else 'normal'
        for w in self.lockable_widgets:
            if w is self.cmb_method:
                w.configure(state='disabled' if locked else 'readonly')
            else:
                w.configure(state=state)
        self.btn_probe.configure(state='disabled' if locked else 'normal')

    def _set_status_pill(self, key, text):
        palette = {
            'idle': ('#E2E8F0', TEXT_SECONDARY),
            'running': ('#DBEAFE', ACCENT_BLUE),
            'paused': ('#FEF3C7', ACCENT_AMBER),
            'stopped': ('#E2E8F0', TEXT_SECONDARY),
            'stopped_risk': ('#FEE2E2', ACCENT_RED),
            'stopped_success': ('#DCFCE7', ACCENT_GREEN),
        }
        bg, fg = palette.get(key, ('#E2E8F0', TEXT_SECONDARY))
        self.lbl_status_pill.configure(text=text, bg=bg, fg=fg)

    def _set_conn_badge(self, text, level='info'):
        palette = {
            'info': ('#EFF6FF', ACCENT_BLUE),
            'ok': ('#DCFCE7', ACCENT_GREEN),
            'warn': ('#FEF3C7', ACCENT_AMBER),
            'error': ('#FEE2E2', ACCENT_RED),
        }
        bg, fg = palette.get(level, ('#EFF6FF', ACCENT_BLUE))
        self.lbl_conn.configure(text=f'微信小窗: {text}', bg=bg, fg=fg)

    def _append_log(self, line, tag='info'):
        self.txt_log.configure(state='normal')
        self.txt_log.insert('end', line + '\n', tag)
        # Keep log buffer bounded to ~1500 lines
        lines_count = int(self.txt_log.index('end-1c').split('.')[0])
        if lines_count > 1500:
            self.txt_log.delete('1.0', f'{lines_count - 1200}.0')
        self.txt_log.see('end')
        self.txt_log.configure(state='disabled')

    def _update_preview(self, arr, seats_rel, empties, info_text):
        self._last_preview_args = (arr, seats_rel, empties, info_text)
        box_w = max(200, self.preview_box.winfo_width() - 12)
        box_h = max(160, self.preview_box.winfo_height() - 12)
        pil_img = render_preview_image(arr, seats_rel, empties, max_w=box_w, max_h=box_h)
        self._photo_ref = ImageTk.PhotoImage(pil_img)
        self.lbl_preview.configure(image=self._photo_ref, text='')
        self.lbl_prev_info.configure(text=info_text)

    def _on_preview_resize(self, _event=None):
        if self._last_preview_args is None:
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(120, self._redraw_last_preview)

    def _redraw_last_preview(self):
        self._resize_job = None
        if self._last_preview_args is not None:
            self._update_preview(*self._last_preview_args)

    def _sync_live_flags(self):
        self.worker.beep_enabled = bool(self.var_beep.get())
        self.worker.preview_enabled = bool(self.var_preview.get())

    def _update_topmost(self):
        try:
            self.root.attributes('-topmost', bool(self.var_topmost.get()))
        except Exception:
            pass

    def on_preset(self, a, b):
        if not self.worker.is_running:
            self.var_imin.set(str(a))
            self.var_imax.set(str(b))

    def on_clear_log(self):
        self.txt_log.configure(state='normal')
        self.txt_log.delete('1.0', 'end')
        self.txt_log.configure(state='disabled')

    def on_open_shots(self):
        os.makedirs(v2.SHOTS_DIR, exist_ok=True)
        try:
            os.startfile(v2.SHOTS_DIR)
        except Exception as e:
            messagebox.showerror('打开失败', str(e))

    def on_probe_click(self):
        if self.worker.is_running or self._probing:
            return
        self._probing = True
        self.btn_probe.configure(state='disabled', text='检测中...')
        threading.Thread(target=self._probe_thread, daemon=True).start()

    def _probe_thread(self):
        uia = None
        try:
            wins = wb.find_wechat_windows()
            browser_hwnd = wins['browser_hwnd']
            render_hwnd = wins['render_hwnd']
            browser_rect = wins['browser_rect']
            if not browser_hwnd or not render_hwnd:
                self.ui_queue.put(('probe_done', '未检测到小窗', 'error', None))
                self.ui_queue.put(('log', time.strftime('%H:%M:%S ') + '[检测] 未找到微信浏览器窗口，请先打开网页并弹出独立小窗', 'warn'))
                return
            if wins['is_minimized']:
                self.ui_queue.put(('probe_done', '已最小化，请恢复', 'warn', None))
                self.ui_queue.put(('log', time.strftime('%H:%M:%S ') + '[检测] 微信窗口已最小化，请恢复后即可后台遮挡运行', 'warn'))
                return

            uia = wb.UiaClient()
            cache, err = v2.scan_and_build_cache(uia, render_hwnd, browser_rect)
            arr, cur_rect, ok_ret = wb.capture_browser(browser_hwnd)
            if wb.is_frame_blank(arr):
                self.ui_queue.put(('probe_done', f'截图为空白 (ok={ok_ret})', 'warn', None))
                self.ui_queue.put(('log', time.strftime('%H:%M:%S ') + '[检测] PrintWindow 返回空白画面', 'warn'))
                return

            occ = wb.is_window_occluded(browser_hwnd)
            occ_label = '后台遮挡' if occ else '前台可见'
            if cache and cache['seats_rel']:
                _, counts, empties = wb.classify_all_seats(arr, cur_rect, cache['seats_rel'])
                self.ui_queue.put(('stats', 0, 0, counts, empties))
                self.ui_queue.put(('preview', arr, cache['seats_rel'], empties, f'{arr.shape[1]}×{arr.shape[0]} · {occ_label} · 检测截图'))
                self.ui_queue.put(('probe_done', f'已连接 · {len(cache["seats_rel"])}座 ({occ_label})', 'ok', None))
                self.ui_queue.put((
                    'log',
                    time.strftime('%H:%M:%S ')
                    + f'[检测] 连接成功：{len(cache["seats_rel"])} 座 ({occ_label})，占用={counts["occupied"]} 已选={counts["selected"]} 空座={counts["empty"]}',
                    'success',
                ))
            else:
                self.ui_queue.put(('preview', arr, [], [], f'{arr.shape[1]}×{arr.shape[0]} · {occ_label} · 未在选座页'))
                self.ui_queue.put(('probe_done', f'已连接但未识别到选座图 ({occ_label})', 'warn', None))
                self.ui_queue.put(('log', time.strftime('%H:%M:%S ') + f'[检测] 窗口已连接但当前非选座页: err={err}', 'warn'))
        except Exception as e:
            self.ui_queue.put(('probe_done', '检测出错', 'error', None))
            self.ui_queue.put(('log', time.strftime('%H:%M:%S ') + f'[检测错误] {e}', 'error'))
        finally:
            if uia is not None:
                uia.close()

    def on_start_stop(self):
        if self._probing:
            messagebox.showinfo('提示', '正在检测微信窗口，请稍候 1 秒后再启动。')
            return
        if self.worker.is_running:
            self.btn_start.configure(state='disabled', text='正在停止...')
            self.btn_pause.configure(state='disabled')
            self.worker.request_stop()
            return

        cfg = self._collect_config(validate=True)
        if cfg is None:
            return
        self.cfg = cfg
        save_config(cfg)

        self._set_inputs_locked(True)
        self.btn_start.configure(text='■ 停止运行', bg=ACCENT_RED, activebackground='#B91C1C')
        self.btn_pause.configure(state='normal', text='⏸ 暂停 (F9)')
        self._set_status_pill('running', '自动抢座中' if cfg['mode'] == 'live' else '监控中')
        self.worker.start(cfg)

    def on_pause_resume(self):
        if not self.worker.is_running:
            return
        if self.worker.is_paused:
            self.resume_run('按钮')
        else:
            self.pause_run('按钮')

    def pause_run(self, source='F9'):
        if not self.worker.is_running or self.worker.is_paused:
            return
        self.worker.set_paused(True)
        self._set_status_pill('paused', '已暂停 (F9)')
        self.btn_pause.configure(text='▶ 继续 (F10)')
        line = time.strftime('%H:%M:%S ') + f'[暂停] ({source}) 已暂停，按 F10 或点击继续恢复'
        append_file_log(line)
        self._append_log(line, 'warn')

    def resume_run(self, source='F10'):
        if not self.worker.is_running or not self.worker.is_paused:
            return
        self.worker.set_paused(False)
        mode_lbl = '自动抢座中' if self.var_mode.get() == 'live' else '监控中'
        self._set_status_pill('running', mode_lbl)
        self.btn_pause.configure(text='⏸ 暂停 (F9)')
        line = time.strftime('%H:%M:%S ') + f'[恢复] ({source}) 已恢复运行'
        append_file_log(line)
        self._append_log(line, 'success')

    def _poll_hotkeys(self):
        f9 = (wb.user32.GetAsyncKeyState(v2.VK_F9) & 0x8000) != 0
        f10 = (wb.user32.GetAsyncKeyState(v2.VK_F10) & 0x8000) != 0
        if f9 and not self._key_state[v2.VK_F9]:
            self.pause_run('热键 F9')
        self._key_state[v2.VK_F9] = f9
        if f10 and not self._key_state[v2.VK_F10]:
            self.resume_run('热键 F10')
        self._key_state[v2.VK_F10] = f10

    def _poll_ui_queue(self):
        self._poll_hotkeys()
        for _ in range(40):
            try:
                item = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == 'log':
                self._append_log(item[1], item[2])
            elif kind == 'status':
                self._set_status_pill(item[1], item[2])
                if item[1] == 'paused':
                    self.btn_pause.configure(text='▶ 继续 (F10)')
            elif kind == 'conn':
                self._set_conn_badge(item[1], item[2])
            elif kind == 'stats':
                round_no, elapsed_ms, counts, empties = item[1], item[2], item[3], item[4]
                for k, lbl in self.stat_labels.items():
                    lbl.configure(text=str(counts.get(k, 0)))
                empty_txt = ', '.join(empties[:8]) if empties else '无'
                if len(empties) > 8:
                    empty_txt += f' ...(+{len(empties) - 8})'
                self.lbl_stat_sub.configure(text=f'轮次: #{round_no}   耗时: {elapsed_ms}ms   空座编号: {empty_txt}')
            elif kind == 'preview':
                self._update_preview(item[1], item[2], item[3], item[4])
            elif kind == 'probe_done':
                self._probing = False
                self.btn_probe.configure(state='normal' if not self.worker.is_running else 'disabled', text='检测微信窗口')
                self._set_conn_badge(item[1], item[2])
            elif kind == 'finished':
                finish_key, finish_text = item[1], item[2]
                self._set_status_pill(finish_key, finish_text)
                self._set_inputs_locked(False)
                self.btn_start.configure(state='normal', text='▶ 开始运行', bg=ACCENT_BLUE, activebackground=ACCENT_BLUE_HOVER)
                self.btn_pause.configure(state='disabled', text='⏸ 暂停 (F9)')

        self.root.after(80, self._poll_ui_queue)

    def on_close(self):
        cfg = self._collect_config(validate=False)
        if cfg is not None:
            save_config(cfg)
        self.worker.stop(join_timeout=2.5)
        self.root.destroy()


def main():
    root = tk.Tk()
    GrabberGUI(root)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        try:
            with open(ERROR_LOG_PATH, 'w', encoding='utf-8') as f:
                f.write(tb)
        except Exception:
            pass
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror('启动失败', f'程序发生异常，详情已写入 gui_error.log:\n\n{tb}')
            r.destroy()
        except Exception:
            pass
        sys.exit(1)
