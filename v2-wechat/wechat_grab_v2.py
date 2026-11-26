"""WeChat built-in browser background seat monitor/grabber (v2).

Features:
- Works in occluded background via Win32 PrintWindow (no screen occupation, no mouse capture).
- Zero hardcoded coordinates or pixel templates: Windows UIA reads buttons, seat IDs,
  and popup/error text dynamically.
- Fast jittered cadence (0.8-1.5s default, --fast 0.4-0.8s, --safe 2.0-3.5s).
- Instant green-select verification + UIA response/popup circuit breaker.
- Hotkeys: F9 pause / F10 resume.
"""
import argparse
import ctypes
from ctypes import wintypes
import os
import random
import sys
import time
import winsound

from PIL import Image

if getattr(sys, 'frozen', False):
    _HERE = os.path.dirname(sys.executable)
else:
    _HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import win_backend as wb

BASE_DIR = wb.BASE_DIR

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

VK_F9 = 0x78
VK_F10 = 0x79
wb.user32.GetAsyncKeyState.argtypes = [wintypes.INT]
wb.user32.GetAsyncKeyState.restype = ctypes.c_short

LOG_PATH = os.path.join(BASE_DIR, 'grab_v2.log')
SHOTS_DIR = os.path.join(BASE_DIR, 'shots')
MAX_SHOTS = 20
os.makedirs(SHOTS_DIR, exist_ok=True)

RISK_KEYWORDS = (
    '访问行为异常',
    '请求已拦截',
    '行为异常',
    '黑名单',
    '封禁',
    '已封',
    '禁止预约',
    '操作过于频繁',
    '请求过于频繁',
    '访问频繁',
    '重新登录',
    '登录已过期',
    '登录失效',
    '请先登录',
    '违规',
)
SUCCESS_KEYWORDS = (
    '自主扫码签到',
    '扫码签到',
    '签到帮助',
    '二维码签到',
    '蓝牙签到',
    '应急码签到',
    '选座成功',
    '预约成功',
    '已预约',
)

paused = False
_key_state = {VK_F9: False, VK_F10: False}


def log(msg):
    line = time.strftime('%H:%M:%S ') + msg
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def beep(enabled, kind='alert'):
    """Play a gentle Windows system chime without blocking or harsh square waves."""
    if not enabled:
        return
    alias_map = {
        'alert': ('SystemNotification', winsound.MB_ICONASTERISK),
        'success': ('SystemAsterisk', winsound.MB_OK),
        'warn': ('SystemExclamation', winsound.MB_ICONEXCLAMATION),
    }
    alias, mb_flag = alias_map.get(kind, ('SystemNotification', winsound.MB_OK))
    try:
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        try:
            winsound.MessageBeep(mb_flag)
        except Exception:
            pass


def prune_old_shots(shots_dir=SHOTS_DIR, max_keep=MAX_SHOTS):
    """Keep at most the newest max_keep v2-*.png files in shots_dir."""
    try:
        files = [
            os.path.join(shots_dir, fn)
            for fn in os.listdir(shots_dir)
            if fn.startswith('v2-') and fn.endswith('.png')
        ]
        if len(files) <= max_keep:
            return
        files.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
        for old_p in files[max_keep:]:
            try:
                os.remove(old_p)
            except Exception:
                pass
    except Exception:
        pass


def save_shot(arr, prefix, seat=''):
    ts = time.strftime('%H%M%S')
    tag = f'{prefix}-{seat}-{ts}.png' if seat else f'{prefix}-{ts}.png'
    path = os.path.join(SHOTS_DIR, tag)
    try:
        Image.fromarray(arr, mode='RGB').save(path)
        prune_old_shots()
        return os.path.join('shots', tag)
    except Exception:
        return ''


def check_hotkeys():
    global paused
    f9 = (wb.user32.GetAsyncKeyState(VK_F9) & 0x8000) != 0
    f10 = (wb.user32.GetAsyncKeyState(VK_F10) & 0x8000) != 0
    if f9 and not _key_state[VK_F9]:
        paused = True
        log('[热键] F9 已暂停，按 F10 继续')
    _key_state[VK_F9] = f9
    if f10 and not _key_state[VK_F10]:
        paused = False
        log('[热键] F10 已恢复')
    _key_state[VK_F10] = f10


def sleep_with_hotkeys(sec):
    end = time.time() + max(0.0, sec)
    while time.time() < end:
        check_hotkeys()
        if paused:
            return
        time.sleep(min(0.05, max(0.0, end - time.time())))


