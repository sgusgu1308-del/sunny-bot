import os
import random
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
from psycopg2.extras import RealDictCursor

from PIL import Image, ImageDraw, ImageFont

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# 기본 설정
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
CARD_DIR = "cards"
KR_TZ = ZoneInfo("Asia/Seoul")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN 환경변수가 없습니다.")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL 환경변수가 없습니다. "
        "Render Environment에 PostgreSQL DATABASE_URL을 등록하세요."
    )


# =========================================================
# 관리자
# =========================================================

ADMIN_IDS = set()

for value in os.environ.get("ADMIN_IDS", "").split(","):
    value = value.strip()

    if value.isdigit():
        ADMIN_IDS.add(int(value))


# =========================================================
# 레벨
# =========================================================

LEVEL_NAMES = {
    1: "돌맹이",
    2: "동",
    3: "은",
    4: "골드",
    5: "다이아",
}

XP_REQUIREMENTS = {
    1: 300,
    2: 1000,
    3: 5000,
    4: 10000,
}

LEVEL_UP_REWARDS = {
    1: 5000,
    2: 10000,
    3: 20000,
    4: 50000,
}


# =========================================================
# 게임 상태
#
# 채팅방별로 따로 관리
# =========================================================

baccarat_games = {}
odd_even_games = {}

baccarat_history = {}

game_lock = asyncio.Lock()
odd_even_lock = asyncio.Lock()
db_lock = asyncio.Lock()

MAX_HISTORY = 20


# =========================================================
# Render Health Check
# =========================================================

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


# =========================================================
# PostgreSQL
# =========================================================

def db_connect():

    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15
    )

    return conn


def init_db():

    conn = db_connect()

    try:

        cur = conn.cursor()

        # users
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT DEFAULT '유저',
                points BIGINT DEFAULT 0,
                real_money BIGINT DEFAULT 0,
                xp BIGINT DEFAULT 0,
                level INTEGER DEFAULT 1,
                last_attendance TEXT,
                total_chat_count BIGINT DEFAULT 0
            )
        """)

        # daily chat
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_chat (
                user_id BIGINT NOT NULL,
                chat_date TEXT NOT NULL,
                chat_count BIGINT DEFAULT 0,
                PRIMARY KEY (user_id, chat_date)
            )
        """)

        # 기존 DB에 컬럼이 없을 경우 추가
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS real_money BIGINT DEFAULT 0
        """)

        cur.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS total_chat_count BIGINT DEFAULT 0
        """)

        conn.commit()

        print("PostgreSQL database initialized.")

    finally:

        conn.close()


# 프로그램 시작 시 DB 초기화
init_db()


# =========================================================
# 유저
# =========================================================

def get_user(
    user_id,
    username="유저"
):

    conn = db_connect()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                user_id,
                username,
                points,
                real_money,
                xp,
                level,
                last_attendance,
                total_chat_count
            FROM users
            WHERE user_id = %s
        """, (user_id,))

        row = cur.fetchone()

        if row is None:

            cur.execute("""
                INSERT INTO users (
                    user_id,
                    username,
                    points,
                    real_money,
                    xp,
                    level,
                    last_attendance,
                    total_chat_count
                )
                VALUES (
                    %s,
                    %s,
                    0,
                    0,
                    0,
                    1,
                    NULL,
                    0
                )
            """, (
                user_id,
                username or "유저"
            ))

            conn.commit()

            return {
                "user_id": user_id,
                "username": username or "유저",
                "points": 0,
                "real_money": 0,
                "xp": 0,
                "level": 1,
                "last_attendance": None,
                "total_chat_count": 0,
            }

        if username and username != row[1]:

            cur.execute("""
                UPDATE users
                SET username = %s
                WHERE user_id = %s
            """, (
                username,
                user_id
            ))

            conn.commit()

        return {
            "user_id": row[0],
            "username": row[1] or "유저",
            "points": row[2] or 0,
            "real_money": row[3] or 0,
            "xp": row[4] or 0,
            "level": row[5] or 1,
            "last_attendance": row[6],
            "total_chat_count": row[7] or 0,
        }

    finally:

        conn.close()


# =========================================================
# 유저 정보 업데이트
# =========================================================

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

    try:

        cur = conn.cursor()

        if points is not None:

            cur.execute("""
                UPDATE users
                SET points = %s
                WHERE user_id = %s
            """, (
                points,
                user_id
            ))

        if real_money is not None:

            cur.execute("""
                UPDATE users
                SET real_money = %s
                WHERE user_id = %s
            """, (
                real_money,
                user_id
            ))

        if xp is not None:

            cur.execute("""
                UPDATE users
                SET xp = %s
                WHERE user_id = %s
            """, (
                xp,
                user_id
            ))

        if level is not None:

            cur.execute("""
                UPDATE users
                SET level = %s
                WHERE user_id = %s
            """, (
                level,
                user_id
            ))

        if last_attendance is not None:

            cur.execute("""
                UPDATE users
                SET last_attendance = %s
                WHERE user_id = %s
            """, (
                last_attendance,
                user_id
            ))

        if total_chat_count is not None:

            cur.execute("""
                UPDATE users
                SET total_chat_count = %s
                WHERE user_id = %s
            """, (
                total_chat_count,
                user_id
            ))

        conn.commit()

    finally:

        conn.close()


# =========================================================
# 경험치 / 레벨
# =========================================================

def add_xp_and_check_level(
    user_id,
    amount
):

    conn = db_connect()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                xp,
                level,
                points
            FROM users
            WHERE user_id = %s
        """, (user_id,))

        row = cur.fetchone()

        if row is None:
            return None

        xp = max(
            0,
            (row[0] or 0) + amount
        )

        level = row[1] or 1
        points = row[2] or 0

        level_ups = []

        while level < 5:

            required = XP_REQUIREMENTS.get(
                level
            )

            reward = LEVEL_UP_REWARDS.get(
                level,
                0
            )

            if required is None:
                break

            if xp < required:
                break

            level += 1
            points += reward

            level_ups.append({
                "level": level,
                "reward": reward
            })

        cur.execute("""
            UPDATE users
            SET
                xp = %s,
                level = %s,
                points = %s
            WHERE user_id = %s
        """, (
            xp,
            level,
            points,
            user_id
        ))

        conn.commit()

        return {
            "xp": xp,
            "level": level,
            "points": points,
            "level_ups": level_ups
        }

    finally:

        conn.close()


