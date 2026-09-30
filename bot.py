"""
bot.py — Telegram Musiqa Bot (python-telegram-bot v20+, async) — BITTA FAYLDA

Ishga tushirish:
    export BOT_TOKEN="123456:ABC..."
    export ADMIN_IDS="123456789"
    python bot.py

Talab qilinadigan tizim dasturi: ffmpeg (PATH orqali chaqiriladi)
Kerakli kutubxonalar: requirements.txt
"""

# ═══════════════════════════════════════════════════════════════════════
# IMPORTLAR
# ═══════════════════════════════════════════════════════════════════════
import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import yt_dlp
import aiosqlite
from pydub import AudioSegment
from shazamio import Shazam
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, APIC
from mutagen.mp3 import MP3

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultAudio,
)
from telegram.constants import ChatAction
from telegram.error import Forbidden, BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("music_bot")



# ═════════════════════════════════════════════════════════════════════════
# SOZLAMALAR (config)
# ═════════════════════════════════════════════════════════════════════════

import os
import logging

# ── Bot tokeni (Render/PythonAnywhere Environment Variables bo'limida o'rnatiladi) ──
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ── Bot username (inline natijalarda va linklarda ishlatiladi, @ belgisiz) ──
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")

# ── Admin panel: admin foydalanuvchilarning Telegram user_id'lari, vergul bilan ──
# Masalan: ADMIN_IDS="123456789,987654321"
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

# ── SQLite baza fayli (foydalanuvchilar, statistika, sozlamalar) ──
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "bot_database.db"))

# ── Broadcast xabar yuborishda floodni oldini olish uchun kechikish (soniya) ──
BROADCAST_DELAY = float(os.getenv("BROADCAST_DELAY", "0.05"))

# ── Vaqtinchalik fayllar papkasi ──
TEMP_DIR = os.getenv("TEMP_DIR", os.path.join(os.path.dirname(__file__), "temp"))
os.makedirs(TEMP_DIR, exist_ok=True)

# ── Yuklab olinadigan audio sifat sozlamalari (yt-dlp) ──
AUDIO_QUALITY = "192"  # kbps

# ── Demucs modeli (vokal ajratish uchun). "htdemucs" tezroq, sifatli. ──
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs")

# ── Har bir foydalanuvchi uchun bitta vaqtda nechta og'ir jarayon (demucs) ruxsat etiladi ──
MAX_CONCURRENT_HEAVY_TASKS = int(os.getenv("MAX_CONCURRENT_HEAVY_TASKS", "1"))

# ── Fayl hajmi chegarasi (Telegram bot API — 50MB, agar local Bot API server bo'lmasa) ──
MAX_FILE_SIZE_MB = 50

# ── Logging ──
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("music_bot")

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN topilmadi! Render/PythonAnywhere Environment Variables bo'limida o'rnating.")

if not ADMIN_IDS:
    logger.warning("ADMIN_IDS o'rnatilmagan! Admin panelga hech kim kira olmaydi. "
                    "Masalan: ADMIN_IDS=123456789")


# ═════════════════════════════════════════════════════════════════════════
# YORDAMCHI FUNKSIYALAR (utils)
# ═════════════════════════════════════════════════════════════════════════

import os
import uuid
import time
import asyncio
from typing import Dict, Optional


# token -> {"path": str, "title": str, "performer": str, "created": float}
# Katta yuklama bo'lsa buni Redis/SQLite bilan almashtirish tavsiya etiladi.
_SESSION_STORE: Dict[str, dict] = {}
_SESSION_TTL_SECONDS = 60 * 60  # 1 soat


def new_temp_path(suffix: str = ".mp3") -> str:
    """Yagona vaqtinchalik fayl yo'lini qaytaradi (mavjud emas)."""
    filename = f"temp_{uuid.uuid4().hex}{suffix}"
    return os.path.join(TEMP_DIR, filename)


def register_session(path: str, title: str = "", performer: str = "") -> str:
    """Fayl yo'lini token bilan bog'laydi — callback_data 64 baytdan oshmasligi uchun."""
    token = uuid.uuid4().hex[:12]
    _SESSION_STORE[token] = {
        "path": path,
        "title": title,
        "performer": performer,
        "created": time.time(),
    }
    return token


def get_session(token: str) -> Optional[dict]:
    _cleanup_expired_sessions()
    return _SESSION_STORE.get(token)


def update_session(token: str, **kwargs) -> None:
    if token in _SESSION_STORE:
        _SESSION_STORE[token].update(kwargs)


def _cleanup_expired_sessions() -> None:
    now = time.time()
    expired = [t for t, v in _SESSION_STORE.items() if now - v["created"] > _SESSION_TTL_SECONDS]
    for t in expired:
        _SESSION_STORE.pop(t, None)


async def safe_delete(*paths: str) -> None:
    """Vaqtinchalik fayl(lar)ni xatolik chiqarmasdan o'chiradi (xotirani tejash uchun)."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                await asyncio.to_thread(os.remove, path)
        except Exception as e:
            logger.warning(f"Faylni o'chirib bo'lmadi: {path} — {e}")


def cleanup_old_temp_files(max_age_seconds: int = 3600) -> None:
    """Eskirib qolgan temp_*.* fayllarni papkadan tozalaydi (server ishga tushganda chaqiriladi)."""
    now = time.time()
    try:
        for fname in os.listdir(TEMP_DIR):
            if not fname.startswith("temp_"):
                continue
            fpath = os.path.join(TEMP_DIR, fname)
            try:
                if now - os.path.getmtime(fpath) > max_age_seconds:
                    os.remove(fpath)
            except OSError:
                pass
    except FileNotFoundError:
        pass


# ═════════════════════════════════════════════════════════════════════════
# YUKLAB OLISH — yt-dlp (downloader)
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import os
import re
from dataclasses import dataclass
from typing import List, Optional

import yt_dlp


YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be)", re.I)
SOUNDCLOUD_RE = re.compile(r"soundcloud\.com", re.I)
SPOTIFY_RE = re.compile(r"open\.spotify\.com", re.I)
URL_RE = re.compile(r"https?://\S+")


@dataclass
class TrackInfo:
    title: str
    uploader: str
    duration: Optional[int]
    webpage_url: str
    thumbnail: Optional[str]


def _base_ydl_opts(outtmpl: str) -> dict:
    return {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": AUDIO_QUALITY,
            },
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
        "writethumbnail": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "default_search": "ytsearch",
    }


def _sync_search(query: str, limit: int = 8) -> List[TrackInfo]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "default_search": "ytsearch",
        "noplaylist": True,
    }
    results: List[TrackInfo] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = info.get("entries", []) if info else []
        for e in entries:
            if not e:
                continue
            results.append(
                TrackInfo(
                    title=e.get("title") or "Nomsiz",
                    uploader=e.get("uploader") or e.get("channel") or "Noma'lum",
                    duration=e.get("duration"),
                    webpage_url=e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                    thumbnail=e.get("thumbnail"),
                )
            )
    return results


async def search_tracks(query: str, limit: int = 8) -> List[TrackInfo]:
    """Berilgan matn bo'yicha YouTube'dan qo'shiqlar ro'yxatini qaytaradi."""
    return await asyncio.to_thread(_sync_search, query, limit)


def _resolve_spotify_query(url: str) -> str:
    """Spotify linkidan trek nomi/ijrochisini olib, YouTube qidiruv so'roviga aylantiradi."""
    try:
        import requests  # yengil, faqat oEmbed uchun
        resp = requests.get(f"https://open.spotify.com/oembed?url={url}", timeout=10)
        if resp.ok:
            title = resp.json().get("title", "")
            if title:
                return title
    except Exception as e:
        logger.warning(f"Spotify oEmbed xatosi: {e}")
    return url


