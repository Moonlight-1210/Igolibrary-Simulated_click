"""Win32 background capture, message clicking, UIA IPC client, and seat classifier."""
import ctypes
from ctypes import wintypes
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time

import numpy as np

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
user32.SetProcessDPIAware()

# Window message and Win32 constants
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
GA_ROOT = 2
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0

EnumTopProc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)

# User32 prototypes (c_void_p for all handle-bearing types to prevent 64-bit truncation)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = ctypes.c_void_p

user32.EnumWindows.argtypes = [EnumTopProc, ctypes.c_void_p]
user32.EnumWindows.restype = wintypes.BOOL

user32.EnumChildWindows.argtypes = [ctypes.c_void_p, EnumChildProc, ctypes.c_void_p]
user32.EnumChildWindows.restype = wintypes.BOOL

user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetClassNameW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsWindow.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsIconic.restype = wintypes.BOOL

user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
user32.GetAncestor.restype = ctypes.c_void_p

user32.GetWindowDC.argtypes = [ctypes.c_void_p]
user32.GetWindowDC.restype = ctypes.c_void_p

user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int

user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = ctypes.c_void_p

user32.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
user32.ScreenToClient.restype = wintypes.BOOL

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.SendMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
user32.SendMessageW.restype = ctypes.c_void_p

user32.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
user32.PostMessageW.restype = wintypes.BOOL

# GDI32 prototypes
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p

gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p

gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p

gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = wintypes.BOOL

gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = wintypes.BOOL

