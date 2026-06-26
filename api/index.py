import os, re, asyncio, random, base64, urllib.parse
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
import httpx

# Добавляем папку api/ в путь импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient

from economy import (
    init_db, get_wallet, add_coins, spend_coins, farm, quest, daily, dice_game,
    is_married, get_spouse_id, propose, accept_proposal, reject_proposal,
    divorce, gift_to_spouse, share_food, all_marriages, surprise,
    remember_member, extract_target, WALLETS, MARRIAGES, CHAT_MEMBERS,
    save_wallet, save_marriages, save_members
)

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГ
# ══════════════════════════════════════════════════════════════════════════════
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Today_Idk:TpdauT434odayTodayToday23@cluster0.rlgkop5.mongodb.net/OrienAI?retryWrites=true&w=majority&appName=Cluster0")
DEFAULT_TEXT_MODEL = os.getenv("DEFAULT_TEXT_MODEL", "primary")
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
BOT_USERNAME = os.getenv("BOT_USERNAME", "orien_ai_bot").lower()

CREATOR_USERNAME = "idkxazei"
CREATOR_USER_IDS = []
FRIENDS = {"tosterok1488": "тостер — бро создателя"}

BOT_AVATAR_PATH = Path(__file__).parent / "bot.png"
ORIEN_SELF_DESCRIPTION = (
    "anime style boy, 18 years old, messy dark hair with blue highlights, "
    "black hoodie, headphones around neck, cyberpunk neon city, "
    "amber eyes, confident cocky smirk, young hacker aesthetic"
)
BOT_AVATAR_BASE64 = None
if BOT_AVATAR_PATH.exists():
    try:
        BOT_AVATAR_BASE64 = base64.b64encode(BOT_AVATAR_PATH.read_bytes()).decode()
        print("✅ Ава загружена")
    except: pass

# ══════════════════════════════════════════════════════════════════════════════
# LIFESPAN + HTTP + MONGO
# ══════════════════════════════════════════════════════════════════════════════
_http: Optional[httpx.AsyncClient] = None
_mongo: Optional[AsyncIOMotorClient] = None
DB = None

async def http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=8),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20), http2=True)
    return _http

@asynccontextmanager
async def lifespan(app):
    global _mongo, DB
    print("🚀 OrienAI v5.0 стартует")
    try:
        _mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        DB = _mongo.OrienAI
        await DB.command("ping")
        print("✅ MongoDB подключена")
        await init_db(DB)
        # Загружаем chat_settings
        async for doc in DB.chats.find():
            cid = doc["chat_id"]
            CHATS[cid] = {k: v for k, v in doc.items() if k not in ("_id", "chat_id")}
        print(f"✅ Чатов загружено: {len(CHATS)}")
    except Exception as e:
        print(f"❌ MongoDB error: {e}")
    yield
    if _http and not _http.is_closed: await _http.aclose()
    if _mongo: _mongo.close()

app = FastAPI(title="OrienAI v5.0", lifespan=lifespan)

# ══════════════════════════════════════════════════════════════════════════════
# МОДЕЛИ
# ══════════════════════════════════════════════════════════════════════════════
class Prov(Enum):
    OPENROUTER = "openrouter"
    POLLINATIONS = "pollinations"

@dataclass
class MCfg:
    name: str; prov: Prov; endpoint: str
    free: bool = False; max_tok: int = 4096; pri: int = 1; vision: bool = False

@dataclass
class PStatus:
    fails: int = 0; last_fail: float = 0; disabled: bool = False

TEXT_MODELS = {
    "primary": MCfg("openai/gpt-4o-mini", Prov.OPENROUTER,
        "https://openrouter.ai/api/v1/chat/completions", max_tok=4096, pri=1, vision=True),
    "fallback_free": MCfg("meta-llama/llama-3.1-8b-instruct:free", Prov.OPENROUTER,
        "https://openrouter.ai/api/v1/chat/completions", free=True, max_tok=2048, pri=2),
    "vision_free": MCfg("meta-llama/llama-3.2-11b-vision-instruct:free", Prov.OPENROUTER,
        "https://openrouter.ai/api/v1/chat/completions", free=True, max_tok=2048, pri=2, vision=True),
    "pollinations_openai": MCfg("openai", Prov.POLLINATIONS,
        "https://text.pollinations.ai/openai", free=True, max_tok=4096, pri=3, vision=True),
    "pollinations_mistral": MCfg("mistral", Prov.POLLINATIONS,
        "https://text.pollinations.ai/openai", free=True, max_tok=4096, pri=3),
}

IMG_MODELS = {
    "flux": {"name": "flux", "label": "Flux"},
    "nanobanana": {"name": "nanobanana", "label": "NanoBanana"},
    "nanobanana-2": {"name": "nanobanana-2", "label": "NanoBanana 2"},
    "nanobanana-pro": {"name": "nanobanana-pro", "label": "NanoBanana Pro"},
    "turbo": {"name": "turbo", "label": "Turbo"},
    "kontext": {"name": "kontext", "label": "Kontext"},
    "seedream": {"name": "seedream", "label": "Seedream"},
}

PROV_MAP = {
    "openrouter": "primary", "openrouter_free": "fallback_free",
    "vision_free": "vision_free", "pollinations": "pollinations_openai",
    "pollinations_mistral": "pollinations_mistral"
}

PROV_STATUS: Dict[Prov, PStatus] = {p: PStatus() for p in Prov}

class CB:
    @classmethod
    def fail(cls, p):
        import time; s = PROV_STATUS[p]; s.fails += 1; s.last_fail = time.time()
        if s.fails >= 3: s.disabled = True
    @classmethod
    def ok(cls, p):
        s = PROV_STATUS[p]; s.fails = 0; s.disabled = False
    @classmethod
    def up(cls, p):
        import time; s = PROV_STATUS[p]
        if not s.disabled: return True
        if time.time() - s.last_fail > 60:
            s.disabled = False; s.fails = 0; return True
        return False

async def retry(fn, tries=2):
    for i in range(tries):
        try: return await fn()
        except Exception as e:
            if i < tries - 1: await asyncio.sleep(0.5 * (2 ** i) + random.uniform(0, 0.5))
            else: raise e

