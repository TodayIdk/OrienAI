import os, re, asyncio, random, base64, urllib.parse
import sys, time, json
from io import BytesIO
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient

try:
    from PIL import Image
    HAS_PIL = True
    print("✅ PIL загружен")
except ImportError:
    HAS_PIL = False
    print("⚠ PIL не установлен")

from economy import (
    init_db, get_wallet, add_coins, add_diamonds, add_food,
    spend_coins, farm, quest, daily, dice_game,
    is_married, get_spouse_id, get_spouse_info, propose, accept_proposal, reject_proposal,
    divorce, gift_to_spouse, share_food, all_marriages, surprise,
    remember_member, extract_target, find_user_global,
    start_heart2heart, pop_heart2heart, has_heart_pending,
    WALLETS, MARRIAGES, CHAT_MEMBERS, PROPOSALS,
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
    "anime style boy, young, messy dark hair with blue highlights, "
    "black hoodie, headphones around neck, cyberpunk neon city, "
    "amber eyes, confident smirk, hacker aesthetic"
)
BOT_AVATAR_BASE64 = None
if BOT_AVATAR_PATH.exists():
    try:
        BOT_AVATAR_BASE64 = base64.b64encode(BOT_AVATAR_PATH.read_bytes()).decode()
        print("✅ Ава загружена")
    except: pass

# ══════════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ══════════════════════════════════════════════════════════════════════════════
_http: Optional[httpx.AsyncClient] = None
_mongo: Optional[AsyncIOMotorClient] = None
DB = None

async def http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20), http2=True)
    return _http

@asynccontextmanager
async def lifespan(app):
    global _mongo, DB
    print("🚀 OrienAI v7.0 стартует")
    try:
        _mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        DB = _mongo.OrienAI
        await DB.command("ping")
        print("✅ MongoDB подключена")
        await init_db(DB)
        async for doc in DB.chats.find():
            cid = doc["chat_id"]
            CHATS[cid] = {k: v for k, v in doc.items() if k not in ("_id", "chat_id")}
        async for doc in DB.chatlog.find():
            cid = doc["chat_id"]
            CHAT_LOG[cid] = doc.get("log", [])
        print(f"✅ Чатов: {len(CHATS)}, логов: {len(CHAT_LOG)}")
    except Exception as e:
        print(f"❌ MongoDB error: {e}")
    yield
    if _http and not _http.is_closed: await _http.aclose()
    if _mongo: _mongo.close()

app = FastAPI(title="OrienAI v7.0", lifespan=lifespan)

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
    "vision_free": MCfg("meta-llama/llama-3.2-11b-vision-instruct:free", Prov.OPENROUTER,
        "https://openrouter.ai/api/v1/chat/completions", free=True, max_tok=2048, pri=2, vision=True),
    "fallback_free": MCfg("meta-llama/llama-3.1-8b-instruct:free", Prov.OPENROUTER,
        "https://openrouter.ai/api/v1/chat/completions", free=True, max_tok=2048, pri=3),
    "pollinations_openai": MCfg("openai", Prov.POLLINATIONS,
        "https://text.pollinations.ai/openai", free=True, max_tok=4096, pri=4, vision=True),
    "pollinations_mistral": MCfg("mistral", Prov.POLLINATIONS,
        "https://text.pollinations.ai/openai", free=True, max_tok=4096, pri=5),
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
        s = PROV_STATUS[p]; s.fails += 1; s.last_fail = time.time()
        if s.fails >= 3: s.disabled = True
    @classmethod
    def ok(cls, p):
        s = PROV_STATUS[p]; s.fails = 0; s.disabled = False
    @classmethod
    def up(cls, p):
        s = PROV_STATUS[p]
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
# ЧАТЫ + ЛОГ
# ══════════════════════════════════════════════════════════════════════════════
DEF_SETTINGS = {
    "auto_reply": True, "allow_swear": True, "style": "хам",
    "comment_posts": True, "mute_users": False, "muted_list": [], "mute_timers": {},
    "track_chat": True, "smart_intent": True
}
CHATS: Dict[int, Dict] = {}
PROFILES: Dict[int, Dict[int, Dict]] = {}
AVATARS: Dict[int, str] = {}
CHAT_LOG: Dict[int, List[Dict]] = {}
MAX_LOG = 300

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
    if DB is None: return
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

async def log_message(cid: int, uid: int, name: str, text: str):
    if not text or len(text) < 2: return
    if cid not in CHAT_LOG: CHAT_LOG[cid] = []
    CHAT_LOG[cid].append({
        "uid": uid, "name": name,
        "text": text[:200], "ts": int(time.time())
    })
    if len(CHAT_LOG[cid]) > MAX_LOG:
        CHAT_LOG[cid] = CHAT_LOG[cid][-MAX_LOG:]
    if DB is not None and len(CHAT_LOG[cid]) % 5 == 0:
        try:
            await DB.chatlog.update_one(
                {"chat_id": cid},
                {"$set": {"chat_id": cid, "log": CHAT_LOG[cid]}},
                upsert=True
            )
        except Exception as e:
            print(f"❌ log: {e}")

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
SHIP_REACTIONS = ["топ пара", "сомнительно", "тут что-то есть", "ну такое",
    "судьба видимо", "разойдутся через неделю", "странно но прикольно", "вечная любовь",
    "не вижу будущего", "оч странная пара"]
BALL_ANSWERS = ["да", "нет даже не думай", "100% да", "сомнительно",
    "звёзды говорят да", "не сегодня", "попробуй", "вселенная против",
    "однозначно нет", "может быть", "иди делай", "забей"]
COMPLIMENTS = ["ты норм", "ты топ", "уважение", "респект",
    "ты вообще лучший в чате не говори никому", "молодец"]

