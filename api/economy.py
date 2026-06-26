"""OrienAI Economy — браки, монетки, ферма, мини-игры (с MongoDB)"""
import time, random
from typing import Dict, Optional, List, Any
import re as _re

# В памяти кэш (синхронизируется с MongoDB)
WALLETS: Dict[int, Dict[int, Dict[str, Any]]] = {}
MARRIAGES: Dict[int, List[Dict[str, Any]]] = {}
PROPOSALS: Dict[tuple, Dict[str, Any]] = {}
CHAT_MEMBERS: Dict[int, Dict[str, Dict[str, Any]]] = {}

DB = None  # сюда положим motor клиент

async def init_db(db):
    """Загружает данные из MongoDB в кэш при старте"""
    global DB
    DB = db
    
    # Кошельки
    async for doc in db.wallets.find():
        cid = doc["chat_id"]
        uid = doc["user_id"]
        if cid not in WALLETS: WALLETS[cid] = {}
        WALLETS[cid][uid] = {k: v for k, v in doc.items() if k not in ("_id", "chat_id", "user_id")}
    
    # Браки
    async for doc in db.marriages.find():
        cid = doc["chat_id"]
        if cid not in MARRIAGES: MARRIAGES[cid] = []
        MARRIAGES[cid].append({k: v for k, v in doc.items() if k not in ("_id", "chat_id")})
    
    # Участники чатов
    async for doc in db.members.find():
        cid = doc["chat_id"]
        if cid not in CHAT_MEMBERS: CHAT_MEMBERS[cid] = {}
        for un, info in doc.get("members", {}).items():
            CHAT_MEMBERS[cid][un] = info
    
    print(f"✅ DB loaded: {sum(len(w) for w in WALLETS.values())} wallets, "
          f"{sum(len(m) for m in MARRIAGES.values())} marriages, "
          f"{sum(len(c) for c in CHAT_MEMBERS.values())} members")

async def save_wallet(cid: int, uid: int):
    if not DB: return
    w = WALLETS.get(cid, {}).get(uid)
    if not w: return
    try:
        await DB.wallets.update_one(
            {"chat_id": cid, "user_id": uid},
            {"$set": {"chat_id": cid, "user_id": uid, **w}},
            upsert=True
        )
    except Exception as e:
        print(f"❌ save_wallet: {e}")

async def save_marriages(cid: int):
    if not DB: return
    try:
        await DB.marriages.delete_many({"chat_id": cid})
        for m in MARRIAGES.get(cid, []):
            await DB.marriages.insert_one({"chat_id": cid, **m})
    except Exception as e:
        print(f"❌ save_marriages: {e}")