# =========================================================
# 채팅 집계
# =========================================================

def count_chat_message(
    user_id,
    username,
    text
):

    if not text:
        return False, 0, 0

    clean_text = "".join(
        text.split()
    )

    if len(clean_text) < 5:
        return False, 0, 0

    today = datetime.now(
        KR_TZ
    ).strftime("%Y-%m-%d")

    conn = db_connect()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO users (
                user_id,
                username,
                points,
                real_money,
                xp,
                level,
                total_chat_count
            )
            VALUES (
                %s,
                %s,
                0,
                0,
                0,
                1,
                0
            )
            ON CONFLICT (user_id)
            DO UPDATE SET username = EXCLUDED.username
        """, (
            user_id,
            username
        ))

        cur.execute("""
            UPDATE users
            SET total_chat_count =
                COALESCE(total_chat_count, 0) + 1
            WHERE user_id = %s
        """, (user_id,))

        cur.execute("""
            INSERT INTO daily_chat (
                user_id,
                chat_date,
                chat_count
            )
            VALUES (
                %s,
                %s,
                1
            )
            ON CONFLICT (
                user_id,
                chat_date
            )
            DO UPDATE SET
                chat_count =
                    daily_chat.chat_count + 1
        """, (
            user_id,
            today
        ))

        cur.execute("""
            SELECT total_chat_count
            FROM users
            WHERE user_id = %s
        """, (user_id,))

        total = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT chat_count
            FROM daily_chat
            WHERE user_id = %s
            AND chat_date = %s
        """, (
            user_id,
            today
        ))

        today_count = cur.fetchone()[0] or 0

        conn.commit()

        return True, today_count, total

    finally:

        conn.close()


def get_today_chat_count(user_id):

    today = datetime.now(
        KR_TZ
    ).strftime("%Y-%m-%d")

    conn = db_connect()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT chat_count
            FROM daily_chat
            WHERE user_id = %s
            AND chat_date = %s
        """, (
            user_id,
            today
        ))

        row = cur.fetchone()

        return row[0] if row else 0

    finally:

        conn.close()


def get_chat_ranking(
    limit=5
):

    today = datetime.now(
        KR_TZ
    ).strftime("%Y-%m-%d")

    conn = db_connect()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                u.user_id,
                u.username,
                COALESCE(
                    d.chat_count,
                    0
                ) AS today_count
            FROM users u

            LEFT JOIN daily_chat d
                ON u.user_id = d.user_id
                AND d.chat_date = %s

            WHERE COALESCE(
                d.chat_count,
                0
            ) > 0

            ORDER BY
                today_count DESC,
                u.user_id ASC

            LIMIT %s
        """, (
            today,
            limit
        ))

        return cur.fetchall()

    finally:

        conn.close()


# =========================================================
# 내정보
# =========================================================

