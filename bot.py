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


# ============================================================
# 기본 설정
# ============================================================

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


# ============================================================
# 레벨 설정
# ============================================================

LEVEL_NAMES = {
    1: "돌맹이",
    2: "동",
    3: "은",
    4: "골드",
    5: "다이아",
}


# 레벨업에 필요한 XP
XP_REQUIREMENTS = {
    1: 300,
    2: 1000,
    3: 5000,
    4: 10000,
}


# 레벨업 시 받는 포인트
LEVEL_UP_REWARDS = {
    1: 5000,
    2: 10000,
    3: 20000,
    4: 50000,
}


# ============================================================
# 바카라 상태
# ============================================================

baccarat_game = {
    "active": False,
    "bets": {},
    "chat_id": None,
}


# 최근 바카라 결과
baccarat_history = []

MAX_HISTORY = 20


db_lock = asyncio.Lock()
game_lock = asyncio.Lock()


# ============================================================
# Render 웹서버
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Telegram Bot is Running Successfully!"
        )

    def log_message(self, format, *args):
        return


def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            "8080"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# SQLite
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=20,
        check_same_thread=False
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=20000"
    )

    return conn


def init_db():

    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '유저',
            points INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_attendance TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# 사용자 조회
# ============================================================

def get_user(
    user_id,
    username="유저"
):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            user_id,
            username,
            points,
            xp,
            level,
            last_attendance
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    if row is None:

        cur.execute("""
            INSERT INTO users
            (
                user_id,
                username,
                points,
                xp,
                level,
                last_attendance
            )
            VALUES (?, ?, 0, 0, 1, NULL)
        """, (
            user_id,
            username
        ))

        conn.commit()

        result = {
            "user_id": user_id,
            "username": username,
            "points": 0,
            "xp": 0,
            "level": 1,
            "last_attendance": None,
        }

    else:

        result = {
            "user_id": row[0],
            "username": row[1],
            "points": row[2],
            "xp": row[3],
            "level": row[4],
            "last_attendance": row[5],
        }

        if username and username != row[1]:

            cur.execute("""
                UPDATE users
                SET username = ?
                WHERE user_id = ?
            """, (
                username,
                user_id
            ))

            conn.commit()

    conn.close()

    return result


# ============================================================
# 사용자 수정
# ============================================================

def update_user(
    user_id,
    points=None,
    xp=None,
    level=None,
    last_attendance=None
):

    conn = db_connect()
    cur = conn.cursor()

    if points is not None:

        cur.execute("""
            UPDATE users
            SET points = ?
            WHERE user_id = ?
        """, (
            points,
            user_id
        ))

    if xp is not None:

        cur.execute("""
            UPDATE users
            SET xp = ?
            WHERE user_id = ?
        """, (
            xp,
            user_id
        ))

    if level is not None:

        cur.execute("""
            UPDATE users
            SET level = ?
            WHERE user_id = ?
        """, (
            level,
            user_id
        ))

    if last_attendance is not None:

        cur.execute("""
            UPDATE users
            SET last_attendance = ?
            WHERE user_id = ?
        """, (
            last_attendance,
            user_id
        ))

    conn.commit()
    conn.close()


# ============================================================
# XP 지급 후 자동 레벨업
# ============================================================