def find_text_rect(texts, label):
    for item in texts:
        if item.get('text') == label and item.get('rect'):
            return item['rect']
    return None


def scan_and_build_cache(uia, render_hwnd, browser_rect):
    res = uia.scan(render_hwnd)
    if not res.get('ok'):
        return None, res.get('error', 'UIA scan failed')

    bx, by, br, bb = browser_rect
    bw, bh = br - bx, bb - by
    texts = res.get('texts') or []
    seats = res.get('seats') or []
    doc_rect = res.get('doc_rect')
    doc_offset = None
    if doc_rect and len(doc_rect) == 4:
        doc_offset = (int(doc_rect[0]) - bx, int(doc_rect[1]) - by, int(doc_rect[2]) - bx, int(doc_rect[3]) - by)

    refresh_rect = find_text_rect(texts, '刷新')
    confirm_rect = find_text_rect(texts, '确认选座')

    refresh_rel = None
    if refresh_rect:
        rx, ry = wb.rect_center(refresh_rect)
        refresh_rel = (rx - bx, ry - by)

    confirm_rel = None
    if confirm_rect:
        cx, cy = wb.rect_center(confirm_rect)
        confirm_rel = (cx - bx, cy - by)

    seats_rel = []
    for s in seats:
        rect = s.get('rect')
        if not rect or len(rect) != 4:
            continue
        sx, sy = wb.rect_center(rect)
        seats_rel.append({'id': s['id'], 'cx': sx - bx, 'cy': sy - by})

    cache = {
        'size': (bw, bh),
        'doc_name': res.get('doc_name', ''),
        'doc_offset': doc_offset,
        'refresh_rel': refresh_rel,
        'confirm_rel': confirm_rel,
        'seats_rel': seats_rel,
        'texts': [t.get('text', '') for t in texts if t.get('text')],
    }
    return cache, None


def cur_doc_rect(browser_rect, cache):
    off = cache.get('doc_offset')
    if not off:
        return None
    bx, by = browser_rect[0], browser_rect[1]
    return (bx + off[0], by + off[1], bx + off[2], by + off[3])


def click_named_button(uia, browser_hwnd, render_hwnd, browser_rect, cache, label, rel_key, method, jitter=3):
    if method == 'uia_invoke':
        res = uia.invoke_text(render_hwnd, label)
        if res.get('invoked'):
            return True
    rel = cache.get(rel_key)
    if not rel:
        return False
    sx = browser_rect[0] + rel[0]
    sy = browser_rect[1] + rel[1]
    return wb.send_click_screen(
        browser_hwnd,
        render_hwnd,
        cur_doc_rect(browser_rect, cache),
        sx,
        sy,
        method='render_doc' if method == 'uia_invoke' else method,
        jitter=jitter,
    )


def click_seat(uia, browser_hwnd, render_hwnd, browser_rect, cache, seat_id, method):
    if method == 'uia_invoke':
        res = uia.invoke_seat(render_hwnd, seat_id)
        if res.get('invoked'):
            return True
    seat_entry = next((s for s in cache['seats_rel'] if s['id'] == seat_id), None)
    if not seat_entry:
        return False
    sx = browser_rect[0] + seat_entry['cx']
    sy = browser_rect[1] + seat_entry['cy']
    return wb.send_click_screen(
        browser_hwnd,
        render_hwnd,
        cur_doc_rect(browser_rect, cache),
        sx,
        sy,
        method='render_doc' if method == 'uia_invoke' else method,
        jitter=2,
    )


def classify_texts_list(text_list):
    joined = ' | '.join(text_list)
    has_success = any(k in joined for k in SUCCESS_KEYWORDS)
    has_risk = any(k in joined for k in RISK_KEYWORDS)
    on_seatmap = ('刷新' in text_list) or ('确认选座' in text_list) or ('请选择座位' in text_list)
    return has_risk, has_success, on_seatmap


def inspect_texts_status(uia, render_hwnd):
    res = uia.get_texts(render_hwnd)
    if not res.get('ok'):
        return [], False, False, False
    text_list = [t.get('text', '') for t in (res.get('texts') or []) if t.get('text')]
    has_risk, has_success, on_seatmap = classify_texts_list(text_list)
    return text_list, has_risk, has_success, on_seatmap


def pick_target_seat(empties, prefer_list):
    for pid in prefer_list:
        if pid in empties:
            return pid
    return random.choice(empties)


