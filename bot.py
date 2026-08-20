import os
import random
import asyncio
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_FILE = "bot_data.db"
CARD_DIR = "cards"
KR_TZ = ZoneInfo("Asia/Seoul")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN 환경변수가 없습니다.")

ADMIN_IDS = set()
for value in os.environ.get("ADMIN_IDS", "").split(","):
    value = value.strip()
    if value.isdigit():
        ADMIN_IDS.add(int(value))

LEVEL_NAMES = {1: "돌맹이", 2: "동", 3: "은", 4: "골드", 5: "다이아"}
XP_REQUIREMENTS = {1: 300, 2: 1000, 3: 5000, 4: 10000}
LEVEL_UP_REWARDS = {1: 5000, 2: 10000, 3: 20000, 4: 50000}

baccarat_game = {"active": False, "bets": {}, "chat_id": None}
odd_even_game = {"active": False, "bets": {}, "chat_id": None}
baccarat_history = []
MAX_HISTORY = 20

game_lock = asyncio.Lock()
odd_even_lock = asyncio.Lock()
db_lock = asyncio.Lock()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Telegram Bot is Running Successfully!")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Web server running on port {port}")
    server.serve_forever()


def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=20, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def init_db():
    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '유저',
            points INTEGER DEFAULT 0,
            real_money INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_attendance TEXT,
            total_chat_count INTEGER DEFAULT 0
        )
    """)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]

    if "real_money" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN real_money INTEGER DEFAULT 0")

    if "total_chat_count" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN total_chat_count INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_chat (
            user_id INTEGER NOT NULL,
            chat_date TEXT NOT NULL,
            chat_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, chat_date)
        )
    """)

    conn.commit()
    conn.close()


init_db()


def get_user(user_id, username="유저"):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, username, points, real_money, xp, level,
               last_attendance, total_chat_count
        FROM users WHERE user_id = ?
    """, (user_id,))
    row = cur.fetchone()

    if row is None:
        cur.execute("""
            INSERT INTO users
            (user_id, username, points, real_money, xp, level,
             last_attendance, total_chat_count)
            VALUES (?, ?, 0, 0, 0, 1, NULL, 0)
        """, (user_id, username))
        conn.commit()

        result = {
            "user_id": user_id,
            "username": username,
            "points": 0,
            "real_money": 0,
            "xp": 0,
            "level": 1,
            "last_attendance": None,
            "total_chat_count": 0,
        }
    else:
        result = {
            "user_id": row[0],
            "username": row[1],
            "points": row[2] or 0,
            "real_money": row[3] or 0,
            "xp": row[4] or 0,
            "level": row[5] or 1,
            "last_attendance": row[6],
            "total_chat_count": row[7] or 0,
        }

        if username and username != row[1]:
            cur.execute(
                "UPDATE users SET username=? WHERE user_id=?",
                (username, user_id)
            )
            conn.commit()

    conn.close()
    return result


def update_user(
    user_id,
    points=None,
    real_money=None,
    xp=None,
    level=None,
    last_attendance=None,
    total_chat_count=None
):
    conn = db_connect()
    cur = conn.cursor()

    if points is not None:
        cur.execute("UPDATE users SET points=? WHERE user_id=?", (points, user_id))
    if real_money is not None:
        cur.execute("UPDATE users SET real_money=? WHERE user_id=?", (real_money, user_id))
    if xp is not None:
        cur.execute("UPDATE users SET xp=? WHERE user_id=?", (xp, user_id))
    if level is not None:
        cur.execute("UPDATE users SET level=? WHERE user_id=?", (level, user_id))
    if last_attendance is not None:
        cur.execute(
            "UPDATE users SET last_attendance=? WHERE user_id=?",
            (last_attendance, user_id)
        )
    if total_chat_count is not None:
        cur.execute(
            "UPDATE users SET total_chat_count=? WHERE user_id=?",
            (total_chat_count, user_id)
        )

    conn.commit()
    conn.close()


def add_xp_and_check_level(user_id, amount):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "SELECT xp, level, points FROM users WHERE user_id=?",
        (user_id,)
    )
    row = cur.fetchone()

    if row is None:
        conn.close()
        return None

    xp, level, points = row
    xp = max(0, xp + amount)
    level_ups = []

    while level < 5:
        required = XP_REQUIREMENTS.get(level)
        reward = LEVEL_UP_REWARDS.get(level, 0)

        if required is None or xp < required:
            break

        level += 1
        points += reward
        level_ups.append({"level": level, "reward": reward})

    cur.execute("""
        UPDATE users
        SET xp=?, level=?, points=?
        WHERE user_id=?
    """, (xp, level, points, user_id))

    conn.commit()
    conn.close()

    return {
        "xp": xp,
        "level": level,
        "points": points,
        "level_ups": level_ups
    }


def count_chat_message(user_id, username, text):
    if not text:
        return False, 0, 0

    clean_text = "".join(text.split())

    if len(clean_text) < 5:
        return False, 0, 0

    today = datetime.now(KR_TZ).strftime("%Y-%m-%d")

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cur.fetchone() is None:
        cur.execute("""
            INSERT INTO users
            (user_id, username, points, real_money, xp, level,
             last_attendance, total_chat_count)
            VALUES (?, ?, 0, 0, 0, 1, NULL, 0)
        """, (user_id, username))
    else:
        cur.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username, user_id)
        )

    cur.execute("""
        UPDATE users
        SET total_chat_count = COALESCE(total_chat_count, 0) + 1
        WHERE user_id=?
    """, (user_id,))

    cur.execute("""
        INSERT INTO daily_chat (user_id, chat_date, chat_count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, chat_date)
        DO UPDATE SET chat_count = daily_chat.chat_count + 1
    """, (user_id, today))

    cur.execute(
        "SELECT total_chat_count FROM users WHERE user_id=?",
        (user_id,)
    )
    total = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT chat_count
        FROM daily_chat
        WHERE user_id=? AND chat_date=?
    """, (user_id, today))
    today_count = cur.fetchone()[0] or 0

    conn.commit()
    conn.close()

    return True, today_count, total


