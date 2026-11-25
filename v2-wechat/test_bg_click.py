"""Harmless 5-second background click verification (ONLY clicks '刷新').

Never clicks any seat or '确认选座'. Measures PrintWindow pixel changes across
3 background click methods (uia_invoke, render_doc, browser_client) and verifies
the real mouse cursor never moves.
"""
import os
import sys
import time

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import win_backend as wb

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

RESULT_PATH = os.path.join(BASE_DIR, 'test_click_result.txt')
REFRESH_LABEL = '刷新'


def find_text_item(texts, label):
    for item in texts:
        if item.get('text') == label and item.get('rect'):
            return item
    return None


def max_body_diff_pixels(base_arr, cur_arr):
    if base_arr is None or cur_arr is None or base_arr.shape != cur_arr.shape:
        return -1
    y0 = 80 if base_arr.shape[0] > 120 else 0
    b = base_arr[y0:, :, :].astype(np.int16)
    c = cur_arr[y0:, :, :].astype(np.int16)
    diff = np.abs(c - b).max(axis=2)
    return int((diff > 15).sum())


def main():
    lines = []

    def out(msg):
        print(msg, flush=True)
        lines.append(msg)

    wins = wb.find_wechat_windows()
    browser_hwnd = wins['browser_hwnd']
    render_hwnd = wins['render_hwnd']
    browser_rect = wins['browser_rect']

    out('=== Background Refresh Click Test (Read-Only + Refresh Only) ===')
    out(
        f'browser_hwnd={browser_hwnd} render_hwnd={render_hwnd} '
        f'browser_rect={browser_rect} is_minimized={wins["is_minimized"]}'
    )

    if not browser_hwnd or not render_hwnd:
        out('[ERR] WeChat browser window or Chrome_RenderWidgetHostHWND not found.')
        with open(RESULT_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        sys.exit(2)

    if wins['is_minimized']:
        out('[ERR] WeChat browser window is minimized; restore it (can stay behind other windows).')
        with open(RESULT_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        sys.exit(3)

    occluded = wb.is_window_occluded(browser_hwnd)
    out(f'is_occluded={occluded}')

    uia = wb.UiaClient()
    try:
        scan_res = uia.scan(render_hwnd)
        if not scan_res.get('ok'):
            out(f'[ERR] UIA scan failed: {scan_res}')
            sys.exit(4)

        doc_rect = scan_res.get('doc_rect')
        seats = scan_res.get('seats') or []
        texts = scan_res.get('texts') or []
        out(f'doc_name={scan_res.get("doc_name")!r} doc_rect={doc_rect} seats_found={len(seats)}')

        base_img, b_rect, ok_ret = wb.capture_browser(browser_hwnd)
        if wb.is_frame_blank(base_img):
            out(f'[ERR] Baseline PrintWindow frame is blank (ok_ret={ok_ret}).')
            sys.exit(5)

        _, counts, empties = wb.classify_all_seats(base_img, b_rect, seats)
        out(f'baseline_capture: ok_ret={ok_ret} size=({base_img.shape[1]},{base_img.shape[0]})')
        out(f'seat_counts: {counts} empties={empties[:10]}')

        refresh_item = find_text_item(texts, REFRESH_LABEL)
        if not refresh_item:
            out(f'[ERR] Could not locate "{REFRESH_LABEL}" in UIA texts: {[t.get("text") for t in texts]}')
            sys.exit(6)

        rx, ry = wb.rect_center(refresh_item['rect'])
        out(f'refresh_target: rect={refresh_item["rect"]} center=({rx}, {ry})')

        methods = ['uia_invoke', 'render_doc', 'browser_client']
        verdicts = {}

        for idx, method in enumerate(methods):
            if idx > 0:
                time.sleep(1.5)

            pre_img, _, _ = wb.capture_browser(browser_hwnd)
            cursor_before = wb.get_cursor_pos()

            if method == 'uia_invoke':
                res = uia.invoke_text(render_hwnd, REFRESH_LABEL)
                inv_info = f'invoked={res.get("invoked")} err={res.get("error", "")!r}'
                triggered = bool(res.get('invoked'))
            else:
                triggered = wb.send_click_screen(
                    browser_hwnd,
                    render_hwnd,
                    doc_rect,
                    rx,
                    ry,
                    method=method,
                    jitter=0,
                )
                inv_info = f'sent={triggered}'

            max_diff = 0
            for _ in range(8):
                time.sleep(0.1)
                cur_img, _, _ = wb.capture_browser(browser_hwnd)
                d = max_body_diff_pixels(pre_img, cur_img)
                if d > max_diff:
                    max_diff = d

            cursor_after = wb.get_cursor_pos()
            cursor_moved = cursor_before != cursor_after
            passed = triggered and (max_diff >= 50)
            verdict = 'PASS' if passed else 'NO_CHANGE'
            verdicts[method] = (verdict, max_diff, cursor_moved, inv_info)
            out(
                f'  [{verdict}] method={method:14s} {inv_info} '
                f'max_changed_pixels={max_diff} cursor_moved={cursor_moved}'
            )

        out('\n=== Summary ===')
        passing = [m for m, v in verdicts.items() if v[0] == 'PASS']
        out(f'passing_methods={passing}')
        if not passing:
            out('[WARN] No refresh pixel change detected (page may be fast/static or coordinates need inspection).')
    finally:
        uia.close()
        with open(RESULT_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        out(f'Saved report to {RESULT_PATH}')


if __name__ == '__main__':
    main()