async def my_info(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_user:
        return

    user = update.effective_user

    u = get_user(
        user.id,
        user.first_name or "유저"
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

    reward_text = (
        f"{next_reward:,}P"
        if isinstance(
            next_reward,
            int
        )
        else next_reward
    )

    today_chat = get_today_chat_count(
        user.id
    )

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


# =========================================================
# 채팅 순위
# =========================================================

async def chat_ranking(
    update,
    context
):

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

    medals = [
        "🥇",
        "🥈",
        "🥉",
        "4️⃣",
        "5️⃣"
    ]

    lines = [
        "💬 오늘 채팅 순위",
        "━━━━━━━━━━━━━━"
    ]

    for i, row in enumerate(rows):

        user_id = row[0]
        username = row[1]
        count = row[2]

        display_name = (
            username
            or f"유저{user_id}"
        )

        lines.append(
            f"{medals[i]} {i+1}위  "
            f"{display_name} — "
            f"{count:,}회"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# 출석
# =========================================================

async def attendance(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_user:
        return

    uid = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    today = datetime.now(
        KR_TZ
    ).strftime("%Y-%m-%d")

    async with db_lock:

        conn = db_connect()

        try:

            cur = conn.cursor()

            cur.execute("""
                SELECT
                    points,
                    last_attendance
                FROM users
                WHERE user_id = %s
            """, (uid,))

            row = cur.fetchone()

            if row is None:

                cur.execute("""
                    INSERT INTO users (
                        user_id,
                        username,
                        points,
                        real_money,
                        xp,
                        level,
                        last_attendance,
                        total_chat_count
                    )
                    VALUES (
                        %s,
                        %s,
                        1000,
                        0,
                        0,
                        1,
                        %s,
                        0
                    )
                """, (
                    uid,
                    username,
                    today
                ))

                new_points = 1000
                already = False

            elif row[1] == today:

                new_points = row[0] or 0
                already = True

            else:

                new_points = (
                    row[0] or 0
                ) + 1000

                cur.execute("""
                    UPDATE users
                    SET
                        points = %s,
                        last_attendance = %s,
                        username = %s
                    WHERE user_id = %s
                """, (
                    new_points,
                    today,
                    username,
                    uid
                ))

                already = False

            conn.commit()

        finally:

            conn.close()

    if already:

        await update.message.reply_text(
            "❌ 오늘은 이미 출석체크를 완료했습니다.\n"
            "🌙 한국시간 00:00 이후 다시 출석할 수 있습니다."
        )

    else:

        await update.message.reply_text(
            "📆 출석체크 완료!\n"
            "🎁 +1,000P 지급\n"
            f"💰 현재: {new_points:,}P"
        )


# =========================================================
# 레벨업
# =========================================================

async def level_up(
    update,
    context
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

    required = XP_REQUIREMENTS[
        u["level"]
    ]

    reward = LEVEL_UP_REWARDS[
        u["level"]
    ]

    await update.message.reply_text(
        "✨ 레벨업은 자동으로 진행됩니다.\n\n"
        f"현재: {u['xp']:,} XP\n"
        f"필요: {required:,} XP\n"
        f"🎁 레벨업 보상: {reward:,}P"
    )


# =========================================================
# 일반 채팅
# =========================================================

async def handle_chat(
    update,
    context
):

    if not update.message:
        return

    if not update.message.text:
        return

    if not update.effective_user:
        return

    if update.message.text.startswith("/"):
        return

    uid = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    get_user(
        uid,
        username
    )

    count_chat_message(
        uid,
        username,
        update.message.text
    )

    result = add_xp_and_check_level(
        uid,
        1
    )

    if result and result["level_ups"]:

        for item in result["level_ups"]:

            level_name = LEVEL_NAMES.get(
                item["level"],
                "최고 등급"
            )

            await update.message.reply_text(
                "🎉 레벨업!\n"
                f"🏅 Lv.{item['level']} "
                f"[{level_name}]\n"
                f"🎁 +{item['reward']:,}P 지급!"
            )


# =========================================================
# 복권
# =========================================================

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
        [x[0] for x in rewards],
        weights=[x[1] for x in rewards],
        k=1
    )[0]


async def buy_lottery(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_user:
        return

    uid = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    args = context.args or []

    use_real = False
    count = 1

    # -----------------------------------------
    # /복권
    # /복권 10
    # /복권 10장
    # /복권 실
    # /복권 실 10장
    # -----------------------------------------

    for arg in args:

        low = arg.lower()

        if low in (
            "실",
            "실머니"
        ):

            use_real = True

        else:

            number_text = (
                low
                .replace("장", "")
                .replace(",", "")
            )

            if number_text.isdigit():

                count = int(
                    number_text
                )

    count = max(
        1,
        min(count, 10)
    )

    cost_each = (
        100
        if use_real
        else 1000
    )

    total_cost = (
        cost_each * count
    )

    u = get_user(
        uid,
        username
    )

    balance = (
        u["real_money"]
        if use_real
        else u["points"]
    )

    unit = (
        "원"
        if use_real
        else "P"
    )

    if balance < total_cost:

        await update.message.reply_text(
            f"❌ {'실머니' if use_real else '포인트'}가 부족합니다.\n"
            f"🎫 구매수량: {count}장\n"
            f"💰 필요금액: {total_cost:,}{unit}\n"
            f"💳 현재: {balance:,}{unit}"
        )

        return

    total_prize = 0

    results = []

    for i in range(count):

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

        total_prize += prize

        results.append(
            f"{i+1}장째 — {rank} "
            f"+{prize:,}{unit}"
        )

    new_balance = (
        balance
        - total_cost
        + total_prize
    )

    if use_real:

        update_user(
            uid,
            real_money=new_balance
        )

    else:

        update_user(
            uid,
            points=new_balance
        )

    await update.message.reply_text(
        "🎫 복권 결과\n"
        "━━━━━━━━━━━━━━\n"
        + "\n".join(results)
        + "\n━━━━━━━━━━━━━━\n"
        f"🎟️ 구매: {count}장\n"
        f"💳 구매금액: -{total_cost:,}{unit}\n"
        f"💰 총 당첨: +{total_prize:,}{unit}\n"
        f"💵 현재 보유: {new_balance:,}{unit}"
    )


# =========================================================
# 관리자
# =========================================================

def is_admin(uid):

    return uid in ADMIN_IDS


async def admin_balance_change(
    update,
    context,
    field,
    title,
    unit
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

        args = context.args or []

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0].replace(",", "")
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1].replace(",", "")
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        get_user(
            target_id
        )

        u = get_user(
            target_id
        )

        current = u[field]

        new_value = (
            current + amount
        )

        update_user(
            target_id,
            **{
                field: new_value
            }
        )

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


async def admin_give(
    update,
    context
):

    await admin_balance_change(
        update,
        context,
        "points",
        "지급",
        "P"
    )


async def admin_take(
    update,
    context
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

        args = context.args or []

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0].replace(",", "")
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1].replace(",", "")
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        u = get_user(
            target_id
        )

        new_value = max(
            0,
            u["points"] - amount
        )

        update_user(
            target_id,
            points=new_value
        )

        await update.message.reply_text(
            "✅ 차감 완료\n"
            f"👤 {target_id}\n"
            f"💸 -{amount:,}P\n"
            f"💳 현재: {new_value:,}P"
        )

    except Exception:

        await update.message.reply_text(
            "사용법: /차감 금액\n"
            "또는 /차감 유저ID 금액"
        )


async def admin_real_give(
    update,
    context
):

    await admin_balance_change(
        update,
        context,
        "real_money",
        "실머니지급",
        "원"
    )


async def admin_real_take(
    update,
    context
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

        args = context.args or []

        if len(args) == 1:

            target_id = (
                update.effective_user.id
            )

            amount = int(
                args[0].replace(",", "")
            )

        elif len(args) == 2:

            target_id = int(
                args[0]
            )

            amount = int(
                args[1].replace(",", "")
            )

        else:

            raise ValueError

        if amount <= 0:
            raise ValueError

        u = get_user(
            target_id
        )

        new_value = max(
            0,
            u["real_money"] - amount
        )

        update_user(
            target_id,
            real_money=new_value
        )

        await update.message.reply_text(
            "✅ 실머니 차감 완료\n"
            f"👤 {target_id}\n"
            f"💸 -{amount:,}원\n"
            f"💳 현재: {new_value:,}원"
        )

    except Exception:

        await update.message.reply_text(
            "사용법: /실머니차감 금액\n"
            "또는 /실머니차감 유저ID 금액"
        )


async def admin_xp_give(
    update,
    context
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

        args = context.args or []

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
            "✅ 경험치 지급 완료\n"
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
                "\n\n🎉 자동 레벨업!\n"
                f"🏅 Lv.{item['level']} "
                f"[{level_name}]\n"
                f"🎁 +{item['reward']:,}P 지급"
            )

        await update.message.reply_text(
            text
        )

    except Exception:

        await update.message.reply_text(
            "사용법: /경험치 100\n"
            "또는 /경험치 유저ID 100"
        )


async def admin_xp_take(
    update,
    context
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

        args = context.args or []

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

        u = get_user(
            target_id
        )

        new_xp = max(
            0,
            u["xp"] - amount
        )

        update_user(
            target_id,
            xp=new_xp
        )

        await update.message.reply_text(
            "✅ 경험치 차감 완료\n"
            f"👤 {target_id}\n"
            f"✨ -{amount:,} XP\n"
            f"📊 현재: {new_xp:,} XP"
        )

    except Exception:

        await update.message.reply_text(
            "사용법: /경험치차감 100\n"
            "또는 /경험치차감 유저ID 100"
        )


# =========================================================
# 카드
# =========================================================

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
            + (
                "DejaVuSans-Bold.ttf"
                if bold
                else "DejaVuSans.ttf"
            )
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

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


def create_card_images():

    os.makedirs(
        CARD_DIR,
        exist_ok=True
    )

    width = 100
    height = 145

    rank_font = get_font(
        18,
        True
    )

    suit_font = get_font(
        17,
        True
    )

    center_font = get_font(
        42,
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
            radius=10,
            fill=(35, 70, 150),
            outline="white",
            width=3
        )

        draw.rounded_rectangle(
            (
                10,
                10,
                width - 10,
                height - 10
            ),
            radius=8,
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
                radius=10,
                outline="black",
                width=2
            )

            draw.text(
                (7, 4),
                rank,
                font=rank_font,
                fill=fill
            )

            draw.text(
                (7, 25),
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
                    width - 7,
                    height - 5
                ),
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
                "file": os.path.join(
                    CARD_DIR,
                    f"{rank}{suit}.png"
                )
            })

    random.shuffle(deck)

    return deck


# =========================================================
# 바카라 규칙
# =========================================================

def card_value(card):

    if card["rank"] == "A":
        return 1

    if card["rank"] in (
        "10",
        "J",
        "Q",
        "K"
    ):
        return 0

    return int(
        card["rank"]
    )


def baccarat_score(cards):

    return (
        sum(
            card_value(card)
            for card in cards
        )
        % 10
    )


# =========================================================
# 바카라 이미지
# =========================================================

def create_baccarat_image(
    player,
    banker,
    result_text=None
):

    # 기존보다 작게
    width = 500
    height = 275

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
            4,
            4,
            width - 4,
            height - 4
        ),
        radius=16,
        outline=(210, 170, 70),
        width=3
    )

    title_font = get_font(
        20,
        True
    )

    label_font = get_font(
        17,
        True
    )

    score_font = get_font(
        17,
        True
    )

    result_font = get_font(
        20,
        True
    )

    draw.text(
        (
            width // 2,
            22
        ),
        "B A C C A R A",
        font=title_font,
        fill=(245, 220, 140),
        anchor="ma"
    )

    player_x = 55
    banker_x = 305
    card_y = 65

    draw.text(
        (
            player_x + 40,
            54
        ),
        "PLAYER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    draw.text(
        (
            banker_x + 40,
            54
        ),
        "BANKER",
        font=label_font,
        fill="white",
        anchor="ms"
    )

    card_width = 80
    card_height = 116

    def paste_cards(
        cards,
        start_x
    ):

        for i, card in enumerate(
            cards
        ):

            card_img = Image.open(
                card["file"]
            ).convert("RGB")

            card_img = card_img.resize(
                (
                    card_width,
                    card_height
                )
            )

            img.paste(
                card_img,
                (
                    start_x + i * 45,
                    card_y
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

    draw.text(
        (
            player_x + 40,
            195
        ),
        f"PLAYER  {baccarat_score(player)}",
        font=score_font,
        fill="white",
        anchor="ma"
    )

    draw.text(
        (
            banker_x + 40,
            195
        ),
        f"BANKER  {baccarat_score(banker)}",
        font=score_font,
        fill="white",
        anchor="ma"
    )

    if result_text:

        draw.text(
            (
                width // 2,
                245
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


# =========================================================
# 베팅 파싱
# =========================================================

def parse_game_bet(args):

    if not args:
        return None

    if len(args) == 2:

        choice = args[0]
        amount_text = args[1]
        money_type = "P"

    elif (
        len(args) == 3
        and args[1].lower()
        in ("실", "실머니")
    ):

        choice = args[0]
        amount_text = args[2]
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
        "odd": "O",

        "짝": "E",
        "even": "E",
    }

    choice = aliases.get(
        choice.lower()
    )

    if choice is None:
        return None

    try:

        amount = int(
            amount_text.replace(
                ",",
                ""
            )
        )

    except ValueError:

        return None

    if amount <= 0:
        return None

    return (
        choice,
        amount,
        money_type
    )


# =========================================================
# 바카라 베팅
#
# /배팅 플 1000
# 을 처음 입력하면 게임 자동 시작
# =========================================================

async def baccarat_bet(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_user:
        return

    chat_id = update.effective_chat.id

    parsed = parse_game_bet(
        context.args
    )

    if (
        not parsed
        or parsed[0]
        not in ("P", "B", "T")
    ):

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

    username = (
        update.effective_user.first_name
        or "유저"
    )

    # 첫 베팅이면 자동으로 게임 시작
    async with game_lock:

        game = baccarat_games.get(
            chat_id
        )

        if game is None or not game["active"]:

            game = {
                "active": True,
                "bets": {},
                "chat_id": chat_id
            }

            baccarat_games[
                chat_id
            ] = game

            asyncio.create_task(
                baccarat_timer(
                    context.application,
                    chat_id
                )
            )

            first_bet = True

        else:

            first_bet = False

    u = get_user(
        uid,
        username
    )

    balance = (
        u["real_money"]
        if money_type == "R"
        else u["points"]
    )

    unit = (
        "원"
        if money_type == "R"
        else "P"
    )

    if balance < amount:

        await update.message.reply_text(
            f"❌ {'실머니' if money_type == 'R' else '포인트'}가 부족합니다.\n"
            f"현재: {balance:,}{unit}"
        )

        return

    new_balance = (
        balance - amount
    )

    if money_type == "R":

        update_user(
            uid,
            real_money=new_balance
        )

    else:

        update_user(
            uid,
            points=new_balance
        )

    async with game_lock:

        # 50초가 지나면서 게임이 닫힌 순간
        # 마지막 베팅이 들어오지 않게 한 번 더 확인
        game = baccarat_games.get(
            chat_id
        )

        if (
            game is None
            or not game["active"]
        ):

            # 돈 되돌림
            if money_type == "R":

                update_user(
                    uid,
                    real_money=balance
                )

            else:

                update_user(
                    uid,
                    points=balance
                )

            await update.message.reply_text(
                "❌ 베팅 시간이 마감되었습니다."
            )

            return

        game["bets"].setdefault(
            uid,
            []
        ).append({
            "type": bet_type,
            "amount": amount,
            "money": money_type,
            "name": username
        })

    names = {
        "P": "PLAYER",
        "B": "BANKER",
        "T": "TIE"
    }

    if first_bet:

        await update.message.reply_text(
            "🎰 바카라 베팅 시작!\n"
            "━━━━━━━━━━━━━━\n"
            "⏱️ 지금부터 50초 동안 자유롭게 베팅할 수 있습니다.\n\n"
            "💰 베팅 방법\n"
            "/배팅 플 5000\n"
            "/배팅 뱅 5000\n"
            "/배팅 타이 5000\n"
            "/배팅 플 실 50000\n\n"
            "🔵 플 = PLAYER\n"
            "🔴 뱅 = BANKER\n"
            "🟢 타이 = TIE\n\n"
            "👇 첫 베팅이 접수되었습니다."
        )

    await update.message.reply_text(
        f"✅ {names[bet_type]} 베팅 완료!\n"
        f"👤 {username}\n"
        f"🎯 {names[bet_type]}\n"
        f"💰 베팅금액: {amount:,}{unit}\n"
        f"💳 베팅 후 보유머니: {new_balance:,}{unit}"
    )


# =========================================================
# 바카라 진행
# =========================================================

async def play_baccarat(
    bot,
    chat_id,
    bets
):

    deck = create_deck()

    player = []
    banker = []

    await bot.send_message(
        chat_id=chat_id,
        text="🎰 바카라 카드 공개를 시작합니다!"
    )

    # PLAYER 1
    player.append(
        deck.pop()
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🔵 PLAYER 첫 번째 카드"
    )

    await asyncio.sleep(
        0.8
    )

    # BANKER 1
    banker.append(
        deck.pop()
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🔴 BANKER 첫 번째 카드"
    )

    await asyncio.sleep(
        0.8
    )

    # PLAYER 2
    player.append(
        deck.pop()
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🔵 PLAYER 두 번째 카드"
    )

    await asyncio.sleep(
        0.8
    )

    # BANKER 2
    banker.append(
        deck.pop()
    )

    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        "🔴 BANKER 두 번째 카드"
    )

    await asyncio.sleep(
        0.8
    )

    ps = baccarat_score(
        player
    )

    bs = baccarat_score(
        banker
    )

    natural = (
        ps in (8, 9)
        or bs in (8, 9)
    )

    player_third = None

    # =============================================
    # 바카라 기본 규칙
    # =============================================

    if not natural:

        if ps <= 5:

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

            await asyncio.sleep(
                0.8
            )

        bs = baccarat_score(
            banker
        )

        banker_draw = False

        if player_third is None:

            banker_draw = (
                bs <= 5
            )

        else:

            tv = card_value(
                player_third
            )

            if bs <= 2:
                banker_draw = True

            elif bs == 3:
                banker_draw = (
                    tv != 8
                )

            elif bs == 4:
                banker_draw = (
                    tv in (
                        2,
                        3,
                        4,
                        5,
                        6,
                        7
                    )
                )

            elif bs == 5:
                banker_draw = (
                    tv in (
                        4,
                        5,
                        6,
                        7
                    )
                )

            elif bs == 6:
                banker_draw = (
                    tv in (
                        6,
                        7
                    )
                )

        if banker_draw:

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

            await asyncio.sleep(
                0.8
            )

    ps = baccarat_score(
        player
    )

    bs = baccarat_score(
        banker
    )

    if ps > bs:

        result = "P"
        result_text = "🔵 PLAYER 승리!"

    elif bs > ps:

        result = "B"
        result_text = "🔴 BANKER 승리!"

    else:

        result = "T"
        result_text = "🟢 TIE!"

    # 최종 카드 이미지
    await send_baccarat_image(
        bot,
        chat_id,
        player,
        banker,
        None
    )

    # 영상/카드 공개가 끝난 후 잠깐 대기
    await asyncio.sleep(
        2
    )

    # 최종 결과
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🎰 바카라 최종 결과\n"
            "━━━━━━━━━━━━━━\n"
            f"🔵 PLAYER: {ps}점\n"
            f"🔴 BANKER: {bs}점\n\n"
            f"🏆 {result_text}"
        )
    )

    # 결과표
    baccarat_history.setdefault(
        chat_id,
        []
    )

    baccarat_history[
        chat_id
    ].append({
        "result": result,
        "player": ps,
        "banker": bs
    })

    if len(
        baccarat_history[chat_id]
    ) > MAX_HISTORY:

        del baccarat_history[
            chat_id
        ][:-MAX_HISTORY]

    hit = []
    miss = []
    settlement = []

    for uid, user_bets in bets.items():

        for bet in user_bets:

            typ = bet["type"]
            amount = bet["amount"]
            money_type = bet["money"]
            username = bet["name"]

            u = get_user(
                uid,
                username
            )

            unit = (
                "원"
                if money_type == "R"
                else "P"
            )

            balance = (
                u["real_money"]
                if money_type == "R"
                else u["points"]
            )

            if typ == result:

                payout = (
                    amount * 9
                    if result == "T"
                    else amount * 2
                )

                new_balance = (
                    balance + payout
                )

                if money_type == "R":

                    update_user(
                        uid,
                        real_money=new_balance
                    )

                else:

                    update_user(
                        uid,
                        points=new_balance
                    )

                hit.append(
                    f"🎯 {username}님 적중!\n"
                    f"💰 적중금액: +{payout:,}{unit}\n"
                    f"💳 보유머니: {new_balance:,}{unit}"
                )

                settlement.append(
                    f"🎯 {username}: "
                    f"{payout:,}{unit} 적중"
                )

            elif (
                result == "T"
                and typ in ("P", "B")
            ):

                new_balance = (
                    balance + amount
                )

                if money_type == "R":

                    update_user(
                        uid,
                        real_money=new_balance
                    )

                else:

                    update_user(
                        uid,
                        points=new_balance
                    )

                settlement.append(
                    f"↩️ {username}: "
                    f"{amount:,}{unit} 반환"
                )

            else:

                miss.append(
                    f"❌ {username}님 미적중\n"
                    f"💸 손실금액: -{amount:,}{unit}"
                )

                settlement.append(
                    f"❌ {username}: "
                    f"-{amount:,}{unit}"
                )

    if hit:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🎯 적중 결과\n"
                "━━━━━━━━━━━━━━\n"
                + "\n\n".join(hit)
            )
        )

    if miss:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "📌 베팅 결과\n"
                "━━━━━━━━━━━━━━\n"
                + "\n\n".join(miss)
            )
        )

    history_lines = [
        "📊 바카라 결과표",
        "━━━━━━━━━━━━━━"
    ]

    for i, item in enumerate(
        reversed(
            baccarat_history[chat_id]
        ),
        1
    ):

        name = {
            "P": "🔵 PLAYER",
            "B": "🔴 BANKER",
            "T": "🟢 TIE"
        }[
            item["result"]
        ]

        history_lines.append(
            f"{i}. {name} "
            f"({item['player']} : "
            f"{item['banker']})"
        )

    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(
            history_lines
        )
    )

    if settlement:

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "💰 이번 바카라 정산\n"
                "━━━━━━━━━━━━━━\n"
                + "\n".join(
                    settlement
                )
            )
        )