def _sync_download(query_or_url: str, out_base: str) -> dict:
    target = query_or_url
    if SPOTIFY_RE.search(query_or_url):
        target = _resolve_spotify_query(query_or_url)
        target = f"ytsearch1:{target}"
    elif not URL_RE.match(query_or_url):
        target = f"ytsearch1:{query_or_url}"

    opts = _base_ydl_opts(out_base + ".%(ext)s")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=True)
        if "entries" in info:  # ytsearch natijasi ro'yxat qaytaradi
            info = info["entries"][0]
        mp3_path = out_base + ".mp3"
        return {
            "path": mp3_path,
            "title": info.get("title") or "Nomsiz",
            "uploader": info.get("uploader") or info.get("channel") or "Noma'lum",
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
        }


async def download_audio(query_or_url: str) -> dict:
    """
    Matn, YouTube/SoundCloud linki yoki Spotify linkidan mp3 yuklab oladi.
    Qaytaradi: {"path", "title", "uploader", "duration", "thumbnail"}
    """
    out_base = os.path.join(TEMP_DIR, f"temp_{os.urandom(6).hex()}")
    return await asyncio.to_thread(_sync_download, query_or_url, out_base)


def is_supported_link(text: str) -> bool:
    return bool(YOUTUBE_RE.search(text) or SOUNDCLOUD_RE.search(text) or SPOTIFY_RE.search(text))


# ═════════════════════════════════════════════════════════════════════════
# SHAZAM — OVOZDAN QO'SHIQ ANIQLASH
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import subprocess
from typing import Optional

from shazamio import Shazam



def _to_wav(src_path: str, dst_path: str) -> None:
    """ffmpeg orqali istalgan audio/video faylni Shazam uchun qulay wav formatga o'tkazadi."""
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ac", "1", "-ar", "44100", "-t", "20",  # Shazam uchun 15-20 soniya yetarli
        dst_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def recognize_song(file_path: str) -> Optional[dict]:
    """
    Berilgan audio/video fayldan qo'shiqni aniqlaydi.
    Qaytaradi: {"title", "artist"} yoki topilmasa None.
    """
    wav_path = file_path + "__shazam.wav"
    try:
        await asyncio.to_thread(_to_wav, file_path, wav_path)
        shazam = Shazam()
        result = await shazam.recognize(wav_path)
        track = result.get("track")
        if not track:
            return None
        return {
            "title": track.get("title", "Noma'lum"),
            "artist": track.get("subtitle", "Noma'lum ijrochi"),
        }
    except Exception as e:
        logger.error(f"Shazam aniqlashda xatolik: {e}")
        return None
    finally:
        import os
        if os.path.exists(wav_path):
            os.remove(wav_path)


# ═════════════════════════════════════════════════════════════════════════
# AUDIO EFFEKTLARI (8D / Concert Hall / Slowed+Reverb)
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import subprocess

import numpy as np
from pydub import AudioSegment



def _run_ffmpeg(args: list) -> None:
    cmd = ["ffmpeg", "-y", *args]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg xatosi: {result.stderr.decode(errors='ignore')[:500]}")


# ───────────────────────── 8D AUDIO ─────────────────────────
def _sync_apply_8d(src_path: str, dst_path: str, period_seconds: float = 8.0) -> str:
    """
    Stereo kanallar orasida sinusoidal panning qo'llab, boshda aylanayotgandek
    hissiyot beruvchi 8D effektini yaratadi.
    """
    audio = AudioSegment.from_file(src_path).set_channels(2)
    sample_rate = audio.frame_rate
    samples = np.array(audio.get_array_of_samples()).astype(np.float32)
    samples = samples.reshape((-1, 2))  # [N, 2] -> left, right

    n = samples.shape[0]
    t = np.arange(n) / sample_rate
    # 0..2*pi davriy panning burchagi
    angle = 2 * np.pi * t / period_seconds
    # -1 (to'liq chap) ... +1 (to'liq o'ng) oralig'ida pan pozitsiyasi
    pan = np.sin(angle)

    left_gain = np.clip(1.0 - pan, 0, 1) * 0.5 + 0.5   # 0.5..1.0..0.5
    right_gain = np.clip(1.0 + pan, 0, 1) * 0.5 + 0.5

    # Engil "aylanish" hissi uchun teskari kanaldan ozgina sizib o'tish (bleed)
    bleed = 0.15
    left = samples[:, 0] * left_gain + samples[:, 1] * bleed * (1 - left_gain)
    right = samples[:, 1] * right_gain + samples[:, 0] * bleed * (1 - right_gain)

    stereo = np.empty((n, 2), dtype=np.float32)
    stereo[:, 0] = left
    stereo[:, 1] = right
    stereo = np.clip(stereo, -32768, 32767).astype(np.int16)

    processed = AudioSegment(
        stereo.tobytes(),
        frame_rate=sample_rate,
        sample_width=2,
        channels=2,
    )
    # Yengil reverb qo'shib, effektni "xonaga o'xshash" qilamiz (ixtiyoriy, ffmpeg orqali)
    processed.export(dst_path, format="mp3", bitrate="192k")
    return dst_path


async def apply_8d(src_path: str) -> str:
    dst_path = new_temp_path("_8d.mp3")
    return await asyncio.to_thread(_sync_apply_8d, src_path, dst_path)