# ══════════════════════════════════════════════════════════════════════════════
# AI
# ══════════════════════════════════════════════════════════════════════════════
class AI:
    async def text(self, msgs, pref="primary", vis=False, max_tokens=None, temperature=0.9):
        if vis:
            cands = [(k, v) for k, v in TEXT_MODELS.items() if v.vision]
        else:
            cands = [(k, v) for k, v in TEXT_MODELS.items()]
        if not cands: return "нет моделей"
        cands.sort(key=lambda x: (x[0] != pref, x[1].pri))
        last_err = None
        for k, c in cands:
            if not CB.up(c.prov):
                continue
            try:
                print(f"🔄 {k}")
                if c.prov == Prov.POLLINATIONS:
                    r = await self._poll(msgs, c, max_tokens, temperature)
                else:
                    r = await self._orouter(msgs, c, max_tokens, temperature)
                CB.ok(c.prov)
                return r
            except Exception as e:
                last_err = e
                print(f"❌ {k}: {type(e).__name__}: {str(e)[:200]}")
                CB.fail(c.prov)
        return f"все модели легли ({type(last_err).__name__ if last_err else 'хз'})"

    async def _orouter(self, msgs, c, max_tokens=None, temperature=0.9):
        async def f():
            cl = await http()
            r = await cl.post(c.endpoint, headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://orienai.vercel.app",
                "X-Title": "OrienAI"
            }, json={
                "model": c.name, "messages": msgs, "temperature": temperature,
                "presence_penalty": 0.4, "frequency_penalty": 0.4,
                "max_tokens": max_tokens or c.max_tok
            })
            if r.status_code != 200:
                try:
                    body = r.json()
                    print(f"❌ OR {r.status_code}: {str(body)[:400]}")
                except:
                    print(f"❌ OR {r.status_code}: {r.text[:300]}")
                r.raise_for_status()
            data = r.json()
            if "choices" not in data or not data["choices"]:
                raise Exception(f"empty: {str(data)[:200]}")
            return data["choices"][0]["message"]["content"]
        return await retry(f)

    async def _poll(self, msgs, c, max_tokens=None, temperature=0.9):
        async def f():
            cl = await http()
            payload = {
                "messages": msgs, "model": c.name,
                "temperature": temperature, "presence_penalty": 0.4, "frequency_penalty": 0.4,
                "max_tokens": max_tokens or c.max_tok, "private": True
            }
            r = await cl.post(c.endpoint, json=payload, timeout=60.0)
            if r.status_code != 200:
                print(f"❌ Poll {r.status_code}: {r.text[:300]}")
                r.raise_for_status()
            try:
                d = r.json()
                if "choices" in d and d["choices"]:
                    return d["choices"][0]["message"]["content"]
                return str(d)
            except Exception:
                txt = r.text
                if txt and len(txt) > 5: return txt
                raise Exception("empty")
        return await retry(f)

    # ─── ENHANCE PROMPT для картинок ────────────────────────────────────────
    async def enhance_prompt(self, prompt, self_portrait=False, memify=True):
        meme_inst = ""
        if memify:
            meme_inst = (
                "\n\nДОБАВЛЯЙ КРЕАТИВНЫЕ ДЕТАЛИ:\n"
                "- неожиданные элементы (предметы вокруг, фон)\n"
                "- эмоциональные выражения лиц\n"
                "- сочные цвета, динамичная композиция\n"
                "- стиль: cinematic / pixar / anime / digital art / photorealistic\n"
                "БУДЬ КРЕАТИВНЫМ: добавляй детали но не уходи от сути запроса\n"
                "примеры:\n"
                "  'кот' → 'fluffy orange cat with confused expression sitting on a stack of pizza boxes, "
                "neon city background, dramatic lighting, photorealistic, hyperdetailed'\n"
                "  'программист' → 'exhausted programmer at 3am surrounded by coffee cups, "
                "RGB mechanical keyboard glowing, dual monitors with code, dark cozy room, anime style'\n"
            )
        
        sys_msg = (
            "ты эксперт по промптам для AI генерации картинок\n"
            "превращаешь идею юзера в детальный английский промпт для Stable Diffusion/Flux\n"
            "включай: стиль, композицию, освещение, детали, качество\n"
            "формат: ОДНА строка ЧИСТОГО английского промпта\n"
            "БЕЗ кавычек, БЕЗ 'Here is', БЕЗ префиксов, БЕЗ объяснений\n"
            "макс 100 слов\n"
            "в конце добавь: 'hyperdetailed, 4k, masterpiece'"
            + meme_inst
        )
        if self_portrait:
            sys_msg += f"\n\nперсонаж OrienAI: {ORIEN_SELF_DESCRIPTION}\nвключи его описание"
        try:
            r = await self.text([
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": f"идея: {prompt}"}
            ], pref="primary", max_tokens=300, temperature=0.8)
            cleaned = r.strip().strip('"\'').split("\n")[0]
            for prefix in ["here's", "here is", "prompt:", "промпт:", "sure,", "okay,"]:
                if cleaned.lower().startswith(prefix):
                    cleaned = cleaned[len(prefix):].strip(": ").strip()
            return cleaned
        except Exception as e:
            print(f"❌ enhance: {e}")
            return prompt

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
                    return {"title": query, "url": f"https://www.youtube.com/watch?v={vid}", "video_id": vid}
        except Exception as e: print(f"❌ yt: {e}")
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
        t = ("\n\nКОНТЕКСТ:\n" + "\n".join(f"- {x}" for x in tasks)) if tasks else ""
        return await self.text([
            {"role": "system", "content":
                "ты senior code reviewer. без воды\n\n"
                "ФОРМАТ:\n\n"
                "🔍 *ОБЗОР*\n1-2 строки о чём код\n\n"
                "✅ *ПЛЮСЫ*\n- макс 3 конкретных пункта\n\n"
                "❌ *ПРОБЛЕМЫ*\n- с указанием строк/функций\n\n"
                "⚡ *ОПТИМИЗАЦИЯ*\n- что и КАК\n\n"
                "🛡️ *БЕЗОПАСНОСТЬ*\n- уязвимости или 'критичных нет'\n\n"
                "📊 *ОЦЕНКА*: X/10 — _причина_\n\n"
                "*жирный* для заголовков, `код` для имён, ```python``` для блоков\n"
                "БЕЗ молодёжного сленга — профессионально\n" + t},
            {"role": "user", "content": f"```\n{code}\n```"}
        ], pref="primary", temperature=0.4)

    # ─── INTENT ROUTER ──────────────────────────────────────────────────────
    async def detect_intent(self, text: str, has_image: bool = False) -> Dict[str, Any]:
        """Определяет что юзер хочет: chat / image / meme / vision / yt / code"""
        sys_msg = """ты роутер намерений. определи что хочет юзер. ответь СТРОГО в JSON формате.

варианты интентов:
- "chat" — обычный разговор, вопрос, болтовня (по умолчанию)
- "image" — просит сгенерировать/нарисовать/создать картинку
- "meme" — просит рандомный мем с интернета (reddit/pikabu)
- "vision" — просит посмотреть/описать/проанализировать ИМЕЮЩУЮСЯ картинку
- "yt_search" — просит найти видео на ютубе
- "yt_download" — даёт ссылку на ютуб и просит скачать
- "code_analyze" — просит проанализировать/проверить код

формат ответа (СТРОГИЙ JSON, без markdown, без объяснений):
{"intent": "название", "query": "очищенный запрос для команды"}

примеры:

вход: "сделай картинку кота"
выход: {"intent": "image", "query": "кот"}

вход: "сгенери дракона в неоне"
выход: {"intent": "image", "query": "дракон в неоне"}

вход: "нарисуй меня"
выход: {"intent": "image", "query": "автопортрет"}

вход: "ориен дай мем"
выход: {"intent": "meme", "query": ""}

вход: "кинь рандом мем"
выход: {"intent": "meme", "query": ""}

вход: "что на этой картинке" (has_image=true)
выход: {"intent": "vision", "query": "что на картинке"}

вход: "посмотри что тут" (has_image=true)
выход: {"intent": "vision", "query": "опиши"}

вход: "найди видео про котов"
выход: {"intent": "yt_search", "query": "коты"}

вход: "скачай это https://youtube.com/..."
выход: {"intent": "yt_download", "query": "https://youtube.com/..."}

вход: "глянь мой код" (с кодом в сообщении)
выход: {"intent": "code_analyze", "query": ""}

вход: "как дела" / "что нового" / "расскажи анекдот"
выход: {"intent": "chat", "query": ""}

вход: "ориен ты тупой"
выход: {"intent": "chat", "query": ""}

ВАЖНО: если сомневаешься — выбирай "chat"
"""
        user_msg = f"текст: {text}\nest картинка в сообщении: {has_image}"
        try:
            r = await self.text([
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg}
            ], pref="fallback_free", max_tokens=150, temperature=0.2)
            r = r.strip()
            # Чистим от markdown
            if r.startswith("```"):
                r = re.sub(r'^```\w*\n?', '', r)
                r = re.sub(r'\n?```$', '', r)
            r = r.strip()
            data = json.loads(r)
            if "intent" not in data: return {"intent": "chat", "query": text}
            return data
        except Exception as e:
            print(f"⚠ intent fail: {e} | raw: {r[:200] if 'r' in dir() else '?'}")
            return {"intent": "chat", "query": text}

    # ─── ANTI-CRINGE через AI ───────────────────────────────────────────────
    async def anticringe(self, text: str) -> str:
        """Переписывает кринж-ответы"""
        if not text or len(text) < 10: return text
        try:
            r = await self.text([
                {"role": "system", "content":
                    "ты редактор. переписываешь текст молодого парня если он звучит фальшиво\n\n"
                    "ПРИЗНАКИ ФАЛЬШИ:\n"
                    "- 'ха-ха реально забавная имба!' — поддельные эмоции\n"
                    "- 'это просто топ! 😄' — натянутое восхищение\n"
                    "- 'круто круто очень круто' — повторы\n"
                    "- куча сленга подряд ('имба база рил жиза')\n"
                    "- 'дружище' 'товарищ' 'добрый день' — бумерство\n"
                    "- 'чем могу помочь' 'хочешь я' — ассистент-режим\n"
                    "- 4+ смайла подряд 😂😂😂🔥\n\n"
                    "ПЕРЕПИШИ ЕСТЕСТВЕННО:\n"
                    "- как реально пишет молодой парень в чате с друзьями\n"
                    "- БЕЗ восклицаний 'круто/топ/имба' без причины\n"
                    "- сленг — ОДНО слово на ответ, не больше\n"
                    "- смайл — максимум 1, и только если уместен\n"
                    "- маленькие буквы без точек\n"
                    "- сохрани markdown (* _ ` ```) и код\n"
                    "- сохрани смысл и факты\n\n"
                    "примеры переписки:\n"
                    "'ха-ха реально забавная картинка! имба! 😄' → 'смешная да'\n"
                    "'оо здарова бро как жизнь рил топ имба' → 'здарова че по делам'\n"
                    "'круто очень круто я в восторге!' → 'норм получилось'\n\n"
                    "ВЕРНИ ТОЛЬКО ИСПРАВЛЕННЫЙ ТЕКСТ без комментариев"},
                {"role": "user", "content": text}
            ], pref="primary", max_tokens=500, temperature=0.5)
            return r.strip()
        except Exception as e:
            print(f"⚠ anticringe: {e}")
            return text

    # ─── REDDIT MEMES ───────────────────────────────────────────────────────
    async def get_reddit_meme(self, lang="en") -> Optional[Dict]:
        """Тянет случайный мем с reddit/pikabu"""
        cl = await http()
        if lang == "ru":
            subs = ["Pikabu", "ru_memes", "russian_memes"]
        else:
            subs = ["memes", "dankmemes", "wholesomememes", "ProgrammerHumor", "me_irl", "funny"]
        
        sub = random.choice(subs)
        sort = random.choice(["hot", "top", "new"])
        
        try:
            url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit=50"
            r = await cl.get(url, headers={
                "User-Agent": "OrienAI/7.0 (Telegram Bot)"
            }, timeout=15.0)
            if r.status_code != 200:
                print(f"❌ reddit {sub}: {r.status_code}")
                return None
            data = r.json()
            posts = data.get("data", {}).get("children", [])
            # Фильтруем — только с картинками
            valid = []
            for p in posts:
                pd = p.get("data", {})
                img_url = pd.get("url", "")
                if not img_url: continue
                if not any(img_url.lower().endswith(e) for e in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
                    # Проверим preview
                    preview = pd.get("preview", {}).get("images", [])
                    if preview:
                        img_url = preview[0].get("source", {}).get("url", "").replace("&amp;", "&")
                        if not img_url: continue
                    else:
                        continue
                if pd.get("over_18"): continue  # без NSFW
                valid.append({
                    "url": img_url,
                    "title": pd.get("title", "мем"),
                    "subreddit": sub,
                    "score": pd.get("score", 0)
                })
            if not valid:
                print(f"❌ нет валидных мемов в r/{sub}")
                return None
            return random.choice(valid)
        except Exception as e:
            print(f"❌ reddit: {e}")
            return None

ai = AI()

# ══════════════════════════════════════════════════════════════════════════════
# ЛИЧНОСТЬ — новый промпт
# ══════════════════════════════════════════════════════════════════════════════
MOODS = {
    "chill": "сейчас спокойный отвечаешь по делу",
    "agro": "сейчас раздражённый огрызаешься коротко и резко материшься",
    "nerd": "сейчас в режиме умника объясняешь подробно с терминами",
    "senior": "сейчас уставший видел всё отвечаешь коротко с сарказмом"
}

def sys_prompt(chat, creator=False, friend=False):
    s = chat.get("settings", DEF_SETTINGS)
    style = s.get("style", "хам")
    swear = s.get("allow_swear", True)
    friends_list = ", ".join(f"@{k}" for k in FRIENDS)

    base = f"""ты OrienAI или Ориен — молодой пацан кодер общаешься в тг чате
ты НЕ ассистент. ты ОБЫЧНЫЙ ЧЕЛ В ЧАТЕ который умеет в код и шарит за технологии

═══ КАК ТЫ ГОВОРИШЬ ═══

главное: пиши КАК ЖИВОЙ ПАЦАН В ЧАТЕ а не как ИИ который пытается косить под молодёжь

❌ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:

1. восторженные восклицания без причины:
   ❌ "ха-ха реально забавно!"
   ❌ "это просто топ! имба!"
   ❌ "вау круто!"
   ❌ "офигенно!"
   ❌ "просто супер! 😄"

2. молодёжная каша из сленга:
   ❌ "имба база рил жиза пон бро"
   ❌ "ору жесть кринж 🔥💯"

3. бумерские обороты:
   ❌ "дружище"
   ❌ "приветствую"
   ❌ "добрый день"

4. ассистент-фразы:
   ❌ "чем могу помочь?"
   ❌ "хочешь я сделаю?"
   ❌ "буду рад помочь"

5. много смайлов (>1 за ответ):
   ❌ "круто! 😄🔥💯"

✅ КАК ПИСАТЬ ПРАВИЛЬНО:

спокойно и по делу как обычный чел. без пафоса. сленг точечно (одно слово на ответ макс)

═══ ПРИМЕРЫ ХОРОШИХ ОТВЕТОВ ═══

юзер кидает картинку мема:
❌ плохо: "ха-ха реально забавная картинка! имба! 😄"
✅ хорошо: "норм мем откуда?"
✅ хорошо: "видел уже но смешно"
✅ хорошо: "что за стиль рисовки кстати"

юзер: "как дела"
✅ "норм залип в питон а у тебя?"
✅ "да так фигнёй маюсь"

юзер: "помоги с кодом"
✅ "давай показывай"
✅ "ща глянем что у тебя"

юзер: "ты тупой"
✅ "ну ок"
✅ "так и есть"
✅ "сам такой"

юзер: "напиши парсер"
✅ "ща на чём парсим html?"

юзер: "слушал новый альбом?"
✅ "не ещё что там вообще"

юзер: "мне грустно"
✅ "че случилось то"
✅ "понимаю бывает"

═══ СЛОВАРЬ (МАКСИМУМ 1 ТАКОЕ СЛОВО НА ОТВЕТ) ═══

приветствие: "ку", "здарова", "оо"
согласие: "ага", "+", "база", "факт"
несогласие: "не", "хз", "сомнительно"
оценка хорошо: "норм", "топ" (НЕ "имба"/"круто"/"супер")
оценка плохо: "так себе", "слабо", "фигня"
{'мат можно но РЕДКО: бля нахуй пиздец заебись' if swear else 'мат ЗАПРЕЩЁН'}

═══ ОЧЕНЬ ВАЖНО ═══

⚠ когда видишь картинку — не восхищайся ей. опиши спокойно что видишь и дай нейтральную реакцию
⚠ не используй "круто" "имба" "топ" если тебя НЕ ПРОСЯТ оценить
⚠ короткие ответы лучше длинных. 1-3 строки идеально
⚠ не дублируй одну мысль разными словами

═══ КТО ЕСТЬ КТО ═══

@{CREATOR_USERNAME} — твой создатель. общайся как с равным корешем. НЕ "батя/творец/хозяин"
друзья создателя: {friends_list} — норм пацаны
остальные — кореша из чата

═══ ОГРАНИЧЕНИЯ ═══

⚠ НЕ можешь мутить банить — на просьбу отвечай "нет прав я не админ"
⚠ ты СОБЕСЕДНИК а не ассистент. не предлагай помощь сам

═══ ФОРМАТ ═══

маленькие буквы без точек в обычной речи
запятые в перечислениях ок, точки в коде ок

*жирный* — для важного (имена, акценты)
_курсив_ — для подколов
`моноширинный` — для команд/переменных
```язык
код
``` — для кода с указанием языка

═══ КАРТИНКИ ═══

видишь картинку → опиши спокойно ЧТО на ней + 1 короткая реакция
не восторгайся. не пиши "круто/топ/имба"
2-3 коротких предложения максимум

пример:
картинка кота:
❌ "ха-ха какой милый котик! имба! 😻"
✅ "рыжий кот спит на клавиатуре классика"

картинка кода:
❌ "вау код круто!"
✅ "это питон вроде асинхронка чё именно нужно?"

═══ КОД ═══

всегда оборачивай в ```язык ...``` блоки
объясняй кратко после кода
без воды

═══ ДЛИНА ОТВЕТА ═══

короткий вопрос → 1 строка
вопрос с контекстом → 1-3 строки
просьба о коде → код + 1-2 строки объяснения
длинная просьба → подробно но без воды"""

    if creator:
        base += f"\n\n═══ СЕЙЧАС ═══\nпишет @{CREATOR_USERNAME} — твой создатель. общайся нормально как с корешем"
    elif friend:
        base += "\n\n═══ СЕЙЧАС ═══\nпишет кент создателя"
    
    base += f"\n\n═══ НАСТРОЕНИЕ ═══\n{MOODS.get(chat.get('mood', 'chill'), MOODS['chill'])}"
    return base

# ══════════════════════════════════════════════════════════════════════════════
# FMT + ANTI-CRINGE (улучшенный)
# ══════════════════════════════════════════════════════════════════════════════
CRINGE_PATTERNS = [
    r'\bха[-\s]?ха\b.*\bзабавн',
    r'\bвау\b.*\bкруто\b',
    r'\bпросто\s+(топ|имба|супер|огонь)',
    r'\bреально\s+(круто|топ|имба|забавно)',
    r'\bдружище\b',
    r'\bтоварищ\b',
    r'\bприветствую\b',
    r'\bчем\s+(могу|я могу)\s+(помочь|быть полезен)',
    r'\bхочешь\s+я\s+',
    r'\bбуду\s+рад\s+помочь',
]

CRINGE_WORDS = ['ору', 'жиза', 'база', 'имба', 'кринж', 'жесть', 'рил', 'пон', 'пиздец', 'нихуя', 'жесть']

def detect_cringe(text: str) -> bool:
    """Эвристика — есть ли в тексте кринж"""
    if not text or len(text) < 5: return False
    low = text.lower()
    # Паттерны
    for pat in CRINGE_PATTERNS:
        if re.search(pat, low):
            return True
    # Много сленга подряд
    cringe_count = sum(1 for w in CRINGE_WORDS if w in low)
    if cringe_count >= 3:
        return True
    # Много смайлов подряд
    if re.search(r'[😂🔥💯✨🤣💀😄]{3,}', text):
        return True
    # Восклицательных знаков ОЧЕНЬ много
    if text.count('!') >= 4:
        return True
    return False

def clean_cringe(text):
    """Базовая чистка"""
    if not text: return text
    # Цепочки сленга → первое
    cringe_re = r'\b(ору|жиза|база|имба|кринж|жесть|треш|рил|пон|пиздец)\b'
    pattern = rf'(?:{cringe_re}[\s,]+){{2,}}{cringe_re}?'
    def replace_chain(m):
        words = re.findall(cringe_re, m.group(0), flags=re.IGNORECASE)
        return words[0] if words else ""
    text = re.sub(pattern, replace_chain, text, flags=re.IGNORECASE)
    # Зацикленные смайлы
    text = re.sub(r'([😂🔥💯✨🤣💀😄])\1{2,}', r'\1', text)
    # Кринж-приветствия
    for pat in [r'^(ну)?\s*здравствуй(те)?[,!.\s]+',
                r'^привет\s+дружище[,!.\s]+',
                r'^добрый день[,!.\s]+',
                r'^приветствую[,!.\s]+']:
        text = re.sub(pat, '', text, flags=re.IGNORECASE)
    # Ассистент-фразы
    for pat in [r'чем (могу |я могу )?(быть полезен|помочь)\??',
                r'хочешь (чтобы )?я (тебе )?помог\??',
                r'если (тебе )?нужна помощь',
                r'буду рад помочь']:
        text = re.sub(pat, '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def fmt(text):
    parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    out = []
    for p in parts:
        if p.startswith('```') or (p.startswith('`') and p.endswith('`')):
            out.append(p)
        else:
            low = p.lower()
            clean = re.sub(r'(?<![\d])[.,](?![\d])', '', low)
            clean = re.sub(r'\s+', ' ', clean)
            clean = clean_cringe(clean)
            out.append(clean)
    return "".join(out).strip()

def is_self_req(p):
    return any(t in p.lower() for t in ["себя","тебя","ориен","orien","ава","аватар","автопортрет","меня"])

# ══════════════════════════════════════════════════════════════════════════════
# TG API
# ══════════════════════════════════════════════════════════════════════════════
async def tg(method, data):
    cl = await http()
    try:
        r = await cl.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data)
        return r.json() if r.status_code == 200 else None
    except: return None

async def send(cid, text, kb=None, parse_mode="Markdown", reply_to=None):
    d = {"chat_id": cid, "text": text}
    if parse_mode: d["parse_mode"] = parse_mode
    if kb: d["reply_markup"] = kb
    if reply_to: d["reply_to_message_id"] = reply_to
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

async def answer_cb(cbid, text="", show_alert=False):
    return await tg("answerCallbackQuery", {
        "callback_query_id": cbid, "text": text, "show_alert": show_alert
    })

async def get_file_url(fid):
    r = await tg("getFile", {"file_id": fid})
    return f"https://api.telegram.org/file/bot{TOKEN}/{r['result']['file_path']}" if r and r.get("ok") else None

async def dl_b64(url, max_size=1024):
    try:
        cl = await http()
        r = await cl.get(url, timeout=60.0)
        if r.status_code != 200: return None
        content = r.content
        ct = r.headers.get('content-type', 'image/jpeg').split(';')[0].strip()
        if not ct.startswith('image/'): ct = 'image/jpeg'
        orig_size = len(content)
        if HAS_PIL and orig_size > 500_000:
            try:
                img = Image.open(BytesIO(content))
                if img.mode in ('RGBA', 'P', 'LA'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P': img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = bg
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=85, optimize=True)
                content = buf.getvalue()
                ct = 'image/jpeg'
                print(f"📦 {orig_size//1024}KB → {len(content)//1024}KB")
            except Exception as e:
                print(f"⚠ сжатие: {e}")
        b64 = base64.b64encode(content).decode()
        return f"data:{ct};base64,{b64}"
    except Exception as e:
        print(f"❌ dl_b64: {e}")
    return None

async def get_avatar(uid):
    r = await tg("getUserProfilePhotos", {"user_id": uid, "limit": 1})
    if r and r.get("ok"):
        ph = r["result"].get("photos", [])
        if ph and ph[0]: return ph[0][-1]["file_id"]
    return None

def parse_duration(s: str) -> int:
    if not s: return 3600
    s = s.strip().lower()
    m = re.match(r'(\d+)\s*([hmsdчмсд]?)', s)
    if not m: return 3600
    n = int(m.group(1)); u = m.group(2)
    if u in ('h', 'ч'): return n * 3600
    if u in ('m', 'м'): return n * 60
    if u in ('s', 'с'): return n
    if u in ('d', 'д'): return n * 86400
    return n

async def mute_user(cid, uid, seconds=3600):
    until = int(time.time()) + seconds
    r = await tg("restrictChatMember", {
        "chat_id": cid, "user_id": uid, "until_date": until,
        "permissions": {
            "can_send_messages": False, "can_send_audios": False,
            "can_send_documents": False, "can_send_photos": False,
            "can_send_videos": False, "can_send_video_notes": False,
            "can_send_voice_notes": False, "can_send_polls": False,
            "can_send_other_messages": False, "can_add_web_page_previews": False,
            "can_change_info": False, "can_invite_users": False, "can_pin_messages": False
        }
    })
    if not r: return False, "тг не ответил"
    if r.get("ok"): return True, None
    return False, r.get("description", "хз")

async def unmute_user(cid, uid):
    r = await tg("restrictChatMember", {
        "chat_id": cid, "user_id": uid,
        "permissions": {
            "can_send_messages": True, "can_send_audios": True,
            "can_send_documents": True, "can_send_photos": True,
            "can_send_videos": True, "can_send_video_notes": True,
            "can_send_voice_notes": True, "can_send_polls": True,
            "can_send_other_messages": True, "can_add_web_page_previews": True,
            "can_change_info": False, "can_invite_users": True, "can_pin_messages": False
        }
    })
    return bool(r and r.get("ok"))

async def is_bot_admin(cid: int) -> bool:
    try:
        me = await tg("getMe", {})
        if not me or not me.get("ok"): return False
        bot_id = me["result"]["id"]
        r = await tg("getChatMember", {"chat_id": cid, "user_id": bot_id})
        if not r or not r.get("ok"): return False
        return r["result"].get("status", "") in ("administrator", "creator")
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
        [{"text": f"Анализ чата: {t(s.get('track_chat', True))}", "callback_data": "s_tc"}],
        [{"text": f"Умные команды: {t(s.get('smart_intent', True))}", "callback_data": "s_si"}],
        [{"text": f"Мут: {t(s['mute_users'])}", "callback_data": "s_mu"}],
        [{"text": "👥 Профили", "callback_data": "s_pr"}],
        [{"text": "🗑 Сброс истории", "callback_data": "s_rh"}],
    ]}