def get_today_chat_count(user_id):
    today = datetime.now(KR_TZ).strftime("%Y-%m-%d")
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT chat_count
        FROM daily_chat
        WHERE user_id=? AND chat_date=?
    """, (user_id, today))

    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def get_chat_ranking(limit=5):
    today = datetime.now(KR_TZ).strftime("%Y-%m-%d")

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT u.user_id,
               u.username,
               COALESCE(d.chat_count, 0) AS today_count
        FROM users u
        LEFT JOIN daily_chat d
          ON u.user_id=d.user_id AND d.chat_date=?
        WHERE COALESCE(d.chat_count, 0) > 0
        ORDER BY today_count DESC, u.user_id ASC
        LIMIT ?
    """, (today, limit))

    rows = cur.fetchall()
    conn.close()
    return rows


async def my_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    u = get_user(user.id, user.first_name or "유저")

    level_name = LEVEL_NAMES.get(u["level"], "최고 등급")
    next_xp = XP_REQUIREMENTS.get(u["level"], "MAX")
    next_reward = LEVEL_UP_REWARDS.get(u["level"], "MAX")

    reward_text = (
        f"{next_reward:,}P"
        if isinstance(next_reward, int)
        else next_reward
    )

    today_chat = get_today_chat_count(user.id)

    await update.message.reply_text(
        f"👤 [{u['username']}] 님의 정보\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏅 레벨: {u['level']} [{level_name}]\n"
        f"💰 보유 포인트: {u['points']:,}P\n"
        f"💵 실머니: {u['real_money']:,}원\n"
        f"✨ 경험치: {u['xp']:,} / {next_xp}\n"
        f"🎁 다음 레벨업 보상: {reward_text}\n"
        f"💬 오늘 채팅: {today_chat:,}회\n"
        f"📚 누적 채팅: {u['total_chat_count']:,}회"
    )


async def chat_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    rows = get_chat_ranking(5)

    if not rows:
        await update.message.reply_text(
            "💬 오늘 채팅 순위\n"
            "━━━━━━━━━━━━━━\n"
            "아직 5글자 이상 채팅을 한 사람이 없습니다."
        )
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["💬 오늘 채팅 순위", "━━━━━━━━━━━━━━"]

    for i, row in enumerate(rows):
        user_id, username, count = row
        display_name = username or f"유저{user_id}"
        lines.append(
            f"{medals[i]} {i+1}위  {display_name} — {count:,}회"
        )

    await update.message.reply_text("\n".join(lines))


async def attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id
    username = update.effective_user.first_name or "유저"
    today = datetime.now(KR_TZ).strftime("%Y-%m-%d")

    async with db_lock:
        conn = db_connect()
        cur = conn.cursor()

        cur.execute(
            "SELECT points, last_attendance FROM users WHERE user_id=?",
            (uid,)
        )
        row = cur.fetchone()

        if row is None:
            cur.execute("""
                INSERT INTO users
                (user_id, username, points, real_money, xp, level,
                 last_attendance, total_chat_count)
                VALUES (?, ?, 1000, 0, 0, 1, ?, 0)
            """, (uid, username, today))
            new_points, already = 1000, False

        elif row[1] == today:
            new_points, already = row[0], True

        else:
            new_points = row[0] + 1000
            cur.execute("""
                UPDATE users
                SET points=?, last_attendance=?, username=?
                WHERE user_id=?
            """, (new_points, today, username, uid))
            already = False

        conn.commit()
        conn.close()

    if already:
        await update.message.reply_text(
            "❌ 오늘은 이미 출석체크를 완료했습니다.\n"
            "🌙 한국시간 00:00 이후 다시 출석할 수 있습니다."
        )
    else:
        await update.message.reply_text(
            f"📆 출석체크 완료!\n"
            f"🎁 +1,000P 지급\n"
            f"💰 현재: {new_points:,}P"
        )


async def level_up(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    u = get_user(update.effective_user.id)

    if u["level"] >= 5:
        await update.message.reply_text(
            "👑 이미 최고 레벨 [다이아]입니다."
        )
        return

    required = XP_REQUIREMENTS[u["level"]]
    reward = LEVEL_UP_REWARDS[u["level"]]

    await update.message.reply_text(
        f"✨ 레벨업은 자동으로 진행됩니다.\n\n"
        f"현재: {u['xp']:,} XP\n"
        f"필요: {required:,} XP\n"
        f"🎁 레벨업 보상: {reward:,}P"
    )


async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.message
        or not update.message.text
        or not update.effective_user
    ):
        return

    if update.message.text.startswith("/"):
        return

    uid = update.effective_user.id
    username = update.effective_user.first_name or "유저"

    get_user(uid, username)

    count_chat_message(
        uid,
        username,
        update.message.text
    )

    result = add_xp_and_check_level(uid, 1)

    if result and result["level_ups"]:
        for item in result["level_ups"]:
            level_name = LEVEL_NAMES.get(
                item["level"],
                "최고 등급"
            )

            await update.message.reply_text(
                f"🎉 레벨업!\n"
                f"🏅 Lv.{item['level']} [{level_name}]\n"
                f"🎁 +{item['reward']:,}P 지급!"
            )