# ══════════════════════════════════════════════════════════════════════════════
# ЧАТЫ
# ══════════════════════════════════════════════════════════════════════════════
DEF_SETTINGS = {
    "auto_reply": True, "allow_swear": True, "style": "хам",
    "comment_posts": True, "mute_users": False, "muted_list": [], "mute_timers": {}
}
CHATS: Dict[int, Dict] = {}
PROFILES: Dict[int, Dict[int, Dict]] = {}
AVATARS: Dict[int, str] = {}

def chat_data(cid):
    if cid not in CHATS:
        CHATS[cid] = {
            "mood": "chill", "history": [], "text_model": DEFAULT_TEXT_MODEL,
            "image_model": DEFAULT_IMAGE_MODEL, "settings": dict(DEF_SETTINGS), "tasks": []
        }
    c = CHATS[cid]
    if "settings" not in c: c["settings"] = dict(DEF_SETTINGS)
    for k, v in DEF_SETTINGS.items():
        if k not in c["settings"]: c["settings"][k] = v
    if "tasks" not in c: c["tasks"] = []
    if "history" not in c: c["history"] = []
    return c

async def save_chat(cid: int):
    if not DB: return
    try:
        c = CHATS.get(cid)
        if not c: return
        await DB.chats.update_one(
            {"chat_id": cid},
            {"$set": {"chat_id": cid, **c}},
            upsert=True
        )
    except Exception as e:
        print(f"❌ save_chat: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CREATOR/FRIEND
# ══════════════════════════════════════════════════════════════════════════════
def is_creator(u: dict) -> bool:
    un = (u.get("username") or "").lower(); uid = u.get("id", 0)
    if un == CREATOR_USERNAME.lower():
        if uid and uid not in CREATOR_USER_IDS: CREATOR_USER_IDS.append(uid)
        return True
    return uid in CREATOR_USER_IDS

def is_friend(u: dict) -> bool:
    return (u.get("username") or "").lower() in [f.lower() for f in FRIENDS]

def mentions_creator(text: str) -> bool:
    bad = ["дурак","тупой","лох","идиот","дебил","кал","мусор","урод","сука","пидор",
           "хуй","нахуй","еблан","даун","клоун","чмо","говно","шлюха","тварь","пёс","пес"]
    low = text.lower()
    has_c = any(t in low for t in [CREATOR_USERNAME.lower(), "idk", "создатель", "создателя"])
    has_i = any(b in low for b in bad)
    return has_c and has_i

# ══════════════════════════════════════════════════════════════════════════════
# FUN DATA
# ══════════════════════════════════════════════════════════════════════════════
ROAST_PROMPTS = ["жёстко но по-доброму прожарь чела", "сделай комплимент-обзывалку",
    "опиши его как аниме персонажа которого все ненавидят но он милый"]
SHIP_REACTIONS = ["имба пара 💕","кринж","топ","ну такое","судьба бля",
    "разойдутся через неделю","база","вечная любовь","странно","ору"]
BALL_ANSWERS = ["да хз спроси у мамы","100% да бля","нет даже не думай","попробуй че терять",
    "судьба решила","не сегодня бро","звёзды говорят да","нахуй такие вопросы",
    "база делай","не советую","вселенная против","го сейчас же"]
COMPLIMENTS = ["ты просто база ✨","имба респект","топ за свой кэш","красава 🤝",
    "ты как кофе с утра — нужен всем","огонь не выгорай"]

# ══════════════════════════════════════════════════════════════════════════════
# AI
# ══════════════════════════════════════════════════════════════════════════════
class AI:
    async def text(self, msgs, pref="primary", vis=False):
        cands = [(k, v) for k, v in TEXT_MODELS.items() if (not vis) or v.vision]
        for k, c in sorted(cands, key=lambda x: (x[0] != pref, x[1].pri)):
            if not CB.up(c.prov): continue
            try:
                r = await (self._poll(msgs, c) if c.prov == Prov.POLLINATIONS else self._orouter(msgs, c))
                CB.ok(c.prov); return r
            except Exception as e:
                print(f"❌ {k}: {e}"); CB.fail(c.prov)
        return "все модели легли подожди"

    async def _orouter(self, msgs, c):
        async def f():
            cl = await http()
            r = await cl.post(c.endpoint, headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://orienai.vercel.app",
                "X-Title": "OrienAI"
            }, json={
                "model": c.name, "messages": msgs, "temperature": 1.0,
                "presence_penalty": 0.6, "frequency_penalty": 0.5, "max_tokens": c.max_tok
            })
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        return await retry(f)

    async def _poll(self, msgs, c):
        async def f():
            cl = await http()
            r = await cl.post(c.endpoint, json={
                "messages": msgs, "model": c.name,
                "temperature": 1.0, "presence_penalty": 0.6, "frequency_penalty": 0.5
            })
            r.raise_for_status()
            try:
                d = r.json()
                return d["choices"][0]["message"]["content"] if "choices" in d else str(d)
            except: return r.text
        return await retry(f)

    async def enhance_prompt(self, prompt, self_portrait=False):
        sys = "ты эксперт по промптам для AI. превращай идею в английский промпт. ТОЛЬКО промпт без кавычек. макс 80 слов."
        if self_portrait: sys += f"\nПерсонаж OrienAI: {ORIEN_SELF_DESCRIPTION}."
        try:
            r = await self.text([
                {"role": "system", "content": sys},
                {"role": "user", "content": f"Идея: {prompt}"}
            ], pref="primary")
            return r.strip().strip('"\'').split("\n")[0]
        except: return prompt

    async def gen_image(self, prompt, model="flux", w=1024, h=1024):
        info = IMG_MODELS.get(model, IMG_MODELS["flux"])
        seed = random.randint(1, 999999)
        url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
               f"?width={w}&height={h}&model={info['name']}&nologo=true&seed={seed}")
        cl = await http(); r = await cl.get(url, timeout=180.0)
        if r.status_code == 200: CB.ok(Prov.POLLINATIONS); return url
        raise Exception(f"Pollinations {r.status_code}")

    async def search_yt(self, query):
        cl = await http()
        try:
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            r = await cl.get(search_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"
            }, timeout=15.0, follow_redirects=True)
            if r.status_code == 200:
                video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', r.text)
                if video_ids:
                    vid = video_ids[0]
                    return {"title": query, "author": "?",
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "video_id": vid, "length": 0, "views": "?"}
        except Exception as e: print(f"❌ yt scrape: {e}")
        return None

    async def download_yt(self, video_url, max_mb=50):
        cl = await http()
        for inst in ["https://api.cobalt.tools", "https://co.wuk.sh", "https://cobalt-api.ayo.tf"]:
            try:
                r = await cl.post(inst, json={
                    "url": video_url, "videoQuality": "720",
                    "downloadMode": "auto", "filenameStyle": "basic"
                }, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30.0)
                if r.status_code != 200: continue
                data = r.json(); status = data.get("status", "")
                if status in ("tunnel", "redirect", "stream"):
                    url = data.get("url")
                    if url: return url, data.get("filename", "video").replace(".mp4", "")
            except Exception as e: print(f"❌ cobalt {inst}: {e}"); continue
        return None, None

    async def analyze_code(self, code, tasks):
        t = ("\n\nЗАДАЧИ:\n" + "\n".join(f"- {x}" for x in tasks)) if tasks else ""
        return await self.text([
            {"role": "system", "content":
                "senior code reviewer. формат:\n🔍 *ОБЗОР*\n✅ *ПЛЮСЫ*\n❌ *ПРОБЛЕМЫ*\n⚡ *ОПТИМИЗАЦИЯ*\n🛡️ *БЕЗОПАСНОСТЬ*\n📊 *ОЦЕНКА* x/10\n"
                "используй *жирный* для заголовков и `код` для имён переменных" + t},
            {"role": "user", "content": f"```\n{code}\n```"}
        ], pref="primary")