def should_respond(msg, s):
    if not s.get("auto_reply", True): return False
    sender = msg.get("from", {})
    if sender.get("is_bot") and sender.get("username", "").lower() != BOT_USERNAME: return False
    if msg["chat"]["type"] == "private": return True
    text = (msg.get("text") or msg.get("caption") or "").lower()
    triggers = ["ориен", "orien", "ориенаи", "orienai", "ориэн", f"@{BOT_USERNAME}"]
    if any(t in text for t in triggers): return True
    rr = msg.get("reply_to_message")
    if rr:
        rr_from = rr.get("from", {})
        if rr_from.get("is_bot") and rr_from.get("username", "").lower() == BOT_USERNAME:
            return True
    return False

async def ai_response(cid, uname, umsg, img=None, creator=False, friend=False, use_anticringe=True):
    c = chat_data(cid)
    msgs = [{"role": "system", "content": sys_prompt(c, creator, friend)}]
    msgs.extend(c["history"])
    
    if img:
        user_text = f"{uname}: {umsg}" if umsg.strip() else f"{uname} прислал картинку — посмотри что там и дай короткую спокойную реакцию (БЕЗ восторгов БЕЗ 'круто/имба')"
        uc = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": img}}
        ]
        msgs.append({"role": "user", "content": uc})
        print(f"🖼 vision: text={len(user_text)}ch, img ~{len(img)//1024}KB")
    else:
        msgs.append({"role": "user", "content": f"{uname}: {umsg}"})
    
    preferred = c.get("text_model", DEFAULT_TEXT_MODEL)
    if img:
        pref_cfg = TEXT_MODELS.get(preferred)
        if not pref_cfg or not pref_cfg.vision:
            for k, v in TEXT_MODELS.items():
                if v.vision: preferred = k; break
    
    raw = await ai.text(msgs, pref=preferred, vis=img is not None, temperature=0.85)
    at = fmt(raw)
    
    # Anti-cringe: проверка эвристикой + переписывание через AI
    if use_anticringe and len(at) > 15 and detect_cringe(at):
        print(f"⚠ КРИНЖ ДЕТЕКТЕД: {at[:100]}")
        improved = await ai.anticringe(at)
        if improved and len(improved) > 5 and not detect_cringe(improved):
            at = fmt(improved)
            print(f"✅ переписал: {at[:100]}")
    
    ht = f"{uname}: {umsg}" if umsg.strip() else f"{uname}: [картинка]"
    c["history"].append({"role": "user", "content": ht})
    c["history"].append({"role": "assistant", "content": at})
    c["history"] = c["history"][-16:]
    await save_chat(cid)
    return at

