#!/usr/bin/env python3
"""Telegram bot that talks to a persistent Claude Code session in a tmux pane.

Claude runs interactively in tmux window 'recom:claude'. This bot sends
keystrokes via tmux send-keys and reads output via tmux capture-pane.
You can also SSH in and `tmux attach -t recom` to talk to Claude directly.
"""

import asyncio
import os
import re
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT = int(os.environ["TELEGRAM_CHAT_ID"])
CLAUDE_PANE = "recom:claude"

_lock = asyncio.Lock()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[@-~]")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _auth(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ALLOWED_CHAT:
            await update.message.reply_text("Unauthorized.")
            return
        return await func(update, context)
    return wrapper


def _capture_pane(lines: int = 300) -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", CLAUDE_PANE, "-p", "-S", f"-{lines}"],
        capture_output=True, text=True,
    )
    return _strip_ansi(r.stdout)


def _send_keys(text: str):
    # -l for literal (no tmux key interpretation)
    subprocess.run(["tmux", "send-keys", "-t", CLAUDE_PANE, "-l", text])
    subprocess.run(["tmux", "send-keys", "-t", CLAUDE_PANE, "Enter"])


def _extract_response(before: str, after: str) -> str:
    """Extract new content that appeared after sending a message."""
    before_lines = before.rstrip().split("\n")
    after_lines = after.rstrip().split("\n")

    # Find divergence point: walk backwards from end of 'before' to find
    # where it matches in 'after', then take everything after that.
    # Simple approach: skip the first N lines that match.
    match_end = min(len(before_lines), len(after_lines))
    diverge = 0
    for i in range(match_end):
        if i < len(before_lines) and i < len(after_lines):
            if before_lines[i] == after_lines[i]:
                diverge = i + 1
            else:
                break

    new_lines = after_lines[diverge:]
    return "\n".join(new_lines).strip()


@_auth
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _lock.locked():
        await update.message.reply_text("Claude is busy. /cancel to interrupt.")
        return

    async with _lock:
        message = update.message.text
        before = _capture_pane()
        _send_keys(message)

        sent = await update.message.reply_text("Working...")
        last_content = before
        last_edit_len = 0
        stable_secs = 0
        total_secs = 0
        max_secs = 300  # 5 min timeout

        while stable_secs < 5 and total_secs < max_secs:
            await asyncio.sleep(1)
            total_secs += 1
            current = _capture_pane()

            if current == last_content:
                stable_secs += 1
            else:
                stable_secs = 0
                last_content = current

                # Progressive update every ~400 chars
                new_text = _extract_response(before, current)
                if new_text and len(new_text) - last_edit_len > 400:
                    try:
                        await sent.edit_text(new_text[-4000:])
                        last_edit_len = len(new_text)
                    except Exception:
                        pass

        final = _extract_response(before, last_content)
        if not final:
            final = "(no output)"

        chunks = [final[i : i + 4000] for i in range(0, len(final), 4000)]
        try:
            await sent.edit_text(chunks[0])
        except Exception:
            await update.message.reply_text(chunks[0])
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk)


@_auth
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subprocess.run(["tmux", "send-keys", "-t", CLAUDE_PANE, "C-c", ""])
    await update.message.reply_text("Sent Ctrl+C.")


@_auth
async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _send_keys("/clear")
    await update.message.reply_text("Cleared conversation.")


@_auth
async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pane = _capture_pane(30)
    await update.message.reply_text(f"```\n{pane[-3900:]}\n```", parse_mode="Markdown")


@_auth
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Recom Claude Agent\n\n"
        "Send any message → forwarded to Claude in tmux.\n"
        "SSH in + `tmux attach -t recom` → talk directly.\n\n"
        "/cancel — Ctrl+C\n"
        "/clear — clear conversation\n"
        "/status — last 30 lines of pane"
    )


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print(f"Telegram bot running (pane={CLAUDE_PANE}, chat_id={ALLOWED_CHAT})")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