def lottery_5th_prize():
    rewards = [
        (100, 250),
        (200, 200),
        (300, 180),
        (500, 140),
        (700, 100),
        (1000, 70),
        (1500, 35),
        (2000, 20),
        (3000, 8),
        (4000, 3),
        (5000, 1)
    ]

    return random.choices(
        [x[0] for x in rewards],
        weights=[x[1] for x in rewards],
        k=1
    )[0]


async def buy_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id
    username = update.effective_user.first_name or "유저"

    use_real = bool(
        context.args
        and context.args[0] in ("실", "실머니")
    )

    cost = 100 if use_real else 1000

    u = get_user(uid, username)
    balance = u["real_money"] if use_real else u["points"]

    if balance < cost:
        await update.message.reply_text(
            f"❌ {'실머니' if use_real else '포인트'}가 부족합니다.\n"
            f"현재: {balance:,}{'원' if use_real else 'P'}"
        )
        return

    rand = random.random() * 100

    if rand < 0.05:
        rank, prize = "1등 🥇", 50000
    elif rand < 0.15:
        rank, prize = "2등 🥈", 30000
    elif rand < 0.95:
        rank, prize = "3등 🥉", 10000
    elif rand < 2.15:
        rank, prize = "4등 🏅", 7000
    else:
        rank, prize = "5등 🎗️", lottery_5th_prize()

    new_balance = balance - cost + prize

    if use_real:
        update_user(uid, real_money=new_balance)
        unit = "원"
    else:
        update_user(uid, points=new_balance)
        unit = "P"

    await update.message.reply_text(
        f"🎫 복권 결과\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏆 등수: {rank}\n"
        f"💰 당첨: {prize:,}{unit}\n"
        f"💳 구매: -{cost:,}{unit}\n"
        f"💵 현재 보유: {new_balance:,}{unit}"
    )


def is_admin(uid):
    return uid in ADMIN_IDS


async def admin_balance_change(update, context, field, title, unit):
    if not update.message or not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 관리자만 사용할 수 있습니다.")
        return

    try:
        args = context.args

        if len(args) == 1:
            target_id = update.effective_user.id
            amount = int(args[0].replace(",", ""))
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1].replace(",", ""))
        else:
            raise ValueError

        if amount <= 0:
            raise ValueError

        u = get_user(target_id)
        current = u[field]
        new_value = current + amount

        update_user(target_id, **{field: new_value})

        await update.message.reply_text(
            f"✅ {title} 완료\n"
            f"👤 {target_id}\n"
            f"💰 +{amount:,}{unit}\n"
            f"💳 현재: {new_value:,}{unit}"
        )

    except Exception:
        await update.message.reply_text(
            f"사용법: /{title} 금액\n"
            f"또는 /{title} 유저ID 금액"
        )


async def admin_give(update, context):
    await admin_balance_change(update, context, "points", "지급", "P")


async def admin_take(update, context):
    if not update.message or not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 관리자만 사용할 수 있습니다.")
        return

    try:
        args = context.args

        if len(args) == 1:
            target_id = update.effective_user.id
            amount = int(args[0].replace(",", ""))
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1].replace(",", ""))
        else:
            raise ValueError

        if amount <= 0:
            raise ValueError

        u = get_user(target_id)
        new_value = max(0, u["points"] - amount)

        update_user(target_id, points=new_value)

        await update.message.reply_text(
            f"✅ 차감 완료\n"
            f"👤 {target_id}\n"
            f"💸 -{amount:,}P\n"
            f"💳 현재: {new_value:,}P"
        )

    except Exception:
        await update.message.reply_text(
            "사용법: /차감 금액\n"
            "또는 /차감 유저ID 금액"
        )


async def admin_real_give(update, context):
    await admin_balance_change(update, context, "real_money", "실머니지급", "원")


async def admin_real_take(update, context):
    if not update.message or not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 관리자만 사용할 수 있습니다.")
        return

    try:
        args = context.args

        if len(args) == 1:
            target_id = update.effective_user.id
            amount = int(args[0].replace(",", ""))
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1].replace(",", ""))
        else:
            raise ValueError

        if amount <= 0:
            raise ValueError

        u = get_user(target_id)
        new_value = max(0, u["real_money"] - amount)

        update_user(target_id, real_money=new_value)

        await update.message.reply_text(
            f"✅ 실머니 차감 완료\n"
            f"👤 {target_id}\n"
            f"💸 -{amount:,}원\n"
            f"💳 현재: {new_value:,}원"
        )

    except Exception:
        await update.message.reply_text(
            "사용법: /실머니차감 금액\n"
            "또는 /실머니차감 유저ID 금액"
        )