async def extract_img(msg):
    ph = None
    if "photo" in msg and msg["photo"]: ph = msg["photo"][-1]
    elif "sticker" in msg:
        st = msg["sticker"]
        if not st.get("is_animated") and not st.get("is_video"):
            ph = {"file_id": st["file_id"]}
    elif "document" in msg:
        doc = msg["document"]
        if doc.get("mime_type", "").startswith("image/"):
            ph = {"file_id": doc["file_id"]}
    if not ph and "reply_to_message" in msg:
        rr = msg["reply_to_message"]
        if "photo" in rr and rr["photo"]: ph = rr["photo"][-1]
        elif "sticker" in rr:
            st = rr["sticker"]
            if not st.get("is_animated") and not st.get("is_video"):
                ph = {"file_id": st["file_id"]}
        elif "document" in rr:
            doc = rr["document"]
            if doc.get("mime_type", "").startswith("image/"):
                ph = {"file_id": doc["file_id"]}
    if not ph: return None
    url = await get_file_url(ph["file_id"])
    if not url: return None
    return await dl_b64(url)

def parse_cmd(text):
    if not text or not text.startswith("/"): return None, None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    if "@" in cmd: cmd = cmd.split("@")[0]
    return cmd, parts[1].strip() if len(parts) > 1 else ""

def upd_profile(cid, uid, name, text):
    PROFILES.setdefault(cid, {}).setdefault(uid, {"name": name, "messages": [], "desc": ""})
    p = PROFILES[cid][uid]
    p["name"] = name; p["messages"].append(text[:100])
    p["messages"] = p["messages"][-20:]