# =========================================================
# 바카라 타이머
#
# 첫 베팅부터 50초
# 40초 = 10초 전 알림
# 50초 = 베팅 종료
# 그 후 카드 공개
# =========================================================

async def baccarat_timer(
    application,
    chat_id
):

    try:

        await asyncio.sleep(
            40
        )

        async with game_lock:

            game = baccarat_games.get(
                chat_id
            )

            if (
                game is None
                or not game["active"]
            ):

                return

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "⏰ 바카라 베팅 마감 10초 전!\n"
                "⚠️ 지금부터 10초 후 베팅이 마감됩니다."
            )
        )

        await asyncio.sleep(
            10
        )

        async with game_lock:

            game = baccarat_games.get(
                chat_id
            )

            if (
                game is None
                or not game["active"]
            ):

                return

            game["active"] = False

            bets = dict(
                game["bets"]
            )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 바카라 베팅 마감!\n"
                "━━━━━━━━━━━━━━\n"
                "🎰 더 이상 베팅할 수 없습니다.\n"
                "🎴 카드를 공개합니다."
            )
        )

        await play_baccarat(
            application.bot,
            chat_id,
            bets
        )

        async with game_lock:

            baccarat_games.pop(
                chat_id,
                None
            )

    except asyncio.CancelledError:

        return

    except Exception as e:

        print(
            "바카라 타이머 오류:",
            repr(e)
        )

        async with game_lock:

            baccarat_games.pop(
                chat_id,
                None
            )


