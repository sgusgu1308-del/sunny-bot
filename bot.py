import logging
import json
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 로그 설정 (봇 상태 모니터링)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = "8901087051:AAHzB6cuZYdMpVn08_BpAI4VNfANu4CWRs4"
DB_FILE = "sunny_bot_db.json" # 내정보 및 출석 데이터 저장 파일

# 데이터 불러오기
def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

# 데이터 저장하기
def save_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 한글 명령어 처리 함수
async def handle_korean_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user = update.message.from_user
    user_id = str(user.id)
    user_name = user.first_name

    db = load_data()

    # 유저 데이터가 없으면 새로 생성 (초기값 세팅)
    if user_id not in db:
        db[user_id] = {"name": user_name, "money": 0, "count": 0, "last_check": ""}

    #     # 1. /출석 명령어
    if text == "/출석":
        from datetime import datetime, timezone, timedelta
        kor_tz = timezone(timedelta(hours=9))
        today = datetime.now(kor_tz).strftime("%Y-%m-%d")

        
        # 오늘 이미 출석했는지 확인
        if db[user_id]["last_check"] == today:
            await update.message.reply_text(f"❌ {user_name}님은 오늘 이미 출석체크를 하셨습니다!")
            return

        # 데이터 업데이트 (500원 고정 지급, 횟수 +1)
        db[user_id]["money"] += 500
        db[user_id]["count"] += 1
        db[user_id]["last_check"] = today
        save_data(db) # 파일에 즉시 저장

        await update.message.reply_text(
            f"🎉 <b>{user_name}</b>님 출석체크 완료!\n"
            f"🎁 <b>500원</b>이 적립되었습니다.\n"
            f"💰 현재 보유 잔액: <b>{db[user_id]['money']}원</b>",
            parse_mode="HTML"
        )

    # 2. /내정보 명령어 (보유 잔액 및 총 출석 횟수 확인)
    elif text == "/내정보":
        current_money = db[user_id]["money"]
        total_count = db[user_id]["count"]
        await update.message.reply_text(
            f"👤 <b>{user_name}</b>님의 정보\n"
            f"💵 현재 잔액: <b>{current_money}원</b>\n"
            f"📅 총 출석 횟수: <b>{total_count}회</b>",
            parse_mode="HTML"
        )

    # 3. /start 안내문구
    elif text == "/start":
        await update.message.reply_text(
            "⛵ 써니호 출석 및 내정보 봇에 오신 것을 환영합니다!\n\n"
            "💬 <b>/출석</b> : 매일 500원을 적립합니다.\n"
            "💬 <b>/내정보</b> : 현재까지 쌓인 돈과 출석 횟수를 확인합니다.",
            parse_mode="HTML"
        )

def main():
    application = Application.builder().token(TOKEN).build()

    # 텍스트 메시지를 감지하여 한글 명령어 작동
    application.add_handler(MessageHandler(filters.TEXT, handle_korean_commands))

    print("500원 적립 시스템 및 /출석, /내정보 명령어가 정상 작동 중입니다!")
    application.run_polling()

if __name__ == '__main__':
    main()