# ───────────────────────── CONCERT HALL (REVERB) ─────────────────────────
def _sync_apply_concert_hall(src_path: str, dst_path: str) -> str:
    # aecho=in_gain:out_gain:delays(ms):decays  — bir nechta delay/decay qo'shilib, katta zal hissi beriladi
    filter_chain = "aecho=0.8:0.88:60|150|350:0.45|0.28|0.18,aecho=0.6:0.7:900:0.15"
    _run_ffmpeg(["-i", src_path, "-af", filter_chain, "-b:a", "192k", dst_path])
    return dst_path


async def apply_concert_hall(src_path: str) -> str:
    dst_path = new_temp_path("_concert.mp3")
    return await asyncio.to_thread(_sync_apply_concert_hall, src_path, dst_path)


# ───────────────────────── SLOWED + REVERB ─────────────────────────
def _sync_apply_slowed_reverb(src_path: str, dst_path: str, speed: float = 0.82) -> str:
    """
    asetrate orqali sample-rate pasaytiriladi -> ham tempo, ham ton (pitch) birga pasayadi
    (klassik "slowed" effekti), so'ng aresample bilan standart chastotaga qaytariladi
    va ustiga yengil reverb qo'shiladi.
    """
    audio = AudioSegment.from_file(src_path)
    base_rate = audio.frame_rate
    new_rate = int(base_rate * speed)

    filter_chain = (
        f"asetrate={new_rate},aresample={base_rate},"
        "aecho=0.7:0.7:40|400:0.35|0.2"
    )
    _run_ffmpeg(["-i", src_path, "-af", filter_chain, "-b:a", "192k", dst_path])
    return dst_path


async def apply_slowed_reverb(src_path: str) -> str:
    dst_path = new_temp_path("_slowed.mp3")
    return await asyncio.to_thread(_sync_apply_slowed_reverb, src_path, dst_path)


# ═════════════════════════════════════════════════════════════════════════
# VOKAL AJRATISH — Demucs AI (Minus)
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import os
import shutil
import subprocess



def _sync_separate(src_path: str) -> str:
    """
    `python -m demucs --two-stems=vocals` orqali audio ajratadi,
    natijadagi no_vocals.wav faylini mp3'ga o'girib qaytaradi.
    """
    out_dir = os.path.join(TEMP_DIR, f"demucs_out_{os.urandom(4).hex()}")
    os.makedirs(out_dir, exist_ok=True)
    try:
        cmd = [
            "python", "-m", "demucs",
            "--two-stems", "vocals",
            "-n", DEMUCS_MODEL,
            "-o", out_dir,
            src_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
        if result.returncode != 0:
            raise RuntimeError(f"Demucs xatosi: {result.stderr.decode(errors='ignore')[:800]}")

        # Demucs natijani <out_dir>/<model_name>/<track_name>/no_vocals.wav ga yozadi
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        no_vocals_wav = os.path.join(out_dir, DEMUCS_MODEL, base_name, "no_vocals.wav")
        if not os.path.exists(no_vocals_wav):
            raise FileNotFoundError("Demucs natija fayli topilmadi.")

        dst_mp3 = new_temp_path("_minus.mp3")
        conv = subprocess.run(
            ["ffmpeg", "-y", "-i", no_vocals_wav, "-b:a", "192k", dst_mp3],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if conv.returncode != 0:
            raise RuntimeError("Natijani mp3'ga o'tkazishda xatolik.")
        return dst_mp3
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


async def remove_vocals(src_path: str) -> str:
    """Qo'shiqning instrumental (minus) versiyasini qaytaradi. Og'ir jarayon — vaqt talab qiladi."""
    return await asyncio.to_thread(_sync_separate, src_path)


# ═════════════════════════════════════════════════════════════════════════
# MP3 -> TELEGRAM VOICE KONVERTATSIYASI
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import subprocess



def _sync_convert_to_voice(src_path: str, dst_path: str) -> str:
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "64k",
        dst_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"Voice konvertatsiya xatosi: {result.stderr.decode(errors='ignore')[:500]}")
    return dst_path


async def convert_to_voice(src_path: str) -> str:
    dst_path = new_temp_path(".ogg")
    return await asyncio.to_thread(_sync_convert_to_voice, src_path, dst_path)


# ═════════════════════════════════════════════════════════════════════════
# METADATA TAHRIRLASH (Title / Performer / Thumbnail)
# ═════════════════════════════════════════════════════════════════════════

import asyncio
from typing import Optional

from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, APIC
from mutagen.mp3 import MP3


def _sync_update_metadata(
    path: str,
    title: Optional[str] = None,
    performer: Optional[str] = None,
    thumbnail_bytes: Optional[bytes] = None,
) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    if title:
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    if performer:
        tags.setall("TPE1", [TPE1(encoding=3, text=performer)])
    if thumbnail_bytes:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=thumbnail_bytes))

    tags.save(path)


async def update_metadata(
    path: str,
    title: Optional[str] = None,
    performer: Optional[str] = None,
    thumbnail_bytes: Optional[bytes] = None,
) -> None:
    await asyncio.to_thread(_sync_update_metadata, path, title, performer, thumbnail_bytes)


def get_duration_seconds(path: str) -> int:
    try:
        return int(MP3(path).info.length)
    except Exception:
        return 0


# ═════════════════════════════════════════════════════════════════════════
# INLINE TUGMALAR (keyboards)
# ═════════════════════════════════════════════════════════════════════════

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def audio_actions_keyboard(token: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🎧 8D", callback_data=f"fx8d:{token}"),
            InlineKeyboardButton("🏟 Concert Hall", callback_data=f"fxconcert:{token}"),
        ],
        [
            InlineKeyboardButton("🐢 Slowed", callback_data=f"fxslowed:{token}"),
            InlineKeyboardButton("🎤 Minus", callback_data=f"fxminus:{token}"),
        ],
        [
            InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"edit:{token}"),
            InlineKeyboardButton("🎙 Ovozli xabar qilish", callback_data=f"voice:{token}"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def edit_menu_keyboard(token: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📝 Nomini o'zgartirish", callback_data=f"edit_title:{token}")],
        [InlineKeyboardButton("🎙 Ijrochini o'zgartirish", callback_data=f"edit_performer:{token}")],
        [InlineKeyboardButton("🖼 Muqovani o'zgartirish", callback_data=f"edit_thumb:{token}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data=f"back:{token}")],
    ]
    return InlineKeyboardMarkup(buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_edit")]])


# ═════════════════════════════════════════════════════════════════════════
# MA'LUMOTLAR BAZASI — SQLite (aiosqlite)
# ═════════════════════════════════════════════════════════════════════════

import time
from typing import Optional, List, Dict

import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at INTEGER,
    last_active INTEGER,
    is_banned INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS actions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await executescript(_SCHEMA)
        await commit()
    logger.info(f"Baza tayyor: {DB_PATH}")


# ───────────────────────── USERS ─────────────────────────
async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await execute(
            """
            INSERT INTO users (user_id, username, first_name, joined_at, last_active, is_banned)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_active=excluded.last_active
            """,
            (user_id, username, first_name, now, now),
        )
        await commit()


async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row[0])


async def set_banned(user_id: int, banned: bool) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not await cur.fetchone():
            return False
        await execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, user_id))
        await commit()
        return True


async def get_user(user_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_user_ids() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT user_id FROM users WHERE is_banned=0")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def get_recent_users(limit: int = 10) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT COUNT(*) FROM users")
        return (await cur.fetchone())[0]


async def count_banned() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT COUNT(*) FROM users WHERE is_banned=1")
        return (await cur.fetchone())[0]


async def count_active_since(seconds_ago: int) -> int:
    since = int(time.time()) - seconds_ago
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (since,))
        return (await cur.fetchone())[0]


