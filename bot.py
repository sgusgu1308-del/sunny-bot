import logging
import json
import os
import random
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = "8901087051:AAHzB6cuZYdMpVn08_BpAI4VNfANu4CWRs4"
DB_FILE = "sunny_bot_db.json"
ADMIN_IDS = ["8684501150", "7155379964"]

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return {}

def save_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def get_baccarat_score(cards):
    card_values = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0}
    return sum(card_values[c.split('_')[0]] for c in cards) % 10

def display_cards(cards):
    s_map = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
    return " ".join([f"[ {s_map[c.split('_')[1]]} {c.split('_')[0]} ]" for c in cards])

def get_level_title(level):
    return {1: "🪨 돌맹이", 2: "🥈 실버", 3: "🥇 골드", 4: "💎 다이아", 5: "🔹 사파이어"}.get(level, "🔹 사파이어")

def get_next_exp_required(level):
    return 1000 if level == 1 else (3000 if level == 2 else 3000 * (2 ** (level - 2)))

async def handle_korean_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip(); user = update.message.from_user; user_id = str(user.id); user_name = user.first_name
    db = load_data()
    
    if user_id not in db: db[user_id] = {"name": user_name, "money": 0, "count": 0, "last_check": "", "level": 1, "exp": 0, "total_chats": 0}
    if "level" not in db[user_id]: db[user_id]["level"] = 1; db[user_id]["exp"] = 0
    if "total_chats" not in db[user_id]: db[user_id]["total_chats"] = 0

    is_command = text.startswith("/")
    if not is_command:
        db[user_id]["exp"] += 1; db[user_id]["total_chats"] += 1
        current_lvl = db[user_id]["level"]; req_exp = get_next_exp_required(current_lvl)
        if db[user_id]["exp"] >= req_exp and current_lvl < 5:
            db[user_id]["level"] += 1; db[user_id]["exp"] -= req_exp; new_lvl = db[user_id]["level"]
            bonus = {2: 3000, 3: 10000, 4: 20000, 5: 50000}.get(new_lvl, 0)
            db[user_id]["money"] += bonus; save_data(db)
            await update.message.reply_text(f"🎊 <b>LEVEL UP!</b> 🎊\n━━━━━━━━━━━━━━━━━━\n👤 <b>{user_name}</b>님의 등급이 상승했습니다!\n✨ 현재 등급: <b>{get_level_title(new_lvl)} (Lv.{new_lvl})</b>\n🎁 레벨업 보상금 <b>{bonus:,}원</b>이 지급되었습니다!💰", parse_mode="HTML")
            return
        else: save_data(db)

    if text == "/출석":
        from datetime import datetime, timezone, timedelta
        kor_tz = timezone(timedelta(hours=9)); today = datetime.now(kor_tz).strftime("%Y-%m-%d")
        if db[user_id]["last_check"] == today:
            await update.message.reply_text(f"❌ {user_name}님은 오늘 이미 출석체크를 하셨습니다!"); return
        db[user_id]["money"] += 500; db[user_id]["count"] += 1; db[user_id]["last_check"] = today; save_data(db)
        await update.message.reply_text(f"🎉 <b>{user_name}</b>님 출석체크 완료!\n🎁 500원 적립! 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")

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
            if winner == "타이" and bet_choice in ["플레이어", "뱅커"]: result_text = f"🤝 <b>무승부(타이) 발생!</b> 금액 {bet_money:,}원이 환불되었습니다."
            else:
                win_money = int(bet_money * payout); db[user_id]["money"] += (win_money - bet_money)
                result_text = f"🎉 <b>적중 성공!</b> [{winner}] 승리!\n🎁 <b>{win_money:,}원</b>을 획득했습니다!"
        else:
            if winner == "타이": result_text = f"🤝 <b>무승부(타이) 발생!</b> 금액 {bet_money:,}원이 환불되었습니다."
            else: db[user_id]["money"] -= bet_money; result_text = f"💥 <b>적중 실패...</b> 승자는 [{winner}]였습니다.\n💸 걸으신 <b>{bet_money:,}원</b>이 소멸되었습니다."
        save_data(db)
        caption_msg = f"🃏 <b>써니호 바카라 테이블 결과</b> 🃏\n━━━━━━━━━━━━━━━━━━\n👤 <b>플레이어:</b> {display_cards(p_cards)} (<b>{p_score}점</b>)\n👑 <b>뱅커:</b> {display_cards(b_cards)} (<b>{b_score}점</b>)\n━━━━━━━━━━━━━━━━━━\n🎯 <b>나의 선택:</b> {bet_choice} | 💰 <b>현재 잔액:</b> {db[user_id]['money']:,}원\n개별결과: {result_text}"
        await update.message.reply_text(caption_msg, parse_mode="HTML")

    elif text == "/복권":
        ticket_price = 1000
        if db[user_id]["money"] < ticket_price:
            await update.message.reply_text(f"❌ 복권 구입 금액(1,000원)이 부족합니다! 현재 잔액: {db[user_id]['money']:,}원"); return
        db[user_id]["money"] -= ticket_price; rand_val = random.randint(1, 1000)
        if rand_val <= 2: prize = 50000; result_text = f"🍀 <b>[1등 대박 특등첨!]</b> 무려 <b>{prize:,}원</b>에 당첨되었습니다!!! 🎉"
        elif 3 <= rand_val <= 12: prize = 10000; result_text = f"🌟 <b>[2등 중박 당첨!]</b> 축하합니다! <b>{prize:,}원</b>에 당첨되었습니다! 🎊"
        elif 13 <= rand_val <= 62: prize = 5000; result_text = f"🎈 <b>[3등 소박 당첨!]</b> <b>{prize:,}원</b>에 당첨되셨습니다!"
        elif 63 <= rand_val <= 462: prize = 0; result_text = "😭 <b>[꽝]</b> 아쉽게도 낙첨되었습니다. 다음 기회를 노려보세요!"
        elif 463 <= rand_val <= 640: prize = 300; result_text = f"🪙 <b>[아차상 당첨]</b> <b>{prize:,}원</b>을 획득하셨습니다."
        elif 641 <= rand_val <= 820: prize = 200; result_text = f"🪙 <b>[아차상 당첨]</b> <b>{prize:,}원</b>을 획득하셨습니다."
        else: prize = 100; result_text = f"🪙 <b>[아차상 당첨]</b> <b>{prize:,}원</b>을 획득하셨습니다."
        db[user_id]["money"] += prize; save_data(db)
        await update.message.reply_text(f"🎟️ <b>써니호 즉석 복권 결과</b> 🎟️\n━━━━━━━━━━━━━━━━━━\n📝 {result_text}\n💰 <b>현재 잔액:</b> {db[user_id]['money']:,}원", parse_mode="HTML")

    elif text == "/랭킹":
        ranked_users = sorted(db.items(), key=lambda x: x[1].get("money", 0), reverse=True)[:10]
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
                add_money = int(parts_charge[1]); db[user_id]["money"] += add_money; save_data(db)
                await update.message.reply_text(f"👑 <b>관리자 권한으로 돈을 충전했습니다!</b>\n💵 추가된 금액: <b>{add_money:,}원</b>\n💰 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")
        except: pass

    elif text == "/내정보":
        lvl = db[user_id]["level"]; current_exp = db[user_id]["exp"]; max_exp = get_next_exp_required(lvl); total_chats = db[user_id]["total_chats"]
        exp_bar = "👑 만렙 달성 👑" if lvl >= 5 else f"{current_exp:,} / {max_exp:,} XP"
        await update.message.reply_text(f"👤 <b>{user_name}님의 스테이터스 카드</b>\n━━━━━━━━━━━━━━━━━━\n👑 등급 명칭: <b>{get_level_title(lvl)}</b>\n📊 현재 레벨: <b>Lv.{lvl}</b>\n📈 경험치량: <code>{exp_bar}</code>\n💬 총 채팅수: <b>{total_chats:,}회</b>\n💵 보유 포인트: <b>{db[user_id]['money']:,}원</b>\n📅 총 출석수: <b>{db[user_id]['count']}회</b>\n━━━━━━━━━━━━━━━━━━", parse_mode="HTML")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_korean_commands))
    application.run_polling()

if __name__ == '__main__': main()