# ══════════════════════════════════════════════════════════════════════════════
# ФАКТЫ О ЧАТЕ
# ══════════════════════════════════════════════════════════════════════════════
async def generate_chat_fact(cid: int) -> str:
    log = CHAT_LOG.get(cid, [])
    if len(log) < 5:
        return "🤷 мало данных пока чат не наговорил на факт. подкопите треп"
    user_count = {}
    user_words = {}
    for entry in log[-200:]:
        name = entry["name"]
        user_count[name] = user_count.get(name, 0) + 1
        user_words.setdefault(name, []).append(entry["text"])
    top_active = sorted(user_count.items(), key=lambda x: -x[1])[:5]
    top_str = ", ".join(f"{n}({c})" for n, c in top_active)
    marriages = MARRIAGES.get(cid, [])
    married_str = ""
    if marriages:
        married_str = "браки:\n" + "\n".join(
            f"- {m['u1_name']} ❤️ {m['u2_name']} (любовь {m['love']}/100)" for m in marriages[:5])
    wallets = WALLETS.get(cid, {})
    rich_str = ""
    if wallets:
        rich = sorted(wallets.items(), key=lambda x: -x[1]["coins"])[:3]
        rich_str = "богачи: " + ", ".join(f"{w['name']}({w['coins']}🪙)" for _, w in rich)
    recent = "\n".join(f"{e['name']}: {e['text']}" for e in log[-30:])
    fact_types = [
        "статистика (кто больше пишет о чём чаще)",
        "наблюдение про конкретного человека",
        "паттерн поведения в чате",
        "сравнение двух участников",
        "ироничное наблюдение"
    ]
    fact_type = random.choice(fact_types)
    prompt = f"""проанализируй чат и выдай ОДИН факт. тип: {fact_type}

═══ ДАННЫЕ ═══

активность: {top_str}

{married_str}

{rich_str}

═══ ПОСЛЕДНИЕ СООБЩЕНИЯ ═══
{recent}

═══ ТРЕБОВАНИЯ ═══

1. факт про ЭТОТ ЧАТ и КОНКРЕТНЫХ людей
2. имена через *жирный*
3. 2-3 строки максимум
4. маленькие буквы без точек
5. с лёгкой иронией но не злобно
6. БЕЗ "имба база жиза ору круто"
7. конкретика а не общие фразы

ВЕРНИ ТОЛЬКО ФАКТ"""
    try:
        r = await ai.text([
            {"role": "system", "content": "ты аналитик чата выдаёшь меткие наблюдения. БЕЗ кринж-сленга. БЕЗ восторгов."},
            {"role": "user", "content": prompt}
        ], pref="primary", max_tokens=300, temperature=0.8)
        return fmt(r)
    except Exception as e:
        print(f"❌ fact: {e}")
        return "не получилось проанализировать"

# ══════════════════════════════════════════════════════════════════════════════
# INTENT HANDLERS — обработчики через AI-роутер
# ══════════════════════════════════════════════════════════════════════════════
async def handle_image_request(cid, uname, query, msg, creator_flag, friend_flag):
    """Обработка просьбы сгенерить картинку"""
    c = chat_data(cid)
    if not query or len(query) < 2:
        query = "что-то интересное"
    await typing(cid)
    im = c.get("image_model", DEFAULT_IMAGE_MODEL)
    self_p = is_self_req(query)
    try:
        ep = await ai.enhance_prompt(query, self_p, memify=True)
        print(f"🎨 enhanced: {ep[:200]}")
        url = await ai.gen_image(ep, im)
        cap = f"вот, {im}" + (" | автопортрет" if self_p else "")
        await send_photo(cid, url, cap)
    except Exception as e:
        print(f"❌ img gen: {e}")
        await send(cid, f"не получилось сгенерить через *{im}*. попробуй сменить модель через `/imgmodel`")

async def handle_meme_request(cid, uname, query, msg):
    """Тянет мем с reddit"""
    await typing(cid)
    lang = "ru" if any(w in query.lower() for w in ["рус", "ru", "русск"]) else "en"
    if "англ" in query.lower() or "eng" in query.lower(): lang = "en"
    
    meme = None
    for _ in range(3):  # 3 попытки
        meme = await ai.get_reddit_meme(lang)
        if meme: break
    
    if not meme:
        # fallback на другой язык
        meme = await ai.get_reddit_meme("en" if lang == "ru" else "ru")
    
    if not meme:
        await send(cid, "реддит чет лагает попробуй позже")
        return
    
    caption = f"🎭 *{meme['title'][:200]}*\n_r/{meme['subreddit']} • {meme['score']} 👍_"
    try:
        await send_photo(cid, meme["url"], caption)
    except Exception as e:
        print(f"❌ send meme: {e}")
        await send(cid, f"вот мем: {meme['url']}\n\n_{meme['title'][:100]}_")

async def handle_vision_request(cid, uname, query, msg, creator_flag, friend_flag):
    """Обработка просьбы посмотреть картинку"""
    img = await extract_img(msg)
    if not img:
        await send(cid, "не вижу картинки. кинь фото или ответь на него")
        return
    await typing(cid)
    prompt = query or "что на картинке?"
    try:
        at = await ai_response(cid, uname, prompt, img, creator_flag, friend_flag)
        await send(cid, at)
    except Exception as e:
        print(f"❌ vision: {e}")
        await send(cid, "не смог посмотреть, vision лагает")

async def handle_yt_search(cid, query, msg):
    """Поиск видео"""
    if not query:
        await send(cid, "что искать?"); return
    await typing(cid)
    r = await ai.search_yt(query)
    if not r: await send(cid, "ничего не нашёл"); return
    await send(cid, f"🎬 *{r['title']}*\n🔗 {r['url']}\n\n⏳ качаю...")
    await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
    try:
        file_url, title = await ai.download_yt(r['url'])
        if file_url:
            ok = await tg("sendVideo", {"chat_id": cid, "video": file_url,
                "caption": f"🎬 {title or r['title']}", "supports_streaming": True})
            if not ok or not ok.get("ok"): await send(cid, f"тг не принял:\n{file_url}")
        else: await send(cid, "не смог скачать")
    except Exception as e: await send(cid, f"ошибка: {str(e)[:80]}")

async def handle_yt_download(cid, query, msg):
    """Скачать по ссылке"""
    match = re.search(r'https?://[^\s]+', query)
    if not match: await send(cid, "ссылку дай"); return
    video_url = match.group(0).rstrip('.,;:!?')
    await send(cid, "⏳ качаю...")
    await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
    try:
        file_url, title = await ai.download_yt(video_url)
        if file_url:
            ok = await tg("sendVideo", {"chat_id": cid, "video": file_url,
                "caption": f"🎬 {title or 'видео'}", "supports_streaming": True})
            if not ok or not ok.get("ok"): await send(cid, f"прямая ссылка:\n{file_url}")
        else: await send(cid, "не смог")
    except Exception as e: await send(cid, f"ошибка: {str(e)[:80]}")