gdi32.GetDIBits.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    RES_DIR = getattr(sys, '_MEIPASS', BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = BASE_DIR


def get_class(hwnd):
    if not hwnd:
        return ''
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_title(hwnd):
    if not hwnd:
        return ''
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def get_rect(hwnd):
    if not hwnd:
        return (0, 0, 0, 0)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def get_pid(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def get_cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def rect_center(rect):
    l, t, r, b = rect
    return ((int(l) + int(r)) // 2, (int(t) + int(b)) // 2)


def _rect_area(rect):
    l, t, r, b = rect
    return max(0, r - l) * max(0, b - t)


def collect_wechat_pids():
    pids = {}
    try:
        out = subprocess.check_output(
            ['tasklist', '/FO', 'CSV', '/NH'],
            text=True,
            encoding='mbcs',
            errors='replace',
        )
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                proc_name = parts[0].strip('"')
                if proc_name.lower() in ('weixin.exe', 'wechat.exe', 'wechatappex.exe'):
                    try:
                        pids[int(parts[1])] = proc_name
                    except ValueError:
                        pass
    except Exception:
        pass
    return pids


def find_wechat_windows():
    """Discover WeChat browser_hwnd (visible Chrome_WidgetWin_*) and render_hwnd."""
    main_hwnd = user32.FindWindowW(None, '微信') or user32.FindWindowW('WeChatMainWndForPC', None)
    pid_map = collect_wechat_pids()
    if main_hwnd:
        mpid = get_pid(main_hwnd)
        if mpid and mpid not in pid_map:
            pid_map[mpid] = 'Weixin.exe'

    if not pid_map:
        return {
            'main_hwnd': main_hwnd or None,
            'browser_hwnd': None,
            'render_hwnd': None,
            'browser_rect': (0, 0, 0, 0),
            'is_minimized': False,
        }

    top_windows = []

    def top_cb(h, _):
        if get_pid(h) in pid_map:
            top_windows.append(h)
        return True

    user32.EnumWindows(EnumTopProc(top_cb), None)

    browser_candidates = []
    render_candidates = []

    def inspect_win(h):
        cls = get_class(h)
        rect = get_rect(h)
        vis = bool(user32.IsWindowVisible(h))
        w = rect[2] - rect[0]
        hgt = rect[3] - rect[1]
        if vis and ('Chrome_WidgetWin' in cls) and w > 200 and hgt > 200:
            browser_candidates.append((h, rect))
        if 'RenderWidgetHost' in cls:
            render_candidates.append((h, cls, vis, rect))

    for th in top_windows:
        inspect_win(th)
        children = []

        def child_cb(ch, _):
            children.append(ch)
            return True

        user32.EnumChildWindows(th, EnumChildProc(child_cb), None)
        for ch in children:
            inspect_win(ch)

    browser_hwnd = None
    browser_rect = (0, 0, 0, 0)
    if browser_candidates:
        browser_hwnd, browser_rect = max(browser_candidates, key=lambda x: _rect_area(x[1]))

    render_hwnd = None
    if render_candidates:
        # Prefer visible Chrome_RenderWidgetHostHWND with non-trivial area,
        # otherwise fall back to largest Chrome_RenderWidgetHostHWND (popped-out mode).
        vis_exact = [x for x in render_candidates if x[1] == 'Chrome_RenderWidgetHostHWND' and x[2] and _rect_area(x[3]) > 2500]
        all_exact = [x for x in render_candidates if x[1] == 'Chrome_RenderWidgetHostHWND' and _rect_area(x[3]) > 2500]
        pool = vis_exact or all_exact or render_candidates
        render_hwnd = max(pool, key=lambda x: _rect_area(x[3]))[0]

    is_minimized = False
    check_hwnd = browser_hwnd or main_hwnd
    if check_hwnd:
        root_hwnd = user32.GetAncestor(check_hwnd, GA_ROOT) or check_hwnd
        if user32.IsIconic(root_hwnd):
            is_minimized = True
        r = get_rect(root_hwnd)
        if r[0] < -10000 or r[1] < -10000:
            is_minimized = True
    if browser_hwnd and (browser_rect[0] < -10000 or browser_rect[1] < -10000):
        is_minimized = True

    return {
        'main_hwnd': main_hwnd or None,
        'browser_hwnd': browser_hwnd,
        'render_hwnd': render_hwnd,
        'browser_rect': browser_rect,
        'is_minimized': is_minimized,
    }


def is_window_occluded(hwnd):
    if not hwnd:
        return None
    rect = get_rect(hwnd)
    cx, cy = rect_center(rect)
    target_root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    hit_hwnd = user32.WindowFromPoint(wintypes.POINT(cx, cy))
    hit_root = user32.GetAncestor(hit_hwnd, GA_ROOT) if hit_hwnd else None
    return hit_root != target_root


def capture_browser(browser_hwnd):
    """Capture browser_hwnd via PrintWindow(PW_RENDERFULLCONTENT) even when occluded."""
    if not browser_hwnd or not user32.IsWindow(browser_hwnd):
        return None, (0, 0, 0, 0), False

    rect = get_rect(browser_hwnd)
    l, t, r, b = rect
    w = max(1, r - l)
    h = max(1, b - t)

    hwnd_dc = user32.GetWindowDC(browser_hwnd)
    if not hwnd_dc:
        return None, rect, False

    mem_dc = None
    hbm = None
    old_bm = None
    try:
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mem_dc:
            return None, rect, False
        hbm = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        if not hbm:
            return None, rect, False
        old_bm = gdi32.SelectObject(mem_dc, hbm)

        ok = bool(user32.PrintWindow(browser_hwnd, mem_dc, PW_RENDERFULLCONTENT))

        if old_bm:
            gdi32.SelectObject(mem_dc, old_bm)
            old_bm = None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = BI_RGB

        buf = ctypes.create_string_buffer(w * h * 4)
        lines = gdi32.GetDIBits(mem_dc, hbm, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if lines <= 0:
            return None, rect, ok

        arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape((h, w, 4))
        rgb = arr[:, :, [2, 1, 0]].copy()
        return rgb, rect, ok
    finally:
        if old_bm and mem_dc:
            gdi32.SelectObject(mem_dc, old_bm)
        if hbm:
            gdi32.DeleteObject(hbm)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(browser_hwnd, hwnd_dc)


def is_frame_blank(arr):
    if arr is None or arr.size == 0:
        return True
    return float(arr.std()) < 5.0


def has_loading_overlay(img):
    """Detect dark translucent center loading toast (e.g. '加载中')."""
    if img is None or img.size == 0:
        return False
    h, w = img.shape[:2]
    cy, cx = h // 2, w // 2
    half_h = min(100, h // 4)
    half_w = min(100, w // 4)
    if half_h <= 0 or half_w <= 0:
        return False
    center = img[cy - half_h:cy + half_h, cx - half_w:cx + half_w]
    dark = ((center[:, :, 0] < 120) & (center[:, :, 1] < 120) & (center[:, :, 2] < 120)).sum()
    return int(dark) > 2000


def _make_lparam(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def send_click_screen(browser_hwnd, render_hwnd, doc_rect, screen_x, screen_y, method='render_doc', jitter=2):
    """Send a background mouse click without moving the real cursor or activating focus."""
    sx = int(screen_x)
    sy = int(screen_y)
    if jitter > 0:
        sx += random.randint(-jitter, jitter)
        sy += random.randint(-jitter, jitter)

    if method == 'browser_client' or method == 'browser_client_post':
        if not browser_hwnd:
            return False
        pt = wintypes.POINT(sx, sy)
        if not user32.ScreenToClient(browser_hwnd, ctypes.byref(pt)):
            return False
        lp = _make_lparam(pt.x, pt.y)
        fn = user32.PostMessageW if method == 'browser_client_post' else user32.SendMessageW
        target = browser_hwnd
    else:
        # render_doc: coordinates relative to document origin (or render_hwnd rect fallback)
        if not render_hwnd:
            return False
        if doc_rect and len(doc_rect) == 4 and doc_rect[2] > doc_rect[0]:
            ox, oy = int(doc_rect[0]), int(doc_rect[1])
        else:
            r = get_rect(render_hwnd)
            ox, oy = r[0], r[1]
        cx = sx - ox
        cy = sy - oy
        lp = _make_lparam(cx, cy)
        fn = user32.SendMessageW
        target = render_hwnd

    fn(target, WM_MOUSEMOVE, 0, lp)
    time.sleep(random.uniform(0.02, 0.04))
    fn(target, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(random.uniform(0.03, 0.06))
    fn(target, WM_LBUTTONUP, 0, lp)
    return True


def classify_seat_patch(img, cx, cy):
    """Classify a single seat patch centered at image-relative (cx, cy)."""
    h, w = img.shape[:2]
    x0 = max(0, int(cx) - 7)
    x1 = min(w, int(cx) + 7)
    y0 = max(0, int(cy) - 7)
    y1 = min(h, int(cy) + 7)
    if x1 <= x0 or y1 <= y0:
        return 'unknown'

    patch = img[y0:y1, x0:x1].astype(np.int16)
    R, G, B = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    red = int(((R > 190) & (G < 150) & (B < 160)).sum())
    green = int(((G > 140) & (R < 150) & (B < 150)).sum())
    blue = int(((B > 180) & ((B - R) > 40) & (R < 170)).sum())
    orange = int(((R > 210) & (G > 130) & (G < 200) & (B < 120)).sum())
    gray = int(((np.abs(R - G) < 15) & (np.abs(G - B) < 15) & (R > 180) & (R < 238)).sum())

    total = float(patch.shape[0] * patch.shape[1])
    thresh_color = max(24, int(total * 0.20))
    thresh_gray = max(45, int(total * 0.35))
    candidates = []
    for st, sc in (('picked', blue), ('selected', green), ('supervise', orange), ('occupied', red)):
        if sc >= thresh_color:
            candidates.append((sc, st))
    if gray >= thresh_gray:
        candidates.append((gray, 'away'))
    if candidates:
        return max(candidates)[1]

    white = int(((R > 225) & (G > 225) & (B > 225)).sum())
    dark = int(((R < 130) & (G < 130) & (B < 130)).sum())
    if (dark / total) < 0.30 and (white / total) >= 0.45:
        return 'empty'
    return 'unknown'


def classify_all_seats(img, browser_rect, seats):
    """Classify all seats using cached browser-relative or screen-coord centers."""
    bx, by = int(browser_rect[0]), int(browser_rect[1])
    states = {}
    counts = {'occupied': 0, 'selected': 0, 'picked': 0, 'supervise': 0, 'away': 0, 'empty': 0, 'unknown': 0}
    empties = []

    for seat in seats:
        sid = seat['id']
        if 'cx' in seat and 'cy' in seat:
            cx, cy = int(seat['cx']), int(seat['cy'])
        else:
            sx, sy = rect_center(seat['rect'])
            cx, cy = sx - bx, sy - by
        st = classify_seat_patch(img, cx, cy)
        states[sid] = st
        counts[st] += 1
        if st == 'empty':
            empties.append(sid)

    return states, counts, empties


class UiaClient:
    """Persistent PowerShell UIA worker client over UTF-8/ASCII JSON lines."""

    def __init__(self, worker_path=None):
        if worker_path:
            self.worker_path = worker_path
        else:
            p_base = os.path.join(BASE_DIR, 'uia_worker.ps1')
            p_res = os.path.join(RES_DIR, 'uia_worker.ps1')
            self.worker_path = p_base if os.path.exists(p_base) else p_res
        self._proc = None
        self._queue = None
        self._reader_thread = None
        self._start()

    def _start(self):
        self.close()
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self._proc = subprocess.Popen(
            [
                'powershell',
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-File',
                self.worker_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=creationflags,
        )
        self._queue = queue.Queue()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        try:
            ready_line = self._queue.get(timeout=15.0)
        except queue.Empty:
            raise RuntimeError('UIA worker timed out during startup')
        if not ready_line:
            raise RuntimeError('UIA worker exited during startup')
        msg = json.loads(ready_line)
        if not msg.get('ready'):
            raise RuntimeError(f'Unexpected UIA worker handshake: {msg}')

    def _read_loop(self):
        proc = self._proc
        q = self._queue
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    def _request(self, payload, timeout=12.0, retry_on_dead=True):
        if self._proc is None or self._proc.poll() is not None:
            if not retry_on_dead:
                return {'ok': False, 'error': 'UIA worker not running'}
            self._start()

        line = json.dumps(payload, ensure_ascii=True)
        try:
            self._proc.stdin.write(line + '\n')
            self._proc.stdin.flush()
            resp_line = self._queue.get(timeout=timeout)
        except (BrokenPipeError, OSError, queue.Empty) as e:
            if retry_on_dead:
                self._start()
                return self._request(payload, timeout=timeout, retry_on_dead=False)
            return {'ok': False, 'error': f'UIA request failed: {e}'}

        if resp_line is None:
            if retry_on_dead:
                self._start()
                return self._request(payload, timeout=timeout, retry_on_dead=False)
            return {'ok': False, 'error': 'UIA worker closed stdout'}

        return json.loads(resp_line)

    def scan(self, render_hwnd, timeout=15.0):
        return self._request({'cmd': 'scan', 'render_hwnd': int(render_hwnd)}, timeout=timeout)

    def get_texts(self, render_hwnd, timeout=10.0):
        return self._request({'cmd': 'texts', 'render_hwnd': int(render_hwnd)}, timeout=timeout)

    def invoke_text(self, render_hwnd, text, timeout=10.0):
        return self._request(
            {'cmd': 'invoke_text', 'render_hwnd': int(render_hwnd), 'text': str(text)},
            timeout=timeout,
        )

    def invoke_seat(self, render_hwnd, seat_id, timeout=10.0):
        return self._request(
            {'cmd': 'invoke_seat', 'render_hwnd': int(render_hwnd), 'seat': str(seat_id)},
            timeout=timeout,
        )

    def close(self):
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write(json.dumps({'cmd': 'quit'}) + '\n')
                proc.stdin.flush()
                proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