async def admin_xp_give(update, context):
    if not update.message or not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 관리자만 사용할 수 있습니다.")
        return

    try:
        args = context.args

        if len(args) == 1:
            target_id = update.effective_user.id
            amount = int(args[0])
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1])
        else:
            raise ValueError

        if amount <= 0:
            raise ValueError

        get_user(target_id)
        result = add_xp_and_check_level(target_id, amount)

        text = (
            f"✅ 경험치 지급 완료\n"
            f"👤 {target_id}\n"
            f"✨ +{amount:,} XP\n"
            f"📊 현재: {result['xp']:,} XP"
        )

        for item in result["level_ups"]:
            level_name = LEVEL_NAMES.get(
                item["level"],
                "최고 등급"
            )

            text += (
                f"\n\n🎉 자동 레벨업!\n"
                f"🏅 Lv.{item['level']} [{level_name}]\n"
                f"🎁 +{item['reward']:,}P 지급"
            )

        await update.message.reply_text(text)

    except Exception:
        await update.message.reply_text(
            "사용법: /경험치 100\n"
            "또는 /경험치 유저ID 100"
        )


async def admin_xp_take(update, context):
    if not update.message or not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 관리자만 사용할 수 있습니다.")
        return

    try:
        args = context.args

        if len(args) == 1:
            target_id = update.effective_user.id
            amount = int(args[0])
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1])
        else:
            raise ValueError

        if amount <= 0:
            raise ValueError

        u = get_user(target_id)
        new_xp = max(0, u["xp"] - amount)

        update_user(target_id, xp=new_xp)

        await update.message.reply_text(
            f"✅ 경험치 차감 완료\n"
            f"👤 {target_id}\n"
            f"✨ -{amount:,} XP\n"
            f"📊 현재: {new_xp:,} XP"
        )

    except Exception:
        await update.message.reply_text(
            "사용법: /경험치차감 100\n"
            "또는 /경험치차감 유저ID 100"
        )


