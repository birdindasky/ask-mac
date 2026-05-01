"""Multi-modal attachment normalization.

Wire shape from the frontend composer:
    [{"type": "text"|"image"|"file", "name": "...", "mime": "...",
      "data": "<base64-for-image, plain-text-for-file>", "size": int}]

This module:
  - validates and clamps shape/size,
  - returns a normalized copy safe to persist to meta.attachments,
  - produces an "inlined" prompt string so any text-only adapter (CLI
    subscriptions, older HTTP APIs) can still see the file contents and
    knows an image was attached.

True image-bytes-to-vision-API plumbing is deferred — the storage shape
is forwards-compatible, so a future adapter can lift the base64 directly
out of meta.attachments.
"""
from __future__ import annotations

# Conservative caps so a paste-bomb can't bloat sqlite or blow the model's window.
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB after base64-decode
MAX_TEXT_FILE_BYTES = 200 * 1024  # 200 KB
MAX_ATTACHMENTS = 8


def normalize_attachments(items: list | None) -> list[dict]:
    """Validate, clamp, and trim a raw attachment list. Drops malformed entries."""
    if not items:
        return []
    out: list[dict] = []
    for it in items:
        if len(out) >= MAX_ATTACHMENTS:
            break
        if not isinstance(it, dict):
            continue
        kind = it.get("type")
        data = it.get("data")
        if kind not in ("image", "file") or not isinstance(data, str) or not data:
            continue
        name = (it.get("name") or "").strip() or ("attachment.bin" if kind == "image" else "attachment.txt")
        mime = (it.get("mime") or "").strip() or ("image/png" if kind == "image" else "text/plain")
        size = int(it.get("size") or len(data))
        cap = MAX_IMAGE_BYTES if kind == "image" else MAX_TEXT_FILE_BYTES
        if size > cap:
            # Skip rather than silently truncate — the frontend should have caught this.
            continue
        out.append({"type": kind, "name": name, "mime": mime, "data": data, "size": size})
    return out


def inline_into_prompt(prompt: str, attachments: list[dict]) -> str:
    """Build the text the model actually sees.

    Text files are inlined inside ``` fences so the model gets full content.
    Images become a one-line `[image attached: name.png]` marker — the bytes
    stay in meta.attachments for vision-capable adapters to pick up later.
    """
    if not attachments:
        return prompt
    parts = [prompt.rstrip()] if prompt and prompt.strip() else []
    for a in attachments:
        if a["type"] == "file":
            lang_hint = ""
            mime = a.get("mime", "")
            name = a.get("name", "")
            if "json" in mime or name.endswith(".json"):
                lang_hint = "json"
            elif "markdown" in mime or name.endswith(".md"):
                lang_hint = "markdown"
            elif name.endswith((".py",)):
                lang_hint = "python"
            elif name.endswith((".ts", ".tsx")):
                lang_hint = "typescript"
            elif name.endswith((".js", ".jsx")):
                lang_hint = "javascript"
            parts.append(f"\n[附件文件: {name}]\n```{lang_hint}\n{a['data']}\n```")
        elif a["type"] == "image":
            parts.append(f"\n[附件图片: {a.get('name')} ({a.get('mime')})]")
    return "\n".join(parts).strip()