def add_xp_and_check_level(
    user_id,
    amount
):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT xp, level, points
        FROM users
        WHERE user_id = ?
    """, (
        user_id,
    ))

    row = cur.fetchone()

    if row is None:

        conn.close()
        return None

    xp = row[0]
    level = row[1]
    points = row[2]

    xp = max(
        0,
        xp + amount
    )

    level_up_messages = []

    while level < 5:

        required_xp = XP_REQUIREMENTS.get(
            level
        )

        reward = LEVEL_UP_REWARDS.get(
            level,
            0
        )

        if required_xp is None:
            break

        if xp < required_xp:
            break

        level += 1

        points += reward

        level_up_messages.append({
            "level": level,
            "reward": reward
        })

    cur.execute("""
        UPDATE users
        SET
            xp = ?,
            level = ?,
            points = ?
        WHERE user_id = ?
    """, (
        xp,
        level,
        points,
        user_id
    ))

    conn.commit()
    conn.close()

    return {
        "xp": xp,
        "level": level,
        "points": points,
        "level_ups": level_up_messages
    }


# ============================================================
# 내정보
# ============================================================

async def my_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    user = update.effective_user

    username = (
        user.first_name
        or "유저"
    )

    u = get_user(
        user.id,
        username
    )

    level_name = LEVEL_NAMES.get(
        u["level"],
        "최고 등급"
    )

    next_xp = XP_REQUIREMENTS.get(
        u["level"],
        "MAX"
    )

    next_reward = LEVEL_UP_REWARDS.get(
        u["level"],
        "MAX"
    )

    if isinstance(next_reward, int):
        reward_text = f"{next_reward:,}P"
    else:
        reward_text = next_reward

    await update.message.reply_text(
        f"👤 [{username}] 님의 정보\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏅 레벨: {u['level']} [{level_name}]\n"
        f"💰 보유 포인트: {u['points']:,}P\n"
        f"✨ 경험치: {u['xp']} / {next_xp}\n"
        f"🎁 다음 레벨업 보상: {reward_text}"
    )


# ============================================================
# 출석
# ============================================================

async def attendance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    today = datetime.now(
        KR_TZ
    ).strftime("%Y-%m-%d")

    async with db_lock:

        conn = db_connect()
        cur = conn.cursor()

        cur.execute("""
            SELECT points, last_attendance
            FROM users
            WHERE user_id = ?
        """, (
            user_id,
        ))

        row = cur.fetchone()

        if row is None:

            cur.execute("""
                INSERT INTO users
                (
                    user_id,
                    username,
                    points,
                    xp,
                    level,
                    last_attendance
                )
                VALUES (?, ?, 1000, 0, 1, ?)
            """, (
                user_id,
                username,
                today
            ))

            conn.commit()

            already = False
            new_points = 1000

        else:

            points = row[0]
            last_attendance = row[1]

            if last_attendance == today:

                already = True
                new_points = points

            else:

                new_points = points + 1000

                cur.execute("""
                    UPDATE users
                    SET
                        points = ?,
                        last_attendance = ?,
                        username = ?
                    WHERE user_id = ?
                """, (
                    new_points,
                    today,
                    username,
                    user_id
                ))

                conn.commit()

                already = False

        conn.close()

    if already:

        await update.message.reply_text(
            "❌ 오늘은 이미 출석체크를 완료했습니다.\n"
            "🌙 한국시간 00:00 이후 다시 출석할 수 있습니다."
        )

        return

    await update.message.reply_text(
        f"📆 출석체크 완료!\n"
        f"🎁 +1,000P 지급\n"
        f"💰 현재: {new_points:,}P"
    )


# ============================================================
# 레벨업
# 자동으로 처리되므로 수동 명령어는 안내용
# ============================================================

async def level_up(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    u = get_user(
        update.effective_user.id
    )

    if u["level"] >= 5:

        await update.message.reply_text(
            "👑 이미 최고 레벨 [다이아]입니다."
        )

        return

    required_xp = XP_REQUIREMENTS[
        u["level"]
    ]

    reward = LEVEL_UP_REWARDS[
        u["level"]
    ]

    if u["xp"] < required_xp:

        await update.message.reply_text(
            f"✨ 레벨업은 자동으로 진행됩니다.\n\n"
            f"현재: {u['xp']:,} XP\n"
            f"필요: {required_xp:,} XP\n"
            f"🎁 레벨업 보상: {reward:,}P"
        )

        return

    result = add_xp_and_check_level(
        u["user_id"],
        0
    )

    if result and result["level_ups"]:

        text = "🎉 레벨업 완료!\n"

        for item in result["level_ups"]:

            text += (
                f"\n🏅 Lv.{item['level']} "
                f"[{LEVEL_NAMES[item['level']]}]\n"
                f"🎁 +{item['reward']:,}P 지급"
            )

        await update.message.reply_text(
            text
        )

    else:

        await update.message.reply_text(
            "ℹ️ 경험치가 기준에 도달하면 "
            "자동으로 레벨업됩니다."
        )


# ============================================================
# 채팅 XP
# ============================================================

async def handle_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    if not update.effective_user:
        return

    text = update.message.text.strip()

    if text.startswith("/"):
        return

    user_id = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    get_user(
        user_id,
        username
    )

    added_xp = 1

    if random.random() < 0.0003:

        bonus = random.randint(
            50,
            100
        )

        added_xp += bonus

        await update.message.reply_text(
            f"🎁 깜짝 경험치 이벤트!\n"
            f"✨ +{bonus} XP 획득!"
        )

    async with db_lock:

        result = add_xp_and_check_level(
            user_id,
            added_xp
        )

    if result and result["level_ups"]:

        for item in result["level_ups"]:

            await update.message.reply_text(
                f"🎉 레벨업!\n"
                f"🏅 Lv.{item['level']} "
                f"[{LEVEL_NAMES[item['level']]}]\n"
                f"🎁 레벨업 보상 "
                f"+{item['reward']:,}P 지급!"
            )


# ============================================================
# 복권
# ============================================================

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
        (5000, 1),
    ]

    return random.choices(
        [r[0] for r in rewards],
        weights=[r[1] for r in rewards],
        k=1
    )[0]


async def buy_lottery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    cost = 1000

    async with db_lock:

        conn = db_connect()
        cur = conn.cursor()

        cur.execute("""
            SELECT points
            FROM users
            WHERE user_id = ?
        """, (
            user_id,
        ))

        row = cur.fetchone()

        if row is None:

            cur.execute("""
                INSERT INTO users
                (
                    user_id,
                    username,
                    points,
                    xp,
                    level
                )
                VALUES (?, ?, 0, 0, 1)
            """, (
                user_id,
                username
            ))

            points = 0

        else:

            points = row[0]

        if points < cost:

            conn.close()

            await update.message.reply_text(
                f"❌ 복권 가격은 {cost:,}P입니다.\n"
                f"현재: {points:,}P"
            )

            return

        rand = random.random() * 100

        if rand < 0.05:

            rank = "1등 🥇"
            prize = 50000

        elif rand < 0.15:

            rank = "2등 🥈"
            prize = 30000

        elif rand < 0.95:

            rank = "3등 🥉"
            prize = 10000

        elif rand < 2.15:

            rank = "4등 🏅"
            prize = 7000

        else:

            rank = "5등 🎗️"
            prize = lottery_5th_prize()

        new_points = (
            points
            - cost
            + prize
        )

        cur.execute("""
            UPDATE users
            SET
                points = ?,
                username = ?
            WHERE user_id = ?
        """, (
            new_points,
            username,
            user_id
        ))

        conn.commit()
        conn.close()

    await update.message.reply_text(
        f"🎫 복권 결과\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏆 등수: {rank}\n"
        f"💰 당첨: {prize:,}P\n"
        f"💳 구매: -{cost:,}P\n"
        f"💵 현재: {new_points:,}P"
    )


# ============================================================
# 관리자
# ============================================================

def is_admin(user_id):

    return user_id in ADMIN_IDS


# ============================================================
# 관리자 포인트 지급
# ============================================================

async def admin_give(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ 관리자만 사용할 수 있습니다."
        )

        return

    try:

        args = context.args

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0]
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1]
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(
            target_id
        )

        new_points = (
            t["points"]
            + amount
        )

        update_user(
            target_id,
            points=new_points
        )

        await update.message.reply_text(
            f"✅ 지급 완료\n"
            f"👤 {target_id}\n"
            f"💰 +{amount:,}P\n"
            f"💳 현재: {new_points:,}P"
        )

    except Exception:

        await update.message.reply_text(
            "사용법: /지급 금액\n"
            "또는 /지급 유저ID 금액"
        )


# ============================================================
# 관리자 포인트 차감
# ============================================================

async def admin_take(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ 관리자만 사용할 수 있습니다."
        )

        return

    try:

        args = context.args

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0]
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1]
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(
            target_id
        )

        new_points = max(
            0,
            t["points"] - amount
        )

        update_user(
            target_id,
            points=new_points
        )

        await update.message.reply_text(
            f"✅ 차감 완료\n"
            f"👤 {target_id}\n"
            f"💸 -{amount:,}P\n"
            f"💳 현재: {new_points:,}P"
        )

    except Exception:

        await update.message.reply_text(
            "사용법: /차감 금액\n"
            "또는 /차감 유저ID 금액"
        )


# ============================================================
# 관리자 경험치 지급
# ============================================================

async def admin_xp_give(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ 관리자만 사용할 수 있습니다."
        )

        return

    try:

        args = context.args

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0]
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1]
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        get_user(
            target_id
        )

        result = add_xp_and_check_level(
            target_id,
            amount
        )

        text = (
            f"✅ 경험치 지급 완료\n"
            f"👤 유저ID: {target_id}\n"
            f"✨ +{amount:,} XP\n"
            f"📊 현재 경험치: "
            f"{result['xp']:,} XP"
        )

        if result["level_ups"]:

            for item in result["level_ups"]:

                text += (
                    f"\n\n🎉 자동 레벨업!\n"
                    f"🏅 Lv.{item['level']} "
                    f"[{LEVEL_NAMES[item['level']]}]\n"
                    f"🎁 +{item['reward']:,}P 지급"
                )

        await update.message.reply_text(
            text
        )

    except Exception:

        await update.message.reply_text(
            "사용법:\n"
            "/경험치 100\n"
            "또는\n"
            "/경험치 유저ID 100"
        )


# ============================================================
# 관리자 경험치 차감
# ============================================================

async def admin_xp_take(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ 관리자만 사용할 수 있습니다."
        )

        return

    try:

        args = context.args

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0]
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1]
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(
            target_id
        )

        new_xp = max(
            0,
            t["xp"] - amount
        )

        update_user(
            target_id,
            xp=new_xp
        )

        await update.message.reply_text(
            f"✅ 경험치 차감 완료\n"
            f"👤 유저ID: {target_id}\n"
            f"✨ -{amount:,} XP\n"
            f"📊 현재 경험치: "
            f"{new_xp:,} XP"
        )

    except Exception:

        await update.message.reply_text(
            "사용법:\n"
            "/경험치차감 100\n"
            "또는\n"
            "/경험치차감 유저ID 100"
        )


# ============================================================
# 카드
# ============================================================

SUITS = {
    "S": ("♠", "black"),
    "H": ("♥", "red"),
    "D": ("♦", "red"),
    "C": ("♣", "black"),
}


RANKS = [
    "A",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "J",
    "Q",
    "K"
]


def get_font(
    size,
    bold=False
):

    paths = [
        (
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf"
            if bold
            else
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf"
        ),
        (
            "/usr/share/fonts/truetype/liberation2/"
            "LiberationSans-Bold.ttf"
            if bold
            else
            "/usr/share/fonts/truetype/liberation2/"
            "LiberationSans-Regular.ttf"
        ),
    ]

    for path in paths:

        if os.path.exists(path):

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


# ============================================================
# 카드 이미지 생성
# 카드 크기 80 x 115
# ============================================================

def create_card_images():

    os.makedirs(
        CARD_DIR,
        exist_ok=True
    )

    width = 80
    height = 115

    rank_font = get_font(
        15,
        True
    )

    suit_font = get_font(
        14,
        True
    )

    center_font = get_font(
        32,
        True
    )

    back_path = os.path.join(
        CARD_DIR,
        "BACK.png"
    )

    if not os.path.exists(
        back_path
    ):

        img = Image.new(
            "RGB",
            (width, height),
            "white"
        )

        draw = ImageDraw.Draw(
            img
        )

        draw.rounded_rectangle(
            (
                2,
                2,
                width - 2,
                height - 2
            ),
            radius=8,
            fill=(35, 70, 150),
            outline="white",
            width=3
        )

        draw.rounded_rectangle(
            (
                8,
                8,
                width - 8,
                height - 8
            ),
            radius=6,
            outline="white",
            width=2
        )

        draw.text(
            (
                width // 2,
                height // 2
            ),
            "★",
            font=center_font,
            fill="white",
            anchor="mm"
        )

        img.save(
            back_path
        )

    for suit_code, (
        symbol,
        color
    ) in SUITS.items():

        fill = (
            "red"
            if color == "red"
            else "black"
        )

        for rank in RANKS:

            path = os.path.join(
                CARD_DIR,
                f"{rank}{suit_code}.png"
            )

            if os.path.exists(path):
                continue

            img = Image.new(
                "RGB",
                (width, height),
                "white"
            )

            draw = ImageDraw.Draw(
                img
            )

            draw.rounded_rectangle(
                (
                    1,
                    1,
                    width - 1,
                    height - 1
                ),
                radius=8,
                outline="black",
                width=2
            )

            draw.text(
                (6, 4),
                rank,
                font=rank_font,
                fill=fill
            )

            draw.text(
                (6, 21),
                symbol,
                font=suit_font,
                fill=fill
            )

            draw.text(
                (
                    width // 2,
                    height // 2
                ),
                symbol,
                font=center_font,
                fill=fill,
                anchor="mm"
            )

            draw.text(
                (
                    width - 6,
                    height - 4
                ),
                rank,
                font=rank_font,
                fill=fill,
                anchor="rs"
            )

            img.save(
                path
            )


create_card_images()


# ============================================================
# 카드 덱
# ============================================================

def create_deck():

    deck = []

    for suit in SUITS:

        for rank in RANKS:

            deck.append({
                "rank": rank,
                "suit": suit,
                "file": os.path.join(
                    CARD_DIR,
                    f"{rank}{suit}.png"
                )
            })

    random.shuffle(deck)

    return deck


def card_value(card):

    rank = card["rank"]

    if rank == "A":
        return 1

    if rank in (
        "10",
        "J",
        "Q",
        "K"
    ):
        return 0

    return int(rank)


def baccarat_score(cards):

    return (
        sum(
            card_value(c)
            for c in cards
        )
        % 10
    )


# ============================================================
# 바카라 이미지
# 카드 80 x 115
# ============================================================

def create_baccarat_image(
    player,
    banker,
    result_text=None
):

    width = 500
    height = 285

    img = Image.new(
        "RGB",
        (width, height),
        (20, 70, 45)
    )

    draw = ImageDraw.Draw(
        img
    )

    draw.rounded_rectangle(
        (
            5,
            5,
            width - 5,
            height - 5
        ),
        radius=18,
        outline=(210, 170, 70),
        width=4
    )

    title_font = get_font(
        22,
        True
    )

    label_font = get_font(
        18,
        True
    )

    score_font = get_font(
        19,
        True
    )

    result_font = get_font(
        20,
        True
    )

    draw.text(
        (
            width // 2,
            25
        ),
        "B A C C A R A",
        font=title_font,
        fill=(245, 220, 140),
        anchor="ma"
    )

    card_w = 80
    card_h = 115
    card_gap = 5

    player_x = 70
    banker_x = 320
    card_y = 75

    draw.text(
        (
            player_x + 40,
            62
        ),
        "PLAYER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    draw.text(
        (
            banker_x + 40,
            62
        ),
        "BANKER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    def paste_cards(
        cards,
        start_x
    ):

        for index, card in enumerate(cards):

            card_img = Image.open(
                card["file"]
            ).convert("RGB")

            card_img = card_img.resize(
                (
                    card_w,
                    card_h
                )
            )

            x = (
                start_x
                + index * (
                    card_w
                    + card_gap
                )
            )

            y = card_y

            img.paste(
                card_img,
                (x, y)
            )

    paste_cards(
        player,
        player_x
    )

    paste_cards(
        banker,
        banker_x
    )

    player_score = baccarat_score(
        player
    )

    banker_score = baccarat_score(
        banker
    )

    draw.text(
        (
            player_x + 40,
            205
        ),
        f"PLAYER  {player_score}",
        font=score_font,
        fill="white",
        anchor="ma"
    )

    draw.text(
        (
            banker_x + 40,
            205
        ),
        f"BANKER  {banker_score}",
        font=score_font,
        fill="white",
        anchor="ma"
    )

    if result_text:

        draw.text(
            (
                width // 2,
                255
            ),
            result_text,
            font=result_font,
            fill=(255, 225, 100),
            anchor="mm"
        )

    path = os.path.join(
        CARD_DIR,
        "baccarat_result.png"
    )

    img.save(
        path,
        quality=95
    )

    return path


async def send_baccarat_image(
    bot,
    chat_id,
    player,
    banker,
    caption=None
):

    path = create_baccarat_image(
        player,
        banker,
        caption
    )

    with open(
        path,
        "rb"
    ) as f:

        await bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(f),
            caption=caption
        )


# ============================================================
# 바카라 베팅 파싱
# ============================================================

def parse_bet(args):

    if len(args) != 2:
        return None

    bet_type = (
        args[0]
        .strip()
        .lower()
    )

    # PLAYER
    if bet_type in (
        "p",
        "플",
        "플레이어",
        "player"
    ):

        bet_type = "P"

    # BANKER
    elif bet_type in (
        "b",
        "뱅",
        "뱅커",
        "banker"
    ):

        bet_type = "B"

    # TIE
    elif bet_type in (
        "t",
        "타이",
        "tie"
    ):

        bet_type = "T"

    else:

        return None

    try:

        amount = int(
            args[1].replace(
                ",",
                ""
            )
        )

    except ValueError:

        return None

    if amount <= 0:

        return None

    return (
        bet_type,
        amount
    )


# ============================================================
# 바카라 베팅
# ============================================================

async def baccarat_bet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    if not baccarat_game["active"]:

        await update.message.reply_text(
            "🎰 현재 베팅 시간이 아닙니다."
        )

        return

    parsed = parse_bet(
        context.args
    )

    if not parsed:

        await update.message.reply_text(
            "사용법:\n"
            "/배팅 플 5000\n"
            "/배팅 뱅 5000\n"
            "/배팅 타이 5000"
        )

        return

    bet_type, amount = parsed

    user_id = (
        update.effective_user.id
    )

    username = (
        update.effective_user.first_name
        or "유저"
    )

    async with game_lock:

        if not baccarat_game["active"]:

            await update.message.reply_text(
                "❌ 베팅이 이미 마감되었습니다."
            )

            return

        if user_id in baccarat_game["bets"]:

            await update.message.reply_text(
                "❌ 이번 바카라에는 "
                "이미 베팅했습니다."
            )

            return

        u = get_user(
            user_id,
            username
        )

        if u["points"] < amount:

            await update.message.reply_text(
                f"❌ 포인트가 부족합니다.\n"
                f"현재: {u['points']:,}P"
            )

            return

        update_user(
            user_id,
            points=u["points"] - amount
        )

        baccarat_game["bets"][user_id] = {
            "type": bet_type,
            "amount": amount,
            "name": username,
        }

    names = {
        "P": "PLAYER",
        "B": "BANKER",
        "T": "TIE",
    }

    await update.message.reply_text(
        f"🎰 베팅 완료!\n"
        f"👤 {username}\n"
        f"🎯 {names[bet_type]}\n"
        f"💰 {amount:,}P"
    )


# ============================================================
# 바카라 결과표
# ============================================================

def add_baccarat_history(
    result,
    player_score,
    banker_score
):

    baccarat_history.append({
        "result": result,
        "player": player_score,
        "banker": banker_score,
    })

    if len(baccarat_history) > MAX_HISTORY:

        del baccarat_history[
            :-MAX_HISTORY
        ]


def make_result_history_text():

    if not baccarat_history:

        return "📊 바카라 결과표\n기록 없음"

    lines = [
        "📊 바카라 결과표",
        "━━━━━━━━━━━━━━"
    ]

    for index, item in enumerate(
        reversed(baccarat_history),
        1
    ):

        result = item["result"]

        if result == "P":
            result_name = "🔵 PLAYER"
        elif result == "B":
            result_name = "🔴 BANKER"
        else:
            result_name = "🟢 TIE"

        lines.append(
            f"{index}. {result_name} "
            f"({item['player']} : "
            f"{item['banker']})"
        )

    return "\n".join(lines)


# ============================================================
# 바카라 게임
# ============================================================

async def play_baccarat(
    bot,
    chat_id
):

    async with game_lock:

        if not baccarat_game["active"]:
            return

        baccarat_game["active"] = False

        bets = dict(
            baccarat_game["bets"]
        )

        baccarat_game["bets"] = {}

    deck = create_deck()

    player = []
    banker = []

    # ========================================================
    # 1. 첫 4장 준비
    # ========================================================

    player.append(
        deck.pop()
    )

    player.append(
        deck.pop()
    )

    banker.append(
        deck.pop()
    )

    banker.append(
        deck.pop()
    )

    # ========================================================
    # 2. PLAYER 두 장 먼저 공개
    # ========================================================

    await bot.send_message(
        chat_id=chat_id,
        text="🎰 바카라 카드 공개를 시작합니다!"
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        [],
        "🔵 PLAYER 카드 공개"
    )

    # ========================================================
    # 3. 3초 후 BANKER 두 장 공개
    # ========================================================

    await asyncio.sleep(3)

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🔴 BANKER 카드 공개"
    )

    player_score = baccarat_score(
        player
    )

    banker_score = baccarat_score(
        banker
    )

    # ========================================================
    # 4. 내추럴
    # ========================================================

    natural = (
        player_score in (8, 9)
        or banker_score in (8, 9)
    )

    # ========================================================
    # 5. 추가 카드
    # ========================================================

    if not natural:

        player_third = None

        # ----------------------------------------------------
        # PLAYER 추가카드
        # ----------------------------------------------------

        if player_score <= 5:

            await asyncio.sleep(1)

            player_third = deck.pop()

            player.append(
                player_third
            )

            await send_baccarat_image(
                bot,
                chat_id,
                player,
                banker,
                "🔵 PLAYER 추가 카드"
            )

        # ----------------------------------------------------
        # BANKER 추가카드 결정
        # ----------------------------------------------------

        banker_score = baccarat_score(
            banker
        )

        if player_third is None:

            # PLAYER가 추가카드를 안 받았으면
            # BANKER는 0~5에서 추가
            if banker_score <= 5:

                await asyncio.sleep(1)

                banker.append(
                    deck.pop()
                )

                await send_baccarat_image(
                    bot,
                    chat_id,
                    player,
                    banker,
                    "🔴 BANKER 추가 카드"
                )

        else:

            third_value = card_value(
                player_third
            )

            banker_draw = False

            if banker_score <= 2:

                banker_draw = True

            elif banker_score == 3:

                banker_draw = (
                    third_value != 8
                )

            elif banker_score == 4:

                banker_draw = (
                    third_value in (
                        2,
                        3,
                        4,
                        5,
                        6,
                        7
                    )
                )

            elif banker_score == 5:

                banker_draw = (
                    third_value in (
                        4,
                        5,
                        6,
                        7
                    )
                )

            elif banker_score == 6:

                banker_draw = (
                    third_value in (
                        6,
                        7
                    )
                )

            if banker_draw:

                await asyncio.sleep(1)

                banker.append(
                    deck.pop()
                )

                await send_baccarat_image(
                    bot,
                    chat_id,
                    player,
                    banker,
                    "🔴 BANKER 추가 카드"
                )

    # ========================================================
    # 6. 최종 점수
    # ========================================================

    player_score = baccarat_score(
        player
    )

    banker_score = baccarat_score(
        banker
    )

    if player_score > banker_score:

        result = "P"

        result_text = (
            "🔵 PLAYER 승리!"
        )

    elif banker_score > player_score:

        result = "B"

        result_text = (
            "🔴 BANKER 승리!"
        )

    else:

        result = "T"

        result_text = (
            "🟢 TIE!"
        )

    # ========================================================
    # 7. 최종 카드 이미지
    # ========================================================

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        result_text
    )

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🎰 바카라 최종 결과\n"
            "━━━━━━━━━━━━━━\n"
            f"🔵 PLAYER: {player_score}점\n"
            f"🔴 BANKER: {banker_score}점\n\n"
            f"🏆 {result_text}"
        )
    )

    # ========================================================
    # 8. 결과 기록
    # ========================================================

    add_baccarat_history(
        result,
        player_score,
        banker_score
    )

    # ========================================================
    # 9. 개인별 정산
    # ========================================================

    settlement_lines = []

    hit_lines = []

    miss_lines = []

    for user_id, bet in bets.items():

        bet_type = bet["type"]
        amount = bet["amount"]
        username = bet["name"]

        names = {
            "P": "PLAYER",
            "B": "BANKER",
            "T": "TIE",
        }

        bet_name = names[
            bet_type
        ]

        u = get_user(
            user_id,
            username
        )

        # ----------------------------------------------------
        # 적중
        # ----------------------------------------------------

        if bet_type == result:

            if result == "T":

                payout = amount * 9

            else:

                payout = amount * 2

            new_points = (
                u["points"]
                + payout
            )

            update_user(
                user_id,
                points=new_points
            )

            xp_result = add_xp_and_check_level(
                user_id,
                1
            )

            hit_lines.append(
                f"🎯 {username}님 "
                f"{bet_name} 적중하셨습니다!\n"
                f"💰 +{payout:,}P\n"
                f"✨ +1 XP"
            )

            settlement_lines.append(
                f"🎯 {username}: "
                f"{bet_name} 적중 "
                f"+{payout:,}P"
            )

            if (
                xp_result
                and xp_result["level_ups"]
            ):

                for item in xp_result[
                    "level_ups"
                ]:

                    hit_lines.append(
                        f"🎉 {username}님 "
                        f"레벨업!\n"
                        f"🏅 Lv.{item['level']} "
                        f"[{LEVEL_NAMES[item['level']]}]\n"
                        f"🎁 +{item['reward']:,}P"
                    )

        # ----------------------------------------------------
        # TIE로 P/B 베팅 반환
        # ----------------------------------------------------

        elif (
            result == "T"
            and bet_type in ("P", "B")
        ):

            payout = amount

            new_points = (
                u["points"]
                + payout
            )

            update_user(
                user_id,
                points=new_points
            )

            settlement_lines.append(
                f"↩️ {username}: "
                f"{bet_name} 무승부 "
                f"{payout:,}P 반환"
            )

        # ----------------------------------------------------
        # 미적중
        # ----------------------------------------------------

        else:

            new_xp = max(
                0,
                u["xp"] - 1
            )

            update_user(
                user_id,
                xp=new_xp
            )

            miss_lines.append(
                f"❌ {username}님 "
                f"{bet_name} 미적중하셨습니다.\n"
                f"💸 -{amount:,}P\n"
                f"✨ -1 XP"
            )

            settlement_lines.append(
                f"❌ {username}: "
                f"{bet_name} 미적중 "
                f"-{amount:,}P"
            )

    # ========================================================
    # 10. 적중자 메시지
    # ========================================================

    if hit_lines:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🎯 적중 결과\n"
                "━━━━━━━━━━━━━━\n"
                + "\n\n".join(
                    hit_lines
                )
            )
        )

    # ========================================================
    # 11. 미적중자 메시지
    # ========================================================

    if miss_lines:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "📌 베팅 결과\n"
                "━━━━━━━━━━━━━━\n"
                + "\n\n".join(
                    miss_lines
                )
            )
        )

    # ========================================================
    # 12. 바카라 결과표
    # ========================================================

    await bot.send_message(
        chat_id=chat_id,
        text=(
            make_result_history_text()
        )
    )

    # ========================================================
    # 13. 전체 정산 요약
    # ========================================================

    if settlement_lines:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "💰 이번 바카라 정산\n"
                "━━━━━━━━━━━━━━\n"
                + "\n".join(
                    settlement_lines
                )
            )
        )


# ============================================================
# 바카라 시작
# ============================================================

async def start_baccarat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_chat:
        return

    chat_id = (
        update.effective_chat.id
    )

    async with game_lock:

        if baccarat_game["active"]:

            await update.message.reply_text(
                "🎰 이미 진행 중인 바카라가 있습니다."
            )

            return

        baccarat_game["active"] = True
        baccarat_game["bets"] = {}
        baccarat_game["chat_id"] = chat_id

    # ========================================================
    # 베팅 시작
    # ========================================================

    await update.message.reply_text(
        "🎰 바카라 베팅 시작!\n"
        "━━━━━━━━━━━━━━\n"
        "⏱️ 지금부터 50초 동안 베팅할 수 있습니다.\n\n"
        "💰 베팅 방법\n"
        "/배팅 플 5000\n"
        "/배팅 뱅 5000\n"
        "/배팅 타이 5000\n\n"
        "🔵 플 = PLAYER\n"
        "🔴 뱅 = BANKER\n"
        "🟢 타이 = TIE"
    )

    # ========================================================
    # 40초 후
    # ========================================================

    await asyncio.sleep(40)

    async with game_lock:

        if not baccarat_game["active"]:
            return

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⏰ 바카라 베팅 마감 10초 전!\n"
            "⚠️ 10초 후 베팅이 마감됩니다."
        )
    )

    # ========================================================
    # 남은 10초
    # ========================================================

    await asyncio.sleep(10)

    # ========================================================
    # 베팅 마감
    # ========================================================

    async with game_lock:

        if not baccarat_game["active"]:
            return

        baccarat_game["active"] = False

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔒 베팅 마감!\n"
            "━━━━━━━━━━━━━━\n"
            "🎰 베팅이 종료되었습니다.\n"
            "⏳ 10초 후 카드를 공개합니다."
        )
    )

    # ========================================================
    # 베팅 마감 후 정확히 10초 대기
    # ========================================================

    await asyncio.sleep(10)

    # ========================================================
    # 카드 공개 및 게임 진행
    # ========================================================

    await play_baccarat(
        context.bot,
        chat_id
    )


# ============================================================
# 도움말
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "📖 명령어\n"
        "━━━━━━━━━━━━━━\n"
        "/내정보 - 내 정보\n"
        "/출석 - 출석체크\n"
        "/레벨업 - 레벨업 상태 확인\n"
        "/복권 - 복권\n"
        "/바카라 - 바카라 시작\n\n"
        "🎰 바카라 베팅\n"
        "/배팅 플 1000\n"
        "/배팅 뱅 1000\n"
        "/배팅 타이 1000\n\n"
        "🔤 영어도 가능\n"
        "/배팅 P 1000\n"
        "/배팅 B 1000\n"
        "/배팅 T 1000\n\n"
        "💬 일반 채팅으로 XP 획득\n\n"
        "👑 관리자 명령어\n"
        "/지급 금액\n"
        "/지급 유저ID 금액\n"
        "/차감 금액\n"
        "/차감 유저ID 금액\n"
        "/경험치 금액\n"
        "/경험치 유저ID 금액\n"
        "/경험치차감 금액\n"
        "/경험치차감 유저ID 금액"
    )


# ============================================================
# 한국어 명령어
# ============================================================

async def korean_commands(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    parts = (
        update.message.text
        .strip()
        .split()
    )

    if not parts:
        return

    text = parts[0]

    handlers = {

        "/내정보": my_info,

        "/출석": attendance,

        "/레벨업": level_up,

        "/복권": buy_lottery,

        "/바카라": start_baccarat,

        "/도움말": help_command,

        "/지급": admin_give,

        "/차감": admin_take,

        "/경험치": admin_xp_give,

        "/경험치차감": admin_xp_take,

        "/배팅": baccarat_bet,

        "/베팅": baccarat_bet,
    }

    handler = handlers.get(
        text
    )

    if handler is None:
        return

    context.args = parts[1:]

    await handler(
        update,
        context
    )


# ============================================================
# 에러 처리
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "BOT ERROR:",
        repr(context.error)
    )


# ============================================================
# 실행
# ============================================================

def main():

    print(
        "================================"
    )

    print(
        "Telegram Bot Starting..."
    )

    print(
        "================================"
    )

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

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            help_command
        )
    )

    # 한국어 명령어
    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^/(내정보|출석|레벨업|복권|바카라|도움말|지급|차감|경험치|경험치차감|배팅|베팅)(?:\s+.*)?$"
            ),
            korean_commands
        ),
        group=0
    )

    # 일반 채팅 XP
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_chat
        ),
        group=1
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "Bot is running."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# 시작
# ============================================================

if __name__ == "__main__":

    main()
