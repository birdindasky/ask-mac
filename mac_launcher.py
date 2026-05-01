"""Ask.app launcher — uvicorn-in-thread + PyWebView main window + AppKit chrome.

Boot sequence:
  1. Pick a free local port, bind it.
  2. Spawn uvicorn on a daemon thread serving app.main:app.
  3. Wait until /api/health is reachable.
  4. Configure the AppKit menu bar, status-bar tray icon, and dark/light
     follow-system observer.
  5. Open the PyWebView main window pointing at http://127.0.0.1:<port>/.

This file ONLY runs on macOS — it imports pyobjc unconditionally. Linux
or Windows builds would need a separate launcher.
"""
from __future__ import annotations
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Optional

# Mark this process as the packaged runtime. settings.py reads this so the
# data path goes to ~/Library/Application Support/Ask/ rather than ~/.ask-dev/.
os.environ.setdefault("MLC_PACKAGED", "1")

import uvicorn  # noqa: E402

from app import settings  # noqa: E402
from app.utils.logging_setup import init_logging  # noqa: E402

init_logging()
log = logging.getLogger("ask.launcher")


# ---------- Port + uvicorn thread ----------

def _pick_port() -> int:
    """Bind to port 0 to let the kernel hand us a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_uvicorn(port: int) -> threading.Thread:
    """Run uvicorn on a daemon thread + watchdog that re-launches on death.

    Without the watchdog, an unhandled exception in the worker silently kills
    the API and the UI shows blank toasts. The watchdog rebinds the same port
    (SO_REUSEADDR is set by uvicorn) and lets the user keep working.
    """
    cfg = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )

    def _run_once():
        server = uvicorn.Server(cfg)
        try:
            server.run()
        except Exception:
            log.exception("uvicorn worker crashed")

    def _supervise():
        # Loop: run, log exit, sleep briefly, restart. Caps at 5 fast restarts
        # per minute to avoid pegging CPU if something is fundamentally broken.
        restarts: list[float] = []
        while True:
            _run_once()
            now = time.time()
            restarts = [t for t in restarts if now - t < 60] + [now]
            log.warning("uvicorn exited; restart #%d in 1s", len(restarts))
            if len(restarts) > 5:
                log.error("uvicorn flapping (>5 restarts/min) — bailing out")
                return
            time.sleep(1.0)

    t = threading.Thread(target=_supervise, name="uvicorn-supervisor", daemon=True)
    t.start()
    return t


def _wait_for_ready(port: int, timeout: float = 10.0) -> bool:
    """Poll /api/health until the server answers OK."""
    import urllib.request
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


# ---------- Window state persistence ----------

def _load_window_state() -> dict:
    try:
        return json.loads(settings.WINDOW_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_window_state(state: dict) -> None:
    try:
        settings.WINDOW_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        log.exception("failed to save window state")


# ---------- AppKit chrome ----------

def _setup_appkit(window, on_quit) -> None:
    """Build NSMenu menubar + NSStatusItem tray + appearance observer.

    Called after PyWebView has created its native window so AppKit's
    sharedApplication is up.
    """
    from AppKit import (  # type: ignore
        NSApplication,
        NSApp,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSImage,
        NSVariableStatusItemLength,
        NSAlert,
        NSAttributedString,
    )
    from Foundation import NSObject, NSBundle  # type: ignore
    import objc  # type: ignore

    app = NSApplication.sharedApplication()

    # Pretend Ask is the active app even when run from `python mac_launcher.py`,
    # so the menu bar shows our name not "Python".
    info = NSBundle.mainBundle().infoDictionary()
    if info:
        info["CFBundleName"] = settings.APP_NAME
        info["CFBundleDisplayName"] = settings.APP_NAME

    # Shared flag: True only while the user is genuinely quitting (⌘+Q,
    # Dock → Quit, etc). The window-closing handler reads this to decide
    # between "hide to tray" (false) and "actually let the window close
    # because we're shutting down anyway" (true).
    _quit_intent = {"value": False}

    class _Actions(NSObject):
        def quit_(self, sender):
            _quit_intent["value"] = True
            on_quit()
            NSApp.terminate_(None)

        def about_(self, sender):
            # Standard About panel = nicer layout (icon + name + version +
            # credits), and it docks to the menu the way macOS users expect.
            credits_text = (
                f"本地多模型:单聊 / 对比 / 辩论 / 讨论 求共识。\n\n"
                f"Bundle: {settings.BUNDLE_ID}\n"
                f"数据目录: {settings.DATA_DIR}\n"
                f"日志目录: {settings.LOG_DIR}"
            )
            credits = NSAttributedString.alloc().initWithString_(credits_text)
            opts = {
                "ApplicationName": settings.APP_NAME,
                "ApplicationVersion": settings.APP_VERSION,
                "Credits": credits,
            }
            # Try to attach the bundle icon explicitly. In dev mode the
            # NSBundle icon isn't picked up automatically because we're
            # running as `python mac_launcher.py`; load assets/Ask.icns
            # from the repo root if we can find it.
            try:
                icon_path = Path(__file__).resolve().parent / "assets" / "Ask.icns"
                if icon_path.is_file():
                    img = NSImage.alloc().initByReferencingFile_(str(icon_path))
                    if img is not None:
                        opts["ApplicationIcon"] = img
            except Exception:
                log.exception("about panel icon load failed")
            NSApp.orderFrontStandardAboutPanelWithOptions_(opts)

        # Menu actions run on the AppKit main thread. pywebview's
        # evaluate_js dispatches BACK to the main thread and waits on a
        # semaphore — calling it from the main thread deadlocks the UI.
        # We push every JS dispatch onto a daemon thread so the menu
        # action returns immediately and evaluate_js can do its callAfter
        # round-trip without contending with itself.
        @objc.python_method
        def _dispatch_js(self, js_code: str):
            def _run():
                try:
                    window.evaluate_js(js_code)
                except Exception:
                    log.exception("evaluate_js failed: %s", js_code[:80])
            threading.Thread(target=_run, name="ask-js-dispatch", daemon=True).start()

        @objc.python_method
        def _switch_mode(self, mode_id: str):
            self._dispatch_js(
                f"window.dispatchEvent(new CustomEvent('ask:set-mode', {{detail: {{mode: '{mode_id}'}}}}))"
            )

        def modeChat_(self, sender): self._switch_mode("chat")
        def modeCompare_(self, sender): self._switch_mode("compare")
        def modeDebate_(self, sender): self._switch_mode("debate")
        def modeDiscuss_(self, sender): self._switch_mode("discuss")

        def newWindow_(self, sender):
            self._dispatch_js("window.dispatchEvent(new CustomEvent('ask:new-session'))")

        def focusComposer_(self, sender):
            self._dispatch_js("window.dispatchEvent(new CustomEvent('ask:focus-composer'))")

        def openSearch_(self, sender):
            self._dispatch_js("window.dispatchEvent(new CustomEvent('ask:open-search'))")

        def openSettings_(self, sender):
            self._dispatch_js("window.dispatchEvent(new CustomEvent('ask:open-settings'))")

        def toggleWindow_(self, sender):
            # AppHelper.callAfter (used by pywebview's show/hide) sometimes
            # silently no-ops if the run loop is in a transitional state.
            # We drive NSWindow + NSApp directly to make the toggle bullet-
            # proof: unhide the app, makeKeyAndOrderFront, then activate.
            try:
                ns_window = window.native
                if ns_window.isVisible():
                    ns_window.orderOut_(None)
                else:
                    NSApp.unhide_(None)
                    ns_window.makeKeyAndOrderFront_(None)
                    NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                log.exception("toggle window failed")

        def hideWindow_(self, sender):
            # Bound to ⌘+W. Keeps the process alive in the tray, mirroring
            # Mail/Messages behavior. Direct orderOut_ avoids the
            # AppHelper.callAfter race that pywebview's hide() suffers.
            try:
                window.native.orderOut_(None)
            except Exception:
                log.exception("hide window failed")

        def appearanceChanged_(self, _notification):
            # Fired when the user toggles macOS Dark/Light. Off-thread
            # dispatch so the system notification doesn't deadlock with
            # evaluate_js's main-thread callback.
            self._dispatch_js("window.dispatchEvent(new CustomEvent('ask:appearance'))")

    actions = _Actions.alloc().init()
    # Hold a reference so AppKit doesn't release it.
    objc._actions_ref = actions  # type: ignore[attr-defined]

    # Build NSMenu
    main_menu = NSMenu.alloc().init()

    # App menu (Ask)
    app_menu_item = NSMenuItem.alloc().init()
    main_menu.addItem_(app_menu_item)
    app_menu = NSMenu.alloc().init()
    about_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"关于 {settings.APP_NAME}", b"about:", ""
    )
    about_item.setTarget_(actions)
    app_menu.addItem_(about_item)
    app_menu.addItem_(NSMenuItem.separatorItem())
    settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "设置…", b"openSettings:", ","
    )
    settings_item.setTarget_(actions)
    app_menu.addItem_(settings_item)
    app_menu.addItem_(NSMenuItem.separatorItem())
    hide_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"隐藏 {settings.APP_NAME}", b"hide:", "h"
    )
    app_menu.addItem_(hide_item)
    app_menu.addItem_(NSMenuItem.separatorItem())
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"退出 {settings.APP_NAME}", b"quit:", "q"
    )
    quit_item.setTarget_(actions)
    app_menu.addItem_(quit_item)
    app_menu_item.setSubmenu_(app_menu)

    # File menu
    file_menu_item = NSMenuItem.alloc().init()
    main_menu.addItem_(file_menu_item)
    file_menu = NSMenu.alloc().initWithTitle_("文件")
    new_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "新建会话", b"newWindow:", "n"
    )
    new_item.setTarget_(actions)
    file_menu.addItem_(new_item)
    file_menu.addItem_(NSMenuItem.separatorItem())
    close_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "关闭窗口", b"hideWindow:", "w"
    )
    close_item.setTarget_(actions)
    file_menu.addItem_(close_item)
    file_menu_item.setSubmenu_(file_menu)

    # Edit menu (standard cut/copy/paste — point at first responder)
    edit_menu_item = NSMenuItem.alloc().init()
    main_menu.addItem_(edit_menu_item)
    edit_menu = NSMenu.alloc().initWithTitle_("编辑")
    for title, sel, key in (
        ("撤销", b"undo:", "z"),
        ("重做", b"redo:", "Z"),
        (None, None, None),
        ("剪切", b"cut:", "x"),
        ("拷贝", b"copy:", "c"),
        ("粘贴", b"paste:", "v"),
        ("全选", b"selectAll:", "a"),
        (None, None, None),
        ("查找…", b"openSearch:", "f"),
    ):
        if title is None:
            edit_menu.addItem_(NSMenuItem.separatorItem())
        else:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key)
            if sel == b"openSearch:":
                mi.setTarget_(actions)
            edit_menu.addItem_(mi)
    edit_menu_item.setSubmenu_(edit_menu)

    # View menu (composer focus + mode shortcuts)
    view_menu_item = NSMenuItem.alloc().init()
    main_menu.addItem_(view_menu_item)
    view_menu = NSMenu.alloc().initWithTitle_("视图")
    composer_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "聚焦输入框", b"focusComposer:", "l"
    )
    composer_item.setTarget_(actions)
    view_menu.addItem_(composer_item)
    view_menu.addItem_(NSMenuItem.separatorItem())
    for title, sel, key in (
        ("单聊", b"modeChat:", "1"),
        ("对比", b"modeCompare:", "2"),
        ("辩论", b"modeDebate:", "3"),
        ("讨论", b"modeDiscuss:", "4"),
    ):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key)
        mi.setTarget_(actions)
        view_menu.addItem_(mi)
    view_menu_item.setSubmenu_(view_menu)

    # Window menu
    window_menu_item = NSMenuItem.alloc().init()
    main_menu.addItem_(window_menu_item)
    window_menu = NSMenu.alloc().initWithTitle_("窗口")
    minimize_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "最小化", b"performMiniaturize:", "m"
    )
    window_menu.addItem_(minimize_item)
    window_menu_item.setSubmenu_(window_menu)
    app.setWindowsMenu_(window_menu)

    # Help menu
    help_menu_item = NSMenuItem.alloc().init()
    main_menu.addItem_(help_menu_item)
    help_menu = NSMenu.alloc().initWithTitle_("帮助")
    help_menu_item.setSubmenu_(help_menu)
    app.setHelpMenu_(help_menu)

    app.setMainMenu_(main_menu)

    # Status-bar tray icon
    status_bar = NSStatusBar.systemStatusBar()
    status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    button = status_item.button()
    if button is not None:
        button.setTitle_("💬")  # plain unicode glyph, no PNG dependency
    tray_menu = NSMenu.alloc().init()
    show_hide = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "显示 / 隐藏 Ask", b"toggleWindow:", ""
    )
    show_hide.setTarget_(actions)
    tray_menu.addItem_(show_hide)
    tray_new = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "新建会话", b"newWindow:", ""
    )
    tray_new.setTarget_(actions)
    tray_menu.addItem_(tray_new)
    tray_menu.addItem_(NSMenuItem.separatorItem())
    tray_quit = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "退出", b"quit:", ""
    )
    tray_quit.setTarget_(actions)
    tray_menu.addItem_(tray_quit)
    status_item.setMenu_(tray_menu)
    objc._status_item_ref = status_item  # type: ignore[attr-defined]

    # Observe macOS appearance changes so 'theme=system' updates the
    # webview promptly. AppleInterfaceThemeChangedNotification is the
    # canonical broadcast for Light/Dark toggles.
    try:
        from Foundation import NSDistributedNotificationCenter  # type: ignore
        center = NSDistributedNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(
            actions, b"appearanceChanged:", "AppleInterfaceThemeChangedNotification", None
        )
    except Exception:
        log.exception("appearance observer attach failed")

    # Belt + suspenders for "close keeps app running":
    #   1. Tell NSApp not to auto-terminate when the last window closes
    #      (cocoa default is NO, but pywebview's delegate may override).
    #   2. Swizzle the existing delegate's windowWillClose_ to be a no-op
    #      when we're not actually quitting — pywebview's default version
    #      calls `app.stop_()` once instances is empty, which kills the
    #      whole process. We need to skip that in the hide-to-tray case.
    #   3. Override applicationShouldTerminate_ so ANY terminate request
    #      (Dock Quit, Force Quit, Activity Monitor, ⌘Q from outside our
    #      menu, kill -TERM) flips quit_intent and proceeds. Without this,
    #      the only path that quits is our own File menu's "退出 Ask"
    #      because every other path hits pywebview's
    #      applicationShouldTerminate_ → should_close → closing event →
    #      our _on_closing which (correctly) cancels for window close
    #      but (incorrectly) was also cancelling app terminate.
    try:
        delegate = NSApp.delegate()
        if delegate is not None:
            orig_app_should_term = getattr(
                delegate, "applicationShouldTerminateAfterLastWindowClosed_", None
            )

            def _no_terminate(self_, sender):
                return False

            # Bind the override onto the class so it overrides at runtime.
            from objc import classAddMethod  # type: ignore
            try:
                # PyObjC: replace via setattr on the class works for Python-defined classes
                cls = delegate.__class__
                cls.applicationShouldTerminateAfterLastWindowClosed_ = _no_terminate  # type: ignore[attr-defined]
            except Exception:
                pass

            orig_will_close = getattr(delegate.__class__, "windowWillClose_", None)

            def _patched_will_close(self_, notification):
                # Only run pywebview's teardown when we're truly quitting.
                # In the hide-to-tray path the window's close was already
                # cancelled in windowShouldClose_, so this normally won't
                # fire — but in case it does (e.g. via NSApp.terminate
                # cascading windowWillClose), let the original run only
                # when quit_intent says yes.
                if _quit_intent.get("value") and orig_will_close is not None:
                    try:
                        return orig_will_close(self_, notification)
                    except Exception:
                        log.exception("orig windowWillClose failed")
                # Otherwise: hide and swallow.
                try:
                    win = notification.object()
                    win.orderOut_(None)
                except Exception:
                    pass

            try:
                delegate.__class__.windowWillClose_ = _patched_will_close  # type: ignore[attr-defined]
            except Exception:
                log.exception("windowWillClose patch failed")

            # Force-flip quit_intent on any terminate request, then return
            # YES so AppKit lets the process die. This intercepts BEFORE
            # pywebview's default applicationShouldTerminate_ runs its
            # should_close loop (which would otherwise see quit_intent=False
            # and cancel the terminate).
            def _patched_should_terminate(self_, sender):
                _quit_intent["value"] = True
                try:
                    on_quit()
                except Exception:
                    log.exception("on_quit during terminate failed")
                return True

            try:
                delegate.__class__.applicationShouldTerminate_ = _patched_should_terminate  # type: ignore[attr-defined]
            except Exception:
                log.exception("applicationShouldTerminate patch failed")

            # ⌘Tab / Dock click "reopen" — bring the window back when it was
            # hidden via ⌘W or red-button close. macOS calls
            # applicationShouldHandleReopen:hasVisibleWindows: on the app
            # delegate; when hasVisibleWindows is False we re-show the
            # main NSWindow and activate the app. Returning YES tells AppKit
            # we handled it (Mail/Messages do the same).
            def _should_handle_reopen(self_, sender, has_visible_windows):
                try:
                    if not has_visible_windows:
                        ns_window = window.native
                        ns_window.makeKeyAndOrderFront_(None)
                        NSApp.activateIgnoringOtherApps_(True)
                except Exception:
                    log.exception("reopen show window failed")
                return True

            try:
                delegate.__class__.applicationShouldHandleReopen_hasVisibleWindows_ = (  # type: ignore[attr-defined]
                    _should_handle_reopen
                )
            except Exception:
                log.exception("applicationShouldHandleReopen patch failed")
    except Exception:
        log.exception("delegate hardening failed")

    # Expose the quit-intent flag so main() can read it from window.events.closing.
    return _quit_intent


# ---------- Entry ----------

def main() -> None:
    log.info("Ask %s starting", settings.APP_VERSION)
    port = _pick_port()
    log.info("uvicorn → 127.0.0.1:%d", port)
    _start_uvicorn(port)
    if not _wait_for_ready(port):
        log.error("uvicorn did not become ready within timeout")
        raise SystemExit(2)

    import webview  # type: ignore

    state = _load_window_state()
    width = max(900, int(state.get("width") or 1200))
    height = max(600, int(state.get("height") or 800))
    x = state.get("x")
    y = state.get("y")

    window = webview.create_window(
        title=settings.APP_NAME,
        url=f"http://127.0.0.1:{port}/",
        width=width,
        height=height,
        x=x,
        y=y,
        background_color="#0c0e14",
        text_select=True,
        confirm_close=False,
    )

    # Holds the quit-intent dict returned by _setup_appkit. The closing
    # handler checks it: if quit_intent['value'] is False the user just
    # hit ⌘+W or the red close button, and we hide rather than close.
    quit_state: dict = {"value": False}

    def _on_loaded():
        # PyWebView fires `loaded` on a worker thread, but every AppKit call
        # below mutates NSApp state and must run on the main thread or
        # we'll get NSInternalInconsistencyException.
        try:
            from Foundation import NSOperationQueue  # type: ignore
            def _setup():
                qi = _setup_appkit(window, on_quit=_persist_window_state)
                if isinstance(qi, dict):
                    quit_state["ref"] = qi
            NSOperationQueue.mainQueue().addOperationWithBlock_(_setup)
        except Exception:
            log.exception("AppKit chrome dispatch failed")

    def _persist_window_state():
        try:
            w, h = window.width, window.height
            x_, y_ = window.x, window.y
            _save_window_state({"width": int(w), "height": int(h), "x": int(x_), "y": int(y_)})
        except Exception:
            log.exception("window state save failed")

    def _on_closing():
        # Always snapshot window state. If the user is genuinely quitting
        # (⌘+Q sets quit_intent), let the close proceed. Otherwise hide
        # to tray and cancel the close.
        _persist_window_state()
        qi = quit_state.get("ref") or {}
        if qi.get("value"):
            return True  # let it close, app is exiting
        # Drive orderOut directly — pywebview's hide() goes through
        # AppHelper.callAfter which can race with the closing event
        # handler on the same run-loop turn.
        try:
            ns_window = window.native
            ns_window.orderOut_(None)
        except Exception:
            log.exception("hide on close failed")
        return False  # cancel default close

    window.events.loaded += _on_loaded
    window.events.closing += _on_closing

    webview.start(gui="cocoa")


if __name__ == "__main__":
    main()