# ───────────────────────── ACTIONS LOG ─────────────────────────
async def log_action(user_id: int, action: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await execute(
            "INSERT INTO actions_log (user_id, action, created_at) VALUES (?, ?, ?)",
            (user_id, action, int(time.time())),
        )
        await commit()


async def count_action(action: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT COUNT(*) FROM actions_log WHERE action=?", (action,))
        return (await cur.fetchone())[0]


async def get_action_breakdown() -> Dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT action, COUNT(*) FROM actions_log GROUP BY action")
        rows = await cur.fetchall()
        return {a: c for a, c in rows}


async def count_actions_since(seconds_ago: int) -> int:
    since = int(time.time()) - seconds_ago
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT COUNT(*) FROM actions_log WHERE created_at >= ?", (since,))
        return (await cur.fetchone())[0]


# ───────────────────────── SETTINGS ─────────────────────────
async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await commit()


async def is_maintenance_mode() -> bool:
    val = await get_setting("maintenance_mode", "0")
    return val == "1"


async def get_force_sub_channel() -> Optional[str]:
    return await get_setting("force_sub_channel", "")


# ═════════════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, BadRequest
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)


# ConversationHandler holatlari
BROADCAST_WAIT, BAN_WAIT, UNBAN_WAIT, USERINFO_WAIT, FORCESUB_WAIT = range(5)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("⛔ Bu buyruq faqat adminlar uchun.")
            elif update.callback_query:
                await update.callback_query.answer("⛔ Ruxsat yo'q.", show_alert=True)
            return ConversationHandler.END
        return await func(update, context, *a, **kw)
    return wrapper


# ───────────────────────── ASOSIY MENYU ─────────────────────────
def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton("📢 Xabar yuborish (Broadcast)", callback_data="adm_broadcast")],
        [
            InlineKeyboardButton("🚫 Bloklash", callback_data="adm_ban"),
            InlineKeyboardButton("✅ Blokdan chiqarish", callback_data="adm_unban"),
        ],
        [InlineKeyboardButton("👤 Foydalanuvchi ma'lumoti", callback_data="adm_userinfo")],
        [InlineKeyboardButton("🕓 So'nggi foydalanuvchilar", callback_data="adm_recent")],
        [InlineKeyboardButton("🔧 Texnik ishlar rejimi", callback_data="adm_maintenance")],
        [InlineKeyboardButton("📌 Majburiy obuna kanali", callback_data="adm_forcesub")],
    ])


@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 <b>Admin panel</b>", parse_mode="HTML", reply_markup=_main_menu_keyboard())


async def admin_menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("🛠 <b>Admin panel</b>", parse_mode="HTML", reply_markup=_main_menu_keyboard())
    return ConversationHandler.END


# ───────────────────────── STATISTIKA ─────────────────────────
@admin_only
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    total_users = await count_users()
    banned = await count_banned()
    active_24h = await count_active_since(24 * 3600)
    active_7d = await count_active_since(7 * 24 * 3600)
    actions_24h = await count_actions_since(24 * 3600)
    breakdown = await get_action_breakdown()

    breakdown_lines = "\n".join(
        f"   • {k}: <b>{v}</b>" for k, v in sorted(breakdown.items(), key=lambda x: -x[1])
    ) or "   — ma'lumot yo'q"

    text = (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"🚫 Bloklangan: <b>{banned}</b>\n"
        f"🟢 Faol (24 soat): <b>{active_24h}</b>\n"
        f"🟢 Faol (7 kun): <b>{active_7d}</b>\n"
        f"⚡️ Harakatlar (24 soat): <b>{actions_24h}</b>\n\n"
        f"📈 <b>Harakatlar taqsimoti (umumiy):</b>\n{breakdown_lines}"
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="adm_back")]])
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


# ───────────────────────── BROADCAST ─────────────────────────
@admin_only
async def admin_broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "📢 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring "
        "(matn, rasm, video yoki audio bo'lishi mumkin).\n\n/cancel — bekor qilish",
    )
    return BROADCAST_WAIT


@admin_only
async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_message = update.message
    user_ids = await get_all_user_ids()

    status = await update.message.reply_text(f"📤 Yuborilmoqda: 0/{len(user_ids)}")
    success, failed = 0, 0

    for i, uid in enumerate(user_ids, start=1):
        try:
            await source_message.copy(chat_id=uid)
            success += 1
        except Forbidden:
            failed += 1  # foydalanuvchi botni bloklagan
        except BadRequest:
            failed += 1
        except Exception as e:
            logger.warning(f"Broadcast xatosi ({uid}): {e}")
            failed += 1

        if i % 25 == 0 or i == len(user_ids):
            try:
                await status.edit_text(f"📤 Yuborilmoqda: {i}/{len(user_ids)} (✅ {success} / ❌ {failed})")
            except Exception:
                pass
        await asyncio.sleep(BROADCAST_DELAY)

    await status.edit_text(
        f"✅ Broadcast yakunlandi.\n\n👥 Jami: {len(user_ids)}\n✅ Yuborildi: {success}\n❌ Yetib bormadi: {failed}"
    )
    return ConversationHandler.END


# ───────────────────────── BAN / UNBAN ─────────────────────────
@admin_only
async def admin_ban_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("🚫 Bloklamoqchi bo'lgan foydalanuvchining ID raqamini yuboring.\n\n/cancel — bekor qilish")
    return BAN_WAIT


