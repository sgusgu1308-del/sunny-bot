import os
import random
import sqlite3
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ==========================================
# 1. RENDER 웹서버 속이기용 Flask 설정 (서버 다운 방지)
# ==========================================
web_app = Flask('')

@web_app.route('/')
def home():
    return "Telegram Bot is Running Successfully!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

# ==========================================
# 2. 데이터베이스 초기화 (데이터 평생 보존)
# ==========================================
DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 유저 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            points INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_attendance TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 관리자 ID 목록
ADMIN_IDS = [7155379964, 8684501150]

# 레벨별 칭호 및 한계치
LEVEL_NAMES = {1: "돌맹이", 2: "동", 3: "은", 4: "골드", 5: "다이아"}
XP_REQUIREMENTS = {1: 300, 2: 1000, 3: 5000, 4: 10000}
LEVEL_UP_COSTS = {1: 5000, 2: 10000, 3: 20000, 4: 50000}

# 바카라 전역 변수 관리
baccarat_game = {
    "is_betting": False,
    "bets": {}, # user_id: {"type": "P/B/T", "amount": 0}
    "time_left": 60
}

# ==========================================
# 3. 헬퍼 함수 (DB 연동)
# ==========================================
def get_user(user_id, username="유저"):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT points, xp, level, last_attendance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id, username, points, xp, level) VALUES (?, ?, 0, 0, 1)", (user_id, username))
        conn.commit()
        points, xp, level, last_attendance = 0, 0, 1, None
    else:
        points, xp, level, last_attendance = row
    conn.close()
    return {"points": points, "xp": xp, "level": level, "last_attendance": last_attendance}