# =========================================================
# 홀짝
# =========================================================

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


# =========================================================
# 홀짝 GIF
#
# 왼쪽 = 처음부터 앞면
# 오른쪽 = 뒷면
# 오른쪽만 뒤집힘
#
# 뒤집는 시간 약 10초
# =========================================================

def create_odd_even_gif(
    card1,
    card2,
    result
):

    width = 500
    height = 300

    frames = []

    bg = (
        20,
        70,
        45
    )

    title_font = get_font(
        23,
        True
    )

    label_font = get_font(
        18,
        True
    )

    result_font = get_font(
        24,
        True
    )

    front1 = Image.open(
        card1["file"]
    ).convert("RGB").resize(
        (
            110,
            160
        )
    )

    front2 = Image.open(
        card2["file"]
    ).convert("RGB").resize(
        (
            110,
            160
        )
    )

    back = Image.open(
        os.path.join(
            CARD_DIR,
            "BACK.png"
        )
    ).convert("RGB").resize(
        (
            110,
            160
        )
    )

    def make_frame(
        right_card,
        result_text=""
    ):

        img = Image.new(
            "RGB",
            (
                width,
                height
            ),
            bg
        )

        draw = ImageDraw.Draw(
            img
        )

        draw.text(
            (
                width // 2,
                25
            ),
            "O D D  &  E V E N",
            font=title_font,
            fill=(245, 220, 140),
            anchor="ma"
        )

        # 왼쪽 카드는 항상 앞면
        img.paste(
            front1,
            (
                110,
                70
            )
        )

        # 오른쪽 카드
        img.paste(
            right_card,
            (
                280,
                70
            )
        )

        draw.text(
            (
                165,
                250
            ),
            "첫 번째",
            font=label_font,
            fill="white",
            anchor="ma"
        )

        draw.text(
            (
                335,
                250
            ),
            "두 번째",
            font=label_font,
            fill="white",
            anchor="ma"
        )

        if result_text:

            draw.text(
                (
                    width // 2,
                    282
                ),
                result_text,
                font=result_font,
                fill=(255, 225, 100),
                anchor="mm"
            )

        return img

    # -----------------------------------------
    # 시작
    # 왼쪽 앞면 / 오른쪽 뒷면
    # -----------------------------------------

    for _ in range(8):

        frames.append(
            make_frame(
                back
            )
        )

    # -----------------------------------------
    # 오른쪽 카드만 뒤집기
    #
    # 20프레임 x 0.5초 = 약 10초
    # -----------------------------------------

    for i in range(20):

        half = 10

        if i < half:

            scale = (
                1.0
                - (
                    i / half
                )
            )

            source = back

        else:

            scale = (
                (
                    i - half
                )
                / half
            )

            source = front2

        scale = max(
            0.06,
            scale
        )

        card_width = max(
            6,
            int(
                110 * scale
            )
        )

        card = source.resize(
            (
                card_width,
                160
            )
        )

        img = Image.new(
            "RGB",
            (
                width,
                height
            ),
            bg
        )

        draw = ImageDraw.Draw(
            img
        )

        draw.text(
            (
                width // 2,
                25
            ),
            "O D D  &  E V E N",
            font=title_font,
            fill=(245, 220, 140),
            anchor="ma"
        )

        # 왼쪽은 항상 앞면
        img.paste(
            front1,
            (
                110,
                70
            )
        )

        # 오른쪽만 회전 연출
        img.paste(
            card,
            (
                335 - card_width // 2,
                70
            )
        )

        draw.text(
            (
                165,
                250
            ),
            "첫 번째",
            font=label_font,
            fill="white",
            anchor="ma"
        )

        draw.text(
            (
                335,
                250
            ),
            "두 번째",
            font=label_font,
            fill="white",
            anchor="ma"
        )

        frames.append(
            img
        )

    # -----------------------------------------
    # 완전히 공개된 상태
    # -----------------------------------------

    for _ in range(6):

        frames.append(
            make_frame(
                front2
            )
        )

    path = os.path.join(
        CARD_DIR,
        "odd_even.gif"
    )

    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=500,
        loop=0
    )

    return path


