import logging
import json
import os
import random
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 로그 설정
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = "8901087051:AAHzB6cuZYdMpVn08_BpAI4VNfANu4CWRs4"
DB_FILE = "sunny_bot_db.json"
ADMIN_IDS = ["7155379964", "8684501150"]

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return {}

def save_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def get_baccarat_score(cards):
    card_values = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0}
    return sum(card_values[c.split('_')] for c in cards) % 10

def display_cards(cards):
    s_map = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
    return " ".join([f"[ {s_map[c.split('_')]} {c.split('_')} ]" for c in cards])

# 레벨별 등급 이름 및 아이콘 정의
def get_level_title(level):
    titles = {1: "🪨 돌맹이", 2: "🥈 실버", 3: "🥇 골드", 4: "💎 다이아", 5: "🔹 사파이어"}
    return titles.get(level, "🔹 사파이어")

# 레벨업에 필요한 경험치 계산 함수
def get_next_exp_required(level):
    if level == 1: return 1000
    if level == 2: return 3000
    return 3000 * (2 ** (level - 2))

async def handle_korean_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip(); user = update.message.from_user; user_id = str(user.id); user_name = user.first_name
    db = load_data()
    
    # 신규 유저 초기 데이터 테이블 설정
    if user_id not in db: 
        db[user_id] = {"name": user_name, "money": 0, "count": 0, "last_check": "", "level": 1, "exp": 0, "total_chats": 0}
    
    # 하위 호환성 필드 마이그레이션 패치
    if "level" not in db[user_id]: db[user_id]["level"] = 1; db[user_id]["exp"] = 0
    if "total_chats" not in db[user_id]: db[user_id]["total_chats"] = 0

    is_command = text.startswith("/")
    
    # [시스템 기능] 일반 채팅 제어 로직 (채팅 증가, 경험치 계산 및 레벨업 시스템)
    if not is_command:
        db[user_id]["exp"] += 1          
        db[user_id]["total_chats"] += 1   
        
        current_lvl = db[user_id]["level"]
        req_exp = get_next_exp_required(current_lvl)
        
        if db[user_id]["exp"] >= req_exp and current_lvl < 5:
            db[user_id]["level"] += 1
            db[user_id]["exp"] -= req_exp  
            new_lvl = db[user_id]["level"]
            
            rewards = {2: 3000, 3: 10000, 4: 20000, 5: 50000}
            bonus = rewards.get(new_lvl, 0)
            db[user_id]["money"] += bonus
            
            save_data(db)
            await update.message.reply_text(
                f"🎊 <b>LEVEL UP!</b> 🎊\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>{user_name}</b>님의 등급이 상승했습니다!\n"
                f"✨ 현재 등급: <b>{get_level_title(new_lvl)} (Lv.{new_lvl})</b>\n"
                f"🎁 레벨업 보상금 <b>{bonus:,}원</b>이 지급되었습니다!💰",
                print_mode="HTML" if 'print_mode' in locals() else None,
                **{"parse_mode": "HTML"}
            )
            return
        else:
            save_data(db)

    # 1. /출석 (한국 시간 밤 12시 정각 초기화 시스템)
    if text == "/출석":
        from datetime import datetime, timezone, timedelta
        kor_tz = timezone(timedelta(hours=9)); today = datetime.now(kor_tz).strftime("%Y-%m-%d")
        if db[user_id]["last_check"] == today:
            await update.message.reply_text(f"❌ {user_name}님은 오늘 이미 출석체크를 하셨습니다!"); return
        db[user_id]["money"] += 500; db[user_id]["count"] += 1; db[user_id]["last_check"] = today; save_data(db)
        await update.message.reply_text(f"🎉 <b>{user_name}</b>님 출석체크 완료!\n🎁 500원 적립! 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")

    # 2. /배팅 (리얼 바카라 시스템)
    elif text.startswith("/배팅"):
        parts = text.split()
        if len(parts) < 3:
            await update.message.reply_text("⚠️ <b>바카라 사용법</b>\n<code>/배팅 [플레이어/뱅커/타이] [금액]</code>\n예시: <code>/배팅 플레이어 500</code>", parse_mode="HTML"); return
        
        bet_choice = parts[1]
        if bet_choice not in ["플레이어", "뱅커", "타이"]:
            await update.message.reply_text("⚠️ 배팅 대상은 <b>플레이어, 뱅커, 타이</b> 중에서 골라주세요!", parse_mode="HTML"); return
        try: bet_money = int(parts[2])
        except ValueError: await update.message.reply_text("⚠️ 배팅 금액은 숫자만 입력해 주세요!"); return
        if bet_money <= 0 or db[user_id]["money"] < bet_money:
            await update.message.reply_text(f"❌ 잔액이 부족하거나 올바르지 않은 금액입니다. 현재 잔액: {db[user_id]['money']:,}원"); return

        shapes = ["S", "H", "D", "C"]; ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        deck = [f"{r}_{s}" for r in ranks for s in shapes]; random.shuffle(deck)

        p_cards = [deck.pop(), deck.pop()]; b_cards = [deck.pop(), deck.pop()]
        p_score = get_baccarat_score(p_cards); b_score = get_baccarat_score(b_cards)

        if p_score > b_score: winner, payout = "플레이어", 2.0
        elif b_score > p_score: winner, payout = "뱅커", 1.95
        else: winner, payout = "타이", 8.0

        if bet_choice == winner:
            if winner == "타이" and bet_choice in ["플레이어", "뱅커"]:
                result_text = f"🤝 <b>무승부(타이) 발생!</b> 금액 {bet_money:,}원이 환불되었습니다."
            else:
                win_money = int(bet_money * payout)
                db[user_id]["money"] += (win_money - bet_money)
                result_text = f"🎉 <b>적중 성공!</b> [{winner}] 승리!\n🎁 <b>{win_money:,}원</b>을 획득했습니다!"
        else:
            if winner == "타이": result_text = f"🤝 <b>무승부(타이) 발생!</b> 금액 {bet_money:,}원이 환불되었습니다."
            else: db[user_id]["money"] -= bet_money; result_text = f"💥 <b>적중 실패...</b> 승자는 [{winner}]였습니다.\n💸 걸으신 <b>{bet_money:,}원</b>이 소멸되었습니다."
        
        save_data(db)

        caption_msg = (
            f"🃏 <b>써니호 바카라 테이블 결과</b> 🃏\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>플레이어:</b> {display_cards(p_cards)} (<b>{p_score}점</b>)\n"
            f"👑 <b>뱅커:</b> {display_cards(b_cards)} (<b>{b_score}점</b>)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>나의 선택:</b> {bet_choice} | 💰 <b>현재 잔액:</b> {db[user_id]['money']:,}원\n"
            f"📝 {result_text}"
        )
        await update.message.reply_text(caption_msg, parse_mode="HTML")

    # 3. 🎟️ 커스텀 확률 반영된 /복권 명령어 시스템
    elif text == "/복권":
        ticket_price = 1000
        if db[user_id]["money"] < ticket_price:
            await update.message.reply_text(f"❌ 복권 구입 금액(1,000원)이 부족합니다! 현재 잔액: {db[user_id]['money']:,}원"); return
        
        db[user_id]["money"] -= ticket_price
        
        # 0.1% 단위 정밀 계산을 위해 1부터 1000까지 난수 발생 (100% = 1000)
        rand_val = random.randint(1, 1000)
        
        if rand_val <= 2:  # 0.2% 확률 (1, 2)
            prize = 50000
            result_text = f"🍀 <b>[1등 대박 특등첨!]</b> 무려 <b>{prize:,}원</b>에 당첨되었습니다!!! 🎉"
        elif 3 <= rand_val <= 12:  # 1.0% 확률 (3 ~ 12까지 10개)
            prize = 10000
            result_text = f"🌟 <b>[2등 중박 당첨!]</b> 축하합니다! <b>{prize:,}원</b>에 당첨되었습니다! 🎊"
        elif 13 <= rand_val <= 62:  # 5.0% 확률 (13 ~ 62까지 50개)
            prize = 5000
            result_text = f"🎈 <b>[3등 소박 당첨!]</b> <b>{prize:,}원</b>에 당첨되셨습니다!"
        elif 63 <= rand_val <= 462:  # 40.0% 확률 (63 ~ 462까지 400개)
            prize = 0
            result_text = "😭 <b>[꽝]</b> 아쉽게도 낙첨되었습니다. 다음 기회를 노려보세요!"
        elif 463 <= rand_val <= 640:  # 17.8% 확률 (463 ~ 640까지 178개)
            prize = 300
            result_text = f"🪙 <b>[아차상 당첨]</b> <b>{prize:,}원</b>을 획득하셨습니다."
        elif 641 <= rand_val <= 820:  # 18.0% 확률 (641 ~ 820까지 180개)
            prize = 200
            result_text = f"🪙 <b>[아차상 당첨]</b> <b>{prize:,}원</b>을 획득하셨습니다."
        else:  # 나머지 18.0% 확률 (821 ~ 1000까지 180개)
            prize = 100
            result_text = f"🪙 <b>[아차상 당첨]</b> <b>{prize:,}원</b>을 획득하셨습니다."
            
        db[user_id]["money"] += prize
        save_data(db)
        await update.message.reply_text(
            f"🎟️ <b>써니호 즉석 복권 결과</b> 🎟️\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📝 {result_text}\n"
            f"💰 <b>현재 잔액:</b> {db[user_id]['money']:,}원",
            parse_mode="HTML"
        )

    # 4. 🏆 /랭킹 명령어 시스템 (보유 자산 포인트 기준 상위 10명)
    elif text == "/랭킹":
        ranked_users = sorted(db.items(), key=lambda x: x[1].get("money", 0), reverse=True)[:10]
        rank_msg = "🏆 <b>써니호 실시간 포인트 랭킹 TOP 10</b> 🏆\n━━━━━━━━━━━━━━━━━━\n"
        
        medal_emojis = {1: "🥇", 2: "🥈", 3: "🥉"}
        for index, (u_id, u_info) in enumerate(ranked_users, start=1):
            medal = medal_emojis.get(index, f"<b>{index}등</b>")
            u_lvl = u_info.get("level", 1)
            rank_msg += f"{medal} {u_info['name']} (Lv.{u_lvl}) : <b>{u_info.get('money', 0):,}원</b>\n"
            
        rank_msg += "━━━━━━━━━━━━━━━━━━"
        await update.message.reply_text(rank_msg, parse_mode="HTML")

    # 5. /돈충전 명령어 (관리자 전용 치트키)
    elif text.startswith("/돈충전") and user_id == str(ADMIN_ID):
        try:
            add_money = int(text.split()[1]); db[user_id]["money"] += add_money; save_data(db)
            await update.message.reply_text(f"👑 <b>관리자 권한으로 돈을 충전했습니다!</b>\n💵 추가된 금액: <b>{add_money:,}원</b>\n💰 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")
        except: pass

    # 6. /내정보 명령어
    elif text == "/내정보":
        lvl = db[user_id]["level"]
        current_exp = db[user_id]["exp"]
        max_exp = get_next_exp_required(lvl)
        total_chats = db[user_id]["total_chats"]
        exp_bar = "👑 만렙 달성 👑" if lvl >= 5 else f"{current_exp:,} / {max_exp:,} XP"
        
        await update.message.reply_text(
            f"👤 <b>{user_name}님의 스테이터스 카드</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👑 등급 명칭: <b>{get_level_title(lvl)}</b>\n"
            f"📊 현재 레벨: <b>Lv.{lvl}</b>\n"
