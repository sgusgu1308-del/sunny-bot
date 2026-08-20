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

# 각 레벨에 도달하기 위한 XP
XP_REQUIREMENTS = {
    1: 300,
    2: 1000,
    3: 5000,
    4: 10000,
}

# 레벨업 선물
# 포인트를 차감하는 비용이 아니라
# 레벨업 성공 시 지급되는 보상입니다.
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
}

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

            cur.execute(
                """
                UPDATE users
                SET username = ?
                WHERE user_id = ?
                """,
                (
                    username,
                    user_id
                )
            )

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

        cur.execute(
            """
            UPDATE users
            SET points = ?
            WHERE user_id = ?
            """,
            (
                points,
                user_id
            )
        )

    if xp is not None:

        cur.execute(
            """
            UPDATE users
            SET xp = ?
            WHERE user_id = ?
            """,
            (
                xp,
                user_id
            )
        )

    if level is not None:

        cur.execute(
            """
            UPDATE users
            SET level = ?
            WHERE user_id = ?
            """,
            (
                level,
                user_id
            )
        )

    if last_attendance is not None:

        cur.execute(
            """
            UPDATE users
            SET last_attendance = ?
            WHERE user_id = ?
            """,
            (
                last_attendance,
                user_id
            )
        )

    conn.commit()
    conn.close()


# ============================================================
# 자동 레벨업
# ============================================================

def check_auto_level_up(user_id):

    u = get_user(user_id)

    current_level = u["level"]
    current_xp = u["xp"]
    current_points = u["points"]

    level_messages = []

    # XP가 충분하면 자동으로 계속 레벨업
    while current_level < 5:

        required_xp = XP_REQUIREMENTS.get(
            current_level
        )

        if required_xp is None:
            break

        if current_xp < required_xp:
            break

        reward = LEVEL_UP_REWARDS.get(
            current_level,
            0
        )

        current_level += 1
        current_points += reward

        level_messages.append(
            f"🎉 Lv.{current_level} "
            f"[{LEVEL_NAMES[current_level]}] 달성!\n"
            f"🎁 레벨업 선물 +{reward:,}P"
        )

    if level_messages:

        update_user(
            user_id,
            points=current_points,
            level=current_level
        )

    return level_messages


# ============================================================
# 내정보
# ============================================================

async def my_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.effective_user:
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

    await update.message.reply_text(
        f"👤 [{username}] 님의 정보\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏅 레벨: {u['level']} [{level_name}]\n"
        f"💰 보유 포인트: {u['points']:,}P\n"
        f"✨ 경험치: {u['xp']} / {next_xp}"
    )


# ============================================================
# 출석
# ============================================================

