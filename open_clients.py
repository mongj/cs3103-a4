#!/usr/bin/env python3
"""Open as many LiveRecall browser clients as fit on the main display.

Each client is its own Google Chrome window, tiled across the primary
screen (the display with the menu bar). A new window gets its own session
storage, so each client id is distinct.

The default fills the screen. Tiles stay just large enough to show the
LiveRecall header and client id.

Examples:
  python open_clients.py            # as many as fit
  python open_clients.py 12         # exactly 12, still filling the screen
  python open_clients.py --tabs 8   # one window, 8 tabs
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import socket
import subprocess
import sys

DEFAULT_URL = "http://127.0.0.1:8000"
CLIENT_PORT = 8000
SERVER_PORT = 8765
APP_NAME = "Google Chrome"
# Smallest tile that still shows the compact header (name and client id).
MIN_W = 260
MIN_H = 170

SCREEN_JS = r"""
ObjC.import("AppKit");
function rect(r) {
  return {x: r.origin.x, y: r.origin.y, w: r.size.width, h: r.size.height};
}
const screen = $.NSScreen.screens.objectAtIndex(0);
JSON.stringify({frame: rect(screen.frame), visible: rect(screen.visibleFrame)});
"""


def main_display():
    """Visible area of the primary display, in Chrome's top-left coordinates."""
    raw = subprocess.check_output(
        ["osascript", "-l", "JavaScript", "-e", SCREEN_JS],
        text=True,
    )
    data = json.loads(raw)
    frame = data["frame"]
    visible = data["visible"]
    if frame["w"] <= 0 or frame["h"] <= 0:
        raise RuntimeError("could not read the main display size")
    top = frame["h"] - (visible["y"] + visible["h"])
    return {
        "x": visible["x"],
        "y": top,
        "w": visible["w"],
        "h": visible["h"],
    }


def max_shape(area, gap):
    """Densest complete grid whose tiles stay at least MIN_W by MIN_H."""
    cols = max(1, math.floor((area["w"] - gap) / (MIN_W + gap)))
    rows = max(1, math.floor((area["h"] - gap) / (MIN_H + gap)))
    return cols, rows


def shape_for(count, area, gap):
    """Column and row counts for `count` tiles, or the full grid when count is None."""
    max_cols, max_rows = max_shape(area, gap)
    if count is None or count >= max_cols * max_rows:
        return max_cols, max_rows
    best = (1, count)
    best_score = None
    for cols in range(1, max_cols + 1):
        rows = math.ceil(count / cols)
        if rows > max_rows:
            continue
        empty = cols * rows - count
        cell_w = (area["w"] - gap * (cols + 1)) / cols
        cell_h = (area["h"] - gap * (rows + 1)) / rows
        aspect_penalty = abs(math.log((cell_w / cell_h) / (area["w"] / area["h"])))
        score = (-empty, -aspect_penalty)
        if best_score is None or score > best_score:
            best_score = score
            best = (cols, rows)
    return best


def tile(count, cols, rows, area, gap):
    """Row-major (left, top, right, bottom) rects covering the visible display."""
    cell_w = (area["w"] - gap * (cols + 1)) / cols
    cell_h = (area["h"] - gap * (rows + 1)) / rows
    rects = []
    for index in range(count):
        row, col = divmod(index, cols)
        left = area["x"] + gap + col * (cell_w + gap)
        top = area["y"] + gap + row * (cell_h + gap)
        rects.append(
            (
                int(round(left)),
                int(round(top)),
                int(round(left + cell_w)),
                int(round(top + cell_h)),
            )
        )
    return rects


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def applescript_list(rects):
    return "{" + ", ".join("{" + ", ".join(str(n) for n in rect) + "}" for rect in rects) + "}"


def run_applescript(source):
    result = subprocess.run(
        ["osascript", "-e", source],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "osascript failed")