async def save_members(cid: int):
    if not DB: return
    try:
        await DB.members.update_one(
            {"chat_id": cid},
            {"$set": {"chat_id": cid, "members": CHAT_MEMBERS.get(cid, {})}},
            upsert=True
        )
    except Exception as e:
        print(f"❌ save_members: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# ВАЛЮТА
# ══════════════════════════════════════════════════════════════════════════════
def get_wallet(cid: int, uid: int, uname: str = "") -> Dict[str, Any]:
    if cid not in WALLETS: WALLETS[cid] = {}
    if uid not in WALLETS[cid]:
        WALLETS[cid][uid] = {
            "name": uname, "coins": 100, "diamonds": 0, "food": 5,
            "last_farm": 0, "last_quest": 0, "last_daily": 0,
            "farm_streak": 0, "quests_done": 0
        }
    w = WALLETS[cid][uid]
    if uname: w["name"] = uname
    return w

async def add_coins(cid: int, uid: int, amount: int, uname: str = ""):
    w = get_wallet(cid, uid, uname); w["coins"] += amount
    await save_wallet(cid, uid)
    return w["coins"]

async def spend_coins(cid: int, uid: int, amount: int) -> bool:
    w = get_wallet(cid, uid)
    if w["coins"] < amount: return False
    w["coins"] -= amount
    await save_wallet(cid, uid)
    return True

async def spend_diamonds(cid: int, uid: int, amount: int) -> bool:
    w = get_wallet(cid, uid)
    if w["diamonds"] < amount: return False
    w["diamonds"] -= amount
    await save_wallet(cid, uid)
    return True

# ══════════════════════════════════════════════════════════════════════════════
# ФЕРМА
# ══════════════════════════════════════════════════════════════════════════════
FARM_COOLDOWN = 3600

async def farm(cid: int, uid: int, uname: str = ""):
    w = get_wallet(cid, uid, uname)
    now = time.time()
    elapsed = now - w["last_farm"]
    if elapsed < FARM_COOLDOWN:
        wait = int(FARM_COOLDOWN - elapsed)
        m, s = divmod(wait, 60)
        return None, f"⏳ ферма не готова жди ещё *{m}м {s}с*"
    if elapsed < FARM_COOLDOWN * 2: w["farm_streak"] += 1
    else: w["farm_streak"] = 1
    base = random.randint(20, 60)
    bonus = min(w["farm_streak"] * 5, 50)
    total = base + bonus
    diamond_drop = 1 if random.random() < 0.1 else 0
    food_drop = random.randint(1, 3) if random.random() < 0.3 else 0
    w["coins"] += total; w["diamonds"] += diamond_drop; w["food"] += food_drop
    w["last_farm"] = now
    await save_wallet(cid, uid)
    text = f"🌾 *собрал урожай!*\n\n`+{total}` 🪙 (база {base} + стрик {bonus})"
    if diamond_drop: text += f"\n`+{diamond_drop}` 💎"
    if food_drop: text += f"\n`+{food_drop}` 🍕"
    text += f"\n\nстрик: *{w['farm_streak']}* 🔥"
    return total, text

# ══════════════════════════════════════════════════════════════════════════════
# КВЕСТЫ
# ══════════════════════════════════════════════════════════════════════════════
QUEST_COOLDOWN = 1800
QUESTS = [
    {"text": "🔧 починить сервер босса", "reward": (50, 100)},
    {"text": "🍕 доставить пиццу программистам", "reward": (30, 70)},
    {"text": "🐛 найти баг в проде", "reward": (80, 150)},
    {"text": "☕ сварить кофе для тиммейтов", "reward": (20, 50)},
    {"text": "🎮 пройти данж в роблоксе", "reward": (40, 90)},
    {"text": "📦 распаковать новый комп", "reward": (60, 120)},
    {"text": "🛡 защитить кошака от собаки", "reward": (35, 75)},
    {"text": "🚀 деплой в пятницу вечером", "reward": (100, 200)},
    {"text": "🎨 нарисовать UI", "reward": (45, 85)},
    {"text": "💻 написать тесты (фу)", "reward": (70, 130)},
]

async def quest(cid: int, uid: int, uname: str = ""):
    w = get_wallet(cid, uid, uname)
    now = time.time()
    elapsed = now - w["last_quest"]
    if elapsed < QUEST_COOLDOWN:
        wait = int(QUEST_COOLDOWN - elapsed)
        m, s = divmod(wait, 60)
        return None, f"⏳ квесты ещё на кд жди *{m}м {s}с*"
    q = random.choice(QUESTS)
    reward = random.randint(*q["reward"])
    success = random.random() < 0.75
    w["last_quest"] = now
    if success:
        w["coins"] += reward; w["quests_done"] += 1
        await save_wallet(cid, uid)
        return reward, f"✅ *квест выполнен!*\n\n{q['text']}\n\n`+{reward}` 🪙\nвсего квестов: *{w['quests_done']}*"
    else:
        loss = random.randint(5, 20)
        w["coins"] = max(0, w["coins"] - loss)
        await save_wallet(cid, uid)
        return -loss, f"❌ *провал...*\n\n{q['text']}\n\n`-{loss}` 🪙 потерял на расходниках"

async def daily(cid: int, uid: int, uname: str = ""):
    w = get_wallet(cid, uid, uname)
    now = time.time()
    if now - w["last_daily"] < 86400:
        wait = int(86400 - (now - w["last_daily"]))
        h, m = divmod(wait // 60, 60)
        return None, f"⏳ дейлик уже забирал жди *{h}ч {m}м*"
    reward = random.randint(200, 400)
    diamond = 1 if random.random() < 0.2 else 0
    w["coins"] += reward; w["diamonds"] += diamond
    w["last_daily"] = now
    await save_wallet(cid, uid)
    text = f"🎁 *ежедневная награда!*\n`+{reward}` 🪙"
    if diamond: text += f"\n`+{diamond}` 💎"
    return reward, text

async def dice_game(cid: int, uid: int, bet: int):
    w = get_wallet(cid, uid)
    if w["coins"] < bet: return None, "не хватает монет"
    if bet < 10: return None, "минимальная ставка *10* 🪙"
    w["coins"] -= bet
    player = random.randint(1, 6) + random.randint(1, 6)
    bot = random.randint(1, 6) + random.randint(1, 6)
    text = f"🎲 ты бросил: *{player}*\n🤖 я бросил: *{bot}*\n\n"
    if player > bot:
        win = bet * 2; w["coins"] += win
        text += f"✅ *выиграл {win}* 🪙!"
    elif player == bot:
        w["coins"] += bet
        text += f"🤝 ничья возврат *{bet}* 🪙"
    else:
        text += f"❌ проиграл *{bet}* 🪙"
    await save_wallet(cid, uid)
    return player, text

# ══════════════════════════════════════════════════════════════════════════════
# БРАКИ
# ══════════════════════════════════════════════════════════════════════════════
def is_married(cid: int, uid: int) -> Optional[Dict]:
    for m in MARRIAGES.get(cid, []):
        if uid in (m["u1"], m["u2"]): return m
    return None

def get_spouse_id(cid: int, uid: int) -> Optional[int]:
    m = is_married(cid, uid)
    if not m: return None
    return m["u2"] if m["u1"] == uid else m["u1"]

def propose(cid: int, from_uid: int, from_name: str, target_uid: int, target_name: str):
    if from_uid == target_uid: return "сам с собой? шиза"
    if is_married(cid, from_uid): return f"*{from_name}* ты уже в браке сначала разведись `/divorce`"
    if is_married(cid, target_uid): return f"*{target_name}* уже в браке :("
    key = (cid, target_uid)
    if key in PROPOSALS:
        old = PROPOSALS[key]
        if time.time() - old["ts"] < 300:
            return f"*{target_name}* уже есть предложение от *{old['from_name']}* пусть сначала ответит"
    PROPOSALS[key] = {"from": from_uid, "from_name": from_name, "target_name": target_name, "ts": time.time()}
    return (f"💍 *{from_name}* делает предложение *{target_name}*!\n\n"
            f"*{target_name}*, ответь:\n"
            f"`/yes` — согласиться 💕\n"
            f"`/no` — отказать 💔\n\n"
            f"_(предложение действует 5 минут)_")

async def accept_proposal(cid: int, uid: int, uname: str):
    key = (cid, uid)
    if key not in PROPOSALS: return None, "тебе никто не предлагал"
    p = PROPOSALS[key]
    if time.time() - p["ts"] > 300:
        del PROPOSALS[key]
        return None, "предложение протухло"
    if cid not in MARRIAGES: MARRIAGES[cid] = []
    MARRIAGES[cid].append({
        "u1": p["from"], "u1_name": p["from_name"],
        "u2": uid, "u2_name": uname,
        "date": time.time(), "love": 50
    })
    del PROPOSALS[key]
    await add_coins(cid, p["from"], 100, p["from_name"])
    await add_coins(cid, uid, 100, uname)
    await save_marriages(cid)
    return True, (f"💖💖💖 *СВАДЬБА!* 💖💖💖\n\n"
                  f"*{p['from_name']}* ❤️ *{uname}*\n\n"
                  f"молодожёнам по `100` 🪙 в подарок!\nгорько! 🥂")

def reject_proposal(cid: int, uid: int, uname: str):
    key = (cid, uid)
    if key not in PROPOSALS: return "тебе никто не предлагал"
    p = PROPOSALS[key]; del PROPOSALS[key]
    return f"💔 *{uname}* отказал(а) *{p['from_name']}*\nне судьба..."

async def divorce(cid: int, uid: int, uname: str):
    m = is_married(cid, uid)
    if not m: return "ты не в браке бро"
    spouse_name = m["u2_name"] if m["u1"] == uid else m["u1_name"]
    days = int((time.time() - m["date"]) // 86400)
    MARRIAGES[cid].remove(m)
    await spend_coins(cid, uid, 50)
    await save_marriages(cid)
    return f"💔 *развод оформлен*\n\n*{uname}* и *{spouse_name}* разошлись после *{days}* дней\n`-50` 🪙 алименты"

async def gift_to_spouse(cid: int, uid: int, uname: str, gift_type: str):
    m = is_married(cid, uid)
    if not m: return "ты не в браке"
    spouse_name = m["u2_name"] if m["u1"] == uid else m["u1_name"]
    gifts = {
        "food": {"cost": 30, "love": 5, "emoji": "🍕", "name": "еду", "currency": "coins"},
        "flowers": {"cost": 50, "love": 10, "emoji": "💐", "name": "цветы", "currency": "coins"},
        "diamond": {"cost": 1, "love": 25, "emoji": "💎", "name": "бриллиант", "currency": "diamonds"},
        "ring": {"cost": 200, "love": 20, "emoji": "💍", "name": "кольцо", "currency": "coins"},
        "car": {"cost": 1000, "love": 50, "emoji": "🚗", "name": "тачку", "currency": "coins"},
    }
    if gift_type not in gifts: return f"подарки: `{'`, `'.join(gifts.keys())}`"
    g = gifts[gift_type]
    if g["currency"] == "diamonds":
        if not await spend_diamonds(cid, uid, g["cost"]): return f"не хватает 💎 (нужно *{g['cost']}*)"
    else:
        if not await spend_coins(cid, uid, g["cost"]): return f"не хватает 🪙 (нужно *{g['cost']}*)"
    m["love"] = min(100, m["love"] + g["love"])
    await save_marriages(cid)
    return (f"{g['emoji']} *{uname}* подарил(а) *{spouse_name}* {g['name']}!\n\n"
            f"💕 любовь: *{m['love']}/100*\nстоимость: `{g['cost']}` {'💎' if g['currency'] == 'diamonds' else '🪙'}")

async def share_food(cid: int, uid: int, uname: str):
    m = is_married(cid, uid)
    if not m: return "ты не в браке"
    w = get_wallet(cid, uid)
    if w["food"] < 1: return "у тебя нет еды 🍕"
    spouse_id = get_spouse_id(cid, uid)
    spouse_name = m["u2_name"] if m["u1"] == uid else m["u1_name"]
    spouse_w = get_wallet(cid, spouse_id, spouse_name)
    w["food"] -= 1; spouse_w["food"] += 1
    m["love"] = min(100, m["love"] + 3)
    await save_wallet(cid, uid)
    await save_wallet(cid, spouse_id)
    await save_marriages(cid)
    return f"🍕 *{uname}* поделился(ась) едой с *{spouse_name}*!\n\n💕 любовь: *{m['love']}/100*"

def all_marriages(cid: int):
    ms = MARRIAGES.get(cid, [])
    if not ms: return None
    lines = [f"💍 *БРАКИ ЧАТА* ({len(ms)})\n"]
    for m in ms:
        days = int((time.time() - m["date"]) // 86400)
        hearts = "❤️" * (m["love"] // 20) + "🤍" * (5 - m["love"] // 20)
        lines.append(f"*{m['u1_name']}* 💕 *{m['u2_name']}*")
        lines.append(f"  {hearts} `{m['love']}/100` | *{days}*д вместе\n")
    return "\n".join(lines)

SURPRISES = [
    ("☕ принёс кофе утром", 5), ("🎵 включил любимую музыку", 5),
    ("📱 запомнил твой день рождения", 10), ("🌹 встретил с цветами", 15),
    ("🍝 приготовил ужин при свечах", 20), ("✈️ забронировал поездку", 30),
    ("💌 написал любовное письмо", 8), ("🎁 неожиданный подарок", 12),
    ("🤗 крепко обнял без причины", 7), ("🎂 испёк торт", 15),
]

async def surprise(cid: int, uid: int, uname: str):
    m = is_married(cid, uid)
    if not m: return "ты не в браке"
    spouse_name = m["u2_name"] if m["u1"] == uid else m["u1_name"]
    text, love_gain = random.choice(SURPRISES)
    cost = 20
    if not await spend_coins(cid, uid, cost): return f"не хватает *{cost}* 🪙 на сюрприз"
    m["love"] = min(100, m["love"] + love_gain)
    await save_marriages(cid)
    return f"✨ *СЮРПРИЗ* от *{uname}* для *{spouse_name}*!\n\n{text}\n\n💕 `+{love_gain}` любви → *{m['love']}/100*\n`-{cost}` 🪙"

# ══════════════════════════════════════════════════════════════════════════════
# ПОИСК ЮЗЕРОВ
# ══════════════════════════════════════════════════════════════════════════════
async def remember_member(cid: int, user: dict):
    un = (user.get("username") or "").lower()
    if not un: return
    if cid not in CHAT_MEMBERS: CHAT_MEMBERS[cid] = {}
    CHAT_MEMBERS[cid][un] = {"id": user.get("id"), "name": user.get("first_name", un)}
    await save_members(cid)

def find_user_by_username(cid: int, username: str) -> Optional[Dict]:
    un = username.lstrip("@").lower()
    return CHAT_MEMBERS.get(cid, {}).get(un)

def extract_target(text: str, reply: Optional[dict], cid: int):
    if reply:
        u = reply.get("from", {})
        if u: return u.get("id"), u.get("first_name", "чел")
    match = _re.search(r'@(\w+)', text or "")
    if match:
        username = match.group(1)
        found = find_user_by_username(cid, username)
        if found: return found["id"], found["name"]
        return None, f"@{username}"
    return None, None