# =========================================================
# 홀짝 베팅
#
# 첫 /홀짝 홀 1000
# -> 게임 자동 시작
# -> 50초 베팅
# =========================================================

async def odd_even_bet(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_user:
        return

    chat_id = update.effective_chat.id

    parsed = parse_game_bet(
        context.args
    )

    if (
        not parsed
        or parsed[0]
        not in ("O", "E")
    ):

        await update.message.reply_text(
            "사용법:\n"
            "/홀짝 홀 10000\n"
            "/홀짝 짝 10000\n"
            "/홀짝 홀 실 50000"
        )

        return

    bet_type, amount, money_type = parsed

    uid = update.effective_user.id

    username = (
        update.effective_user.first_name
        or "유저"
    )

    async with odd_even_lock:

        game = odd_even_games.get(
            chat_id
        )

        if game is None or not game["active"]:

            game = {
                "active": True,
                "bets": {},
                "chat_id": chat_id
            }

            odd_even_games[
                chat_id
            ] = game

            asyncio.create_task(
                odd_even_timer(
                    context.application,
                    chat_id
                )
            )

            first_bet = True

        else:

            first_bet = False

    u = get_user(
        uid,
        username
    )

    balance = (
        u["real_money"]
        if money_type == "R"
        else u["points"]
    )

    unit = (
        "원"
        if money_type == "R"
        else "P"
    )

    if balance < amount:

        await update.message.reply_text(
            f"❌ {'실머니' if money_type == 'R' else '포인트'}가 부족합니다.\n"
            f"현재: {balance:,}{unit}"
        )

        return

    new_balance = (
        balance - amount
    )

    if money_type == "R":

        update_user(
            uid,
            real_money=new_balance
        )

    else:

        update_user(
            uid,
            points=new_balance
        )

    async with odd_even_lock:

        game = odd_even_games.get(
            chat_id
        )

        if (
            game is None
            or not game["active"]
        ):

            if money_type == "R":

                update_user(
                    uid,
                    real_money=balance
                )

            else:

                update_user(
                    uid,
                    points=balance
                )

            await update.message.reply_text(
                "❌ 홀짝 베팅 시간이 마감되었습니다."
            )

            return

        game["bets"].setdefault(
            uid,
            []
        ).append({
            "type": bet_type,
            "amount": amount,
            "money": money_type,
            "name": username
        })

    name = (
        "홀"
        if bet_type == "O"
        else "짝"
    )

    if first_bet:

        await update.message.reply_text(
            "🎴 홀짝 베팅 시작!\n"
            "━━━━━━━━━━━━━━\n"
            "⏱️ 지금부터 50초 동안 자유롭게 베팅할 수 있습니다.\n\n"
            "💰 베팅 방법\n"
            "/홀짝 홀 10000\n"
            "/홀짝 짝 10000\n"
            "/홀짝 홀 실 50000\n\n"
            "🟢 홀 = ODD\n"
            "🔵 짝 = EVEN\n\n"
            "👇 첫 베팅이 접수되었습니다."
        )

    await update.message.reply_text(
        f"✅ {name} 베팅 완료!\n"
        f"👤 {username}\n"
        f"🎯 {name}\n"
        f"💰 베팅금액: {amount:,}{unit}\n"
        f"💳 베팅 후 보유머니: {new_balance:,}{unit}"
    )


# =========================================================
# 홀짝 타이머
# =========================================================

async def odd_even_timer(
    application,
    chat_id
):

    try:

        # 50초 중 40초 경과
        await asyncio.sleep(
            40
        )

        async with odd_even_lock:

            game = odd_even_games.get(
                chat_id
            )

            if (
                game is None
                or not game["active"]
            ):

                return

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "⏰ 홀짝 베팅 마감 10초 전!\n"
                "⚠️ 지금부터 10초 후 베팅이 마감됩니다."
            )
        )

        # 마지막 10초
        await asyncio.sleep(
            10
        )

        async with odd_even_lock:

            game = odd_even_games.get(
                chat_id
            )

            if (
                game is None
                or not game["active"]
            ):

                return

            game["active"] = False

            bets = dict(
                game["bets"]
            )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 홀짝 베팅 마감!\n"
                "━━━━━━━━━━━━━━\n"
                "🎴 카드를 공개합니다."
            )
        )

        deck = create_deck()

        card1 = deck.pop()
        card2 = deck.pop()

        total = (
            odd_even_value(card1)
            + odd_even_value(card2)
        )

        result = (
            "O"
            if total % 2
            else "E"
        )

        result_name = (
            "🟢 홀"
            if result == "O"
            else "🔵 짝"
        )

        # 카드 공개 영상 생성
        path = create_odd_even_gif(
            card1,
            card2,
            result_name
        )

        with open(
            path,
            "rb"
        ) as f:

            await application.bot.send_animation(
                chat_id=chat_id,
                animation=InputFile(f),
                caption=(
                    "🎴 카드 공개 중...\n"
                    "왼쪽 카드는 앞면,\n"
                    "오른쪽 카드가 뒤집힙니다."
                )
            )

        # 영상이 완전히 끝난 뒤 2초
        await asyncio.sleep(
            2
        )

        # 최종 결과
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎴 홀짝 결과\n"
                "━━━━━━━━━━━━━━\n"
                f"첫 번째 카드: "
                f"{card1['rank']}\n"
                f"두 번째 카드: "
                f"{card2['rank']}\n"
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

                u = get_user(
                    uid,
                    username
                )

                unit = (
                    "원"
                    if money_type == "R"
                    else "P"
                )

                balance = (
                    u["real_money"]
                    if money_type == "R"
                    else u["points"]
                )

                if typ == result:

                    payout = (
                        amount * 2
                    )

                    new_balance = (
                        balance + payout
                    )

                    if money_type == "R":

                        update_user(
                            uid,
                            real_money=new_balance
                        )

                    else:

                        update_user(
                            uid,
                            points=new_balance
                        )

                    hit.append(
                        f"🎯 {username}님 적중!\n"
                        f"💰 적중금액: +{payout:,}{unit}\n"
                        f"💳 보유머니: {new_balance:,}{unit}"
                    )

                else:

                    miss.append(
                        f"❌ {username}님 미적중\n"
                        f"💸 손실금액: -{amount:,}{unit}"
                    )

        if hit:

            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎯 적중 결과\n"
                    "━━━━━━━━━━━━━━\n"
                    + "\n\n".join(hit)
                )
            )

        if miss:

            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📌 베팅 결과\n"
                    "━━━━━━━━━━━━━━\n"
                    + "\n\n".join(miss)
                )
            )

        async with odd_even_lock:

            odd_even_games.pop(
                chat_id,
                None
            )

    except asyncio.CancelledError:

        return

    except Exception as e:

        print(
            "홀짝 타이머 오류:",
            repr(e)
        )

        async with odd_even_lock:

            odd_even_games.pop(
                chat_id,
                None
            )


