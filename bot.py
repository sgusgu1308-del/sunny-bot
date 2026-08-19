import logging, json, os, random, asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = "8901087051:AAHzB6cuZYdMpVn08_BpAI4VNfANu4CWRs4"
DB_FILE = "sunny_bot_db.json"
ADMIN_IDS = ["8684501150", "7155379964"]

GAME_STATUS = {"is_running": False, "start_time": None, "round_count": 1, "history_matrix": [], "deck": [], "p_cards": [], "b_cards": [], "active_bets": {}}

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return {}

def save_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def get_baccarat_score(cards):
    return sum({"A":1,"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":0,"J":0,"Q":0,"K":0}[c.split('_')[0]] for c in cards) % 10

def display_cards(cards):
    s_map = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
    return " ".join([f"[ {s_map[c.split('_')[1]]} {c.split('_')[0]} ]" for c in cards])

# 🚨 문법 에러 원인이던 중괄호 f-string 문제를 일반 더하기(+) 문법으로 완벽 복구했습니다.
def get_img_url(card):
    r, s = card.split('_')
    s_name = {"S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}[s]
    return "https://github.io" + str(r) + "_of_" + str(s_name) + ".png"

def build_pattern_board():
    if not GAME_STATUS["history_matrix"]: return "아직 진행된 게임 기록이 없습니다."
    display_list = GAME_STATUS["history_matrix"][-36:]
    rows = [[] for _ in range(6)]
    for i, emoji in enumerate(display_list): rows[i % 6].append(emoji)
    return "\n".join([" ".join(row) for row in rows])

def get_level_title(level):
    return {1: "🪨 돌맹이", 2: "🥈 실버", 3: "🥇 골드", 4: "💎 다이아", 5: "🔹 사파이어"}.get(level, "🔹 사파이어")

def get_next_exp_required(level):
    return 1000 if level == 1 else (3000 if level == 2 else 3000 * (2 ** (level - 2)))

async def start_baccarat_timer(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    shapes, ranks = ["S", "H", "D", "C"], ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    GAME_STATUS["deck"] = [f"{r}_{s}" for r in ranks for s in shapes]; random.shuffle(GAME_STATUS["deck"])
    GAME_STATUS["p_cards"] = [GAME_STATUS["deck"].pop(), GAME_STATUS["deck"].pop()]
    GAME_STATUS["b_cards"] = [GAME_STATUS["deck"].pop(), GAME_STATUS["deck"].pop()]
    
    await asyncio.sleep(15)
    await context.bot.send_photo(chat_id=chat_id, photo=get_img_url(GAME_STATUS['p_cards'][0]), caption=f"🃏 <b>[{GAME_STATUS['round_count']}회차] 15초 경과 (플레이어 1번째 오픈)</b>\n━━━━━━━━━━━━━━━━━━\n👤 플레이어 패: {display_cards([GAME_STATUS['p_cards'][0]])}\n━━━━━━━━━━━━━━━━━━\n💬 다음 카드는 15초 뒤에 공개됩니다.", parse_mode="HTML")
    
    await asyncio.sleep(15)
    await context.bot.send_photo(chat_id=chat_id, photo=get_img_url(GAME_STATUS['b_cards'][0]), caption=f"🃏 <b>[{GAME_STATUS['round_count']}회차] 30초 경과 (뱅커 1번째 오픈)</b>\n━━━━━━━━━━━━━━━━━━\n👤 플레이어 패: {display_cards([GAME_STATUS['p_cards'][0]])}\n👑 뱅커 패: {display_cards([GAME_STATUS['b_cards'][0]])}\n━━━━━━━━━━━━━━━━━━\n💬 다음 카드는 15초 뒤에 공개됩니다.", parse_mode="HTML")
    
    await asyncio.sleep(15)
    await context.bot.send_photo(chat_id=chat_id, photo=get_img_url(GAME_STATUS['p_cards'][1]), caption=f"🃏 <b>[{GAME_STATUS['round_count']}회차] 45초 경과 (플레이어 2번째 오픈)</b>\n━━━━━━━━━━━━━━━━━━\n👤 플레이어 오픈 패: {display_cards(GAME_STATUS['p_cards'])}\n👑 뱅커 패: {display_cards([GAME_STATUS['b_cards'][0]])}\n━━━━━━━━━━━━━━━━━━\n🚨 <b>주의: 이제부터 배팅이 전면 마감됩니다!</b>", parse_mode="HTML")
    
    await asyncio.sleep(15)
    p_score, b_score = get_baccarat_score(GAME_STATUS["p_cards"]), get_baccarat_score(GAME_STATUS["b_cards"])
    if p_score > b_score: winner, payout, code_emoji = "플레이어", 2.0, "🔵"
    elif b_score > p_score: winner, payout, code_emoji = "뱅커", 1.95, "🔴"
    else: winner, payout, code_emoji = "타이", 8.0, "🟢"
    
    GAME_STATUS["history_matrix"].append(code_emoji)
    board_str = build_pattern_board()
    
    db = load_data(); summary_results = []
    for u_id, b_info in GAME_STATUS["active_bets"].items():
        if u_id not in db: continue
        bet_choice, bet_money = b_info["choice"], b_info["money"]
        if bet_choice == winner:
            if winner == "타이" and bet_choice in ["플레이어", "뱅커"]: db[u_id]["money"] += bet_money; summary_results.append(f"🤝 {b_info['name']}: 타이 발생으로 금액 {bet_money:,}원 전액 환불")
            else: win_amount = int(bet_money * payout); db[u_id]["money"] += win_amount; summary_results.append(f"🎉 {b_info['name']}: [{bet_choice}] 적중 성공! (+{win_amount:,}원)")
        else:
            if winner == "타이": db[u_id]["money"] += bet_money; summary_results.append(f"🤝 {b_info['name']}: 타이 발생으로 금액 {bet_money:,}원 전액 환불")
            else: summary_results.append(f"💥 {b_info['name']}: 적중 실패... (-{bet_money:,}원)")
    save_data(db)
    
    bet_summary_text = "\n".join(summary_results) if summary_results else "이번 회차에 참여한 유저가 없습니다."
    final_msg = f"🏆 <b>써니호 바카라 [{GAME_STATUS['round_count']}회차] 최종 결과</b> 🏆\n━━━━━━━━━━━━━━━━━━\n👤 <b>플레이어 카드:</b> {display_cards(GAME_STATUS['p_cards'])} (<b>{p_score}점</b>)\n👑 <b>뱅커 카드:</b> {display_cards(GAME_STATUS['b_cards'])} (<b>{b_score}점</b>)\n━━━━━━━━━━━━━━━━━━\n🎯 <b>최종 승자:</b> 🎉 <b>[{winner}]</b> 승리! 🎉\n\n📊 <b>실시간 출현 패턴 현황판 (육매)</b>\n<code>{board_str}</code>\n━━━━━━━━━━━━━━━━━━\n📝 <b>테이블 배팅 정산 내역:</b>\n{bet_summary_text}"
    await context.bot.send_photo(chat_id=chat_id, photo=get_img_url(GAME_STATUS['b_cards'][1]), caption=final_msg, parse_mode="HTML")
    
    GAME_STATUS["is_running"], GAME_STATUS["start_time"], GAME_STATUS["active_bets"] = False, None, {}
    GAME_STATUS["round_count"] += 1

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
            bonus = {2: 3000, 3: 10000, 4: 20000, 5: 50000}.get(new_lvl, 0); db[user_id]["money"] += bonus; save_data(db)
            await update.message.reply_text(f"🎊 <b>LEVEL UP!</b> 🎊\n━━━━━━━━━━━━━━━━━━\n👤 <b>{user_name}</b>님의 등급이 상승했습니다!\n✨ 현재 등급: <b>{get_level_title(new_lvl)} (Lv.{new_lvl})</b>\n🎁 레벨업 보상금 <b>{bonus:,}원</b>이 지급되었습니다!💰", parse_mode="HTML")
            return
        else: save_data(db)

    if text == "/출석":
        kor_tz = timezone(timedelta(hours=9)); today = datetime.now(kor_tz).strftime("%Y-%m-%d")
        if db[user_id]["last_check"] == today:
            await update.message.reply_text(f"❌ {user_name}님은 오늘 이미 출석체크를 하셨습니다!"); return
        db[user_id]["money"] += 500; db[user_id]["count"] += 1; db[user_id]["last_check"] = today; save_data(db)
        await update.message.reply_text(f"🎉 <b>{user_name}</b>님 출석체크 완료!\n🎁 500원 적립! 현재 잔액: <b>{db[user_id]['money']:,}원</b>", parse_mode="HTML")

    elif text.startswith("/배팅"):
        parts = text.split()
        if len(parts) < 3: await update.message.reply_text("⚠️ <b>바카라 사용법</b>\n<code>/배팅 [플레이어/뱅커/타이] [금액]</code>\n예시: <code>/배팅 플레이어 500</code>", parse_mode="HTML"); return
        bet_choice = parts[1]
        if bet_choice not in ["플레이어", "뱅커", "타이"]: await update.message.reply_text("⚠️ 배팅 대상은 <b>플레이어, 뱅커, 타이</b> 중에서 골라주세요!", parse_mode="HTML"); return
        try: bet_money = int(parts[2])
        except ValueError: await update.message.reply_text("⚠️ 배팅 금액은 숫자만 입력해 주세요!"); return
        if bet_money <= 0 or db[user_id]["money"] < bet_money: await update.message.reply_text(f"❌ 잔액이 부족하거나 올바르지 않은 금액입니다. 현재 잔액: {db[user_id]['money']:,}원"); return

        if GAME_STATUS["is_running"]:
            elapsed = (datetime.now() - GAME_STATUS["start_time"]).total_seconds()
            if elapsed >= 48: await update.message.reply_text("🚨 <b>배팅 마감 실패!</b>\n카드가 오픈되기 10초 전이므로 이번 회차 배팅이 마감되었습니다! 다음 회차를 노려주세요.", parse_mode="HTML"); return

        db[user_id]["money"] -= bet_money; save_data(db)
        if user_id in GAME_STATUS["active_bets"]: GAME_STATUS["active_bets"][user_id]["money"] += bet_money
        else: GAME_STATUS["active_bets"][user_id] = {"choice": bet_choice, "money": bet_money, "name": user_name}

        board_str = build_pattern_board()
        if not GAME_STATUS["is_running"]:
            GAME_STATUS["is_running"], GAME_STATUS["start_time"] = True, datetime.now()
            await update.message.reply_text(f"🎰 <b>써니호 바카라 [{GAME_STATUS['round_count']}회차] 게임 시작!</b>\n━━━━━━━━━━━━━━━━━━\n👤 <b>{user_name}</b>님이 <b>[{bet_choice}]</b>에 <b>{bet_money:,}원</b> 배팅완료!\n\n📊 <b>현재 패턴 현황판:</b>\n<code>{board_str}</code>\n━━━━━━━━━━━━━━━━━━\n⏳ <b>앞으로 1분간 카드 한 장씩 오픈을 시작합니다!</b>\n💡 [팁] 결과 오픈 10초 전까지 추가 배팅 가능!", parse_mode="HTML")
            asyncio.create_task(start_baccarat_timer(context, update.message.chat_id))
        else: await update.message.reply_text(f"➕ <b>추가 배팅 완료!</b>\n👤 <b>{user_name}</b>님이 <b>[{bet_choice}]</b>에 <b>{bet_money:,}원</b>을 추가 탑승하셨습니다!\n💰 보유 포인트: {db[user_id]['money']:,}원", parse_mode="HTML")

    elif text.startswith("/복권"):
        parts = text.split(); count = 1
        if len(parts) >= 2:
            try:
                count = int(parts[1])