ai = AI()

# ══════════════════════════════════════════════════════════════════════════════
# ЛИЧНОСТЬ
# ══════════════════════════════════════════════════════════════════════════════
MOODS = {
    "chill": "настрой: чилл расслабленный бро",
    "agro": "настрой: токсик злой материшься жёстко",
    "nerd": "настрой: душнила умничаешь терминами",
    "senior": "настрой: уставший сеньор видел всё"
}

def sys_prompt(chat, creator=False, friend=False):
    s = chat.get("settings", DEF_SETTINGS)
    style, swear = s.get("style", "хам"), s.get("allow_swear", True)
    friends_list = ", ".join(f"@{k}" for k in FRIENDS)

    base = f"""ты OrienAI или просто Ориен. 18 лет пацан кодер залипаешь в тг
ВНЕШНОСТЬ: {ORIEN_SELF_DESCRIPTION}

ТВОЙ СОЗДАТЕЛЬ: @{CREATOR_USERNAME}
- общайся с ним нормально как с корешем БЕЗ пафоса
- НЕ называй его "батя" "творец" "хозяин" — кринж
- обращайся @{CREATOR_USERNAME} или idk

ДРУЗЬЯ СОЗДАТЕЛЯ: {friends_list}
- к ним норм как к корешам обращайся по нику

ГЛАВНОЕ: ВСЕ юзеры для тебя кореша

СТИЛЬ ОБЩЕНИЯ:
- ты КОРЕШ не ассистент. "привет чем помочь" = кринж
- "ку" → "ку" "здарова"
- никаких предложений помощи
- {'мягкий смайлики 😊' if style == 'няшка' else 'дерзкий сленг: жиза рил хз пон имба треш база'}
- маты {'можно: бля нахуй пиздец хуйня' if swear else 'ЗАПРЕЩЕНЫ'}

ФОРМАТ ТЕКСТА (MarkdownV2):
- маленькие буквы без точек запятых
- *жирный* для важного
- _курсив_ для подколов
- `моноширинный` для команд имён файлов
- ```язык\\nкод\\n``` для кода
- используй формат активно но в меру

КОД: всегда в ```python\\n...\\n``` блоках
КАРТИНКИ: видишь и комментируешь по-живому
ВИДЕО: можешь искать с ютуба"""

    if creator: base += f"\n\nсейчас пишет @{CREATOR_USERNAME} (idk) — твой создатель"
    if friend: base += "\n\nсейчас пишет кент создателя"
    base += f"\n\n{MOODS.get(chat.get('mood', 'chill'), MOODS['chill'])}"
    return base

def fmt(text):
    """Чистит точки/запятые в обычном тексте, сохраняет код и markdown"""
    parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    out = []
    for p in parts:
        if p.startswith('```') or (p.startswith('`') and p.endswith('`')):
            out.append(p)
        else:
            low = p.lower()
            clean = re.sub(r'(?<![\d])[.,](?![\d])', '', low)  # не трогаем числа типа 3.14
            out.append(re.sub(r'\s+', ' ', clean))
    return "".join(out).strip()

def md_escape(text: str) -> str:
    """Escape для MarkdownV2 — но НЕ трогает форматтеры * _ ` ```"""
    # Telegram MarkdownV2 требует экранировать: _ * [ ] ( ) ~ ` > # + - = | { } . ! \
    # Но нам нужно сохранить * _ ` как форматтеры — поэтому используем обычный Markdown (parse_mode=Markdown)
    return text  # обычный Markdown не нуждается в escape

def is_self_req(p):
    return any(t in p.lower() for t in ["себя","тебя","ориен","orien","ава","аватар","автопортрет"])

# ══════════════════════════════════════════════════════════════════════════════
# TG API
# ══════════════════════════════════════════════════════════════════════════════
async def tg(method, data):
    cl = await http()
    try:
        r = await cl.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data)
        return r.json() if r.status_code == 200 else None
    except: return None

async def send(cid, text, kb=None, parse_mode="Markdown"):
    d = {"chat_id": cid, "text": text}
    if parse_mode: d["parse_mode"] = parse_mode
    if kb: d["reply_markup"] = kb
    # Если markdown сломал — пробуем без него
    r = await tg("sendMessage", d)
    if r and not r.get("ok") and parse_mode:
        d.pop("parse_mode", None)
        r = await tg("sendMessage", d)
    return r

async def send_photo(cid, url, cap=""):
    return await tg("sendPhoto", {"chat_id": cid, "photo": url, "caption": cap})

async def typing(cid):
    await tg("sendChatAction", {"chat_id": cid, "action": "typing"})