def wait_after_refresh(browser_hwnd, seats_rel, expected_size):
    """Wait until loading overlay clears and seats settle; return (arr, cur_rect, counts, empties)."""
    time.sleep(0.15)
    deadline = time.time() + 2.5
    while True:
        arr, cur_rect, _ = wb.capture_browser(browser_hwnd)
        if wb.is_frame_blank(arr):
            return arr, cur_rect, None, []
        if (cur_rect[2] - cur_rect[0], cur_rect[3] - cur_rect[1]) != expected_size:
            return arr, cur_rect, None, []
        loading = wb.has_loading_overlay(arr)
        if loading and time.time() < deadline:
            time.sleep(0.06)
            continue
        _, counts, empties = wb.classify_all_seats(arr, cur_rect, seats_rel)
        if loading:
            # Timed out while loading overlay is still up: never emit false empties
            counts['empty'] = 0
            return arr, cur_rect, counts, []
        colored = (
            counts['occupied']
            + counts['selected']
            + counts.get('picked', 0)
            + counts['away']
            + counts['supervise']
        )
        if (len(seats_rel) >= 30 and colored < 20) and time.time() < deadline:
            time.sleep(0.06)
            continue
        return arr, cur_rect, counts, empties


def main():
    global paused
    ap = argparse.ArgumentParser(description='WeChat background seat grabber v2')
    ap.add_argument('--mode', choices=['monitor', 'live'], default='monitor')
    ap.add_argument('--interval-min', type=float, default=0.8)
    ap.add_argument('--interval-max', type=float, default=1.5)
    ap.add_argument('--fast', action='store_true', help='Shortcut for 0.4-0.8s cadence')
    ap.add_argument('--safe', action='store_true', help='Shortcut for 2.0-3.5s cadence')
    ap.add_argument('--click-method', choices=['render_doc', 'browser_client', 'uia_invoke'], default='render_doc')
    ap.add_argument('--prefer', default='', help='Comma-separated preferred seat numbers, e.g. 001,002')
    ap.add_argument('--minutes', type=float, default=120.0, help='Run duration in minutes (0 = unlimited)')
    ap.add_argument('--rescan-every', type=int, default=30, help='Re-scan UIA layout every N rounds')
    ap.add_argument('--no-beep', action='store_true', help='Disable alert beeps')
    args = ap.parse_args()

    if args.fast:
        args.interval_min, args.interval_max = 0.4, 0.8
    elif args.safe:
        args.interval_min, args.interval_max = 2.0, 3.5
    if args.interval_min > args.interval_max:
        args.interval_min, args.interval_max = args.interval_max, args.interval_min

    prefer_list = [p.strip() for p in args.prefer.split(',') if p.strip()]
    beep_on = not args.no_beep

    wins = wb.find_wechat_windows()
    browser_hwnd = wins['browser_hwnd']
    render_hwnd = wins['render_hwnd']
    browser_rect = wins['browser_rect']

    if not browser_hwnd or not render_hwnd:
        log('[错误] 找不到微信浏览器窗口或渲染子窗口，请先打开网页（建议点击右上角弹出独立小窗）。')
        sys.exit(2)

    log(
        f'[启动] mode={args.mode} cadence={args.interval_min:.2f}-{args.interval_max:.2f}s '
        f'click={args.click_method} prefer={prefer_list or "任意"} '
        f'browser_hwnd={browser_hwnd} render_hwnd={render_hwnd} occluded={wb.is_window_occluded(browser_hwnd)}'
    )
    log('[热键] F9 = 暂停，F10 = 继续（全程后台运行，不占鼠标不抢焦点）')

    uia = wb.UiaClient()
    cache = None
    last_warn_ts = 0.0
    last_empty_shot_ts = 0.0
    last_alert_beep_ts = 0.0
    prev_empty_count = 0
    consecutive_fails = 0
    round_no = 0
    start_ts = time.time()

    try:
        while args.minutes <= 0 or (time.time() - start_ts) < args.minutes * 60.0:
            check_hotkeys()
            if paused:
                time.sleep(0.2)
                continue

            # Ensure window handles stay valid, visible, and un-minimized
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

            wins_now = wb.find_wechat_windows() if not browser_hwnd else {
                'is_minimized': bool(wb.user32.IsIconic(wb.user32.GetAncestor(browser_hwnd, wb.GA_ROOT) or browser_hwnd)),
                'browser_rect': wb.get_rect(browser_hwnd),
            }
            browser_rect = wins_now['browser_rect']
            if not browser_hwnd or not render_hwnd or wins_now['is_minimized'] or browser_rect[0] < -10000:
                if time.time() - last_warn_ts >= 10.0:
                    log('[等待] 微信浏览器窗口不可用或已最小化（可被遮挡但请勿最小化），等待恢复...')
                    last_warn_ts = time.time()
                sleep_with_hotkeys(1.0)
                continue

            cur_size = (browser_rect[2] - browser_rect[0], browser_rect[3] - browser_rect[1])
            need_scan = (
                cache is None
                or cache['size'] != cur_size
                or (args.rescan_every > 0 and round_no > 0 and round_no % args.rescan_every == 0)
            )
            if need_scan:
                new_cache, err = scan_and_build_cache(uia, render_hwnd, browser_rect)
                if not new_cache or not new_cache['seats_rel'] or not new_cache['refresh_rel']:
                    texts, has_risk, _, _ = inspect_texts_status(uia, render_hwnd)
                    if has_risk:
                        log(f'[熔断] 检测到风控/异常提示: {texts}')
                        beep(beep_on, 'warn')
                        break
                    if time.time() - last_warn_ts >= 10.0:
                        log(
                            f'[等待] 当前未识别到完整选座页 (seats={len(new_cache["seats_rel"]) if new_cache else 0}, '
                            f'err={err}, texts={texts[:8]})'
                        )
                        last_warn_ts = time.time()
                    sleep_with_hotkeys(1.5)
                    continue
                cache = new_cache
                if round_no == 0:
                    log(
                        f'[就绪] 座位={len(cache["seats_rel"])} 个, '
                        f'刷新={cache["refresh_rel"]}, 确认={cache["confirm_rel"]}'
                    )

            round_no += 1
            t_round = time.time()

            click_named_button(
                uia,
                browser_hwnd,
                render_hwnd,
                browser_rect,
                cache,
                '刷新',
                'refresh_rel',
                args.click_method,
                jitter=3,
            )
            arr, cur_rect, counts, empties = wait_after_refresh(browser_hwnd, cache['seats_rel'], cache['size'])
            if wb.is_frame_blank(arr):
                if time.time() - last_warn_ts >= 10.0:
                    log('[等待] 后台截图为空白（可能窗口刚最小化或重建），等待恢复...')
                    last_warn_ts = time.time()
                sleep_with_hotkeys(1.0)
                continue

            if counts is None or (cur_rect[2] - cur_rect[0], cur_rect[3] - cur_rect[1]) != cache['size']:
                cache = None
                continue

            elapsed_ms = int((time.time() - t_round) * 1000)
            empty_preview = f' 编号={empties[:6]}' if empties else ''
            log(
                f'[第{round_no}轮 {elapsed_ms}ms] '
                f'占用={counts["occupied"]} 已选={counts["selected"] + counts.get("picked", 0)} '
                f'暂离={counts["away"]} 监督={counts["supervise"]} 空座={counts["empty"]}{empty_preview}'
            )

            # Anti-false-positive check when empty count spikes abruptly
            if len(empties) >= 25 and prev_empty_count < 5:
                texts, has_risk, has_success, on_seatmap = inspect_texts_status(uia, render_hwnd)
                if has_success:
                    shot_p = save_shot(arr, 'v2-spike')
                    log(f'[成功] 检测到签到/成功页面 ({shot_p}): {texts}')
                    beep(beep_on, 'success')
                    break
                if has_risk:
                    shot_p = save_shot(arr, 'v2-spike')
                    log(f'[熔断] 检测到风控/异常弹窗 ({shot_p}): {texts}')
                    beep(beep_on, 'warn')
                    break
                if not on_seatmap:
                    shot_p = save_shot(arr, 'v2-spike')
                    log(f'[跳转] 已离开选座页（可能预约成功或跳转广告页，请查看微信确认）({shot_p}): {texts}')
                    beep(beep_on, 'success')
                    break

            prev_empty_count = len(empties)

            if empties and args.mode == 'monitor':
                now_ts = time.time()
                if now_ts - last_alert_beep_ts >= 3.0:
                    beep(beep_on, 'alert')
                    last_alert_beep_ts = now_ts
                if now_ts - last_empty_shot_ts >= 30.0:
                    shot_p = save_shot(arr, 'v2-empty')
                    last_empty_shot_ts = now_ts
                    log(f'  [发现空座] {empties} (截图 {shot_p})')
                else:
                    log(f'  [发现空座] {empties}')

            elif empties and args.mode == 'live':
                time.sleep(0.08)
                arr2, cur_rect2, _ = wb.capture_browser(browser_hwnd)
                if not wb.is_frame_blank(arr2) and (cur_rect2[2] - cur_rect2[0], cur_rect2[3] - cur_rect2[1]) == cache['size']:
                    _, _, empties2 = wb.classify_all_seats(arr2, cur_rect2, cache['seats_rel'])
                    empties2_set = set(empties2)
                    confirmed = [sid for sid in empties if sid in empties2_set]
                    if not confirmed:
                        log(f'  -> [跳过] 二次帧确认无空座（刷新闪烁过滤，原={empties[:6]}）')
                        sleep_with_hotkeys(random.uniform(args.interval_min, args.interval_max))
                        continue
                    empties = confirmed
                    arr = arr2
                    cur_rect = cur_rect2

                target = pick_target_seat(empties, prefer_list)
                shot_found = save_shot(arr, 'v2-found', target)
                log(f'  -> [抢座] 目标={target} 空座共{len(empties)}个 (截图 {shot_found})')

                click_seat(uia, browser_hwnd, render_hwnd, cur_rect, cache, target, args.click_method)

                # Poll up to 1.2s at 60ms intervals to verify target was selected (blue picked or green selected)
                seat_entry = next((s for s in cache['seats_rel'] if s['id'] == target), None)
                selected_ok = False
                poll_arr = None
                deadline = time.time() + 1.2
                while time.time() < deadline and seat_entry:
                    time.sleep(0.06)
                    poll_arr, poll_rect, _ = wb.capture_browser(browser_hwnd)
                    if not wb.is_frame_blank(poll_arr):
                        st = wb.classify_seat_patch(poll_arr, seat_entry['cx'], seat_entry['cy'])
                        if st in ('picked', 'selected'):
                            selected_ok = True
                            break

                if not selected_ok:
                    texts, has_risk, has_success, on_seatmap = inspect_texts_status(uia, render_hwnd)
                    if f'{target}号' in texts:
                        selected_ok = True
                    else:
                        shot_ng = save_shot(poll_arr if not wb.is_frame_blank(poll_arr) else arr, 'v2-notgreen', target)
                        log(f'  -> [未选中] 座位 {target} 未选中 (截图 {shot_ng}) texts={texts[:8]}')
                        if has_success:
                            log('[成功] 检测到签到/成功页面，停止！')
                            beep(beep_on, 'success')
                            break
                        if has_risk:
                            log('[熔断] 检测到风控/异常提示，立即停止！')
                            beep(beep_on, 'warn')
                            break
                        if not on_seatmap:
                            log('[跳转] 已离开选座页（可能选座成功或跳转广告页，请查看微信确认），停止。')
                            beep(beep_on, 'success')
                            break

                if selected_ok:
                    time.sleep(random.uniform(0.05, 0.12))
                    if not cache.get('confirm_rel'):
                        new_cache, _ = scan_and_build_cache(uia, render_hwnd, cur_rect)
                        if new_cache:
                            cache = new_cache
                    click_named_button(
                        uia,
                        browser_hwnd,
                        render_hwnd,
                        cur_rect,
                        cache,
                        '确认选座',
                        'confirm_rel',
                        args.click_method,
                        jitter=3,
                    )
                    log(f'  -> [已确认] 座位 {target} 已选中并点击「确认选座」，等待结果...')
                    time.sleep(1.8)

                    after_arr, _, _ = wb.capture_browser(browser_hwnd)
                    shot_cf = save_shot(after_arr if not wb.is_frame_blank(after_arr) else arr, 'v2-confirm', target)
                    texts, has_risk, has_success, on_seatmap = inspect_texts_status(uia, render_hwnd)
                    log(f'  -> [结果] 截图={shot_cf} on_seatmap={on_seatmap} texts={texts[:12]}')

                    if has_success:
                        log('[成功] 检测到签到/成功页面，选座成功！')
                        beep(beep_on, 'success')
                        break
                    if has_risk:
                        log('[熔断] 检测到风控/异常弹窗，立即停止！')
                        beep(beep_on, 'warn')
                        break
                    if not on_seatmap:
                        log('[成功/跳转] 已离开选座页（可能已预约成功或弹出广告页，请查看微信确认），停止！')
                        beep(beep_on, 'success')
                        break

                    consecutive_fails += 1
                    if consecutive_fails >= 3:
                        paused = True
                        log('[熔断] 连续 3 次选座未成功，已自动暂停保护账号（按 F10 可恢复）。')
                        beep(beep_on, 'warn')
                        continue
                    log(f'  -> [继续] 座位可能已被抢（连续未成功 {consecutive_fails}/3），冷却 1.5s 后继续')
                    sleep_with_hotkeys(1.5)
                    continue

            sleep_with_hotkeys(random.uniform(args.interval_min, args.interval_max))
    finally:
        uia.close()
        log('[结束]')


if __name__ == '__main__':
    main()