SUITS = {
    "S": ("♠", "black"),
    "H": ("♥", "red"),
    "D": ("♦", "red"),
    "C": ("♣", "black"),
}

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def get_font(size, bold=False):
    paths = [
        (
            "/usr/share/fonts/truetype/dejavu/"
            + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
        ),
        (
            "/usr/share/fonts/truetype/liberation2/"
            + (
                "LiberationSans-Bold.ttf"
                if bold
                else "LiberationSans-Regular.ttf"
            )
        ),
    ]

    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def create_card_images():
    os.makedirs(CARD_DIR, exist_ok=True)

    width, height = 100, 145
    rank_font = get_font(18, True)
    suit_font = get_font(17, True)
    center_font = get_font(42, True)

    back_path = os.path.join(CARD_DIR, "BACK.png")

    if not os.path.exists(back_path):
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle(
            (2, 2, width - 2, height - 2),
            radius=10,
            fill=(35, 70, 150),
            outline="white",
            width=3
        )

        draw.rounded_rectangle(
            (10, 10, width - 10, height - 10),
            radius=8,
            outline="white",
            width=2
        )

        draw.text(
            (width // 2, height // 2),
            "★",
            font=center_font,
            fill="white",
            anchor="mm"
        )

        img.save(back_path)

    for suit_code, (symbol, color) in SUITS.items():
        fill = "red" if color == "red" else "black"

        for rank in RANKS:
            path = os.path.join(CARD_DIR, f"{rank}{suit_code}.png")

            if os.path.exists(path):
                continue

            img = Image.new("RGB", (width, height), "white")
            draw = ImageDraw.Draw(img)

            draw.rounded_rectangle(
                (1, 1, width - 1, height - 1),
                radius=10,
                outline="black",
                width=2
            )

            draw.text((7, 4), rank, font=rank_font, fill=fill)
            draw.text((7, 25), symbol, font=suit_font, fill=fill)

            draw.text(
                (width // 2, height // 2),
                symbol,
                font=center_font,
                fill=fill,
                anchor="mm"
            )

            draw.text(
                (width - 7, height - 5),
                rank,
                font=rank_font,
                fill=fill,
                anchor="rs"
            )

            img.save(path)


create_card_images()


def create_deck():
    deck = []

    for suit in SUITS:
        for rank in RANKS:
            deck.append({
                "rank": rank,
                "suit": suit,
                "file": os.path.join(CARD_DIR, f"{rank}{suit}.png")
            })

    random.shuffle(deck)
    return deck


def card_value(card):
    if card["rank"] == "A":
        return 1

    if card["rank"] in ("10", "J", "Q", "K"):
        return 0

    return int(card["rank"])


def baccarat_score(cards):
    return sum(card_value(c) for c in cards) % 10


def create_baccarat_image(player, banker, result_text=None):
    width, height = 620, 330

    img = Image.new("RGB", (width, height), (20, 70, 45))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (5, 5, width - 5, height - 5),
        radius=18,
        outline=(210, 170, 70),
        width=4
    )

    title_font = get_font(24, True)
    label_font = get_font(20, True)
    score_font = get_font(20, True)
    result_font = get_font(22, True)

    draw.text(
        (width // 2, 25),
        "B A C C A R A",
        font=title_font,
        fill=(245, 220, 140),
        anchor="ma"
    )

    player_x, banker_x, card_y = 80, 390, 80

    draw.text(
        (player_x + 50, 65),
        "PLAYER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    draw.text(
        (banker_x + 50, 65),
        "BANKER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    def paste_cards(cards, start_x):
        for i, card in enumerate(cards):
            card_img = Image.open(card["file"]).convert("RGB").resize((100, 145))
            img.paste(card_img, (start_x + i * 55, card_y))

    paste_cards(player, player_x)
    paste_cards(banker, banker_x)

    draw.text(
        (player_x + 50, 245),
        f"PLAYER  {baccarat_score(player)}",
        font=score_font,
        fill="white",
        anchor="ma"
    )

    draw.text(
        (banker_x + 50, 245),
        f"BANKER  {baccarat_score(banker)}",
        font=score_font,
        fill="white",
        anchor="ma"
    )

    if result_text:
        draw.text(
            (width // 2, 300),
            result_text,
            font=result_font,
            fill=(255, 225, 100),
            anchor="mm"
        )

    path = os.path.join(CARD_DIR, "baccarat_result.png")
    img.save(path, quality=95)
    return path


async def send_baccarat_image(bot, chat_id, player, banker, caption=None):
    path = create_baccarat_image(player, banker, caption)

    with open(path, "rb") as f:
        await bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(f),
            caption=caption
        )


def parse_game_bet(args):
    if len(args) == 2:
        choice, amount_text = args
        money_type = "P"

    elif len(args) == 3 and args[1].lower() in ("실", "실머니"):
        choice, _, amount_text = args
        money_type = "R"

    else:
        return None

    aliases = {
        "p": "P",
        "플": "P",
        "플레이어": "P",
        "player": "P",
        "b": "B",
        "뱅": "B",
        "뱅커": "B",
        "banker": "B",
        "t": "T",
        "타이": "T",
        "tie": "T",
        "홀": "O",
        "짝": "E",
        "odd": "O",
        "even": "E",
    }

    choice = aliases.get(choice.lower())

    if choice is None:
        return None

    try:
        amount = int(amount_text.replace(",", ""))
    except ValueError:
        return None

    if amount <= 0:
        return None

    return choice, amount, money_type


async def baccarat_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    parsed = parse_game_bet(context.args)

    if not parsed or parsed[0] not in ("P", "B", "T"):
        await update.message.reply_text(
            "사용법:\n"
            "/배팅 플 5000\n"
            "/배팅 뱅 5000\n"
            "/배팅 타이 5000\n"
            "/배팅 플 실 50000"
        )
        return

    bet_type, amount, money_type = parsed
    uid = update.effective_user.id
    username = update.effective_user.first_name or "유저"

    async with game_lock:
        if not baccarat_game["active"]:
            await update.message.reply_text("❌ 현재 바카라 베팅 시간이 아닙니다.")
            return

        u = get_user(uid, username)
        balance = u["real_money"] if money_type == "R" else u["points"]
        unit = "원" if money_type == "R" else "P"

        if balance < amount:
            await update.message.reply_text(
                f"❌ {'실머니' if money_type == 'R' else '포인트'}가 부족합니다.\n"
                f"현재: {balance:,}{unit}"
            )
            return

        new_balance = balance - amount

        if money_type == "R":
            update_user(uid, real_money=new_balance)
        else:
            update_user(uid, points=new_balance)

        baccarat_game["bets"].setdefault(uid, []).append({
            "type": bet_type,
            "amount": amount,
            "money": money_type,
            "name": username,
        })

    names = {"P": "PLAYER", "B": "BANKER", "T": "TIE"}

    await update.message.reply_text(
        f"✅ {names[bet_type]} 베팅 완료되었습니다!\n"
        f"👤 {username}\n"
        f"🎯 {names[bet_type]}\n"
        f"💰 베팅금액: {amount:,}{unit}\n"
        f"💳 베팅 후 보유머니: {new_balance:,}{unit}"
    )


async def play_baccarat(bot, chat_id, bets):
    deck = create_deck()
    player, banker = [], []

    await bot.send_message(
        chat_id=chat_id,
        text="🎰 바카라 카드 공개를 시작합니다!"
    )

    player.append(deck.pop())
    await send_baccarat_image(bot, chat_id, player, banker, "🔵 PLAYER 첫 번째 카드")
    await asyncio.sleep(random.uniform(1.0, 2.0))

    banker.append(deck.pop())
    await send_baccarat_image(bot, chat_id, player, banker, "🔴 BANKER 첫 번째 카드")
    await asyncio.sleep(random.uniform(1.0, 2.0))

    player.append(deck.pop())
    await send_baccarat_image(bot, chat_id, player, banker, "🔵 PLAYER 두 번째 카드")
    await asyncio.sleep(random.uniform(1.0, 2.0))

    banker.append(deck.pop())
    await send_baccarat_image(bot, chat_id, player, banker, "🔴 BANKER 두 번째 카드")
    await asyncio.sleep(random.uniform(1.0, 2.0))

    ps = baccarat_score(player)
    bs = baccarat_score(banker)

    natural = ps in (8, 9) or bs in (8, 9)
    player_third = None

    if not natural:
        if ps <= 5:
            player_third = deck.pop()
            player.append(player_third)

            await send_baccarat_image(
                bot,
                chat_id,
                player,
                banker,
                "🔵 PLAYER 추가 카드"
            )
            await asyncio.sleep(random.uniform(1.0, 2.0))

        bs = baccarat_score(banker)
        banker_draw = False

        if player_third is None:
            banker_draw = bs <= 5
        else:
            tv = card_value(player_third)

            if bs <= 2:
                banker_draw = True
            elif bs == 3:
                banker_draw = tv != 8
            elif bs == 4:
                banker_draw = tv in (2, 3, 4, 5, 6, 7)
            elif bs == 5:
                banker_draw = tv in (4, 5, 6, 7)
            elif bs == 6:
                banker_draw = tv in (6, 7)

        if banker_draw:
            banker.append(deck.pop())

            await send_baccarat_image(
                bot,
                chat_id,
                player,
                banker,
                "🔴 BANKER 추가 카드"
            )
            await asyncio.sleep(random.uniform(1.0, 2.0))

    ps = baccarat_score(player)
    bs = baccarat_score(banker)

    if ps > bs:
        result = "P"
        result_text = "🔵 PLAYER 승리!"
    elif bs > ps:
        result = "B"
        result_text = "🔴 BANKER 승리!"
    else:
        result = "T"
        result_text = "🟢 TIE!"

    await send_baccarat_image(bot, chat_id, player, banker, result_text)

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎰 바카라 최종 결과\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔵 PLAYER: {ps}점\n"
            f"🔴 BANKER: {bs}점\n\n"
            f"🏆 {result_text}"
        )
    )

    baccarat_history.append({
        "result": result,
        "player": ps,
        "banker": bs
    })

    if len(baccarat_history) > MAX_HISTORY:
        del baccarat_history[:-MAX_HISTORY]

    hit = []
    miss = []
    settlement = []

    for uid, user_bets in bets.items():
        for bet in user_bets:
            typ = bet["type"]
            amount = bet["amount"]
            money_type = bet["money"]
            username = bet["name"]

            u = get_user(uid, username)
            unit = "원" if money_type == "R" else "P"
            balance = u["real_money"] if money_type == "R" else u["points"]

            if typ == result:
                payout = amount * 9 if result == "T" else amount * 2
                new_balance = balance + payout

                if money_type == "R":
                    update_user(uid, real_money=new_balance)
                else:
                    update_user(uid, points=new_balance)

                hit.append(
                    f"🎯 {username}님 적중하셨습니다!\n"
                    f"💰 적중금액: +{payout:,}{unit}\n"
                    f"💳 적중 후 보유머니: {new_balance:,}{unit}"
                )

                settlement.append(
                    f"🎯 {username}: {payout:,}{unit} 적중"
                )

            elif result == "T" and typ in ("P", "B"):
                new_balance = balance + amount

                if money_type == "R":
                    update_user(uid, real_money=new_balance)
                else:
                    update_user(uid, points=new_balance)

                settlement.append(
                    f"↩️ {username}: {amount:,}{unit} 반환"
                )

            else:
                miss.append(
                    f"❌ {username}님 미적중하셨습니다.\n"
                    f"💸 손실금액: -{amount:,}{unit}"
                )

                add_xp_and_check_level(uid, -1)

                settlement.append(
                    f"❌ {username}: -{amount:,}{unit}"
                )

    if hit:
        await bot.send_message(
            chat_id=chat_id,
            text="🎯 적중 결과\n━━━━━━━━━━━━━━\n" + "\n\n".join(hit)
        )

    if miss:
        await bot.send_message(
            chat_id=chat_id,
            text="📌 베팅 결과\n━━━━━━━━━━━━━━\n" + "\n\n".join(miss)
        )

    history_lines = ["📊 바카라 결과표", "━━━━━━━━━━━━━━"]

    for i, item in enumerate(reversed(baccarat_history), 1):
        name = {
            "P": "🔵 PLAYER",
            "B": "🔴 BANKER",
            "T": "🟢 TIE"
        }[item["result"]]

        history_lines.append(
            f"{i}. {name} ({item['player']} : {item['banker']})"
        )

    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(history_lines)
    )

    if settlement:
        await bot.send_message(
            chat_id=chat_id,
            text="💰 이번 바카라 정산\n━━━━━━━━━━━━━━\n" + "\n".join(settlement)
        )


