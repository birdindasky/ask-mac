"""py2app build script for Ask.app.

Usage:
    source venv312/bin/activate
    python setup.py py2app          # alias mode (fast, dev iteration)
    python setup.py py2app -A       # explicit alias mode
    make build                      # full standalone build (calls this)

ARM64-only by default — the user's machine is M-series and shipping a
universal2 build would double bundle size for no benefit.
"""
from __future__ import annotations
from setuptools import setup

APP = ["mac_launcher.py"]
DATA_FILES = [
    ("static", [
        "static/index.html",
        "static/app.js",
        "static/i18n.js",
        "static/style.css",
    ]),
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/Ask.icns",
    "plist": {
        "CFBundleName": "Ask",
        "CFBundleDisplayName": "Ask",
        "CFBundleIdentifier": "com.birdindasky.ask",
        "CFBundleVersion": "0.2.0",
        "CFBundleShortVersionString": "0.2.0",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "Ask 需要 AppleEvents 权限以打开和操作窗口。",
        # Allow local-loopback only — keeps the bundled webview happy without
        # punching App Transport Security holes for the wider internet.
        "NSAppTransportSecurity": {
            "NSAllowsLocalNetworking": True,
            "NSExceptionDomains": {
                "127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True},
                "localhost": {"NSExceptionAllowsInsecureHTTPLoads": True},
            },
        },
    },
    "packages": [
        "app",
        "uvicorn",
        "fastapi",
        "starlette",
        "pydantic",
        "pydantic_core",
        "anthropic",
        "openai",
        "httpx",
        "httpcore",
        "anyio",
        "sniffio",
        "h11",
        "certifi",
        "tiktoken",
        "jieba",
    ],
    "includes": [
        "webview",
        "webview.platforms.cocoa",
        # tiktoken loads encoder definitions through this namespace package;
        # listing the leaf module makes py2app pull it in.
        "tiktoken_ext.openai_public",
        # google.genai lives under a namespace package — py2app's `packages`
        # list rejects dotted names, so we pull the package in via includes.
        "google.genai",
        "google.auth",
    ],
    "excludes": [
        "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "pytest",
    ],
    "frameworks": [],
    # ARM64-only: smaller bundle, faster builds. universal2 would double the size.
    "arch": "arm64",
    "strip": True,
    "optimize": 1,
}

setup(
    app=APP,
    name="Ask",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