def update_user(user_id, points=None, xp=None, level=None, last_attendance=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if points is not None:
        cursor.execute("UPDATE users SET points = ? WHERE user_id = ?", (points, user_id))
    if xp is not None:
        cursor.execute("UPDATE users SET xp = ? WHERE user_id = ?", (xp, user_id))
    if level is not None:
        cursor.execute("UPDATE users SET level = ? WHERE user_id = ?", (level, user_id))
    if last_attendance is not None:
        cursor.execute("UPDATE users SET last_attendance = ? WHERE user_id = ?", (last_attendance, user_id))
    conn.commit()
    conn.close()

# ==========================================
# 4. 명령어 및 핵심 기능 구현
# ==========================================

# [기능 1] 내 정보 기능
async def my_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.first_name
    u = get_user(user_id, username)
    
    level_name = LEVEL_NAMES.get(u['level'], "최고 등급")
    next_xp = XP_REQUIREMENTS.get(u['level'], "MAX")
    next_cost = LEVEL_UP_COSTS.get(u['level'], "MAX")
    
    msg = (
        f"👤 [{username}] 님의 정보\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏅 레벨: {u['level']} [{level_name}]\n"
        f"💰 보유 포인트: {u['points']:,} 원\n"
        f"✨ 경험치: {u['xp']} / {next_xp}\n"
        f"🔼 다음 레벨업 비용: {f'{next_cost:,} 원' if isinstance(next_cost, int) else next_cost}"
    )
    await update.message.reply_text(msg)

# [기능 2] 한국 시간 기준 00시 초기화 출석체크
async def attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u = get_user(user_id)
    
    # 한국 시간(UTC+9) 계산
    kr_now = datetime.utcnow() + timedelta(hours=9)
    today_str = kr_now.strftime("%Y-%m-%d")
    
    if u['last_attendance'] == today_str:
        await update.message.reply_text("❌ 오늘은 이미 출석체크를 완료하셨습니다! 밤 12시(00시) 이후에 다시 시도해 주세요.")
        return
        
    new_points = u['points'] + 1000
    update_user(user_id, points=new_points, last_attendance=today_str)
    await update.message.reply_text(f"📆 출석체크 완료! 1,000포인트(원)가 지급되었습니다. (현재: {new_points:,} 원)")

# [기능 3] 레벨업 기능
async def level_up(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u = get_user(user_id)
    current_lvl = u['level']
    
    if current_lvl >= 5:
        await update.message.reply_text("👑 이미 최고 레벨인 [다이아] 등급입니다!")
        return
        
    req_xp = XP_REQUIREMENTS[current_lvl]
    req_cost = LEVEL_UP_COSTS[current_lvl]
    
    if u['xp'] < req_xp:
        await update.message.reply_text(f"❌ 경험치가 부족합니다! ({u['xp']}/{req_xp} 필요)")
        return
    if u['points'] < req_cost:
        await update.message.reply_text(f"❌ 포인트가 부족합니다! ({req_cost:,} 원 필요)")
        return
        
    update_user(user_id, points=u['points'] - req_cost, level=current_lvl + 1)
    await update.message.reply_text(f"🎉 축하합니다! 레벨업에 성공하여 [{LEVEL_NAMES[current_lvl+1]}] 등급이 되었습니다!")

# [기능 4] 채팅당 경험치 + 낮은 확률 깜짝 보너스
async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith('/'):
        return # 명령어는 제외
        
    user_id = update.effective_user.id
    u = get_user(user_id, update.effective_user.first_name)
    
    added_xp = 1
    bonus_triggered = False
    
    # 1/10000 ~ 5/10000 확률로 보너스 이벤트 발생 (평균 약 0.03%)
    if random.random() < 0.0003:
        bonus_xp = random.randint(50, 100)
        added_xp += bonus_xp
        bonus_triggered = True
        
    new_xp = u['xp'] + added_xp
    update_user(user_id, xp=new_xp)
    
    if bonus_triggered:
        await update.message.reply_text(f"🎁 깜짝 축하합니다! 돌발 이벤트로 대량의 경험치 {added_xp-1}XP를 획득하셨습니다! 🎉")

# [기능 5] 복권 기능
async def buy_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u = get_user(user_id)
    
    cost = 1000
    if u['points'] < cost:
        await update.message.reply_text("❌ 복권 구입 비용은 1,000원입니다. 포인트가 부족합니다.")
        return
        
    rand = random.random() * 100 # 0% ~ 100%
    prize = 0
    rank = ""
    
    if rand < 0.05:
        rank = "1등 🥇"
        prize = 50000
    elif rand < 0.05 + 0.1:
        rank = "2등 🥈"
        prize = 30000
    elif rand < 0.15 + 0.8:
        rank = "3등 🥉"
        prize = 10000
    elif rand < 0.95 + 1.2:
        rank = "4등 🏅"
        prize = 7000
    elif rand < 30.0: # 약 28% 확률로 5등 당첨되도록 알아서 배분
        rank = "5등 🎗️"
        prize = random.randint(100, 5000)
    else:
        rank = "낙첨 😭"
        prize = 0
        
    new_points = u['points'] - cost + prize
    update_user(user_id, points=new_points)
    
    if prize > 0:
        await update.message.reply_text(f"🎫 복권 긁기 결과: **[{rank}]** 당첨!! 🎉\n💰 상금 {prize:,}원이 지급되었습니다! (현재: {new_points:,} 원)")
    else:
        await update.message.reply_text(f"🎫 복권 긁기 결과: **[{rank}]** 다음 기회에... (현재: {new_points:,} 원)")

# [기능 6] 관리자 포인트 지급 / 차감 명령어
async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if admin_id not in ADMIN_IDS:
        return
        
    try:
        # 사용법: /지급 유저ID 금액  또는  /지급 금액 (본인에게)
        args = context.args
        if len(args) == 1:
            target_id = admin_id
            amount = int(args[0])
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1])
        else:
            await update.message.reply_text("💡 사용법: `/지급 [유저ID] [금액]` 또는 `/지급 [금액]`")
            return
            
        t = get_user(target_id)
        update_user(target_id, points=t['points'] + amount)
        await update.message.reply_text(f"✅ [{target_id}]님에게 {amount:,}포인트를 지급했습니다. (현재: {t['points'] + amount:,}원)")
    except Exception as e:
        await update.message.reply_text("❌ 형식이 올바르지 않습니다.")

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if admin_id not in ADMIN_IDS:
        return
        
    try:
        args = context.args
        if len(args) == 1:
            target_id = admin_id
            amount = int(args[0])
        elif len(args) == 2:
            target_id = int(args[0])
            amount = int(args[1])
        else:
            await update.message.reply_text("💡 사용법: `/차감 [유저ID] [금액]` 또는 `/차감 [금액]`")
            return
            
        t = get_user(target_id)
        update_user(target_id, points=max(0, t['points'] - amount))
        await update.message.reply_text(f"✅ [{target_id}]님에게서 {amount:,}포인트를 빼갔습니다. (현재: {max(0, t['points'] - amount):,}원)")
    except Exception as e:
        await update.message.reply_text("❌ 형식이 올바르지 않습니다.")

# ==========================================
# [기능 7] 1분 주기 순차 카드 오픈 바카라 시스템
# ==========================================
def get_card():
    shapes = ['♠️', '♥️', '♦️', '♣️']
    numbers = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
    return f"{random.choice(shapes)}{random.choice(numbers)}"

def calc_baccarat_score(cards):
    score = 0
    for card in cards:
        num = card[2:]
        if num in ['10', 'J', 'Q', 'K']:
            continue
        elif num == 'A':
            score += 1
        else:
            score += int(num)
    return score % 10

async def baccarat_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not baccarat_game["is_betting"]:
        await update.message.reply_text("🎰 현재 진행 중인 바카라 베팅 기간이 아닙니다. 다음 게임을 기다려주세요.")
        return
        
    try:
        # /바카라 [플레이어/뱅커/타이] [금액]
        type_input = context.args[0]