def open_windows(url, rects):
    script = f"""
tell application "{APP_NAME}"
    set specs to {applescript_list(rects)}
    set pageUrl to "{url}"
    set ids to {{}}
    repeat with spec in specs
        set w to make new window
        set URL of active tab of w to pageUrl
        set bounds of w to spec
        set end of ids to (id of w)
    end repeat
    delay 0.4
    repeat with i from 1 to count of specs
        try
            set bounds of (first window whose id is item i of ids) to item i of specs
        end try
    end repeat
    activate
end tell
"""
    run_applescript(script)


def open_tab_window(url, count, rect):
    left, top, right, bottom = rect
    extra = max(0, count - 1)
    script = f"""
tell application "{APP_NAME}"
    set w to make new window
    set pageUrl to "{url}"
    set bounds of w to {{{left}, {top}, {right}, {bottom}}}
    set URL of active tab of w to pageUrl
    repeat {extra} times
        tell w to make new tab with properties {{URL:pageUrl}}
    end repeat
    delay 0.3
    set bounds of w to {{{left}, {top}, {right}, {bottom}}}
    activate
end tell
"""
    run_applescript(script)


def warn_if_down():
    if not listening(CLIENT_PORT):
        print(
            f"Note: nothing is serving the client on {DEFAULT_URL}.\n"
            "      Start it with:  cd client && yarn dev",
            file=sys.stderr,
        )
    if not listening(SERVER_PORT):
        print(
            "Note: solution_server.py is not listening on port 8765.\n"
            "      Start it with:  python solution_server.py --port 8765",
            file=sys.stderr,
        )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "count", nargs="?", type=int, default=None,
        help="number of clients (default: as many as fit on the main display)",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"client URL (default {DEFAULT_URL})")
    parser.add_argument("--tabs", action="store_true", help="open tabs in one window instead of tiled windows")
    parser.add_argument("--gap", type=int, default=8, help="pixels between tiled windows")
    parser.add_argument("--dry-run", action="store_true", help="print the layout and do not open Chrome")
    args = parser.parse_args()
    if args.count is not None and args.count < 1:
        parser.error("count must be >= 1")
    if args.gap < 0:
        parser.error("--gap must be >= 0")
    if any(char in args.url for char in '"\\\n\r'):
        parser.error("--url contains characters that cannot be passed to Chrome")
    if not args.url.startswith(("http://", "https://")):
        parser.error("--url must start with http:// or https://")
    return args


def main():
    args = parse_args()
    if shutil.which("osascript") is None:
        raise SystemExit("osascript is required (macOS).")

    area = main_display()
    cols, rows = shape_for(None if args.tabs else args.count, area, args.gap)
    count = args.count if args.count is not None else cols * rows
    if not args.tabs and count > cols * rows:
        print(
            f"The main display fits {cols * rows} windows at {MIN_W}x{MIN_H}; opening {cols * rows}.",
            file=sys.stderr,
        )
        count = cols * rows
    if args.tabs:
        rects = [(
            int(round(area["x"])),
            int(round(area["y"])),
            int(round(area["x"] + area["w"])),
            int(round(area["y"] + area["h"])),
        )]
    else:
        rects = tile(count, cols, rows, area, args.gap)

    if args.dry_run:
        kind = f"{count} tabs in 1 window" if args.tabs else f"{len(rects)} windows ({cols}x{rows})"
        print(f"{kind} on the main display ({int(area['w'])}x{int(area['h'])} pt) -> {args.url}")
        for index, rect in enumerate(rects, start=1):
            left, top, right, bottom = rect
            print(f"  {index}: {right - left}x{bottom - top} at ({left}, {top})")
        return

    warn_if_down()
    try:
        if args.tabs:
            open_tab_window(args.url, count, rects[0])
            print(f"Opened {count} tabs at {args.url}")
        else:
            open_windows(args.url, rects)
            print(f"Opened {len(rects)} windows ({cols}x{rows}) at {args.url}")
    except RuntimeError as exc:
        raise SystemExit(
            f"{exc}\n\nIf macOS asked for permission, allow this terminal to control "
            "Google Chrome and run the script again."
        ) from exc


if __name__ == "__main__":
    main()