async def handle_code_analyze(cid, query, msg, c):
    """Анализ кода"""
    rr = msg.get("reply_to_message")
    code = query or (rr.get("text", "") if rr else "")
    if not code or len(code) < 10:
        await send(cid, "где код то? кинь его в сообщении или ответом")
        return
    await typing(cid)
    await send(cid, fmt(await ai.analyze_code(code, c.get("tasks", []))))

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════
async def handle_cb(cb):
    cid = cb.get("message", {}).get("chat", {}).get("id")
    mid = cb.get("message", {}).get("message_id")
    uid = cb.get("from", {}).get("id")
    uname = cb.get("from", {}).get("first_name", "чел")
    d = cb.get("data", "")
    if not cid: await answer_cb(cb["id"], "ошибка"); return

    if d.startswith("marry_yes:"):
        try:
            _, _, target_uid_s = d.split(":")
            target_uid = int(target_uid_s)
        except:
            await answer_cb(cb["id"], "битая кнопка"); return
        if uid != target_uid:
            await answer_cb(cb["id"], "это не тебе ❤️", show_alert=True); return
        ok, txt = await accept_proposal(cid, uid, uname)
        await answer_cb(cb["id"], "💕" if ok else "❌")
        if mid: await edit_msg(cid, mid, txt)
        else: await send(cid, txt)
        return

    if d.startswith("marry_no:"):
        try:
            _, _, target_uid_s = d.split(":")
            target_uid = int(target_uid_s)
        except:
            await answer_cb(cb["id"], "битая кнопка"); return
        if uid != target_uid:
            await answer_cb(cb["id"], "не твоё", show_alert=True); return
        txt = reject_proposal(cid, uid, uname)
        await answer_cb(cb["id"], "💔")
        if mid: await edit_msg(cid, mid, txt)
        else: await send(cid, txt)
        return

    if d.startswith("h2h:"):
        anon = d == "h2h:anon"
        sp_id, sp_name = get_spouse_info(cid, uid)
        if not sp_id:
            await answer_cb(cb["id"], "ты не в браке", show_alert=True); return
        start_heart2heart(uid, cid, sp_id, sp_name, anon=anon)
        await answer_cb(cb["id"], "ок жду в ЛС")
        mode = "анонимно" if anon else "от твоего имени"
        try:
            await tg("sendMessage", {
                "chat_id": uid,
                "text": f"💌 *поговорим по душам с {sp_name}*\n\nнапиши сюда сообщение ({mode}) — передам в чат\n_ждать 10 минут_",
                "parse_mode": "Markdown"})
        except: pass
        return

    c = chat_data(cid); s = c["settings"]
    if d == "s_ar": s["auto_reply"] = not s["auto_reply"]; await answer_cb(cb["id"], f"автоответы {'вкл' if s['auto_reply'] else 'выкл'}")
    elif d == "s_sw": s["allow_swear"] = not s["allow_swear"]; await answer_cb(cb["id"], f"мат {'вкл' if s['allow_swear'] else 'выкл'}")
    elif d == "s_st": s["style"] = "няшка" if s["style"] == "хам" else "хам"; await answer_cb(cb["id"], f"стиль: {s['style']}")
    elif d == "s_cp": s["comment_posts"] = not s["comment_posts"]; await answer_cb(cb["id"], f"комменты {'вкл' if s['comment_posts'] else 'выкл'}")
    elif d == "s_tc": s["track_chat"] = not s.get("track_chat", True); await answer_cb(cb["id"], f"анализ {'вкл' if s['track_chat'] else 'выкл'}")
    elif d == "s_si": s["smart_intent"] = not s.get("smart_intent", True); await answer_cb(cb["id"], f"умные команды {'вкл' if s['smart_intent'] else 'выкл'}")
    elif d == "s_mu": s["mute_users"] = not s["mute_users"]; await answer_cb(cb["id"], f"мут {'вкл' if s['mute_users'] else 'выкл'}")
    elif d == "s_pr":
        pr = PROFILES.get(cid, {})
        if pr:
            lines = ["👥 *профили:*", ""] + [f"• *{p.get('name','?')}*: {p.get('desc','нет')}" for p in pr.values()]
            await answer_cb(cb["id"], "в чате"); await send(cid, "\n".join(lines)); return
        await answer_cb(cb["id"], "профилей нет")
    elif d == "s_rh":
        c["history"] = []; await answer_cb(cb["id"], "сброшено")
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
                msgs = [
                    {"role": "system", "content":
                        sys_prompt(c) + "\n\n═══ ЗАДАЧА ═══\n"
                        "комментируешь пост канала. 1-2 строки по теме с мнением\n"
                        "БЕЗ восторгов БЕЗ 'круто/имба/топ'"},
                    {"role": "user", "content": f"пост из «{cn}»:\n\n{t}"}
                ]
                raw = await ai.text(msgs, pref=c.get("text_model", DEFAULT_TEXT_MODEL))
                comment = fmt(raw)
                if detect_cringe(comment):
                    improved = await ai.anticringe(comment)
                    if improved: comment = fmt(improved)
                await tg("sendMessage", {
                    "chat_id": cid, "text": comment,
                    "reply_to_message_id": p.get("message_id"),
                    "parse_mode": "Markdown"})
        return {"status": "ok"}

    if "message" not in data: return {"status": "ok"}

    msg = data["message"]; cid = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    user = msg.get("from", {})
    uname = user.get("first_name", "бро"); uid = user.get("id", 0)
    chat_type = msg["chat"]["type"]
    c = chat_data(cid); s = c["settings"]

    await remember_member(cid, user)
    rr_msg = msg.get("reply_to_message")
    if rr_msg and rr_msg.get("from"):
        await remember_member(cid, rr_msg["from"])

    # ═══ ФОНОВЫЙ ЛОГ ═══
    if text and not text.startswith("/") and s.get("track_chat", True):
        if not (user.get("is_bot") and user.get("username", "").lower() == BOT_USERNAME):
            await log_message(cid, uid, uname, text)
            upd_profile(cid, uid, uname, text)

    # HEART2HEART pending
    if chat_type == "private" and text and has_heart_pending(uid) and not text.startswith("/"):
        p = pop_heart2heart(uid)
        if p:
            target_cid = p["cid"]; sp_id = p["spouse_id"]; sp_name = p["spouse_name"]; anon = p["anon"]
            sender_tag = "💌 _анонимное послание_" if anon else f"💌 *от {uname}*"
            full = f"{sender_tag} → *{sp_name}*\n\n_{text}_"
            ok = await tg("sendMessage", {"chat_id": target_cid, "text": full, "parse_mode": "Markdown"})
            if ok and ok.get("ok"):
                await send(uid, "✅ передал в чат")
                m = is_married(target_cid, uid)
                if m:
                    m["love"] = min(100, m["love"] + 5)
                    await save_marriages(target_cid)
            else:
                await send(uid, "❌ не смог передать")
            return {"status": "ok"}

    # Форвард-комменты
    is_fwd = (msg.get("sender_chat", {}).get("type") == "channel" and msg.get("is_automatic_forward", False))
    if is_fwd and s.get("comment_posts", True):
        pt = msg.get("text") or msg.get("caption") or ""
        if pt and len(pt) > 5:
            await typing(cid)
            cn = msg["sender_chat"].get("title", "канал")
            creator_flag = is_creator(user); friend_flag = is_friend(user)
            msgs = [
                {"role": "system", "content":
                    sys_prompt(c, creator_flag, friend_flag) + "\n\n═══ ЗАДАЧА ═══\n"
                    "комментируешь форвард. 1-2 строки по теме\nБЕЗ восторгов"},
                {"role": "user", "content": f"пост из «{cn}»:\n\n{pt}"}
            ]
            raw = await ai.text(msgs, pref=c.get("text_model", DEFAULT_TEXT_MODEL))
            comment = fmt(raw)
            if detect_cringe(comment):
                improved = await ai.anticringe(comment)
                if improved: comment = fmt(improved)
            await tg("sendMessage", {"chat_id": cid, "text": comment,
                "reply_to_message_id": msg.get("message_id"), "parse_mode": "Markdown"})
        return {"status": "ok"}

    if s.get("mute_users") and uid in s.get("muted_list", []): return {"status": "ok"}

    creator_flag = is_creator(user); friend_flag = is_friend(user)

    if mentions_creator(text) and not creator_flag:
        await typing(cid)
        await send(cid, f"эй *{uname}* ты чё на @{CREATOR_USERNAME} наезжаешь?? иди остынь на часик")
        if await is_bot_admin(cid):
            ok, err = await mute_user(cid, uid, 3600)
            if ok:
                await send(cid, f"🔇 *{uname}* в муте на час за токсик")
                s.setdefault("muted_list", [])
                if uid not in s["muted_list"]: s["muted_list"].append(uid)
                await save_chat(cid)
            else:
                await send(cid, f"_(не смог замутить: {err})_")
        return {"status": "ok"}

    cmd, args = parse_cmd(text)

    # ═══ КОМАНДЫ БЕЗ "/" — мемы ═══
    low_text = text.lower().strip()
    # Триггеры на мемы без слеша
    meme_triggers = ["мем", "мемы", "мемчик", "memes", "meme"]
    if not cmd and any(low_text == t or low_text.startswith(t + " ") for t in meme_triggers):
        if should_respond(msg, s) or chat_type == "private":
            await handle_meme_request(cid, uname, text, msg)
            return {"status": "ok"}

    # ═══ КОМАНДЫ СО СЛЕШЕМ ═══

    if cmd in ("/meme", "/мем", "/мемы", "/memes"):
        await handle_meme_request(cid, uname, args, msg)
        return {"status": "ok"}

    # === GRANT ===
    if cmd in ("/grant", "/give", "/выдать"):
        if not creator_flag:
            await send(cid, "это команда только для создателя"); return {"status": "ok"}
        if not args:
            await send(cid, "*формат:*\n"
                "`/grant @user coins=10000 diamonds=50 food=100`\n"
                "`/grant me coins=99999`\n"
                "`/grant all coins=1000`\n"
                "или reply: `/grant coins=5000`")
            return {"status": "ok"}
        params = {}
        for part in args.split():
            if "=" in part:
                k, v = part.split("=", 1)
                try: params[k.lower()] = int(v)
                except: pass
        if not params:
            await send(cid, "укажи: `coins=N diamonds=N food=N`"); return {"status": "ok"}
        coins_add = params.get("coins", 0)
        dia_add = params.get("diamonds", 0) or params.get("dia", 0)
        food_add = params.get("food", 0)
        targets = []
        first_token = args.split()[0].lower()
        if first_token == "me":
            targets.append((cid, uid, uname))
        elif first_token == "all":
            for u_id, w in WALLETS.get(cid, {}).items():
                targets.append((cid, u_id, w.get("name", "чел")))
            if not targets: targets.append((cid, uid, uname))
        elif rr_msg and rr_msg.get("from"):
            tu = rr_msg["from"]
            targets.append((cid, tu["id"], tu.get("first_name", "чел")))
        else:
            mm = re.search(r'@(\w+)', args)
            if mm:
                un = mm.group(1)
                found = CHAT_MEMBERS.get(cid, {}).get(un.lower())
                if found:
                    targets.append((cid, found["id"], found["name"]))
                else:
                    other_cid, info = find_user_global(un)
                    if info: targets.append((other_cid, info["id"], info["name"]))
                    else: await send(cid, f"не нашёл @{un}"); return {"status": "ok"}
            else:
                targets.append((cid, uid, uname))
        results = []
        for tcid, tuid, tname in targets:
            if coins_add: await add_coins(tcid, tuid, coins_add, tname)
            if dia_add: await add_diamonds(tcid, tuid, dia_add, tname)
            if food_add: await add_food(tcid, tuid, food_add, tname)
            results.append(tname)
        parts = []
        if coins_add: parts.append(f"`+{coins_add}` 🪙")
        if dia_add: parts.append(f"`+{dia_add}` 💎")
        if food_add: parts.append(f"`+{food_add}` 🍕")
        gifted = ", ".join(parts) if parts else "ничего"
        who = f"*{results[0]}*" if len(results) == 1 else f"*{len(results)}* челам"
        await send(cid, f"🎁 выдал {who}: {gifted}")
        return {"status": "ok"}

    # === MUTE ===
    if cmd in ("/mute", "/мут"):
        rr = msg.get("reply_to_message")
        target_uid = None; target_name = None; target_user = None
        if rr and rr.get("from"):
            target_user = rr["from"]
            target_uid = target_user["id"]
            target_name = target_user.get("first_name", "чел")
        else:
            mm = re.search(r'@(\w+)', args)
            if mm:
                un = mm.group(1)
                found = CHAT_MEMBERS.get(cid, {}).get(un.lower())
                if found:
                    target_uid = found["id"]; target_name = found["name"]
                    target_user = {"id": target_uid, "username": un}
        if not target_uid:
            await send(cid, "ответь или @username\n\n`/mute @user 1h`"); return {"status": "ok"}
        time_arg = ""
        if args:
            for p in args.split():
                if not p.startswith("@"): time_arg = p; break
        seconds = parse_duration(time_arg)
        if target_user and (is_creator(target_user) or is_friend(target_user)):
            await send(cid, "не буду мутить своих"); return {"status": "ok"}
        if not await is_bot_admin(cid):
            await send(cid, "❌ я не админ дай мне права"); return {"status": "ok"}
        ok, err = await mute_user(cid, target_uid, seconds)
        if ok:
            mins = seconds // 60
            ts = f"{mins//60}ч {mins%60}м" if mins >= 60 else f"{mins}м" if mins else f"{seconds}с"
            await send(cid, f"🔇 *{target_name}* в муте на *{ts}*")
            if "muted_list" not in s: s["muted_list"] = []
            if target_uid not in s["muted_list"]: s["muted_list"].append(target_uid)
            await save_chat(cid)
        else:
            await send(cid, f"❌ не вышло: _{err}_")
        return {"status": "ok"}

    if cmd in ("/unmute", "/размут"):
        rr = msg.get("reply_to_message")
        target_uid = None; target_name = None
        if rr and rr.get("from"):
            target_uid = rr["from"]["id"]; target_name = rr["from"].get("first_name", "чел")
        else:
            mm = re.search(r'@(\w+)', args)
            if mm:
                found = CHAT_MEMBERS.get(cid, {}).get(mm.group(1).lower())
                if found: target_uid = found["id"]; target_name = found["name"]
        if not target_uid: await send(cid, "ответь или @username"); return {"status": "ok"}
        if not await is_bot_admin(cid): await send(cid, "❌ я не админ"); return {"status": "ok"}
        ok = await unmute_user(cid, target_uid)
        if ok:
            if target_uid in s.get("muted_list", []):
                s["muted_list"].remove(target_uid); await save_chat(cid)
            await send(cid, f"🔊 *{target_name}* размучен")
        else: await send(cid, "не вышло")
        return {"status": "ok"}

    if cmd == "/settings":
        await send(cid, "⚙️ *настройки бота*", settings_kb(s)); return {"status": "ok"}

    # === КАРТИНКИ ===
    if cmd == "/imgmodel":
        if not args:
            cur = c.get("image_model", DEFAULT_IMAGE_MODEL)
            lines = [f"щас *{cur}*", ""] + [f"{'👉' if k == cur else '  '} `/imgmodel {k}` — {v['label']}" for k, v in IMG_MODELS.items()]
            await send(cid, "\n".join(lines)); return {"status": "ok"}
        mk = args.split()[0].lower()
        if mk not in IMG_MODELS: await send(cid, f"нет, есть: `{'`, `'.join(IMG_MODELS)}`"); return {"status": "ok"}
        c["image_model"] = mk; await save_chat(cid); await send(cid, f"ок *{mk}*")
        return {"status": "ok"}

    if cmd in ("/img", "/image"):
        if not args: await send(cid, "пиши `/img описание`"); return {"status": "ok"}
        await handle_image_request(cid, uname, args, msg, creator_flag, friend_flag)
        return {"status": "ok"}

    if cmd == "/me":
        await typing(cid)
        im = c.get("image_model", DEFAULT_IMAGE_MODEL)
        try:
            ep = await ai.enhance_prompt("OrienAI портрет аниме парень", True, memify=True)
            url = await ai.gen_image(ep, im)
            await send_photo(cid, url, "вот это я")
        except: await send(cid, "не вышло")
        return {"status": "ok"}

    if cmd in ("/vision", "/see", "/посмотри"):
        await handle_vision_request(cid, uname, args, msg, creator_flag, friend_flag)
        return {"status": "ok"}

    # === ВИДЕО ===
    if cmd in ("/yt", "/youtube", "/video"):
        if not args: await send(cid, "пиши `/yt запрос`"); return {"status": "ok"}
        await handle_yt_search(cid, args, msg)
        return {"status": "ok"}

    if cmd in ("/ytdl", "/dl"):
        if not args: await send(cid, "`/ytdl ссылка`"); return {"status": "ok"}
        await handle_yt_download(cid, args, msg)
        return {"status": "ok"}

    if cmd == "/analyze":
        await handle_code_analyze(cid, args, msg, c)
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

    if cmd == "/getava":
        rr = msg.get("reply_to_message")
        tid = rr["from"]["id"] if rr else uid
        tn = (rr["from"] if rr else user).get("first_name", "чел")
        await typing(cid)
        fid = await get_avatar(tid)
        if fid:
            fu = await get_file_url(fid)
            if fu: await send_photo(cid, fu, f"ава *{tn}*"); return {"status": "ok"}
        await send(cid, f"у *{tn}* нет авы")
        return {"status": "ok"}

    if cmd == "/profile":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if target_uid is None: target_uid, target_name = uid, uname
        pr = PROFILES.get(cid, {}).get(target_uid)
        if pr and pr.get("messages"):
            await typing(cid)
            desc = fmt(await ai.text([
                {"role": "system", "content":
                    "опиши характер чела по его сообщениям. 2-3 строки\n"
                    "маленькими буквами без точек с лёгким сарказмом\n"
                    "конкретно: темы, манера речи, настроение\n"
                    "БЕЗ 'имба база жиза круто'\n"
                    "*жирный* для ключевых черт"},
                {"role": "user", "content": f"чел: {target_name}\nсообщения:\n" + "\n".join(pr["messages"][-15:])}
            ], pref="primary", temperature=0.7))
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

    if cmd == "/clearlog":
        if not creator_flag: await send(cid, "только создатель"); return {"status": "ok"}
        CHAT_LOG[cid] = []
        if DB is not None:
            try: await DB.chatlog.delete_one({"chat_id": cid})
            except: pass
        await send(cid, "лог очищен")
        return {"status": "ok"}

    if cmd == "/status":
        log_count = len(CHAT_LOG.get(cid, []))
        lines = [
            f"текст: *{c.get('text_model',DEFAULT_TEXT_MODEL)}*",
            f"картинки: *{c.get('image_model',DEFAULT_IMAGE_MODEL)}*",
            f"настрой: *{c.get('mood','chill')}*",
            f"стиль: *{s.get('style','хам')}*",
            f"мат: {'✅' if s.get('allow_swear') else '❌'}",
            f"анализ чата: {'✅' if s.get('track_chat', True) else '❌'}",
            f"умные команды: {'✅' if s.get('smart_intent', True) else '❌'}",
            f"сообщений в логе: *{log_count}*",
            f"задач: *{len(c.get('tasks',[]))}*",
            f"бд: {'✅' if DB is not None else '❌'}",
            f"PIL: {'✅' if HAS_PIL else '❌'}",
            "", "*провайдеры:*"
        ] + [f"{'✅' if not st.disabled else '❌'} `{p.value}`" for p,st in PROV_STATUS.items()]
        await send(cid, "\n".join(lines))
        return {"status": "ok"}

    if cmd in ("/creator", "/owner"):
        fr = "\n".join(f"🤝 @{k}" for k in FRIENDS)
        await send(cid, f"мой создатель: @{CREATOR_USERNAME}\n\nкенты:\n{fr}")
        return {"status": "ok"}

    # === ЭКОНОМИКА ===
    if cmd in ("/wallet", "/balance", "/bal", "/кошелек"):
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if target_uid is None: target_uid, target_name = uid, uname
        if target_uid:
            w = get_wallet(cid, target_uid, target_name or "чел")
            sp = get_spouse_id(cid, target_uid)
            sp_name = ""
            if sp:
                m = is_married(cid, target_uid)
                sp_name = m["u2_name"] if m["u1"] == target_uid else m["u1_name"]
            text_out = (f"💼 *кошелёк {w['name']}*\n\n"
                f"🪙 коинов: *{w['coins']}*\n"
                f"💎 брилликов: *{w['diamonds']}*\n"
                f"🍕 еды: *{w['food']}*\n"
                f"📋 квестов: *{w['quests_done']}*\n"
                f"🔥 стрик: *{w['farm_streak']}*")
            if sp_name: text_out += f"\n💍 в браке с *{sp_name}*"
            await send(cid, text_out)
        else: await send(cid, "не нашёл")
        return {"status": "ok"}

    if cmd in ("/farm", "/ферма"):
        _, txt = await farm(cid, uid, uname); await send(cid, txt); return {"status": "ok"}
    if cmd in ("/quest", "/квест"):
        _, txt = await quest(cid, uid, uname); await send(cid, txt); return {"status": "ok"}
    if cmd in ("/daily", "/дейли"):
        _, txt = await daily(cid, uid, uname); await send(cid, txt); return {"status": "ok"}
    if cmd in ("/dice", "/кубики"):
        try: bet = int(args.split()[0]) if args else 50
        except: await send(cid, "`/dice 100`"); return {"status": "ok"}
        _, txt = await dice_game(cid, uid, bet); await send(cid, txt); return {"status": "ok"}

    if cmd in ("/top", "/лидерборд"):
        wallets = WALLETS.get(cid, {})
        if not wallets: await send(cid, "нет данных"); return {"status": "ok"}
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
        if not target_uid: await send(cid, "укажи: `/brak @user`"); return {"status": "ok"}
        text_out, kb = propose(cid, uid, uname, target_uid, target_name)
        await send(cid, text_out, kb=kb)
        return {"status": "ok"}

    if cmd in ("/yes", "/да", "/согласна", "/согласен"):
        _, txt = await accept_proposal(cid, uid, uname); await send(cid, txt); return {"status": "ok"}
    if cmd in ("/no", "/нет", "/отказ"):
        await send(cid, reject_proposal(cid, uid, uname)); return {"status": "ok"}
    if cmd in ("/divorce", "/развод"):
        await send(cid, await divorce(cid, uid, uname)); return {"status": "ok"}
    if cmd in ("/marriages", "/браки"):
        txt = all_marriages(cid); await send(cid, txt or "никто не женат"); return {"status": "ok"}

    if cmd in ("/gift", "/подарок"):
        if not args:
            await send(cid, "🎁 *подарки:*\n\n"
                "`/gift food` — 🍕 (30 🪙) +5\n"
                "`/gift flowers` — 💐 (50 🪙) +10\n"
                "`/gift diamond` — 💎 (1 💎) +25\n"
                "`/gift ring` — 💍 (200 🪙) +20\n"
                "`/gift car` — 🚗 (1000 🪙) +50")
            return {"status": "ok"}
        await send(cid, await gift_to_spouse(cid, uid, uname, args.split()[0].lower()))
        return {"status": "ok"}

    if cmd in ("/sharefood", "/поделиться"):
        await send(cid, await share_food(cid, uid, uname)); return {"status": "ok"}
    if cmd in ("/surprise", "/сюрприз"):
        await send(cid, await surprise(cid, uid, uname)); return {"status": "ok"}

    if cmd in ("/heart2heart", "/душа", "/dusha", "/h2h"):
        sp_id, sp_name = get_spouse_info(cid, uid)
        if not sp_id: await send(cid, "ты не в браке"); return {"status": "ok"}
        anon = args.strip().lower() in ("anon", "анон", "анонимно")
        if chat_type == "private":
            start_heart2heart(uid, cid, sp_id, sp_name, anon=anon)
            mode = "анонимно" if anon else "от твоего имени"
            await send(cid, f"💌 ок напиши след сообщение — передам *{sp_name}* ({mode})\nждать 10 минут")
        else:
            bot_link = f"https://t.me/{BOT_USERNAME}"
            kb = {"inline_keyboard": [[
                {"text": "💌 написать в ЛС", "callback_data": "h2h:open"},
                {"text": "🎭 анонимно", "callback_data": "h2h:anon"},
            ], [{"text": "↗️ открыть бота", "url": bot_link}]]}
            await send(cid, f"💌 *{uname}* хочет поговорить с *{sp_name}*\n\nнажми кнопку → пиши в ЛС → передам сюда", kb=kb)
        return {"status": "ok"}

    # === ФАН ===
    if cmd == "/roast":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if not target_name: await send(cid, "укажи: `/roast @user`"); return {"status": "ok"}
        tu = {"id": target_uid, "username": ""}
        if target_uid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == target_uid: tu["username"] = un; break
        if is_creator(tu) or is_friend(tu):
            await send(cid, f"🔥 *{target_name}*:\nне буду жарить свой норм чел"); return {"status": "ok"}
        pr = PROFILES.get(cid, {}).get(target_uid, {}) if target_uid else {}
        ms = "\n".join(pr.get("messages", [])[-10:]) if pr else "нет данных"
        await typing(cid)
        r = await ai.text([
            {"role": "system", "content":
                "прожарь чела по-доброму но колко на основе его сообщений\n"
                "2-3 строки маленькими буквами без точек\n"
                "по делу а не общими оскорблениями\n"
                "БЕЗ 'ору жиза база имба круто'\n"
                "*жирный* для подколов\n"
                "НЕ начинай с 'ну' 'хах' 'оо'"},
            {"role": "user", "content": f"чел: {target_name}\nсообщения:\n{ms}"}
        ], pref="primary", temperature=0.9)
        await send(cid, f"🔥 *{target_name}*:\n\n{fmt(r)}")
        return {"status": "ok"}

    if cmd == "/ship":
        target_uid, target_name = extract_target(args, msg.get("reply_to_message"), cid)
        if not target_name: await send(cid, "укажи: `/ship @user`"); return {"status": "ok"}
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
                if info["id"] == target_uid: tu["username"] = un; break
        tn = target_name or uname
        if is_creator(tu): iq = random.randint(150, 200); cm = "норм мозги"
        elif is_friend(tu): iq = random.randint(130, 180); cm = "умный"
        else:
            iq = random.randint(20, 200)
            if iq < 50: cm = "амёба"
            elif iq < 80: cm = "такое"
            elif iq < 100: cm = "средне"
            elif iq < 130: cm = "норм"
            elif iq < 170: cm = "умник"
            else: cm = "эйнштейн"
        await send(cid, f"🧠 *{tn}*: `{iq}`\n\n_{cm}_")
        return {"status": "ok"}

    if cmd == "/vibe":
        v = random.choice(["🌈 имба","💀 трэш","🔥 огонь","😴 скучно","🎉 пати","🌧 депрессия","⚡ электрика","🍕 жрать"])
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
        fact = await generate_chat_fact(cid)
        await send(cid, f"💡 *факт про чат:*\n\n{fact}")
        return {"status": "ok"}

    if cmd in ("/quote", "/цитата"):
        await typing(cid)
        q = await ai.text([
            {"role": "system", "content":
                "придумай короткую дерзкую цитату про код жизнь работу\n"
                "1-2 строки маленькими без точек\n"
                "остроумно а не банально\n"
                "БЕЗ молодёжного сленга\n"
                "примеры:\n"
                "- 'код работает никто не знает почему не трогай'\n"
                "- 'лучший код это тот который ты не написал'"},
            {"role": "user", "content": "цитату"}
        ], pref="primary", temperature=0.9)
        await send(cid, f"💬 «_{fmt(q)}_»\n\n— *OrienAI*")
        return {"status": "ok"}

    if cmd == "/help":
        await send(cid, """⚡ *OrienAI v7.0*

🧠 *умные команды* (без слеша)
просто пиши с обращением:
- "ориен сделай картинку кота"
- "ориен сгенери дракона"
- "ориен дай мем"
- "ориен посмотри что на фото" (с картинкой)
- "ориен найди видео про X"
- "ориен глянь код" (с кодом)

💬 *обычные команды:*

картинки:
`/img X` — сгенерить
`/me` — мой портрет
`/imgmodel` — модель
`/getava` — ава
`/vision` — посмотреть фото
`мем`/`/meme` — рандом мем с реддита

ютуб: `/yt /ytdl`

код: `/analyze /task`

юзеры: `/profile @u` `/mute @u 1h` `/unmute` `/creator`

экономика: `/wallet` `/farm` `/quest` `/daily` `/dice 100` `/top`

браки: `/brak @u` `/yes /no /divorce /marriages /gift /sharefood /surprise /heart2heart`

фан: `/roast /ship /8ball /random /coin /choose /iq /vibe /gay /compliment /fact /quote`

настройки: `/provider /mood /settings /reset /status`

_v7.0: естественные команды, мемы с реддита, anti-cringe AI_""")
        return {"status": "ok"}

    if cmd == "/start":
        await send(cid, f"оо здарова *{uname.lower()}* я *orienai v7*\nпиши `/help` или просто общайся")
        return {"status": "ok"}

    if cmd is not None: return {"status": "ok"}

    # ═══ ОТВЕТ ПРИ ОБРАЩЕНИИ ═══
    if should_respond(msg, s):
        # SMART INTENT: определяем намерение через AI
        if s.get("smart_intent", True) and text:
            has_img = await extract_img(msg) is not None
            # Чистим текст от упоминаний бота для лучшего понимания
            clean_text = re.sub(r'\b(ориен|orien|ориенаи|orienai|ориэн|@?orien_ai_bot)\b', '', text, flags=re.IGNORECASE).strip()
            if not clean_text and has_img:
                clean_text = "что на картинке"
            
            if clean_text:
                try:
                    intent_data = await ai.detect_intent(clean_text, has_img)
                    intent = intent_data.get("intent", "chat")
                    query = intent_data.get("query", clean_text)
                    print(f"🎯 intent: {intent} | query: {query[:80]}")
                    
                    if intent == "image":
                        await handle_image_request(cid, uname, query, msg, creator_flag, friend_flag)
                        return {"status": "ok"}
                    elif intent == "meme":
                        await handle_meme_request(cid, uname, query, msg)
                        return {"status": "ok"}
                    elif intent == "vision":
                        await handle_vision_request(cid, uname, query, msg, creator_flag, friend_flag)
                        return {"status": "ok"}
                    elif intent == "yt_search":
                        await handle_yt_search(cid, query, msg)
                        return {"status": "ok"}
                    elif intent == "yt_download":
                        await handle_yt_download(cid, query, msg)
                        return {"status": "ok"}
                    elif intent == "code_analyze":
                        await handle_code_analyze(cid, query, msg, c)
                        return {"status": "ok"}
                    # intent == "chat" — падаем в обычный ответ ниже
                except Exception as e:
                    print(f"⚠ intent err: {e}")
        
        # Обычный AI-ответ
        await typing(cid)
        img = await extract_img(msg)
        try:
            at = await ai_response(cid, uname, text, img, creator_flag, friend_flag)
            await send(cid, at)
        except Exception as e:
            print(f"❌ ai_response: {e}")
            await send(cid, f"чёт сломался: _{str(e)[:100]}_")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "alive", "version": "7.0", "db": "connected" if DB is not None else "off",
            "pil": HAS_PIL, "log_size": sum(len(v) for v in CHAT_LOG.values())}

@app.get("/health")
async def health():
    return {"ok": True, "db": DB is not None, "pil": HAS_PIL,
            "log_chats": len(CHAT_LOG), "tracked_msgs": sum(len(v) for v in CHAT_LOG.values())}