async def baccarat_timer(application, chat_id):
    try:
        await asyncio.sleep(40)

        async with game_lock:
            if not baccarat_game["active"]:
                return

        await application.bot.send_message(
            chat_id=chat_id,
            text="⏰ 바카라 베팅 마감 10초 전!\n⚠️ 10초 후 베팅이 마감됩니다."
        )

        await asyncio.sleep(10)

        async with game_lock:
            if not baccarat_game["active"]:
                return

            baccarat_game["active"] = False
            bets = dict(baccarat_game["bets"])
            baccarat_game["bets"] = {}
            baccarat_game["chat_id"] = None

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 베팅 마감!\n"
                "━━━━━━━━━━━━━━\n"
                "🎰 베팅이 종료되었습니다.\n"
                "⏳ 10초 후 카드를 공개합니다."
            )
        )

        await asyncio.sleep(10)
        await play_baccarat(application.bot, chat_id, bets)

    except Exception as e:
        print("바카라 타이머 오류:", repr(e))


async def start_baccarat(update, context):
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    async with game_lock:
        if baccarat_game["active"]:
            await update.message.reply_text("🎰 이미 진행 중인 바카라가 있습니다.")
            return

        baccarat_game["active"] = True
        baccarat_game["bets"] = {}
        baccarat_game["chat_id"] = chat_id

    await update.message.reply_text(
        "🎰 바카라 베팅 시작!\n"
        "━━━━━━━━━━━━━━\n"
        "⏱️ 지금부터 50초 동안 베팅할 수 있습니다.\n\n"
        "💰 베팅 방법\n"
        "/배팅 플 5000\n"
        "/배팅 뱅 5000\n"
        "/배팅 타이 5000\n"
        "/배팅 플 실 50000\n\n"
        "🔵 플 = PLAYER\n"
        "🔴 뱅 = BANKER\n"
        "🟢 타이 = TIE"
    )

    context.application.create_task(
        baccarat_timer(context.application, chat_id)
    )


