import logging
import json
import os
import random
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 로그 설정 (봇 상태 모니터링)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = "8901087051:AAHzB6cuZYdMpVn08_BpAI4VNfANu4CWRs4"
DB_FILE = "sunny_bot_db.json"
ADMIN_IDS = ["8684501150", "7155379964"]

# 바카라 글로벌 세션 및 카지노 육매 패턴판 기록 배열 초기화
GAME_STATUS = {
    "round_count": 1,
    "history_matrix": []
}

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_baccarat_score(cards):
    card_values = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0}
    return sum(card_values[c.split('_')[0]] for c in cards) % 10

def display_cards(cards):
    s_map = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
    return " ".join([f"[ {s_map[c.split('_')[1]]} {c.split('_')[0]} ]" for c in cards])

def get_img_url(card):
    r, s = card.split('_')
    s_name = {"S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}[s]
    return f"https://github.io{r}_of_{s_name}.png"

def build_pattern_board():
    if not GAME_STATUS["history_matrix"]:
        return "아직 진행된 게임 기록이 없습니다."
    display_list = GAME_STATUS["history_matrix"][-36:]
    rows = [[] for _ in range(6)]
    for i, emoji in enumerate(display_list):
        rows[i % 6].append(emoji)
    return "\n".join([" ".join(row) for row in rows])

def get_level_title(level):
    return {1: "🪨 돌맹이", 2: "🥈 실버", 3: "🥇 골드", 4: "💎 다이아", 5: "🔹 사파이어"}.get(level, "🔹 사파이어")

def get_next_exp_required(level):
    if level == 1: return 1000
    if level == 2: return 3000
    return 3000 * (2 ** (level - 2))

async def handle_korean_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    user = update.message.from_user
    user_id = str(user.id)
    user_name = user.first_name
    db = load_data()
    
    if user_id not in db:
        db[user_id] = {"name": user_name, "money": 0, "count": 0, "last_check": "", "level": 1, "exp": 0, "total_chats": 0}
    if "level" not in db[user_id]: db[user_id]["level"] = 1; db[user_id]["exp"] = 0
    if "total_chats" not in db[user_id]: db[user_id]["total_chats"] = 0

    is_command = text.startswith("/")
    if not is_command:
        db[user_id]["exp"] += 1
        db[user_id]["total_chats"] += 1
        current_lvl = db[user_id]["level"]
        req_exp = get_next_exp_required(current_lvl)
        if db[user_id]["exp"] >= req_exp and current_lvl < 5:
            db[user_id]["level"] += 1
            db[user_id]["exp"] -= req_exp
            new_lvl = db[user_id]["level"]
            bonus = {2: 3000, 3: 10000, 4: 20000, 5: 50000}.get(new_lvl, 0)
            db[user_id]["money"] += bonus
            save_data(db)
            await update.message.reply_text(f"🎊 <b>LEVEL UP!</b> 🎊\n━━━━━━━━━━━━━━━━━━\n👤 <b>{user_name}</b>님의 등급이 상승했습니다!\n✨ 현재 등급: <b>{get_level_title(new_lvl)} (Lv.{new_lvl})</b>\n🎁 레벨업 보상금 <b>{bonus:,}원</b>이 지급되었습니다!💰", parse_mode="HTML")
            return
        else:
            save_data(db)

    if text == "/출석":
        kor_tz = timezone(timedelta(hours=9))
        today = datetime.now(kor_tz).strftime("%Y-%m-%d")
        if db[user_id]["last_check"] == today:
            await update.message.reply_text(f"❌ {user_name}님은 오늘 이미 출석체크를 하셨습니다!")
            return
        db[user_id]["money"] += 500
        db[user_id]["count"] += 1
        db[user_id]["last_check"] = today
        save_data(db)
        await update.message.reply_text(f"🎉 <b>{user_name}</b>님 출석체크 완료!\n🎁 500원 적립! 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")

    elif text.startswith("/배팅"):
        parts = text.split()
        if len(parts) < 3:
            await update.message.reply_text("⚠️ <b>바카라 사용법</b>\n<code>/배팅 [플레이어/뱅커/타이] [금액]</code>\n예시: <code>/배팅 플레이어 500</code>", parse_mode="HTML")
            return
        bet_choice = parts[1]
        if bet_choice not in ["플레이어", "뱅커", "타이"]:
            await update.message.reply_text("⚠️ 배팅 대상은 <b>플레이어, 뱅커, 타이</b> 중에서 골라주세요!", parse_mode="HTML")
            return
        try:
            bet_money = int(parts[2])
        except ValueError:
            await update.message.reply_text("⚠️ 배팅 금액은 숫자만 입력해 주세요!")
            return
        if bet_money <= 0 or db[user_id]["money"] < bet_money:
            await update.message.reply_text(f"❌ 잔액이 부족하거나 올바르지 않은 금액입니다. 현재 잔액: {db[user_id]['money']:,}원")
            return

        # 🃏 서버 부하 없는 즉시 카드 생성 및 정산 엔진 가동
        shapes = ["S", "H", "D", "C"]
        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        deck = [f"{r}_{s}" for r in ranks for s in shapes]
        random.shuffle(deck)
        
        p_cards = [deck.pop(), deck.pop()]
        b_cards = [deck.pop(), deck.pop()]
        p_score = get_baccarat_score(p_cards)
        b_score = get_baccarat_score(b_cards)
        
        if p_score > b_score: winner, payout, code_emoji = "플레이어", 2.0, "🔵"
        elif b_score > p_score: winner, payout, code_emoji = "뱅커", 1.95, "🔴"
        else: winner, payout, code_emoji = "타이", 8.0, "🟢"
        
        GAME_STATUS["history_matrix"].append(code_emoji)
        board_str = build_pattern_board()
        
        if bet_choice == winner:
            if winner == "타이" and bet_choice in ["플레이어", "뱅커"]:
                result_text = f"🤝 <b>무승부(타이) 발생!</b> 금액 {bet_money:,}원이 환불되었습니다."
            else:
                win_amount = int(bet_money * payout)
                db[user_id]["money"] += (win_amount - bet_money)
                result_text = f"🎉 <b>적중 성공!</b> [{winner}] 승리! (+{win_amount:,}원)"
        else:
            if winner == "타이":
                result_text = f"🤝 <b>무승부(타이) 발생!</b> 금액 {bet_money:,}원이 환불되었습니다."
            else:
                db[user_id]["money"] -= bet_money
                result_text = f"💥 <b>적중 실패...</b> 승자는 [{winner}]였습니다. (-{bet_money:,}원)"
        save_data(db)
        
        final_msg = f"🏆 <b>써니호 바카라 [{GAME_STATUS['round_count']}회차] 최종 결과</b> 🏆\n━━━━━━━━━━━━━━━━━━\n👤 <b>플레이어 카드:</b> {display_cards(p_cards)} (<b>{p_score}점</b>)\n👑 <b>뱅커 딜러 카드:</b> {display_cards(b_cards)} (<b>{b_score}점</b>)\n━━━━━━━━━━━━━━━━━━\n🎯 <b>나의 선택:</b> {bet_choice} | 💰 <b>현재 잔액:</b> {db[user_id]['money']:,}원\n📝 {result_text}\n\n📊 <b>실시간 출현 패턴 현황판 (육매)</b>\n<code>{board_str}</code>"
        await update.message.reply_photo(photo=get_img_url(b_cards[0]), caption=final_msg, parse_mode="HTML")
        GAME_STATUS["round_count"] += 1

    elif text.startswith("/복권"):
        parts = text.split()
        count = 1
        if len(parts) >= 2:
            try:
                count = int(parts[1])
                if count < 1 or count > 10:
                    await update.message.reply_text("⚠️ 복권은 한 번에 1장에서 최대 10장까지만 구매할 수 있습니다!")
                    return
            except ValueError:
                await update.message.reply_text("⚠️ 구매 수량은 숫자만 입력해 주세요!")
                return

        total_cost = 1000 * count
        if db[user_id]["money"] < total_cost:
            await update.message.reply_text(f"❌ 복권 {count}장 구매를 위한 포인트({total_cost:,}원)가 부족합니다! 현재 잔액: {db[user_id]['money']:,}원")
            return
        
        db[user_id]["money"] -= total_cost
        receipt_text = ""
        total_prize = 0
        for i in range(1, count + 1):
            rand_val = random.randint(1, 1000)
            if rand_val <= 2: prize, res = 50000, "🍀 1등 대박 특등첨! (+50,000원)"
            elif 3 <= rand_val <= 12: prize, res = 10000, "🌟 2등 중박 당첨! (+10,000원)"
            elif 13 <= rand_val <= 62: prize, res = 5000, "🎈 3등 소박 당첨! (+5,000원)"
            elif 63 <= rand_val <= 462: prize, res = 0, "😭 꽝 (낙첨)"
            elif 463 <= rand_val <= 640: prize, res = 300, "🪙 아차상 당첨 (+300원)"
            elif 641 <= rand_val <= 820: prize, res = 200, "🪙 아차상 당첨 (+200원)"
            else: prize, res = 100, "🪙 아차상 당첨 (+100원)"
            total_prize += prize
            receipt_text += f"🎫 {i}번째 장: {res}\n"
            
        db[user_id]["money"] += total_prize
        save_data(db)
        await update.message.reply_text(f"🎟️ <b>써니호 즉석 묶음 복권 개봉 ({count}장 결과)</b> 🎟️\n━━━━━━━━━━━━━━━━━━\n{receipt_text}━━━━━━━━━━━━━━━━━━\n💵 총 비용: <b>{total_cost:,}원</b>\n🎁 총 상금: <b>{total_prize:,}원</b>\n💰 최종 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")

    elif text == "/랭킹":
        ranked_users = sorted(db.items(), key=lambda x: x.get("money", 0), reverse=True)[:10]
        rank_msg = "🏆 <b>써니호 실시간 포인트 랭킹 TOP 10</b> 🏆\n━━━━━━━━━━━━━━━━━━\n"
        medal_emojis = {1: "🥇", 2: "🥈", 3: "🥉"}
        for index, (u_id, u_info) in enumerate(ranked_users, start=1):
            medal = medal_emojis.get(index, f"<b>{index}등</b>")
            rank_msg += f"{medal} {u_info['name']} (Lv.{u_info.get('level', 1)}) : <b>{u_info.get('money', 0):,}원</b>\n"
        rank_msg += "━━━━━━━━━━━━━━━━━━"
        await update.message.reply_text(rank_msg, parse_mode="HTML")

    elif text.startswith("/돈충전") and user_id in ADMIN_IDS:
        try:
            parts_charge = text.split()
            if len(parts_charge) >= 2:
                add_money = int(parts_charge[1])
                db[user_id]["money"] += add_money
                save_data(db)
                await update.message.reply_text(f"👑 <b>관리자 권한으로 돈을 충전했습니다!</b>\n💵 추가된 금액: <b>{add_money:,}원</b>\n💰 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")
        except: pass

    elif text == "/내정보":
        lvl = db[user_id]["level"]
        current_exp = db[user_id]["exp"]