@admin_only
async def admin_ban_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Iltimos, faqat raqamli ID yuboring.")
        return BAN_WAIT
    uid = int(text)
    ok = await set_banned(uid, True)
    if ok:
        await update.message.reply_text(f"✅ Foydalanuvchi {uid} bloklandi.")
    else:
        await update.message.reply_text("❌ Bunday foydalanuvchi bazada topilmadi.")
    return ConversationHandler.END


@admin_only
async def admin_unban_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("✅ Blokdan chiqarmoqchi bo'lgan foydalanuvchi ID raqamini yuboring.\n\n/cancel — bekor qilish")
    return UNBAN_WAIT


@admin_only
async def admin_unban_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Iltimos, faqat raqamli ID yuboring.")
        return UNBAN_WAIT
    uid = int(text)
    ok = await set_banned(uid, False)
    if ok:
        await update.message.reply_text(f"✅ Foydalanuvchi {uid} blokdan chiqarildi.")
    else:
        await update.message.reply_text("❌ Bunday foydalanuvchi bazada topilmadi.")
    return ConversationHandler.END


# ───────────────────────── FOYDALANUVCHI MA'LUMOTI ─────────────────────────
@admin_only
async def admin_userinfo_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("👤 Foydalanuvchi ID raqamini yuboring.\n\n/cancel — bekor qilish")
    return USERINFO_WAIT


@admin_only
async def admin_userinfo_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Iltimos, faqat raqamli ID yuboring.")
        return USERINFO_WAIT
    uid = int(text)
    user = await get_user(uid)
    if not user:
        await update.message.reply_text("❌ Bunday foydalanuvchi topilmadi.")
        return ConversationHandler.END

    joined = time.strftime("%Y-%m-%d %H:%M", time.localtime(user["joined_at"]))
    last_active = time.strftime("%Y-%m-%d %H:%M", time.localtime(user["last_active"]))
    status = "🚫 Bloklangan" if user["is_banned"] else "✅ Faol"

    await update.message.reply_text(
        f"👤 <b>Foydalanuvchi ma'lumoti</b>\n\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Username: @{user['username'] or '—'}\n"
        f"Ism: {user['first_name'] or '—'}\n"
        f"Ro'yxatdan o'tgan: {joined}\n"
        f"Oxirgi faollik: {last_active}\n"
        f"Holati: {status}",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ───────────────────────── SO'NGGI FOYDALANUVCHILAR ─────────────────────────
@admin_only
async def admin_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = await get_recent_users(10)
    if not users:
        text = "Hozircha foydalanuvchilar yo'q."
    else:
        lines = []
        for u in users:
            uname = f"@{u['username']}" if u["username"] else u["first_name"] or "—"
            joined = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["joined_at"]))
            lines.append(f"• <code>{u['user_id']}</code> — {uname} ({joined})")
        text = "🕓 <b>So'nggi 10 foydalanuvchi:</b>\n\n" + "\n".join(lines)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="adm_back")]])
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


# ───────────────────────── TEXNIK ISHLAR REJIMI ─────────────────────────
@admin_only
async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    current = await is_maintenance_mode()
    new_value = "0" if current else "1"
    await set_setting("maintenance_mode", new_value)
    status_label = "✅ YOQILDI" if new_value == "1" else "❌ OCHIRILDI"
    await query.answer(f"Texnik ishlar rejimi: {status_label}", show_alert=True)

    state_text = "🟠 Yoqilgan (faqat adminlar foydalana oladi)" if new_value == "1" else "🟢 O'chirilgan (bot hammaga ochiq)"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="adm_back")]])
    await query.message.edit_text(f"🔧 <b>Texnik ishlar rejimi</b>\n\nHozirgi holat: {state_text}", parse_mode="HTML", reply_markup=keyboard)


# ───────────────────────── MAJBURIY OBUNA ─────────────────────────
@admin_only
async def admin_forcesub_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = await get_force_sub_channel()
    current_text = current if current else "o'rnatilmagan"
    await query.message.edit_text(
        f"📌 Hozirgi majburiy obuna kanali: <b>{current_text}</b>\n\n"
        "Yangi kanal username'ini yuboring (masalan: @mychannel).\n"
        "O'chirish uchun: <code>off</code> deb yozing.\n\n/cancel — bekor qilish",
        parse_mode="HTML",
    )
    return FORCESUB_WAIT


@admin_only
async def admin_forcesub_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "off":
        await set_setting("force_sub_channel", "")
        await update.message.reply_text("✅ Majburiy obuna o'chirildi.")
        return ConversationHandler.END

    if not text.startswith("@"):
        await update.message.reply_text("❌ Kanal username'i @ bilan boshlanishi kerak. Masalan: @mychannel")
        return FORCESUB_WAIT

    # Bot kanalda admin ekanligini tekshirib ko'ramiz
    try:
        member = await context.bot.get_chat_member(text, context.bot.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "⚠️ Bot ushbu kanalda admin emas. Iltimos, botni kanalga admin qilib qo'shing, "
                "aks holda a'zolikni tekshira olmayman."
            )
            return FORCESUB_WAIT
    except Exception as e:
        await update.message.reply_text(f"❌ Kanalni tekshirib bo'lmadi: {e}\nUsername to'g'riligiga ishonch hosil qiling.")
        return FORCESUB_WAIT

    await set_setting("force_sub_channel", text)
    await update.message.reply_text(f"✅ Majburiy obuna kanali o'rnatildi: {text}")
    return ConversationHandler.END


# ───────────────────────── CANCEL ─────────────────────────
async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=_main_menu_keyboard())
    return ConversationHandler.END


# ───────────────────────── HANDLERLARNI RO'YXATGA OLISH ─────────────────────────
def register_admin_handlers(app):
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^adm_stats$"))
    app.add_handler(CallbackQueryHandler(admin_recent, pattern="^adm_recent$"))
    app.add_handler(CallbackQueryHandler(admin_maintenance_toggle, pattern="^adm_maintenance$"))
    app.add_handler(CallbackQueryHandler(admin_menu_back, pattern="^adm_back$"))

    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_entry, pattern="^adm_broadcast$")],
        states={BROADCAST_WAIT: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_broadcast_send)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False,
    )
    ban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_entry, pattern="^adm_ban$")],
        states={BAN_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_apply)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False,
    )
    unban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_entry, pattern="^adm_unban$")],
        states={UNBAN_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_apply)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False,
    )
    userinfo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_userinfo_entry, pattern="^adm_userinfo$")],
        states={USERINFO_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_userinfo_show)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False,
    )
    forcesub_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_forcesub_entry, pattern="^adm_forcesub$")],
        states={FORCESUB_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_forcesub_apply)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False,
    )

    for conv in (broadcast_conv, ban_conv, unban_conv, userinfo_conv, forcesub_conv):
        app.add_handler(conv)