def odd_even_value(card):
    rank = card["rank"]

    if rank == "A":
        return 1
    if rank == "J":
        return 11
    if rank == "Q":
        return 12
    if rank == "K":
        return 13

    return int(rank)


def create_odd_even_gif(card1, card2, result):
    width, height = 500, 260
    frames = []
    bg = (20, 70, 45)

    title_font = get_font(23, True)
    label_font = get_font(18, True)
    result_font = get_font(22, True)

    front = Image.open(card1["file"]).convert("RGB").resize((110, 160))
    back = Image.open(
        os.path.join(CARD_DIR, "BACK.png")
    ).convert("RGB").resize((110, 160))
    second = Image.open(card2["file"]).convert("RGB").resize((110, 160))

    for _ in range(40):
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)

        draw.text(
            (width // 2, 25),
            "O D D  &  E V E N",
            font=title_font,
            fill=(245, 220, 140),
            anchor="ma"
        )

        img.paste(front, (110, 70))
        img.paste(back, (280, 70))

        draw.text(
            (165, 245),
            "첫 번째",
            font=label_font,
            fill="white",
            anchor="ma"
        )

        draw.text(
            (335, 245),
            "두 번째",
            font=label_font,
            fill="white",
            anchor="ma"
        )

        frames.append(img)

    for i in range(20):
        scale = max(0.06, 1.0 - i / 20.0)
        w = max(6, int(110 * scale))
        resized = second.resize((w, 160))

        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)

        draw.text(
            (width // 2, 25),
            "O D D  &  E V E N",
            font=title_font,
            fill=(245, 220, 140),
            anchor="ma"
        )

        img.paste(front, (110, 70))
        img.paste(resized, (335 - w // 2, 70))

        frames.append(img)

    for _ in range(4):
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)

        draw.text(
            (width // 2, 25),
            "O D D  &  E V E N",
            font=title_font,
            fill=(245, 220, 140),
            anchor="ma"
        )

        img.paste(front, (110, 70))
        img.paste(second, (280, 70))

        draw.text(
            (width // 2, 245),
            result,
            font=result_font,
            fill=(255, 225, 100),
            anchor="ma"
        )

        frames.append(img)

    path = os.path.join(CARD_DIR, "odd_even.gif")

    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=250,
        loop=0
    )

    return path


async def odd_even_timer(application, chat_id):
    try:
        await asyncio.sleep(40)

        async with odd_even_lock:
            if not odd_even_game["active"]:
                return

        await application.bot.send_message(
            chat_id=chat_id,
            text="⏰ 홀짝 베팅 마감 10초 전!\n⚠️ 10초 후 베팅이 마감됩니다."
        )

        await asyncio.sleep(10)

        async with odd_even_lock:
            if not odd_even_game["active"]:
                return

            odd_even_game["active"] = False
            bets = dict(odd_even_game["bets"])
            odd_even_game["bets"] = {}
            odd_even_game["chat_id"] = None

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 홀짝 베팅 마감!\n"
                "━━━━━━━━━━━━━━\n"
                "🎴 카드 공개 준비 중...\n"
                "⏳ 약 15초 후 결과가 공개됩니다."
            )
        )

        deck = create_deck()
        card1 = deck.pop()
        card2 = deck.pop()

        total = odd_even_value(card1) + odd_even_value(card2)
        result = "O" if total % 2 else "E"

        result_name = "🟢 홀" if result == "O" else "🔵 짝"

        path = create_odd_even_gif(card1, card2, result_name)

        with open(path, "rb") as f:
            await application.bot.send_animation(
                chat_id=chat_id,
                animation=InputFile(f),
                caption=(
                    f"🎴 홀짝 결과\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"첫 번째 카드: {card1['rank']}\n"
                    f"두 번째 카드: {card2['rank']}\n"
                    f"합계: {total}\n\n"
                    f"🏆 결과: {result_name}"
                )
            )

        hit = []
        miss = []

        for uid, user_bets in bets.items():
            for bet in user_bets:
                typ = bet["type"]
                amount = bet["amount"]
                money_type = bet["money"]
                username = bet["name"]

                u = get_user(uid, username)
                unit = "원" if money_type == "R" else "P"
                balance = u["real_money"] if money_type == "R" else u["points"]

                if typ == result:
                    payout = amount * 2
                    new_balance = balance + payout

                    if money_type == "R":
                        update_user(uid, real_money=new_balance)
                    else:
                        update_user(uid, points=new_balance)

                    hit.append(
                        f"🎯 {username}님 적중하셨습니다!\n"
                        f"💰 적중금액: +{payout:,}{unit}\n"
                        f"💳 적중 후 보유머니: {new_balance:,}{unit}"
                    )
                else:
                    miss.append(
                        f"❌ {username}님 미적중하셨습니다.\n"
                        f"💸 손실금액: -{amount:,}{unit}"
                    )

                    add_xp_and_check_level(uid, -1)

        if hit:
            await application.bot.send_message(
                chat_id=chat_id,
                text="🎯 적중 결과\n━━━━━━━━━━━━━━\n" + "\n\n".join(hit)
            )

        if miss:
            await application.bot.send_message(
                chat_id=chat_id,
                text="📌 베팅 결과\n━━━━━━━━━━━━━━\n" + "\n\n".join(miss)
            )

    except Exception as e:
        print("홀짝 타이머 오류:", repr(e))


async def odd_even_bet(update, context):
    if not update.message or not update.effective_user:
        return

    parsed = parse_game_bet(context.args)

    if not parsed or parsed[0] not in ("O", "E"):
        await update.message.reply_text(
            "사용법:\n"
            "/홀짝 홀 10000\n"
            "/홀짝 짝 10000\n"
            "/홀짝 홀 실 50000"
        )
        return

    bet_type, amount, money_type = parsed
    uid = update.effective_user.id
    username = update.effective_user.first_name or "유저"

    async with odd_even_lock:
        if not odd_even_game["active"]:
            await update.message.reply_text("❌ 현재 홀짝 베팅 시간이 아닙니다.")
            return

        u = get_user(uid, username)
        balance = u["real_money"] if money_type == "R" else u["points"]
        unit = "원" if money_type == "R" else "P"

        if balance < amount:
            await update.message.reply_text(
                f"❌ {'실머니' if money_type == 'R' else '포인트'}가 부족합니다.\n"
                f"현재: {balance:,}{unit}"
            )
            return

        new_balance = balance - amount

        if money_type == "R":
            update_user(uid, real_money=new_balance)
        else:
            update_user(uid, points=new_balance)

        odd_even_game["bets"].setdefault(uid, []).append({
            "type": bet_type,
            "amount": amount,
            "money": money_type,
            "name": username
        })

    name = "홀" if bet_type == "O" else "짝"

    await update.message.reply_text(
        f"✅ {name} 베팅 완료되었습니다!\n"
        f"👤 {username}\n"
        f"🎯 {name}\n"
        f"💰 베팅금액: {amount:,}{unit}\n"
        f"💳 베팅 후 보유머니: {new_balance:,}{unit}"
    )


async def start_odd_even(update, context):
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    async with odd_even_lock:
        if odd_even_game["active"]:
            await update.message.reply_text("🎴 이미 진행 중인 홀짝 게임이 있습니다.")
            return

        odd_even_game["active"] = True
        odd_even_game["bets"] = {}
        odd_even_game["chat_id"] = chat_id

    await update.message.reply_text(
        "🎴 홀짝 베팅 시작!\n"
        "━━━━━━━━━━━━━━\n"
        "⏱️ 지금부터 50초 동안 자유롭게 베팅할 수 있습니다.\n\n"
        "💰 베팅 방법\n"
        "/홀짝 홀 10000\n"
        "/홀짝 짝 10000\n"
        "/홀짝 홀 실 50000\n\n"
        "🟢 홀 = ODD\n"
        "🔵 짝 = EVEN"
    )

    context.application.create_task(
        odd_even_timer(context.application, chat_id)
    )


async def help_command(update, context):
    if not update.message:
        return

    await update.message.reply_text(
        "📖 명령어\n"
        "━━━━━━━━━━━━━━\n"
        "/내정보\n"
        "/출석\n"
        "/레벨업\n"
        "/채팅순위\n"
        "/복권\n"
        "/복권 실\n\n"
        "/바카라\n"
        "/배팅 플 1000\n"
        "/배팅 뱅 1000\n"
        "/배팅 타이 1000\n"
        "/배팅 플 실 50000\n\n"
        "/홀짝\n"
        "/홀짝 홀 1000\n"
        "/홀짝 짝 1000\n"
        "/홀짝 홀 실 50000\n\n"
        "👑 관리자\n"
        "/지급 금액\n"
        "/지급 유저ID 금액\n"
        "/차감 금액\n"
        "/차감 유저ID 금액\n"
        "/실머니지급 금액\n"
        "/실머니지급 유저ID 금액\n"
        "/실머니차감 금액\n"
        "/실머니차감 유저ID 금액\n"
        "/경험치 금액\n"
        "/경험치 유저ID 금액\n"
        "/경험치차감 금액\n"
        "/경험치차감 유저ID 금액"
    )


async def korean_commands(update, context):
    if not update.message or not update.message.text:
        return

    parts = update.message.text.strip().split()

    if not parts:
        return

    command = parts[0].split("@")[0]

    handlers = {
        "/내정보": my_info,
        "/출석": attendance,
        "/레벨업": level_up,
        "/채팅순위": chat_ranking,
        "/복권": buy_lottery,
        "/바카라": start_baccarat,
        "/홀짝": start_odd_even,
        "/도움말": help_command,
        "/지급": admin_give,
        "/차감": admin_take,
        "/실머니지급": admin_real_give,
        "/실머니차감": admin_real_take,
        "/경험치": admin_xp_give,
        "/경험치차감": admin_xp_take,
        "/배팅": baccarat_bet,
        "/베팅": baccarat_bet,
    }

    handler = handlers.get(command)

    if handler is None:
        return

    context.args = parts[1:]
    await handler(update, context)


async def error_handler(update, context):
    print("BOT ERROR:", repr(context.error))


def main():
    print("================================")
    print("Telegram Bot Starting...")
    print("================================")

    web_thread = Thread(
        target=run_web_server,
        daemon=True
    )
    web_thread.start()

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", help_command)
    )

    command_pattern = (
        r"^/(내정보|출석|레벨업|채팅순위|복권|바카라|홀짝|도움말|"
        r"지급|차감|실머니지급|실머니차감|경험치|경험치차감|배팅|베팅)"
        r"(?:@[\w_]+)?"
        r"(?:\s+.*)?$"
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(command_pattern),
            korean_commands
        ),
        group=0
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_chat
        ),
        group=1
    )

    application.add_error_handler(error_handler)

    print("Bot is running.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