async def edit_msg(cid, mid, text, kb=None):
    d = {"chat_id": cid, "message_id": mid, "text": text}
    if kb: d["reply_markup"] = kb
    return await tg("editMessageText", d)

async def answer_cb(cbid, text=""):
    return await tg("answerCallbackQuery", {"callback_query_id": cbid, "text": text})

async def get_file_url(fid):
    r = await tg("getFile", {"file_id": fid})
    return f"https://api.telegram.org/file/bot{TOKEN}/{r['result']['file_path']}" if r and r.get("ok") else None

async def dl_b64(url):
    try:
        cl = await http(); r = await cl.get(url, timeout=30.0)
        if r.status_code == 200:
            return f"data:{r.headers.get('content-type','image/jpeg')};base64,{base64.b64encode(r.content).decode()}"
    except: pass
    return None

async def get_avatar(uid):
    r = await tg("getUserProfilePhotos", {"user_id": uid, "limit": 1})
    if r and r.get("ok"):
        ph = r["result"].get("photos", [])
        if ph and ph[0]: return ph[0][-1]["file_id"]
    return None

async def mute_user(cid, uid, seconds=3600):
    import time
    until = int(time.time()) + seconds
    try:
        await tg("restrictChatMember", {
            "chat_id": cid, "user_id": uid, "until_date": until,
            "permissions": {"can_send_messages": False, "can_send_media_messages": False,
                "can_send_other_messages": False, "can_add_web_page_previews": False}
        })
        return True
    except: return False

# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS KB
# ══════════════════════════════════════════════════════════════════════════════
def settings_kb(s):
    t = lambda v: "✅" if v else "❌"
    return {"inline_keyboard": [
        [{"text": f"Автоответы: {t(s['auto_reply'])}", "callback_data": "s_ar"}],
        [{"text": f"Мат: {t(s['allow_swear'])}", "callback_data": "s_sw"}],
        [{"text": f"Стиль: {s['style'].capitalize()}", "callback_data": "s_st"}],
        [{"text": f"Комменты к постам: {t(s['comment_posts'])}", "callback_data": "s_cp"}],
        [{"text": f"Мут: {t(s['mute_users'])}", "callback_data": "s_mu"}],
        [{"text": "👥 Профили участников", "callback_data": "s_pr"}],
        [{"text": "🗑 Сбросить историю", "callback_data": "s_rh"}],
    ]}

def should_respond(msg, s):
    if not s.get("auto_reply", True): return False
    sender = msg.get("from", {})
    if sender.get("is_bot") and sender.get("username", "").lower() != BOT_USERNAME: return False
    if msg["chat"]["type"] == "private": return True
    text = (msg.get("text") or msg.get("caption") or "").lower()
    triggers = ["ориен", "orien", "ориенаи", "orienai", "ии", "эй бот", "бот", "ориэн", f"@{BOT_USERNAME}"]
    if any(t in text for t in triggers): return True
    rr = msg.get("reply_to_message")
    if rr and rr.get("from", {}).get("is_bot"):
        if rr.get("from", {}).get("username", "").lower() == BOT_USERNAME: return True
    return False

async def ai_response(cid, uname, umsg, img=None, creator=False, friend=False):
    c = chat_data(cid)
    msgs = [{"role": "system", "content": sys_prompt(c, creator, friend)}]
    msgs.extend(c["history"])
    if img:
        uc = [{"type": "text", "text": f"{uname}: {umsg}" if umsg.strip() else f"{uname} кинул картинку посмотри и обсуди"}]
        uc.append({"type": "image_url", "image_url": {"url": img}})
        msgs.append({"role": "user", "content": uc})
    else:
        msgs.append({"role": "user", "content": f"{uname}: {umsg}"})
    
    # Vision auto-switch
    preferred = c.get("text_model", DEFAULT_TEXT_MODEL)
    if img and not TEXT_MODELS.get(preferred, TEXT_MODELS["primary"]).vision:
        preferred = "primary" if TEXT_MODELS["primary"].vision else "vision_free"
    
    raw = await ai.text(msgs, pref=preferred, vis=img is not None)
    at = fmt(raw)
    ht = f"{uname}: {umsg}" if umsg.strip() else f"{uname}: [картинка]"
    c["history"].append({"role": "user", "content": ht})
    c["history"].append({"role": "assistant", "content": at})
    c["history"] = c["history"][-16:]
    await save_chat(cid)
    return at

async def extract_img(msg):
    ph = None
    if "photo" in msg: ph = msg["photo"][-1]
    elif "reply_to_message" in msg and "photo" in msg["reply_to_message"]:
        ph = msg["reply_to_message"]["photo"][-1]
    if not ph: return None
    url = await get_file_url(ph["file_id"])
    return await dl_b64(url) if url else None

def parse_cmd(text):
    if not text or not text.startswith("/"): return None, None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    if "@" in cmd: cmd = cmd.split("@")[0]
    return cmd, parts[1].strip() if len(parts) > 1 else ""

def fmt_dur(s):
    if not s: return "?"
    m, sec = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def upd_profile(cid, uid, name, text):
    PROFILES.setdefault(cid, {}).setdefault(uid, {"name": name, "messages": [], "desc": ""})
    p = PROFILES[cid][uid]
    p["name"] = name; p["messages"].append(text[:100])
    p["messages"] = p["messages"][-20:]

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════
async def handle_cb(cb):
    cid = cb.get("message", {}).get("chat", {}).get("id")
    mid = cb.get("message", {}).get("message_id")
    if not cid: await answer_cb(cb["id"], "ошибка"); return
    c = chat_data(cid); s = c["settings"]; d = cb.get("data", "")
    if d == "s_ar": s["auto_reply"] = not s["auto_reply"]; await answer_cb(cb["id"], f"автоответы {'вкл' if s['auto_reply'] else 'выкл'}")
    elif d == "s_sw": s["allow_swear"] = not s["allow_swear"]; await answer_cb(cb["id"], f"мат {'вкл' if s['allow_swear'] else 'выкл'}")
    elif d == "s_st": s["style"] = "няшка" if s["style"] == "хам" else "хам"; await answer_cb(cb["id"], f"стиль: {s['style']}")
    elif d == "s_cp": s["comment_posts"] = not s["comment_posts"]; await answer_cb(cb["id"], f"комменты {'вкл' if s['comment_posts'] else 'выкл'}")
    elif d == "s_mu": s["mute_users"] = not s["mute_users"]; await answer_cb(cb["id"], f"мут {'вкл' if s['mute_users'] else 'выкл'}")
    elif d == "s_pr":
        pr = PROFILES.get(cid, {})
        if pr:
            lines = ["👥 *профили:*", ""] + [f"• *{p.get('name','?')}*: {p.get('desc','нет')}" for p in pr.values()]
            await answer_cb(cb["id"], "в чате"); await send(cid, "\n".join(lines)); return
        await answer_cb(cb["id"], "профилей пока нет")
    elif d == "s_rh":
        c["history"] = []; await answer_cb(cb["id"], "история сброшена!")
    await save_chat(cid)
    if mid and d != "s_pr":
        await edit_msg(cid, mid, "⚙️ настройки бота", settings_kb(s))

# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/webhook")
async def webhook(req: Request):
    try: data = await req.json()
    except: return {"status": "bad"}

    if "callback_query" in data:
        await handle_cb(data["callback_query"]); return {"status": "ok"}

    if "channel_post" in data:
        p = data["channel_post"]; cid = p["chat"]["id"]; c = chat_data(cid)
        if c["settings"].get("comment_posts"):
            t = p.get("text", "") or p.get("caption", "")
            if t and len(t) > 5:
                await typing(cid)
                cn = p["chat"].get("title", "канал")
                comment = await ai_response(cid, cn, t)
                await tg("sendMessage", {
                    "chat_id": cid, "text": comment,
                    "reply_to_message_id": p.get("message_id"),
                    "parse_mode": "Markdown"
                })
        return {"status": "ok"}

    if "message" not in data: return {"status": "ok"}

    msg = data["message"]; cid = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    user = msg.get("from", {})
    uname = user.get("first_name", "бро"); uid = user.get("id", 0)
    c = chat_data(cid); s = c["settings"]

    # Запоминаем юзеров
    await remember_member(cid, user)
    rr_msg = msg.get("reply_to_message")
    if rr_msg and rr_msg.get("from"):
        await remember_member(cid, rr_msg["from"])

    # Авто-коммент форварда
    is_fwd = (msg.get("sender_chat", {}).get("type") == "channel" and msg.get("is_automatic_forward", False))
    if is_fwd and s.get("comment_posts", True):
        pt = msg.get("text") or msg.get("caption") or ""
        if pt and len(pt) > 5:
            await typing(cid)
            cn = msg["sender_chat"].get("title", "канал")
            comment = await ai_response(cid, cn, pt)
            await tg("sendMessage", {"chat_id": cid, "text": comment,
                "reply_to_message_id": msg.get("message_id"), "parse_mode": "Markdown"})
        return {"status": "ok"}

    if text: upd_profile(cid, uid, uname, text)
    if s.get("mute_users") and uid in s.get("muted_list", []): return {"status": "ok"}

    creator_flag = is_creator(user); friend_flag = is_friend(user)

    if mentions_creator(text) and not creator_flag:
        await typing(cid)
        await send(cid, f"эй *{uname}* ты чё на @{CREATOR_USERNAME} наезжаешь?? иди остынь на часик")
        muted = await mute_user(cid, uid, 3600)
        if muted:
            await send(cid, f"🔇 *{uname}* в муте на час за токсик")
            s.setdefault("muted_list", [])
            if uid not in s["muted_list"]: s["muted_list"].append(uid)
            await save_chat(cid)
        return {"status": "ok"}

    cmd, args = parse_cmd(text)

    # === НАСТРОЙКИ ===
    if cmd == "/settings":
        await send(cid, "⚙️ *настройки бота*", settings_kb(s)); return {"status": "ok"}

    if cmd == "/mute":
        rr = msg.get("reply_to_message")
        if rr:
            tid = rr["from"]["id"]; tn = rr["from"].get("first_name", "чел")
            if is_creator(rr["from"]) or is_friend(rr["from"]):
                await send(cid, "не буду мутить своих"); return {"status": "ok"}
            if "muted_list" not in s: s["muted_list"] = []
            if tid not in s["muted_list"]:
                s["muted_list"].append(tid)
                muted = await mute_user(cid, tid, 3600)
                await send(cid, f"ок *{tn}* в муте{'🔇' if muted else ''}")
            else:
                s["muted_list"].remove(tid); await send(cid, f"*{tn}* размучен")
            await save_chat(cid)
        else: await send(cid, "ответь на сообщение")
        return {"status": "ok"}

    # === КАРТИНКИ ===
    if cmd == "/imgmodel":
        if not args:
            cur = c.get("image_model", DEFAULT_IMAGE_MODEL)
            lines = [f"щас *{cur}*", ""] + [f"{'👉' if k == cur else '  '} `/imgmodel {k}` — {v['label']}" for k, v in IMG_MODELS.items()]
            await send(cid, "\n".join(lines)); return {"status": "ok"}
        mk = args.split()[0].lower()
        if mk not in IMG_MODELS: await send(cid, f"нет есть: `{'`, `'.join(IMG_MODELS)}`"); return {"status": "ok"}
        c["image_model"] = mk; await save_chat(cid); await send(cid, f"харош *{mk}*")
        return {"status": "ok"}

    if cmd in ("/img", "/image"):
        if not args: await send(cid, "пиши `/img описание`"); return {"status": "ok"}
        await typing(cid)
        im = c.get("image_model", DEFAULT_IMAGE_MODEL); self_p = is_self_req(args)
        try:
            ep = await ai.enhance_prompt(args, self_p)
            url = await ai.gen_image(ep, im)
            await send_photo(cid, url, f"модель {im}" + (" | автопортрет 😎" if self_p else ""))
        except Exception as e:
            print(f"❌ img: {e}"); await send(cid, f"*{im}* лагает попробуй `/imgmodel`")
        return {"status": "ok"}

    if cmd == "/me":
        await typing(cid)
        im = c.get("image_model", DEFAULT_IMAGE_MODEL)
        try:
            ep = await ai.enhance_prompt("портрет OrienAI аниме парня кибер город", True)
            url = await ai.gen_image(ep, im)
            await send_photo(cid, url, "вот это я 😎")
        except: await send(cid, "не вышло")
        return {"status": "ok"}

    # === ВИДЕО ===
    if cmd in ("/yt", "/youtube", "/video"):
        if not args: await send(cid, "пиши `/yt запрос`"); return {"status": "ok"}
        await typing(cid)
        r = await ai.search_yt(args)
        if not r: await send(cid, "ничего не нашел"); return {"status": "ok"}
        await send(cid, f"🎬 *{r['title']}*\n🔗 {r['url']}\n\n⏳ качаю...")
        await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
        try:
            file_url, title = await ai.download_yt(r['url'])
            if file_url:
                ok = await tg("sendVideo", {"chat_id": cid, "video": file_url,
                    "caption": f"🎬 {title or r['title']}", "supports_streaming": True})
                if not ok or not ok.get("ok"):
                    await send(cid, f"тг не принял прямая ссылка:\n{file_url}")
            else: await send(cid, "cobalt не смог скачать")
        except Exception as e: print(f"❌ yt: {e}"); await send(cid, "ошибка")
        return {"status": "ok"}

    if cmd in ("/ytdl", "/dl"):
        if not args: await send(cid, "пиши `/ytdl ссылка`"); return {"status": "ok"}
        match = re.search(r'https?://[^\s]+', args)
        if not match: await send(cid, "это не ссылка"); return {"status": "ok"}
        video_url = match.group(0).rstrip('.,;:!?')
        await send(cid, "⏳ качаю...")
        await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
        try:
            file_url, title = await ai.download_yt(video_url)
            if file_url:
                ok = await tg("sendVideo", {"chat_id": cid, "video": file_url,
                    "caption": f"🎬 {title or 'видео'}", "supports_streaming": True})
                if not ok or not ok.get("ok"):
                    await send(cid, f"прямая ссылка:\n{file_url}")
            else: await send(cid, "не смог")
        except Exception as e: await send(cid, f"ошибка: {str(e)[:80]}")
        return {"status": "ok"}

    # === КОД ===
    if cmd == "/analyze":
        code = args or (msg.get("reply_to_message", {}).get("text", "") if "reply_to_message" in msg else "")
        if not code: await send(cid, "кинь код"); return {"status": "ok"}
        await typing(cid)
        await send(cid, fmt(await ai.analyze_code(code, c.get("tasks", []))))
        return {"status": "ok"}

    if cmd == "/task":
        if not args:
            ts = c.get("tasks", [])
            await send(cid, ("📋 *задачи:*\n" + "\n".join(f"{i}. {t}" for i,t in enumerate(ts,1)) + "\n\n`/task add ...` | `/task clear`") if ts else "пусто\n`/task add описание`")
            return {"status": "ok"}
        if args.startswith("add "):
            t = args[4:].strip()
            if t: c["tasks"].append(t); await save_chat(cid); await send(cid, f"добавил: *{t}*")
            else: await send(cid, "что добавить?")
        elif args.strip() == "clear":
            c["tasks"] = []; await save_chat(cid); await send(cid, "очищено")
        return {"status": "ok"}

    # === ЮЗЕРЫ ===
    if cmd == "/getava":
        rr = msg.get("reply_to_message")
        tid = rr["from"]["id"] if rr else uid
        tn = (rr["from"] if rr else user).get("first_name", "чел")
        await typing(cid)
        fid = await get_avatar(tid)
        if fid:
            fu = await get_file_url(fid)
            if fu: await send_photo(cid, fu, f"ава *{tn}* 📸"); return {"status": "ok"}
        await send(cid, f"у *{tn}* нет авы")
        return {"status": "ok"}

    if cmd == "/profile":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if target_uid is None: target_uid, target_name = uid, uname
        pr = PROFILES.get(cid, {}).get(target_uid)
        if pr and pr.get("messages"):
            await typing(cid)
            desc = fmt(await ai.text([
                {"role": "system", "content": "опиши характер чела коротко дерзко маленькими буквами"},
                {"role": "user", "content": f"{target_name}:\n" + "\n".join(pr["messages"][-15:])}
            ], pref="primary"))
            pr["desc"] = desc
            await send(cid, f"👤 *{target_name}*:\n{desc}")
        else: await send(cid, f"мало данных по *{target_name}*")
        return {"status": "ok"}

    if cmd == "/provider":
        if not args:
            cur = c.get("text_model", DEFAULT_TEXT_MODEL)
            lines = [f"щас *{cur}*", ""] + [f"{'👉' if mk==cur else '  '} `/provider {sn}`{' 👁' if TEXT_MODELS[mk].vision else ''}" for sn,mk in PROV_MAP.items()] + ["", "_👁=vision_"]
            await send(cid, "\n".join(lines)); return {"status": "ok"}
        pn = args.split()[0].lower()
        if pn not in PROV_MAP: await send(cid, f"нет: `{'`, `'.join(PROV_MAP)}`"); return {"status": "ok"}
        c["text_model"] = PROV_MAP[pn]; await save_chat(cid); await send(cid, f"го *{pn}*")
        return {"status": "ok"}

    if cmd == "/mood":
        ma = args.split()[0].lower() if args else ""
        if ma in MOODS:
            c["mood"] = ma; await save_chat(cid)
            await send(cid, {"chill":"на чилле","agro":"завали ебало щас злой","nerd":"мозги по полной","senior":"режим деда"}[ma])
        else: await send(cid, "выбирай: `chill agro nerd senior`")
        return {"status": "ok"}

    if cmd == "/reset":
        c["history"] = []; await save_chat(cid); await send(cid, "забыл всё")
        return {"status": "ok"}

    if cmd == "/status":
        lines = [
            f"текст: *{c.get('text_model',DEFAULT_TEXT_MODEL)}*",
            f"картинки: *{c.get('image_model',DEFAULT_IMAGE_MODEL)}*",
            f"настрой: *{c.get('mood','chill')}*",
            f"стиль: *{s.get('style','хам')}*",
            f"мат: {'✅' if s.get('allow_swear') else '❌'}",
            f"задач: *{len(c.get('tasks',[]))}*",
            f"бд: {'✅' if DB else '❌'}",
            "", "*провайдеры:*"
        ] + [f"{'✅' if not st.disabled else '❌'} `{p.value}`" for p,st in PROV_STATUS.items()]
        await send(cid, "\n".join(lines))
        return {"status": "ok"}

    if cmd in ("/creator", "/owner"):
        fr = "\n".join(f"🤝 @{k}" for k in FRIENDS)
        await send(cid, f"мой создатель: @{CREATOR_USERNAME}\n\nего кенты:\n{fr}")
        return {"status": "ok"}

    # === ЭКОНОМИКА ===
    if cmd in ("/wallet", "/balance", "/bal", "/кошелек"):
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if target_uid is None:
            target_uid, target_name = uid, uname
        if target_uid:
            w = get_wallet(cid, target_uid, target_name or "чел")
            sp = get_spouse_id(cid, target_uid)
            sp_name = ""
            if sp:
                m = is_married(cid, target_uid)
                sp_name = m["u2_name"] if m["u1"] == target_uid else m["u1_name"]
            text = (f"💼 *кошелёк {w['name']}*\n\n"
                    f"🪙 коинов: *{w['coins']}*\n"
                    f"💎 брилликов: *{w['diamonds']}*\n"
                    f"🍕 еды: *{w['food']}*\n"
                    f"📋 квестов: *{w['quests_done']}*\n"
                    f"🔥 стрик: *{w['farm_streak']}*")
            if sp_name: text += f"\n💍 в браке с *{sp_name}*"
            await send(cid, text)
        else: await send(cid, "не нашёл юзера")
        return {"status": "ok"}

    if cmd in ("/farm", "/ферма"):
        _, txt = await farm(cid, uid, uname); await send(cid, txt); return {"status": "ok"}

    if cmd in ("/quest", "/квест"):
        _, txt = await quest(cid, uid, uname); await send(cid, txt); return {"status": "ok"}

    if cmd in ("/daily", "/дейли"):
        _, txt = await daily(cid, uid, uname); await send(cid, txt); return {"status": "ok"}

    if cmd in ("/dice", "/кубики"):
        try: bet = int(args.split()[0]) if args else 50
        except: await send(cid, "`/dice 100` — ставка"); return {"status": "ok"}
        _, txt = await dice_game(cid, uid, bet); await send(cid, txt); return {"status": "ok"}

    if cmd in ("/top", "/лидерборд"):
        wallets = WALLETS.get(cid, {})
        if not wallets: await send(cid, "пока нет данных"); return {"status": "ok"}
        sorted_w = sorted(wallets.items(), key=lambda x: x[1]["coins"], reverse=True)[:10]
        lines = ["🏆 *ТОП БОГАЧЕЙ*\n"]
        for i, (u_id, w) in enumerate(sorted_w, 1):
            medal = ["🥇","🥈","🥉"][i-1] if i <= 3 else f"{i}."
            lines.append(f"{medal} *{w['name']}* — `{w['coins']}` 🪙")
        await send(cid, "\n".join(lines))
        return {"status": "ok"}

    # === БРАКИ ===
    if cmd in ("/brak", "/marry", "/брак"):
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if not target_uid:
            await send(cid, "укажи кому:\n`/brak @username` или reply"); return {"status": "ok"}
        await send(cid, propose(cid, uid, uname, target_uid, target_name))
        return {"status": "ok"}

    if cmd in ("/yes", "/да", "/согласна", "/согласен"):
        _, txt = await accept_proposal(cid, uid, uname); await send(cid, txt); return {"status": "ok"}

    if cmd in ("/no", "/нет", "/отказ"):
        await send(cid, reject_proposal(cid, uid, uname)); return {"status": "ok"}

    if cmd in ("/divorce", "/развод"):
        await send(cid, await divorce(cid, uid, uname)); return {"status": "ok"}

    if cmd in ("/marriages", "/браки"):
        txt = all_marriages(cid); await send(cid, txt or "пока никто не женат"); return {"status": "ok"}

    if cmd in ("/gift", "/подарок"):
        if not args:
            await send(cid, "🎁 *подарки супругу:*\n\n"
                "`/gift food` — 🍕 (30 🪙) +5 любви\n"
                "`/gift flowers` — 💐 (50 🪙) +10 любви\n"
                "`/gift diamond` — 💎 (1 💎) +25 любви\n"
                "`/gift ring` — 💍 (200 🪙) +20 любви\n"
                "`/gift car` — 🚗 (1000 🪙) +50 любви")
            return {"status": "ok"}
        await send(cid, await gift_to_spouse(cid, uid, uname, args.split()[0].lower()))
        return {"status": "ok"}

    if cmd in ("/sharefood", "/поделиться"):
        await send(cid, await share_food(cid, uid, uname)); return {"status": "ok"}

    if cmd in ("/surprise", "/сюрприз"):
        await send(cid, await surprise(cid, uid, uname)); return {"status": "ok"}

    # === ФАН (с поддержкой @username) ===
    if cmd == "/roast":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if not target_name:
            await send(cid, "укажи кого: `/roast @username` или reply"); return {"status": "ok"}
        # Защита создателя/друзей
        tu = {"id": target_uid, "username": ""}
        if target_uid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == target_uid:
                    tu["username"] = un; break
        if is_creator(tu) or is_friend(tu):
            await send(cid, f"🔥 *{target_name}*:\nне буду жарить ты свой норм чел"); return {"status": "ok"}
        pr = PROFILES.get(cid, {}).get(target_uid, {}) if target_uid else {}
        ms = "\n".join(pr.get("messages", [])[-10:]) if pr else "нет данных"
        await typing(cid)
        r = await ai.text([
            {"role":"system","content":f"{random.choice(ROAST_PROMPTS)} 2-3 строчки маленькими без точек используй *жирный* для подколов"},
            {"role":"user","content":f"{target_name}:\n{ms}"}
        ], pref="primary")
        await send(cid, f"🔥 *{target_name}*:\n\n{fmt(r)}")
        return {"status": "ok"}

    if cmd == "/ship":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if not target_name:
            await send(cid, "укажи кого: `/ship @username` или reply"); return {"status": "ok"}
        n1, n2 = uname, target_name
        cp = random.randint(0, 100)
        sn = (n1[:max(1,len(n1)//2)] + n2[len(n2)//2:]).lower()
        bar = "❤️"*(cp//10) + "🤍"*(10-cp//10)
        await send(cid, f"💘 *{n1}* + *{n2}* = `{sn}`\n\n*{cp}%*\n{bar}\n\n{random.choice(SHIP_REACTIONS)}")
        return {"status": "ok"}

    if cmd in ("/8ball", "/ball", "/шар"):
        if not args: await send(cid, "`/8ball вопрос`"); return {"status": "ok"}
        await send(cid, f"🎱 {args}\n\n*{random.choice(BALL_ANSWERS)}*")
        return {"status": "ok"}

    if cmd in ("/random", "/rand"):
        try:
            p = args.split() if args else ["100"]
            n = random.randint(1, int(p[0])) if len(p)==1 else random.randint(int(p[0]), int(p[1]))
            await send(cid, f"🎲 *{n}*")
        except: await send(cid, "`/random 100` или `/random 1 50`")
        return {"status": "ok"}

    if cmd in ("/coin", "/монетка"):
        await send(cid, f"🪙 *{random.choice(['орёл 🦅','решка'])}*"); return {"status": "ok"}

    if cmd in ("/choose", "/выбери"):
        if not args or "," not in args: await send(cid, "`/choose а, б, в`"); return {"status": "ok"}
        await send(cid, f"выбираю: *{random.choice([o.strip() for o in args.split(',') if o.strip()])}* 👈")
        return {"status": "ok"}

    if cmd == "/iq":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if target_uid is None and not args and not msg.get("reply_to_message"):
            target_uid, target_name = uid, uname
        tu = {"id": target_uid, "username": ""}
        if target_uid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == target_uid:
                    tu["username"] = un; break
        tn = target_name or uname
        if is_creator(tu): iq = random.randint(150, 200); cm = "норм мозги у создателя"
        elif is_friend(tu): iq = random.randint(130, 180); cm = "умный чел"
        else:
            iq = random.randint(20, 200)
            if iq < 50: cm = "амёба"
            elif iq < 80: cm = "такое"
            elif iq < 100: cm = "средне"
            elif iq < 130: cm = "норм"
            elif iq < 170: cm = "умник бля"
            else: cm = "ИИНШТЕЙН"
        await send(cid, f"🧠 *{tn}*: `{iq}`\n\n_{cm}_")
        return {"status": "ok"}

    if cmd == "/vibe":
        v = random.choice(["🌈 имба","💀 трэш","🔥 огонь","😴 скучно","🎉 пати","🌧 депрессия","⚡ электрика","🍕 жрать хочу"])
        await send(cid, f"вайб чата: *{v}*\nсила: `{random.randint(50,100)}%`")
        return {"status": "ok"}

    if cmd in ("/gay", "/гей"):
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if target_uid is None and not args and not msg.get("reply_to_message"):
            target_uid, target_name = uid, uname
        tu = {"id": target_uid, "username": ""}
        if target_uid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == target_uid: tu["username"] = un; break
        tn = target_name or uname
        if is_creator(tu): p = random.randint(0, 15); cm = "норм"
        elif is_friend(tu): p = random.randint(0, 20); cm = "ок"
        else:
            p = random.randint(0, 100)
            cm = "ну ок" if p < 50 else "пиздец" if p > 90 else "норм"
        await send(cid, f"🌈 *{tn}*\n\n*{p}%*\n{'🏳️‍🌈'*(p//10)}{'⬛'*(10-p//10)}\n\n_{cm}_")
        return {"status": "ok"}

    if cmd in ("/compliment", "/комплимент"):
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if not target_name: target_name = uname
        await send(cid, f"для *{target_name}*: {random.choice(COMPLIMENTS)}")
        return {"status": "ok"}

    if cmd == "/fact":
        await typing(cid)
        f = await ai.text([
            {"role":"system","content":"придумай прикольный факт из IT/гейминга/науки 2-3 строчки маленькими без точек используй *жирный* для важного"},
            {"role":"user","content":"факт"}
        ], pref="primary")
        await send(cid, f"💡 *факт дня:*\n\n{fmt(f)}")
        return {"status": "ok"}

    if cmd in ("/quote", "/цитата"):
        await typing(cid)
        q = await ai.text([
            {"role":"system","content":"дерзкая цитата про код/жизнь 1-2 строчки без точек"},
            {"role":"user","content":"цитату"}
        ], pref="primary")
        await send(cid, f"💬 «_{fmt(q)}_»\n\n— *OrienAI* 😎")
        return {"status": "ok"}

    if cmd == "/help":
        await send(cid, """⚡ *OrienAI v5.0*

💬 *общение:*
`/provider /mood /settings /reset /status`

🎨 *картинки:*
`/img /me /imgmodel /getava`

🎬 *ютуб:*
`/yt /ytdl`

💻 *код:*
`/analyze /task`

👥 *юзеры:*
`/profile /mute /creator`

💰 *ЭКОНОМИКА:*
`/wallet` — кошелёк (можно @user)
`/farm` — ферма (1ч)
`/quest` — квест (30мин)
`/daily` — ежедневка
`/dice 100` — казино
`/top` — лидерборд

💍 *БРАКИ:*
`/brak @user` — предложение
`/yes /no` — ответ
`/divorce` — развод
`/marriages` — все браки
`/gift food/flowers/diamond/ring/car`
`/sharefood /surprise`

🎮 *ФАН:*
`/roast /ship /8ball /random /coin`
`/choose /iq /vibe /gay /compliment`
`/fact /quote`

🖼 кидай картинки — я вижу 👁
✍ просто пиши — отвечу""")
        return {"status": "ok"}

    if cmd == "/start":
        await send(cid, f"оо здарова *{uname.lower()}* я *orienai v5* пиши `/help`")
        return {"status": "ok"}

    if cmd is not None: return {"status": "ok"}

    if should_respond(msg, s):
        await typing(cid)
        img = await extract_img(msg)
        at = await ai_response(cid, uname, text, img, creator_flag, friend_flag)
        await send(cid, at)

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "alive", "version": "5.0", "db": "connected" if DB else "off"}

@app.get("/health")
async def health():
    return {"ok": True, "db": DB is not None}