# =========================================================
# 기존 /바카라 명령도 유지
#
# 단, 이제 /배팅 플 1000으로 바로 시작 가능
# =========================================================

async def start_baccarat(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    async with game_lock:

        game = baccarat_games.get(
            chat_id
        )

        if game and game["active"]:

            await update.message.reply_text(
                "🎰 현재 바카라 베팅이 진행 중입니다."
            )

            return

        baccarat_games[
            chat_id
        ] = {
            "active": True,
            "bets": {},
            "chat_id": chat_id
        }

        asyncio.create_task(
            baccarat_timer(
                context.application,
                chat_id
            )
        )

    await update.message.reply_text(
        "🎰 바카라 베팅 시작!\n"
        "━━━━━━━━━━━━━━\n"
        "⏱️ 지금부터 50초 동안 자유롭게 베팅할 수 있습니다.\n\n"
        "💰 베팅 방법\n"
        "/배팅 플 5000\n"
        "/배팅 뱅 5000\n"
        "/배팅 타이 5000\n"
        "/배팅 플 실 50000\n\n"
        "🔵 플 = PLAYER\n"
        "🔴 뱅 = BANKER\n"
        "🟢 타이 = TIE"
    )


async def start_odd_even(
    update,
    context
):

    if not update.message:
        return

    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    async with odd_even_lock:

        game = odd_even_games.get(
            chat_id
        )

        if game and game["active"]:

            await update.message.reply_text(
                "🎴 현재 홀짝 베팅이 진행 중입니다."
            )

            return

        odd_even_games[
            chat_id
        ] = {
            "active": True,
            "bets": {},
            "chat_id": chat_id
        }

        asyncio.create_task(
            odd_even_timer(
                context.application,
                chat_id
            )
        )

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


# =========================================================
# 도움말
# =========================================================

async def help_command(
    update,
    context
):

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
        "/복권 10장\n"
        "/복권 실\n"
        "/복권 실 10장\n\n"

        "🎰 바카라\n"
        "/배팅 플 1000\n"
        "/배팅 뱅 1000\n"
        "/배팅 타이 1000\n"
        "/배팅 플 실 50000\n\n"

        "🎴 홀짝\n"
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


# =========================================================
# 한국어 명령 처리
# =========================================================

async def korean_commands(
    update,
    context
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

    command = (
        parts[0]
        .split("@")[0]
    )

    handlers = {

        "/내정보": my_info,

        "/출석": attendance,

        "/레벨업": level_up,

        "/채팅순위": chat_ranking,

        "/복권": buy_lottery,

        "/바카라": start_baccarat,

        "/홀짝": odd_even_bet,

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

    handler = handlers.get(
        command
    )

    if handler is None:
        return

    context.args = parts[1:]

    await handler(
        update,
        context
    )


# =========================================================
# 오류 처리
# =========================================================

async def error_handler(
    update,
    context
):

    error = context.error

    print(
        "BOT ERROR:",
        repr(error)
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "================================"
    )

    print(
        "Telegram Bot Starting..."
    )

    print(
        "PostgreSQL Mode"
    )

    print(
        "================================"
    )

    # Render Health Check
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
    command_pattern = (
        r"^/(내정보|출석|레벨업|채팅순위|복권|"
        r"바카라|홀짝|도움말|지급|차감|"
        r"실머니지급|실머니차감|경험치|"
        r"경험치차감|배팅|베팅)"
        r"(?:@[\w_]+)?"
        r"(?:\s+.*)?$"
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                command_pattern
            ),
            korean_commands
        ),
        group=0
    )

    # 일반 채팅
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
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

    # 이전 업데이트를 버리고 현재부터 시작
    # 단, 이것만으로 다중 봇 Conflict가 해결되는 것은 아님.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