async def attendance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.effective_user:
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

        cur.execute(
            """
            SELECT points, last_attendance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        )

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

            points, last_attendance = row

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
# ============================================================

async def level_up(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id

    u = get_user(user_id)

    messages = check_auto_level_up(
        user_id
    )

    if messages:

        await update.message.reply_text(
            "✨ 자동 레벨업 결과\n"
            "━━━━━━━━━━━━━━\n"
            + "\n\n".join(messages)
        )

        return

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

    await update.message.reply_text(
        f"📊 아직 레벨업 조건이 부족합니다.\n"
        f"✨ 현재 경험치: {u['xp']:,} XP\n"
        f"✨ 필요 경험치: {required_xp:,} XP\n\n"
        f"🎁 레벨업 시 +{reward:,}P 지급"
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

        conn = db_connect()

        conn.execute(
            """
            UPDATE users
            SET xp = xp + ?
            WHERE user_id = ?
            """,
            (
                added_xp,
                user_id
            )
        )

        conn.commit()
        conn.close()

    # 채팅 XP 획득 후 자동 레벨업 확인
    level_messages = check_auto_level_up(
        user_id
    )

    for message in level_messages:

        await update.message.reply_text(
            message
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

    if not update.message or not update.effective_user:
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

        cur.execute(
            """
            SELECT points
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        )

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

    if not update.message or not update.effective_user:
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

            target_id = update.effective_user.id
            amount = int(args[0])

        elif len(args) == 2:

            target_id = int(args[0])
            amount = int(args[1])

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(target_id)

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

    if not update.message or not update.effective_user:
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

            target_id = update.effective_user.id
            amount = int(args[0])

        elif len(args) == 2:

            target_id = int(args[0])
            amount = int(args[1])

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(target_id)

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

    if not update.message or not update.effective_user:
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

            target_id = update.effective_user.id
            amount = int(args[0])

        elif len(args) == 2:

            target_id = int(args[0])
            amount = int(args[1])

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(target_id)

        new_xp = t["xp"] + amount

        update_user(
            target_id,
            xp=new_xp
        )

        level_messages = check_auto_level_up(
            target_id
        )

        text = (
            f"✅ 경험치 지급 완료\n"
            f"👤 유저ID: {target_id}\n"
            f"✨ +{amount:,} XP\n"
            f"📊 현재 경험치: {new_xp:,} XP"
        )

        if level_messages:

            text += (
                "\n\n"
                + "\n\n".join(level_messages)
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

    if not update.message or not update.effective_user:
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

            target_id = update.effective_user.id
            amount = int(args[0])

        elif len(args) == 2:

            target_id = int(args[0])
            amount = int(args[1])

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        t = get_user(target_id)

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
            f"📊 현재 경험치: {new_xp:,} XP"
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
# ============================================================

def create_card_images():

    os.makedirs(
        CARD_DIR,
        exist_ok=True
    )

    # 실제 카드 기본 이미지
    width = 160
    height = 230

    rank_font = get_font(
        26,
        True
    )

    suit_font = get_font(
        24,
        True
    )

    center_font = get_font(
        65,
        True
    )

    back_path = os.path.join(
        CARD_DIR,
        "BACK.png"
    )

    if not os.path.exists(back_path):

        img = Image.new(
            "RGB",
            (
                width,
                height
            ),
            "white"
        )

        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle(
            (
                4,
                4,
                width - 4,
                height - 4
            ),
            radius=15,
            fill=(35, 70, 150),
            outline="white",
            width=5
        )

        draw.rounded_rectangle(
            (
                17,
                17,
                width - 17,
                height - 17
            ),
            radius=10,
            outline="white",
            width=3
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
                (
                    width,
                    height
                ),
                "white"
            )

            draw = ImageDraw.Draw(img)

            draw.rounded_rectangle(
                (
                    3,
                    3,
                    width - 3,
                    height - 3
                ),
                radius=15,
                outline="black",
                width=3
            )

            draw.text(
                (
                    13,
                    10
                ),
                rank,
                font=rank_font,
                fill=fill
            )

            draw.text(
                (
                    13,
                    42
                ),
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
                    width - 13,
                    height - 10
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
# 카드 표시 크기 80 x 115
# ============================================================

def create_baccarat_image(
    player,
    banker,
    result_text=None
):

    width = 500
    height = 300

    img = Image.new(
        "RGB",
        (
            width,
            height
        ),
        (20, 70, 45)
    )

    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (
            6,
            6,
            width - 6,
            height - 6
        ),
        radius=20,
        outline=(210, 170, 70),
        width=4
    )

    title_font = get_font(
        24,
        True
    )

    label_font = get_font(
        18,
        True
    )

    score_font = get_font(
        18,
        True
    )

    result_font = get_font(
        22,
        True
    )

    draw.text(
        (
            width // 2,
            25
        ),
        "B A C C A R A T",
        font=title_font,
        fill=(245, 220, 140),
        anchor="ma"
    )

    # 요청한 카드 크기
    card_w = 80
    card_h = 115

    card_gap = 6

    # 플레이어 / 뱅커 영역
    player_x = 55
    banker_x = 295
    card_y = 70

    draw.text(
        (
            player_x + 40,
            55
        ),
        "PLAYER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    draw.text(
        (
            banker_x + 40,
            55
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
                (
                    x,
                    y
                )
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
#
# 플 / 플레이어 / P
# 뱅 / 뱅커 / B
# 타이 / 타이 / T
# ============================================================

def parse_bet(args):

    if len(args) != 2:
        return None

    bet_type_raw = args[0].strip().upper()

    bet_map = {
        "P": "P",
        "PLAYER": "P",
        "플": "P",
        "플레이어": "P",

        "B": "B",
        "BANKER": "B",
        "뱅": "B",
        "뱅커": "B",

        "T": "T",
        "TIE": "T",
        "타이": "T",
    }

    bet_type = bet_map.get(
        bet_type_raw
    )

    if bet_type is None:
        return None

    try:

        amount = int(args[1])

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

    if not update.message or not update.effective_user:
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
            "/베팅 플 5000\n"
            "/베팅 뱅 5000\n"
            "/베팅 타이 5000"
        )

        return

    bet_type, amount = parsed

    user_id = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    async with game_lock:

        if not baccarat_game["active"]:

            await update.message.reply_text(
                "❌ 베팅 시간이 종료되었습니다."
            )

            return

        if user_id in baccarat_game["bets"]:

            await update.message.reply_text(
                "❌ 이번 게임에는 이미 베팅했습니다."
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
# 바카라 게임
#
# 베팅 1분
# ↓
# 베팅 마감
# ↓
# 10초 대기
# ↓
# 플레이어 2장
# ↓ 3초
# 뱅커 2장
# ↓
# 추가카드 필요하면 1초
# 플레이어 → 뱅커
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

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🎰 베팅 마감!\n"
            "⏳ 10초 후 카드가 공개됩니다."
        )
    )

    # 베팅 마감 후 카드 공개까지 10초
    await asyncio.sleep(10)

    # --------------------------------------------------------
    # PLAYER 첫 2장
    # --------------------------------------------------------

    player.append(
        deck.pop()
    )

    player.append(
        deck.pop()
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🎰 PLAYER 카드 공개"
    )

    # 플레이어 2장 공개 후 3초
    await asyncio.sleep(3)

    # --------------------------------------------------------
    # BANKER 첫 2장
    # --------------------------------------------------------

    banker.append(
        deck.pop()
    )

    banker.append(
        deck.pop()
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🏦 BANKER 카드 공개"
    )

    # --------------------------------------------------------
    # 내추럴 여부
    # --------------------------------------------------------

    player_score = baccarat_score(
        player
    )

    banker_score = baccarat_score(
        banker
    )

    if (
        player_score in (8, 9)
        or banker_score in (8, 9)
    ):

        pass

    else:

        # ----------------------------------------------------
        # PLAYER 세 번째 카드
        # ----------------------------------------------------

        if player_score <= 5:

            await asyncio.sleep(1)

            player.append(
                deck.pop()
            )

            await send_baccarat_image(
                bot,
                chat_id,
                player,
                banker,
                "🎰 PLAYER 추가 카드"
            )

        # ----------------------------------------------------
        # PLAYER가 추가카드를 안 받았으면
        # BANKER는 0~5일 때 추가카드
        # ----------------------------------------------------

        if len(player) == 2:

            banker_score = baccarat_score(
                banker
            )

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
                    "🏦 BANKER 추가 카드"
                )

        else:

            # ------------------------------------------------
            # PLAYER가 세 번째 카드를 받은 경우
            # 바카라 정식 BANKER 규칙
            # ------------------------------------------------

            banker_score = baccarat_score(
                banker
            )

            third = card_value(
                player[-1]
            )

            draw = False

            if banker_score <= 2:

                draw = True

            elif banker_score == 3:

                draw = (
                    third != 8
                )

            elif banker_score == 4:

                draw = third in (
                    2,
                    3,
                    4,
                    5,
                    6,
                    7
                )

            elif banker_score == 5:

                draw = third in (
                    4,
                    5,
                    6,
                    7
                )

            elif banker_score == 6:

                draw = third in (
                    6,
                    7
                )

            if draw:

                await asyncio.sleep(1)

                banker.append(
                    deck.pop()
                )

                await send_baccarat_image(
                    bot,
                    chat_id,
                    player,
                    banker,
                    "🏦 BANKER 추가 카드"
                )

    # --------------------------------------------------------
    # 최종 점수
    # --------------------------------------------------------

    player_score = baccarat_score(
        player
    )

    banker_score = baccarat_score(
        banker
    )

    if player_score > banker_score:

        result = "P"
        result_text = "👤 PLAYER 승리!"

    elif banker_score > player_score:

        result = "B"
        result_text = "🏦 BANKER 승리!"

    else:

        result = "T"
        result_text = "🤝 TIE!"

    # --------------------------------------------------------
    # 최종 이미지
    # --------------------------------------------------------

    await asyncio.sleep(1)

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
            "🎰 최종 결과\n"
            "━━━━━━━━━━━━━━\n"
            f"👤 PLAYER: {player_score}점\n"
            f"🏦 BANKER: {banker_score}점\n\n"
            f"🏆 {result_text}"
        )
    )

    # --------------------------------------------------------
    # 정산
    # --------------------------------------------------------

    lines = []
    xp_lines = []

    for user_id, bet in bets.items():

        bet_type = bet["type"]
        amount = bet["amount"]
        username = bet["name"]

        payout = 0

        # ----------------------------------------------------
        # 승리
        # ----------------------------------------------------

        if bet_type == result:

            if result == "T":

                payout = amount * 9

            else:

                payout = amount * 2

            u = get_user(
                user_id
            )

            update_user(
                user_id,
                points=u["points"] + payout,
                xp=u["xp"] + 1
            )

            lines.append(
                f"🎉 {username}: +{payout:,}P"
            )

            xp_lines.append(
                f"✨ {username}: +1 XP"
            )

            # 승리 후 자동 레벨업
            level_messages = check_auto_level_up(
                user_id
            )

            for level_message in level_messages:

                xp_lines.append(
                    f"🏅 {username}: "
                    f"{level_message.replace(chr(10), ' ')}"
                )

        # ----------------------------------------------------
        # TIE 발생 시 PLAYER/BANKER 베팅 반환
        # ----------------------------------------------------

        elif (
            result == "T"
            and bet_type in ("P", "B")
        ):

            payout = amount

            u = get_user(
                user_id
            )

            update_user(
                user_id,
                points=u["points"] + payout
            )

            lines.append(
                f"↩️ {username}: "
                f"{payout:,}P 반환"
            )

        # ----------------------------------------------------
        # 패배
        # ----------------------------------------------------

        else:

            u = get_user(
                user_id
            )

            new_xp = max(
                0,
                u["xp"] - 1
            )

            update_user(
                user_id,
                xp=new_xp
            )

            lines.append(
                f"💥 {username}: "
                f"-{amount:,}P"
            )

            xp_lines.append(
                f"💥 {username}: -1 XP"
            )

    # --------------------------------------------------------
    # 포인트 정산
    # --------------------------------------------------------

    if lines:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "💰 정산 결과\n"
                "━━━━━━━━━━━━━━\n"
                + "\n".join(lines)
            )
        )

    else:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "💰 이번 게임 "
                "당첨자가 없습니다."
            )
        )

    # --------------------------------------------------------
    # 바카라 XP
    # --------------------------------------------------------

    if xp_lines:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "✨ 바카라 경험치 결과\n"
                "━━━━━━━━━━━━━━\n"
                + "\n".join(xp_lines)
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

    chat_id = update.effective_chat.id

    async with game_lock:

        if baccarat_game["active"]:

            await update.message.reply_text(
                "🎰 이미 진행 중인 바카라가 있습니다."
            )

            return

        baccarat_game["active"] = True
        baccarat_game["bets"] = {}

    await update.message.reply_text(
        "🎰 바카라 1회차 시작!\n\n"
        "⏰ 베팅시간: 1분\n"
        "🔒 베팅 마감 후 10초 뒤 카드 공개\n\n"
        "사용법:\n"
        "/베팅 플 5000\n"
        "/베팅 뱅 5000\n"
        "/베팅 타이 5000\n\n"
        "또는\n"
        "/베팅 P 5000\n"
        "/베팅 B 5000\n"
        "/베팅 T 5000"
    )

    # 베팅 1분
    await asyncio.sleep(60)

    async with game_lock:

        if not baccarat_game["active"]:
            return

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
        "/복권 - 복권\n"
        "/레벨업 - 레벨업 상태 확인\n"
        "/바카라 - 바카라 시작\n\n"
        "🎰 바카라 베팅\n"
        "/베팅 플 1000\n"
        "/베팅 뱅 1000\n"
        "/베팅 타이 1000\n\n"
        "영문도 가능\n"
        "/베팅 P 1000\n"
        "/베팅 B 1000\n"
        "/베팅 T 1000\n\n"
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
                r"^/(내정보|출석|레벨업|복권|바카라|도움말|지급|차감|경험치|경험치차감|베팅)(?:\s+.*)?$"
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