# ═════════════════════════════════════════════════════════════════════════
# MIDDLEWARE — ban / texnik ishlar / majburiy obuna tekshiruvi
# ═════════════════════════════════════════════════════════════════════════

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ApplicationHandlerStop
from telegram.error import BadRequest



async def track_and_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.is_bot:
        return

    # 1) Foydalanuvchini bazaga yozish / yangilash
    try:
        await upsert_user(user.id, user.username, user.first_name)
    except Exception as e:
        logger.error(f"Foydalanuvchini bazaga yozishda xatolik: {e}")

    is_admin_user = user.id in ADMIN_IDS

    # 2) Ban tekshiruvi (adminlarga tegmaydi)
    if not is_admin_user and await is_banned(user.id):
        await _reply(update, "🚫 Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        raise ApplicationHandlerStop

    # 3) Texnik ishlar rejimi (adminlarga tegmaydi)
    if not is_admin_user and await is_maintenance_mode():
        await _reply(update, "🛠 Bot hozirda texnik ishlar tufayli vaqtincha ishlamayapti. Birozdan so'ng qayta urinib ko'ring.")
        raise ApplicationHandlerStop

    # 4) Majburiy obuna (adminlarga tegmaydi)
    if not is_admin_user:
        channel = await get_force_sub_channel()
        if channel:
            subscribed = await _check_subscription(context, channel, user.id)
            if not subscribed:
                await _send_force_sub_prompt(update, channel)
                raise ApplicationHandlerStop


async def _reply(update: Update, text: str) -> None:
    try:
        if update.message:
            await update.message.reply_text(text)
        elif update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
    except Exception:
        pass


async def _check_subscription(context: ContextTypes.DEFAULT_TYPE, channel: str, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except BadRequest:
        # Foydalanuvchi kanalga umuman a'zo bo'lmagan yoki kanal topilmadi
        return False
    except Exception as e:
        logger.warning(f"Obuna tekshiruvida xatolik: {e}")
        # Xatolik bo'lsa, foydalanuvchini bloklamaslik (fail-open) — UX buzilmasligi uchun
        return True


async def _send_force_sub_prompt(update: Update, channel: str) -> None:
    channel_url = f"https://t.me/{channel.lstrip('@')}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kanalga o'tish", url=channel_url)],
        [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_subscription")],
    ])
    text = (
        "⚠️ Botdan foydalanish uchun avval quyidagi kanalga obuna bo'ling, "
        "so'ng \"✅ A'zo bo'ldim\" tugmasini bosing."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)


async def handle_check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    channel = await get_force_sub_channel()
    if not channel:
        await query.answer("✅ Endi botdan foydalanishingiz mumkin.", show_alert=True)
        await query.message.delete()
        return

    subscribed = await _check_subscription(context, channel, query.from_user.id)
    if subscribed:
        await query.answer("✅ Rahmat! Endi botdan foydalanishingiz mumkin.", show_alert=True)
        await query.message.delete()
    else:
        await query.answer("❌ Siz hali kanalga obuna bo'lmagansiz.", show_alert=True)


# ═════════════════════════════════════════════════════════════════════════
# ASOSIY BOT LOGIKASI VA HANDLERLAR
# ═════════════════════════════════════════════════════════════════════════

import asyncio
import os
import uuid

from telegram import (
    Update,
    InlineQueryResultAudio,
    InputMediaAudio,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    InlineQueryHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)



# ConversationHandler holatlari (metadata tahrirlash uchun)
ASK_TITLE, ASK_PERFORMER, ASK_THUMB = range(3)


# ─────────────────────────────────────────────────────────────
# Umumiy yordamchi: audio yuborish + tugmalarni biriktirish
# ─────────────────────────────────────────────────────────────
async def send_audio_with_actions(update_or_query, context: ContextTypes.DEFAULT_TYPE, path: str, title: str, performer: str):
    token = register_session(path, title=title, performer=performer)
    duration = get_duration_seconds(path)

    chat = update_or_query.effective_chat if hasattr(update_or_query, "effective_chat") else update_or_query.message.chat
    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_VOICE)

    with open(path, "rb") as f:
        await context.bot.send_audio(
            chat_id=chat.id,
            audio=f,
            title=title,
            performer=performer,
            duration=duration if duration else None,
            reply_markup=audio_actions_keyboard(token),
        )
    return token


