"""
Telegram MP4 → WebM Sticker Bot
Requirements:
    pip install python-telegram-bot==20.7
    ffmpeg must be installed on the system (apt install ffmpeg)

Usage:
    1. Get a bot token from @BotFather on Telegram
    2. Set your token in BOT_TOKEN below (or use an environment variable)
    3. Run: python mp4_to_webm_bot.py
    4. Send any .mp4 file to your bot → get back a sticker-ready .webm
"""

import os
import logging
import subprocess
import tempfile
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Telegram animated sticker constraints
MAX_SIDE   = 512     # px  — longest side must be exactly 512
MAX_FRAMES = 3       # sec — max duration
MAX_SIZE   = 256_000 # bytes — 256 KB hard limit
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def convert_to_sticker_webm(input_path: str, output_path: str) -> bool:
    """
    Convert input video to a Telegram-sticker-compliant WebM.

    Constraints applied:
      • VP9 video codec, no audio
      • Longest side scaled to 512 px (other side ≤ 512, divisible by 2)
      • Duration trimmed to 3 s
      • Two-pass encode targeting ≤ 256 KB
    """
    # ── Scale filter: fit inside 512×512, keep aspect ratio ──────────────────
    scale_filter = (
        "scale='if(gt(iw,ih),512,trunc(512*iw/ih/2)*2)':"
        "     'if(gt(iw,ih),trunc(512*ih/iw/2)*2,512)'"
    )

    # ── Calculate target bitrate from file size budget ────────────────────────
    # budget_bits / duration_s = bitrate; leave ~5 % margin
    budget_bits  = MAX_SIZE * 8 * 0.95
    target_brate = int(budget_bits / MAX_FRAMES)   # bits/s

    tmpdir = tempfile.mkdtemp()
    passlog = os.path.join(tmpdir, "ffmpeg2pass")

    common_args = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(MAX_FRAMES),          # trim to 3 s
        "-vf", scale_filter,
        "-c:v", "libvpx-vp9",
        "-b:v", str(target_brate),
        "-an",                          # no audio
        "-deadline", "good",
        "-cpu-used", "2",
    ]

    # Pass 1
    pass1 = common_args + [
        "-pass", "1",
        "-passlogfile", passlog,
        "-f", "null", "/dev/null",
    ]
    # Pass 2
    pass2 = common_args + [
        "-pass", "2",
        "-passlogfile", passlog,
        output_path,
    ]

    try:
        subprocess.run(pass1, check=True, capture_output=True)
        subprocess.run(pass2, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        log.error("FFmpeg error: %s", e.stderr.decode())
        return False
    finally:
        # Clean up pass-log files
        for f in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


# ─── HANDLERS ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Send me an MP4 video and I'll convert it into a "
        "Telegram-sticker-ready WebM file.\n\n"
        "📌 Rules Telegram enforces:\n"
        "  • Max 512×512 px\n"
        "  • Max 3 seconds\n"
        "  • No audio\n"
        "  • ≤ 256 KB\n\n"
        "Just drop an .mp4 here!"
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message

    # Accept both Video and Document (when sent as file)
    if message.video:
        file_obj = message.video
    elif message.document and message.document.mime_type == "video/mp4":
        file_obj = message.document
    else:
        await message.reply_text("⚠️ Please send an MP4 file.")
        return

    await message.reply_text("⏳ Converting… this may take a few seconds.")

    # Download the file
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path  = os.path.join(tmpdir, "input.mp4")
        output_path = os.path.join(tmpdir, "sticker.webm")

        tg_file = await file_obj.get_file()
        await tg_file.download_to_drive(input_path)

        success = convert_to_sticker_webm(input_path, output_path)

        if not success or not os.path.exists(output_path):
            await message.reply_text("❌ Conversion failed. Make sure FFmpeg is installed.")
            return

        size = os.path.getsize(output_path)
        if size > MAX_SIZE:
            await message.reply_text(
                f"⚠️ The output is {size // 1024} KB — still over 256 KB.\n"
                "Try a shorter or lower-resolution clip."
            )
            return

        with open(output_path, "rb") as f:
            await message.reply_document(
                document=f,
                filename="sticker.webm",
                caption=(
                    f"✅ Done! ({size // 1024} KB)\n\n"
                    "To create a sticker pack:\n"
                    "1. Open @Stickers bot\n"
                    "2. Send /newpack or /addsticker\n"
                    "3. Upload this WebM file"
                ),
            )


async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me an MP4 video file (as a file, not as a compressed video)."
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError(
            "Set your bot token: edit BOT_TOKEN in the script "
            "or run:  BOT_TOKEN=xxx python mp4_to_webm_bot.py"
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.ALL, handle_other))

    log.info("Bot is running…")
    app.run_polling()


if __name__ == "__main__":
    main()
