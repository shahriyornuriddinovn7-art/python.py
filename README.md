# Telegram Musiqa Bot

python-telegram-bot (v20+, async) + yt-dlp + Shazam + ffmpeg/pydub/Demucs asosidagi to'liq musiqa bot.

## Xususiyatlari
- Matn / YouTube / SoundCloud / Spotify link orqali yuklab olish
- Inline qidiruv (`@bot_username qo'shiq nomi`)
- Ovozli xabar orqali qo'shiqni aniqlash (Shazam)
- Effektlar: 🎧 8D, 🏟 Concert Hall, 🐢 Slowed+Reverb, 🎤 Minus (Demucs AI)
- Metadata tahrirlash: nom, ijrochi, muqova rasmi
- MP3 → Telegram Voice konvertatsiyasi
- Vaqtinchalik fayllarni avtomatik tozalash
- **To'liq admin panel** (pastga qarang)

## Admin panel

`/admin` buyrug'i orqali (faqat `ADMIN_IDS` ro'yxatidagilar uchun) quyidagilar mavjud:

| Bo'lim | Tavsif |
|---|---|
| 📊 Statistika | jami/bloklangan/faol foydalanuvchilar, 24 soat/7 kunlik faollik, har bir harakat (yuklab olish, effektlar, shazam) bo'yicha son |
| 📢 Broadcast | barcha foydalanuvchilarga matn/rasm/video/audio xabar yuborish, progress va yetib bormagan (bloklagan) foydalanuvchilar hisoblanadi |
| 🚫 Bloklash / ✅ Blokdan chiqarish | foydalanuvchi ID orqali botdan foydalanishni taqiqlash/qaytarish |
| 👤 Foydalanuvchi ma'lumoti | ID bo'yicha to'liq profil (username, ro'yxatdan o'tgan sana, oxirgi faollik, holati) |
| 🕓 So'nggi foydalanuvchilar | eng oxirgi 10 ta ro'yxatdan o'tgan foydalanuvchi |
| 🔧 Texnik ishlar rejimi | yoqilganda faqat adminlar botdan foydalana oladi, boshqalarga xabar chiqadi |
| 📌 Majburiy obuna kanali | belgilangan kanalga a'zo bo'lmagan foydalanuvchilar botdan foydalana olmaydi (bot kanalda admin bo'lishi shart) |

Admin foydalanuvchilar ban, texnik ishlar rejimi va majburiy obuna tekshiruvlaridan
avtomatik ravishda ozod qilinadi (`middleware.py`).

Statistika va foydalanuvchilar ro'yxati `bot_database.db` (SQLite) faylida saqlanadi —
bot qayta ishga tushirilganda ma'lumot yo'qolmaydi.

## Lokal ishga tushirish

```bash
git clone <repo>
cd telegram_music_bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# ffmpeg tizimda o'rnatilgan bo'lishi shart:
# Ubuntu/Debian: sudo apt install ffmpeg
# macOS: brew install ffmpeg

export BOT_TOKEN="123456:ABC-YourTelegramBotToken"
export BOT_USERNAME="your_bot_username"

python bot.py
```

## Render.com'da deploy qilish

1. Repozitoriyani GitHub'ga yuklang.
2. Render.com'da **New → Background Worker** yarating (webhook emas, long polling ishlatilgani uchun Web Service emas, **Worker** tanlanadi).
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python bot.py` (yoki Procfile avtomatik aniqlaydi)
5. Environment → Environment Variables bo'limida qo'shing:
   - `BOT_TOKEN` = Telegram bot tokeningiz
   - `BOT_USERNAME` = bot usernamesi (@ belgisiz)
   - `ADMIN_IDS` = admin(lar)ning Telegram user_id raqami, vergul bilan (masalan: `123456789,987654321`) — o'z ID'ingizni bilish uchun @userinfobot ga yozing
6. **Native Environment** yoki Docker orqali `ffmpeg` mavjudligiga ishonch hosil qiling. Agar Render standart muhitida ffmpeg bo'lmasa, `apt.txt` fayliga `ffmpeg` qo'shing yoki Dockerfile ishlatib, `apt-get install -y ffmpeg` buyrug'ini qo'shing.
7. Deploy qiling — bot long polling rejimida ishga tushadi.

> **Eslatma (baza fayli):** Render'ning standart (ephemeral) disklarida `bot_database.db`
> fayli har deploy/restart'da o'chib ketishi mumkin. Statistika va foydalanuvchilar
> ro'yxatini doimiy saqlash uchun Render'da **Persistent Disk** qo'shing va
> `DB_PATH` environment variable'ni shu disk yo'liga ko'rsating (masalan `/data/bot_database.db`).

> **Eslatma (Demucs / vokal ajratish):** Demucs CPU-only serverlarda sekin ishlaydi
> (bir necha daqiqa). Render'ning bepul tarifida bu vaqt tugashi (timeout) yoki
> xotira yetishmasligi mumkin. Production uchun kamida 2GB+ RAM'li reja yoki
> GPU'li alohida worker tavsiya etiladi.

## PythonAnywhere'da ishga tushirish

1. Fayllarni Files bo'limi orqali yuklang yoki `git clone` qiling.
2. Bash konsolda: `pip install --user -r requirements.txt`
3. **ffmpeg**: PythonAnywhere bepul tarifida tizim paketi o'rnatish imkoni cheklangan —
   Bash konsolda `ffmpeg -version` bilan mavjudligini tekshiring. Agar yo'q bo'lsa,
   pullik "Always-on task" rejasi yoki maxsus virtual muhit kerak bo'lishi mumkin.
4. Environment Variables'ni `.env` fayl yoki konsolda `export BOT_TOKEN=...` orqali o'rnating.
5. **Always-on task** yarating: `python3 /home/username/telegram_music_bot/bot.py`
   (PythonAnywhere bepul tarifida doimiy background process cheklangan —
   pullik "Tasks" xizmati kerak bo'ladi, chunki bot long polling orqali doim ishlaydi).

## Loyihaning tuzilishi

Barcha kod **bitta faylda** — `bot.py` (quyidagi bo'limlarga bo'lingan holda,
izohlar bilan ajratilgan):

```
telegram_music_bot/
├── bot.py             # BARCHA KOD: config, utils, downloader, shazam,
│                      #   effektlar, vocal remover, voice converter,
│                      #   metadata editor, keyboards, database (SQLite),
│                      #   admin panel, middleware, asosiy handlerlar
├── requirements.txt
├── Procfile
└── README.md
```

`bot.py` ichida quyidagi tartibda bo'limlar mavjud (fayl ichida `# ════...`
sarlavhalari bilan ajratilgan): SOZLAMALAR → YORDAMCHI FUNKSIYALAR →
YUKLAB OLISH (yt-dlp) → SHAZAM → AUDIO EFFEKTLARI → VOKAL AJRATISH (Demucs) →
VOICE KONVERTATSIYA → METADATA TAHRIRLASH → INLINE TUGMALAR →
MA'LUMOTLAR BAZASI (SQLite) → ADMIN PANEL → MIDDLEWARE → ASOSIY BOT LOGIKASI.

## Muhim eslatmalar

- Telegram Bot API orqali yuborilishi mumkin bo'lgan fayl hajmi standart holatda
  **50MB** bilan cheklangan. Katta fayllar uchun local Bot API server kerak bo'ladi.
- Spotify to'g'ridan-to'g'ri audio strimini bermaydi (DRM), shuning uchun Spotify
  linklari uchun trek nomi/ijrochisi olinib, YouTube'dan mos audio topiladi.
- Inline qidiruv (`@bot_username ...`) natijalari tanlanganda audio botga
  yuklanib, foydalanuvchiga shaxsiy xabar sifatida yuboriladi — bu Telegram
  inline audio uchun to'g'ridan-to'g'ri strim URL talab qilinishi sababli.