# ─────────────────────────────────────────────────────────────
# /start va /help
# ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎵 Assalomu alaykum! Men musiqa botiman.\n\n"
        "• Qo'shiq nomi, ijrochi yoki YouTube/SoundCloud/Spotify havolasini yuboring — men uni yuklab beraman.\n"
        "• Ovozli xabar yoki qisqa video/audio yuboring — men qo'shiqni Shazam orqali topib beraman.\n"
        "• Istalgan chatda @bot_username qo'shiq_nomi deb yozing — inline qidiruv ishlaydi.\n\n"
        "Har bir audio ostida effektlar mavjud: 8D, Concert Hall, Slowed, Minus (vokalsiz), "
        "tahrirlash va ovozli xabarga aylantirish."
    )
    if update.effective_user.id in ADMIN_IDS:
        text += "\n\n🛠 Siz adminsiz — admin panelga kirish uchun /admin buyrug'ini yuboring."
    await update.message.reply_text(text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ─────────────────────────────────────────────────────────────
# Matn xabar: link yoki qidiruv so'rovi
# ─────────────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return

    status_msg = await update.message.reply_text("🔎 Qidirilmoqda va yuklab olinmoqda...")
    try:
        result = await download_audio(query)
        token = await send_audio_with_actions(update, context, result["path"], result["title"], result["uploader"])
        await log_action(update.effective_user.id, "download")
        # Fayl Telegram serveriga yuklangach, lokal nusxa endi kerak emas emas —
        # lekin effekt tugmalari uchun uni saqlab turamiz (session TTL bilan avtomatik tozalanadi).
    except Exception as e:
        logger.error(f"Yuklab olishda xatolik: {e}")
        await update.message.reply_text(
            "❌ Kechirasiz, ushbu so'rov bo'yicha audio topib/yuklab bo'lmadi. "
            "Boshqa nom bilan urinib ko'ring yoki to'g'ridan-to'g'ri havola yuboring."
        )
    finally:
        await status_msg.delete()


# ─────────────────────────────────────────────────────────────
# Voice / Audio / Video xabar: Shazam orqali aniqlash
# ─────────────────────────────────────────────────────────────
async def handle_media_for_shazam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.voice or msg.audio or msg.video_note or msg.video
    if not media:
        return

    status_msg = await update.message.reply_text("🎙 Qo'shiq aniqlanmoqda (Shazam)...")
    local_path = new_temp_path(".ogg" if msg.voice else ".mp4")
    try:
        tg_file = await context.bot.get_file(media.file_id)
        await tg_file.download_to_drive(local_path)

        recognized = await recognize_song(local_path)
        if not recognized:
            await status_msg.edit_text("❌ Afsuski, qo'shiqni aniqlay olmadim. Aniqroq/tinchroq audio bilan urinib ko'ring.")
            return

        await status_msg.edit_text(f"✅ Topildi: {recognized['title']} — {recognized['artist']}\n⬇️ Yuklanmoqda...")
        query = f"{recognized['title']} {recognized['artist']}"
        result = await download_audio(query)
        await send_audio_with_actions(update, context, result["path"], result["title"], result["uploader"])
        await log_action(update.effective_user.id, "shazam")
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Shazam/yuklashda xatolik: {e}")
        await status_msg.edit_text("❌ Xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
    finally:
        await safe_delete(local_path)


# ─────────────────────────────────────────────────────────────
# Inline qidiruv: @bot_username qo'shiq_nomi
# ─────────────────────────────────────────────────────────────
async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if not query or len(query) < 2:
        return

    try:
        tracks = await search_tracks(query, limit=8)
    except Exception as e:
        logger.error(f"Inline qidiruvda xatolik: {e}")
        return

    results = []
    for t in tracks:
        if not t.duration:
            continue
        # Diqqat: Telegram inline audio natijasi to'g'ridan-to'g'ri fayl URL talab qiladi.
        # YouTube o'zining audio-stream URL'i vaqtinchalik bo'lgani uchun, bu yerda
        # foydalanuvchi natijani tanlaganda botga xabar yuborilib, asl yuklab olish/yuborish
        # chosen_inline_result orqali amalga oshiriladi (pastda ko'ring).
        results.append(
            InlineQueryResultAudio(
                id=str(uuid.uuid4()),
                audio_url=t.thumbnail or "https://upload.wikimedia.org/wikipedia/commons/4/47/Music_icon.svg",
                title=t.title,
                performer=t.uploader,
                audio_duration=t.duration,
                input_message_content=None,
            )
        )
    # Eslatma: audio_url sifatida haqiqiy audio stream berish uchun pastdagi
    # "chosen_inline_result_handler" orqali fayl botga yuklanib qayta yuboriladi.
    await update.inline_query.answer(results, cache_time=10, is_personal=True)


async def handle_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi inline natijalardan birini tanlaganda chaqiriladi.
    Bu yerda asl qo'shiqni yt-dlp orqali yuklab, foydalanuvchiga to'g'ridan-to'g'ri xabar orqali yuboramiz
    (chunki Telegram inline audio faqat to'g'ridan-to'g'ri URL bilan ishlaydi, YouTube esa buni bermaydi).
    """
    chosen = update.chosen_inline_result
    query_title = chosen.query
    user_id = chosen.from_user.id
    try:
        result = await download_audio(query_title)
        token = register_session(result["path"], title=result["title"], performer=result["uploader"])
        duration = get_duration_seconds(result["path"])
        with open(result["path"], "rb") as f:
            await context.bot.send_audio(
                chat_id=user_id,
                audio=f,
                title=result["title"],
                performer=result["uploader"],
                duration=duration if duration else None,
                reply_markup=audio_actions_keyboard(token),
            )
    except Exception as e:
        logger.error(f"Inline natija yuborishda xatolik: {e}")


# ─────────────────────────────────────────────────────────────
# Effekt tugmalari (callback query)
# ─────────────────────────────────────────────────────────────
EFFECT_HANDLERS = {
    "fx8d": ("🎧 8D effekt qo'llanmoqda...", apply_8d, "_8d"),
    "fxconcert": ("🏟 Concert Hall effekti qo'llanmoqda...", apply_concert_hall, "_concert"),
    "fxslowed": ("🐢 Slowed + Reverb qo'llanmoqda...", apply_slowed_reverb, "_slowed"),
}


async def handle_effect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, token = query.data.split(":", 1)
    session = get_session(token)
    if not session or not os.path.exists(session["path"]):
        await query.answer("❌ Fayl muddati tugagan. Qo'shiqni qayta yuboring.", show_alert=True)
        return

    await query.answer()
    label, effect_fn, _suffix = EFFECT_HANDLERS[action]
    status = await query.message.reply_text(label)
    processed_path = None
    try:
        processed_path = await effect_fn(session["path"])
        new_token = await send_audio_with_actions(
            update, context, processed_path,
            title=f"{session.get('title','')} [{action.replace('fx','').upper()}]",
            performer=session.get("performer", ""),
        )
        await log_action(update.effective_user.id, action)
    except Exception as e:
        logger.error(f"Effekt xatosi ({action}): {e}")
        await status.edit_text("❌ Effektni qo'llashda xatolik yuz berdi.")
    else:
        await status.delete()
    finally:
        await safe_delete(processed_path)


async def handle_minus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, token = query.data.split(":", 1)
    session = get_session(token)
    if not session or not os.path.exists(session["path"]):
        await query.answer("❌ Fayl muddati tugagan. Qo'shiqni qayta yuboring.", show_alert=True)
        return

    await query.answer()
    status = await query.message.reply_text(
        "🎤 Vokal ajratilmoqda (AI orqali)... Bu jarayon 1-3 daqiqa vaqt olishi mumkin, iltimos kuting."
    )
    processed_path = None
    try:
        processed_path = await remove_vocals(session["path"])
        await send_audio_with_actions(
            update, context, processed_path,
            title=f"{session.get('title','')} [MINUS]",
            performer=session.get("performer", ""),
        )
        await log_action(update.effective_user.id, "fxminus")
    except Exception as e:
        logger.error(f"Minus (vokal ajratish) xatosi: {e}")
        await status.edit_text(
            "❌ Vokalni ajratishda xatolik yuz berdi. Server resurslari yetarli emasligi mumkin."
        )
    else:
        await status.delete()
    finally:
        await safe_delete(processed_path)


async def handle_voice_convert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, token = query.data.split(":", 1)
    session = get_session(token)
    if not session or not os.path.exists(session["path"]):
        await query.answer("❌ Fayl muddati tugagan. Qo'shiqni qayta yuboring.", show_alert=True)
        return

    await query.answer()
    status = await query.message.reply_text("🎙 Ovozli xabarga aylantirilmoqda...")
    voice_path = None
    try:
        voice_path = await convert_to_voice(session["path"])
        with open(voice_path, "rb") as f:
            await context.bot.send_voice(chat_id=query.message.chat.id, voice=f)
        await log_action(query.from_user.id, "voice_convert")
    except Exception as e:
        logger.error(f"Voice konvertatsiya xatosi: {e}")
        await status.edit_text("❌ Ovozli xabarga aylantirishda xatolik yuz berdi.")
    else:
        await status.delete()
    finally:
        await safe_delete(voice_path)


# ─────────────────────────────────────────────────────────────
# Tahrirlash (ConversationHandler): Title / Performer / Thumbnail
# ─────────────────────────────────────────────────────────────
async def handle_edit_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, token = query.data.split(":", 1)
    session = get_session(token)
    if not session or not os.path.exists(session["path"]):
        await query.answer("❌ Fayl muddati tugagan.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    await query.message.reply_text("Nimani tahrirlaysiz?", reply_markup=edit_menu_keyboard(token))
    return ConversationHandler.END  # asosiy menyu ConversationHandlerga kirmaydi, faqat tanlovlar kiradi


async def edit_field_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, token = query.data.split(":", 1)
    session = get_session(token)
    if not session or not os.path.exists(session["path"]):
        await query.answer("❌ Fayl muddati tugagan.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    context.user_data["edit_token"] = token

    if action == "edit_title":
        await query.message.reply_text("📝 Yangi nomini yuboring:", reply_markup=cancel_keyboard())
        return ASK_TITLE
    elif action == "edit_performer":
        await query.message.reply_text("🎙 Yangi ijrochi nomini yuboring:", reply_markup=cancel_keyboard())
        return ASK_PERFORMER
    elif action == "edit_thumb":
        await query.message.reply_text("🖼 Yangi muqova rasmini (rasm sifatida) yuboring:", reply_markup=cancel_keyboard())
        return ASK_THUMB
    return ConversationHandler.END


async def receive_new_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = context.user_data.get("edit_token")
    session = get_session(token) if token else None
    if not session:
        await update.message.reply_text("❌ Sessiya topilmadi, qaytadan urinib ko'ring.")
        return ConversationHandler.END

    new_title = update.message.text.strip()
    await update_metadata(session["path"], title=new_title)
    update_session(token, title=new_title)
    await update.message.reply_text("✅ Nomi yangilandi.")
    await send_audio_with_actions(update, context, session["path"], new_title, session.get("performer", ""))
    return ConversationHandler.END


async def receive_new_performer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = context.user_data.get("edit_token")
    session = get_session(token) if token else None
    if not session:
        await update.message.reply_text("❌ Sessiya topilmadi, qaytadan urinib ko'ring.")
        return ConversationHandler.END

    new_performer = update.message.text.strip()
    await update_metadata(session["path"], performer=new_performer)
    update_session(token, performer=new_performer)
    await update.message.reply_text("✅ Ijrochi yangilandi.")
    await send_audio_with_actions(update, context, session["path"], session.get("title", ""), new_performer)
    return ConversationHandler.END


async def receive_new_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = context.user_data.get("edit_token")
    session = get_session(token) if token else None
    if not session:
        await update.message.reply_text("❌ Sessiya topilmadi, qaytadan urinib ko'ring.")
        return ConversationHandler.END

    if not update.message.photo:
        await update.message.reply_text("Iltimos, rasm sifatida yuboring.")
        return ASK_THUMB

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await tg_file.download_as_bytearray())

    await update_metadata(session["path"], thumbnail_bytes=photo_bytes)
    await update.message.reply_text("✅ Muqova yangilandi.")
    await send_audio_with_actions(update, context, session["path"], session.get("title", ""), session.get("performer", ""))
    return ConversationHandler.END


async def cancel_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❌ Tahrirlash bekor qilindi.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# Xatoliklarni global ushlab qolish
# ─────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Kutilmagan xatolik: {context.error}", exc_info=context.error)


# ─────────────────────────────────────────────────────────────
# Ilovani yig'ish
# ─────────────────────────────────────────────────────────────
async def _post_init(app: Application) -> None:
    """Application ishga tushishidan oldin bazani tayyorlaydi."""
    await init_db()
    logger.info(f"Bot ishga tayyor. Adminlar soni: {len(ADMIN_IDS)}")


def build_application() -> Application:
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(_post_init).build()

    # ── Middleware: har bir update uchun ban/texnik ishlar/majburiy obuna tekshiruvi ──
    # group=-1 -> barcha boshqa handlerlardan oldin ishlaydi
    app.add_handler(MessageHandler(filters.ALL, track_and_guard), group=-1)
    app.add_handler(CallbackQueryHandler(track_and_guard, pattern=r"^(?!check_subscription$).*"), group=-1)
    app.add_handler(CallbackQueryHandler(handle_check_subscription_callback, pattern=r"^check_subscription$"))

    # ── Admin panel ──
    register_admin_handlers(app)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Matn (qidiruv / link)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Voice / audio / video → Shazam
    app.add_handler(MessageHandler(
        (filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE) & ~filters.COMMAND,
        handle_media_for_shazam,
    ))

    # Inline qidiruv
    app.add_handler(InlineQueryHandler(handle_inline_query))
    from telegram.ext import ChosenInlineResultHandler
    app.add_handler(ChosenInlineResultHandler(handle_chosen_inline_result))

    # Effekt tugmalari
    app.add_handler(CallbackQueryHandler(handle_effect_callback, pattern=r"^(fx8d|fxconcert|fxslowed):"))
    app.add_handler(CallbackQueryHandler(handle_minus_callback, pattern=r"^fxminus:"))
    app.add_handler(CallbackQueryHandler(handle_voice_convert_callback, pattern=r"^voice:"))
    app.add_handler(CallbackQueryHandler(handle_edit_menu_callback, pattern=r"^edit:"))

    # Tahrirlash conversation
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_field_entry, pattern=r"^(edit_title|edit_performer|edit_thumb):")],
        states={
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_title)],
            ASK_PERFORMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_performer)],
            ASK_THUMB: [MessageHandler(filters.PHOTO, receive_new_thumb)],
        },
        fallbacks=[CallbackQueryHandler(cancel_edit_callback, pattern=r"^cancel_edit$")],
        per_message=False,
    )
    app.add_handler(edit_conv)

    app.add_error_handler(error_handler)
    return app


def main():
    cleanup_old_temp_files()
    app = build_application()
    logger.info("Bot ishga tushdi (long polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
