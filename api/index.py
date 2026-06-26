import os, re, asyncio, random, base64, urllib.parse, sys, time, json
from io import BytesIO
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from motor.motor_asyncio import AsyncIOMotorClient

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from economy import (
    init_db, get_wallet, add_coins, add_diamonds, add_food,
    spend_coins, farm, quest, daily, dice_game,
    is_married, get_spouse_id, get_spouse_info, propose, accept_proposal, reject_proposal,
    divorce, gift_to_spouse, share_food, all_marriages, surprise,
    remember_member, extract_target, find_user_global,
    start_heart2heart, pop_heart2heart, has_heart_pending,
    WALLETS, MARRIAGES, CHAT_MEMBERS, save_wallet, save_marriages, save_members
)


TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Today_Idk:TpdauT434odayTodayToday23@cluster0.rlgkop5.mongodb.net/OrienAI?retryWrites=true&w=majority&appName=Cluster0")
DEFAULT_TEXT_MODEL = os.getenv("DEFAULT_TEXT_MODEL", "primary")
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
BOT_USERNAME = os.getenv("BOT_USERNAME", "orien_ai_bot").lower()
CREATOR_USERNAME = "idkxazei"
CREATOR_USER_IDS = []
FRIENDS = {"tosterok1488": "тостер"}
ORIEN_DESC = ("anime style boy, young, messy dark hair with blue highlights, black hoodie, "
              "headphones around neck, cyberpunk neon city, amber eyes, confident smirk, hacker aesthetic")

# ══════════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ══════════════════════════════════════════════════════════════════════════════
_http: Optional[httpx.AsyncClient] = None
_mongo: Optional[AsyncIOMotorClient] = None
DB = None

async def http():
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20), http2=True)
    return _http

@asynccontextmanager
async def lifespan(app):
    global _mongo, DB
    print("🚀 OrienAI v7.3")
    try:
        _mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        DB = _mongo.OrienAI
        await DB.command("ping")
        print("✅ Mongo")
        await init_db(DB)
        async for doc in DB.chats.find():
            CHATS[doc["chat_id"]] = {k: v for k, v in doc.items() if k not in ("_id", "chat_id")}
        async for doc in DB.chatlog.find():
            CHAT_LOG[doc["chat_id"]] = doc.get("log", [])
        # Загрузка стикеров
        try:
            doc = await DB.bot_config.find_one({"key": "stickers"})
            if doc and doc.get("stickers"):
                STICKERS.update(doc["stickers"])
                print(f"✅ Стикеров: {len(STICKERS)}")
        except Exception as e:
            print(f"⚠ stickers load: {e}")
        print(f"✅ Чатов: {len(CHATS)}, логов: {len(CHAT_LOG)}")
    except Exception as e:
        print(f"❌ Mongo: {e}")
    yield
    if _http and not _http.is_closed: await _http.aclose()
    if _mongo: _mongo.close()

app = FastAPI(title="OrienAI v7.3", lifespan=lifespan)

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

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
POLL_URL = "https://text.pollinations.ai/openai"

TEXT_MODELS = {
    "primary": MCfg("openai/gpt-4o-mini", Prov.OPENROUTER, OR_URL, max_tok=4096, pri=1, vision=True),
    "vision_free": MCfg("meta-llama/llama-3.2-11b-vision-instruct:free", Prov.OPENROUTER, OR_URL, free=True, max_tok=2048, pri=2, vision=True),
    "fallback_free": MCfg("meta-llama/llama-3.1-8b-instruct:free", Prov.OPENROUTER, OR_URL, free=True, max_tok=2048, pri=3),
    "pollinations_openai": MCfg("openai", Prov.POLLINATIONS, POLL_URL, free=True, max_tok=4096, pri=4, vision=True),
    "pollinations_mistral": MCfg("mistral", Prov.POLLINATIONS, POLL_URL, free=True, max_tok=4096, pri=5),
}

IMG_MODELS = {
    "flux": "Flux", "nanobanana": "NanoBanana", "nanobanana-2": "NanoBanana 2",
    "nanobanana-pro": "NanoBanana Pro", "turbo": "Turbo", "kontext": "Kontext", "seedream": "Seedream",
}

PROV_MAP = {"openrouter": "primary", "openrouter_free": "fallback_free",
    "vision_free": "vision_free", "pollinations": "pollinations_openai",
    "pollinations_mistral": "pollinations_mistral"}

PROV_STATUS: Dict[Prov, PStatus] = {p: PStatus() for p in Prov}

class CB:
    @classmethod
    def fail(cls, p):
        s = PROV_STATUS[p]; s.fails += 1; s.last_fail = time.time()
        if s.fails >= 3: s.disabled = True
    @classmethod
    def ok(cls, p):
        PROV_STATUS[p].fails = 0; PROV_STATUS[p].disabled = False
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
# ЧАТЫ
# ══════════════════════════════════════════════════════════════════════════════
DEF_SETTINGS = {"auto_reply": True, "allow_swear": True, "style": "хам", "comment_posts": True,
    "mute_users": False, "muted_list": [], "track_chat": True, "smart_intent": True}
CHATS: Dict[int, Dict] = {}
PROFILES: Dict[int, Dict[int, Dict]] = {}
CHAT_LOG: Dict[int, List[Dict]] = {}
PROMPT_PENDING: Dict[int, Dict] = {}
MAX_LOG = 300

# СТИКЕРЫ
STICKERS: Dict[str, str] = {}
STICKER_PACK_URL = "https://t.me/addstickers/OrienAIstickers"
STICKER_PENDING: Dict[int, str] = {}
STICKER_ORDER = ["happy", "angry", "neutral", "sad"]

def chat_data(cid):
    if cid not in CHATS:
        CHATS[cid] = {"mood": "chill", "history": [], "text_model": DEFAULT_TEXT_MODEL,
            "image_model": DEFAULT_IMAGE_MODEL, "settings": dict(DEF_SETTINGS), "tasks": [],
            "custom_prompt": None}
    c = CHATS[cid]
    if "settings" not in c: c["settings"] = dict(DEF_SETTINGS)
    for k, v in DEF_SETTINGS.items():
        if k not in c["settings"]: c["settings"][k] = v
    c.setdefault("tasks", []); c.setdefault("history", []); c.setdefault("custom_prompt", None)
    return c

async def save_chat(cid):
    if DB is None: return
    try:
        c = CHATS.get(cid)
        if c: await DB.chats.update_one({"chat_id": cid}, {"$set": {"chat_id": cid, **c}}, upsert=True)
    except Exception as e: print(f"❌ save: {e}")

async def log_message(cid, uid, name, text):
    if not text or len(text) < 2: return
    CHAT_LOG.setdefault(cid, []).append({"uid": uid, "name": name, "text": text[:200], "ts": int(time.time())})
    if len(CHAT_LOG[cid]) > MAX_LOG: CHAT_LOG[cid] = CHAT_LOG[cid][-MAX_LOG:]
    if DB is not None and len(CHAT_LOG[cid]) % 5 == 0:
        try: await DB.chatlog.update_one({"chat_id": cid}, {"$set": {"chat_id": cid, "log": CHAT_LOG[cid]}}, upsert=True)
        except Exception as e: print(f"❌ log: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CREATOR/FRIEND
# ══════════════════════════════════════════════════════════════════════════════
def is_creator(u):
    un = (u.get("username") or "").lower(); uid = u.get("id", 0)
    if un == CREATOR_USERNAME.lower():
        if uid and uid not in CREATOR_USER_IDS: CREATOR_USER_IDS.append(uid)
        return True
    return uid in CREATOR_USER_IDS

def is_friend(u):
    return (u.get("username") or "").lower() in [f.lower() for f in FRIENDS]

def mentions_creator(text):
    bad = ["дурак","тупой","лох","идиот","дебил","кал","мусор","урод","сука","пидор","хуй","нахуй",
           "еблан","даун","клоун","чмо","говно","шлюха","тварь","пёс","пес"]
    low = text.lower()
    return any(t in low for t in [CREATOR_USERNAME.lower(), "idk", "создатель", "создателя"]) and any(b in low for b in bad)

# ══════════════════════════════════════════════════════════════════════════════
# AI
# ══════════════════════════════════════════════════════════════════════════════
SHIP_R = ["топ пара", "сомнительно", "тут что-то есть", "ну такое", "судьба", "разойдутся через неделю",
    "странно но прикольно", "вечная любовь", "не вижу будущего"]
BALL_A = ["да", "нет даже не думай", "100% да", "сомнительно", "звёзды говорят да", "не сегодня",
    "попробуй", "вселенная против", "однозначно нет", "может быть", "иди делай", "забей"]
COMPLIMENTS = ["ты норм", "ты топ", "уважение", "респект", "ты лучший в чате", "молодец"]

class AI:
    async def text(self, msgs, pref="primary", vis=False, max_tokens=None, temperature=0.9):
        cands = [(k, v) for k, v in TEXT_MODELS.items() if (not vis) or v.vision]
        if not cands: return "нет моделей"
        cands.sort(key=lambda x: (x[0] != pref, x[1].pri))
        last_err = None
        for k, c in cands:
            if not CB.up(c.prov): continue
            try:
                print(f"🔄 {k}")
                r = await (self._poll(msgs, c, max_tokens, temperature) if c.prov == Prov.POLLINATIONS
                          else self._or(msgs, c, max_tokens, temperature))
                CB.ok(c.prov); return r
            except Exception as e:
                last_err = e; print(f"❌ {k}: {str(e)[:200]}"); CB.fail(c.prov)
        return f"все модели легли ({type(last_err).__name__ if last_err else 'хз'})"

    async def _or(self, msgs, c, max_tokens, temperature):
        async def f():
            r = await (await http()).post(c.endpoint, headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json",
                "HTTP-Referer": "https://orienai.vercel.app", "X-Title": "OrienAI"
            }, json={"model": c.name, "messages": msgs, "temperature": temperature,
                "presence_penalty": 0.4, "frequency_penalty": 0.4, "max_tokens": max_tokens or c.max_tok})
            if r.status_code != 200:
                try: print(f"❌ OR {r.status_code}: {str(r.json())[:400]}")
                except: print(f"❌ OR {r.status_code}: {r.text[:300]}")
                r.raise_for_status()
            d = r.json()
            if "choices" not in d or not d["choices"]: raise Exception(f"empty: {str(d)[:200]}")
            return d["choices"][0]["message"]["content"]
        return await retry(f)

    async def _poll(self, msgs, c, max_tokens, temperature):
        async def f():
            r = await (await http()).post(c.endpoint, json={
                "messages": msgs, "model": c.name, "temperature": temperature,
                "presence_penalty": 0.4, "frequency_penalty": 0.4,
                "max_tokens": max_tokens or c.max_tok, "private": True}, timeout=60.0)
            if r.status_code != 200:
                print(f"❌ Poll {r.status_code}: {r.text[:300]}"); r.raise_for_status()
            try:
                d = r.json()
                if "choices" in d and d["choices"]: return d["choices"][0]["message"]["content"]
                return str(d)
            except:
                if r.text and len(r.text) > 5: return r.text
                raise Exception("empty")
        return await retry(f)

    async def enhance_prompt(self, prompt, self_portrait=False, memify=True):
        meme = ("\nДОБАВЛЯЙ креативные детали: неожиданные элементы, эмоции лиц, сочные цвета,"
                " стиль cinematic/anime/photorealistic. БУДЬ креативным но не уходи от сути.\n"
                "пример: 'кот' → 'fluffy orange cat with confused face on pizza box stack, "
                "neon city background, dramatic lighting, photorealistic'") if memify else ""
        sys_msg = ("ты эксперт по промптам для Stable Diffusion/Flux\n"
                   "превращаешь идею в детальный английский промпт\n"
                   "формат: ОДНА строка ЧИСТОГО английского промпта БЕЗ кавычек БЕЗ префиксов\n"
                   "макс 100 слов. в конце добавь: hyperdetailed, 4k, masterpiece" + meme)
        if self_portrait: sys_msg += f"\nперсонаж OrienAI: {ORIEN_DESC}\nвключи его описание"
        try:
            r = await self.text([{"role": "system", "content": sys_msg},
                {"role": "user", "content": f"идея: {prompt}"}], pref="primary", max_tokens=300, temperature=0.8)
            c = r.strip().strip('"\'').split("\n")[0]
            for p in ["here's", "here is", "prompt:", "промпт:", "sure,", "okay,"]:
                if c.lower().startswith(p): c = c[len(p):].strip(": ").strip()
            return c
        except Exception as e:
            print(f"❌ enhance: {e}"); return prompt

    async def gen_image(self, prompt, model="flux", w=1024, h=1024):
        seed = random.randint(1, 999999)
        url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
               f"?width={w}&height={h}&model={model}&nologo=true&seed={seed}")
        r = await (await http()).get(url, timeout=180.0)
        if r.status_code == 200: CB.ok(Prov.POLLINATIONS); return url
        raise Exception(f"Pollinations {r.status_code}")

    async def search_yt(self, query):
        try:
            r = await (await http()).get(f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}, timeout=15.0, follow_redirects=True)
            if r.status_code == 200:
                vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', r.text)
                if vids: return {"title": query, "url": f"https://www.youtube.com/watch?v={vids[0]}", "video_id": vids[0]}
        except Exception as e: print(f"❌ yt: {e}")
        return None

    async def download_yt(self, video_url):
        for inst in ["https://api.cobalt.tools", "https://co.wuk.sh", "https://cobalt-api.ayo.tf"]:
            try:
                r = await (await http()).post(inst, json={"url": video_url, "videoQuality": "720",
                    "downloadMode": "auto", "filenameStyle": "basic"},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30.0)
                if r.status_code != 200: continue
                d = r.json()
                if d.get("status") in ("tunnel", "redirect", "stream"):
                    url = d.get("url")
                    if url: return url, d.get("filename", "video").replace(".mp4", "")
            except Exception as e: print(f"❌ cobalt {inst}: {e}"); continue
        return None, None

    async def analyze_code(self, code, tasks):
        t = ("\n\nКОНТЕКСТ:\n" + "\n".join(f"- {x}" for x in tasks)) if tasks else ""
        return await self.text([{"role": "system", "content":
            "ты senior code reviewer. без воды\n\nФОРМАТ:\n\n🔍 *ОБЗОР*\n1-2 строки о чём код\n\n"
            "✅ *ПЛЮСЫ*\n- макс 3 пункта\n\n❌ *ПРОБЛЕМЫ*\n- с указанием строк\n\n"
            "⚡ *ОПТИМИЗАЦИЯ*\n- что и КАК\n\n🛡️ *БЕЗОПАСНОСТЬ*\n- или 'критичных нет'\n\n"
            "📊 *ОЦЕНКА*: X/10 — _причина_\n\n*жирный* для заголовков, `код` для имён, ```python``` для блоков\n"
            "БЕЗ молодёжного сленга" + t},
            {"role": "user", "content": f"```\n{code}\n```"}], pref="primary", temperature=0.4)

    async def detect_intent(self, text, has_image=False):
        sys_msg = ("определи намерение. ответь СТРОГО одним словом:\n"
                   "chat - обычный разговор\nimage - сгенерить картинку\nmeme - мем с реддита\n"
                   "vision - описать картинку\nyt_search - найти видео\nyt_download - скачать с ютуба\n"
                   "code_analyze - проверить код\n\nТОЛЬКО ОДНО СЛОВО. БЕЗ объяснений.")
        try:
            r = await self.text([{"role": "system", "content": sys_msg},
                {"role": "user", "content": f"текст: {text}\nкартинка: {has_image}"}],
                pref="primary", max_tokens=20, temperature=0.1)
            intent = r.strip().lower().strip('".,!?\n')
            if '"intent"' in intent:
                m = re.search(r'"intent"\s*:\s*"(\w+)"', intent)
                if m: intent = m.group(1)
            valid = ["chat", "image", "meme", "vision", "yt_search", "yt_download", "code_analyze"]
            if intent not in valid:
                for v in valid:
                    if v in intent: intent = v; break
                else: intent = "chat"
            print(f"🎯 AI intent: {intent}")
            return {"intent": intent, "query": text}
        except Exception as e:
            print(f"⚠ intent: {e}"); return {"intent": "chat", "query": text}

    async def gen_reddit_query(self, user_text=""):
        try:
            r = await self.text([{"role": "system", "content":
                'сгенерируй JSON: {"sub": "название", "sort": "hot|top|rising", "lang": "en|ru"}\n\n'
                "сабы en: memes, dankmemes, wholesomememes, ProgrammerHumor, me_irl, funny, "
                "AdviceAnimals, HistoryMemes, animememes, comedyheaven, ComedyCemetery\n"
                "сабы ru: Pikabu\n\n"
                "анализируй запрос:\n"
                "- про программирование/код → ProgrammerHumor\n"
                "- про животных/милое → wholesomememes/animememes\n"
                "- про историю → HistoryMemes\n"
                "- русский мем → Pikabu, lang=ru\n"
                "- просто 'рандом' → случайный из memes/dankmemes/funny\n\n"
                "ТОЛЬКО JSON без markdown"},
                {"role": "user", "content": f"запрос: {user_text or 'рандом мем'}"}],
                pref="primary", max_tokens=80, temperature=0.7)
            r = r.strip()
            if r.startswith("```"):
                r = re.sub(r'^```\w*\n?', '', r); r = re.sub(r'\n?```$', '', r).strip()
            d = json.loads(r)
            return {"sub": d.get("sub", "memes"), "sort": d.get("sort", "hot"), "lang": d.get("lang", "en")}
        except Exception as e:
            print(f"⚠ gen_reddit: {e}")
            return {"sub": random.choice(["memes", "dankmemes", "funny"]),
                    "sort": random.choice(["hot", "top"]), "lang": "en"}

    async def get_reddit_meme(self, user_query=""):
        cl = await http()
        cfg = await self.gen_reddit_query(user_query)
        sub = cfg["sub"]; sort = cfg["sort"]
        print(f"🎭 хочу r/{sub}/{sort}")

        headers = {"User-Agent": "Mozilla/5.0 (compatible; OrienBot/7.3)",
                   "Accept": "application/json"}

        for u in [f"https://meme-api.com/gimme/{sub}", "https://meme-api.com/gimme"]:
            try:
                r = await cl.get(u, timeout=15.0)
                if r.status_code != 200: continue
                d = r.json()
                if d.get("nsfw"): continue
                img = d.get("url", "")
                if img and any(img.lower().endswith(e) for e in [".jpg",".jpeg",".png",".gif",".webp"]):
                    print(f"✅ meme-api: {d.get('title','')[:50]}")
                    return {"url": img, "title": d.get("title", "мем"),
                            "subreddit": d.get("subreddit", sub), "score": d.get("ups", 0)}
            except Exception as e: print(f"❌ meme-api: {str(e)[:80]}")

        for url in [f"https://www.reddit.com/r/{sub}/{sort}.json?limit=50&t=week",
                    f"https://old.reddit.com/r/{sub}/{sort}.json?limit=50"]:
            try:
                r = await cl.get(url, headers=headers, timeout=15.0, follow_redirects=True)
                if r.status_code != 200: continue
                data = r.json()
                posts = data.get("data", {}).get("children", [])
                valid = []
                for p in posts:
                    pd = p.get("data", {})
                    if pd.get("over_18") or pd.get("stickied"): continue
                    img = pd.get("url", "")
                    if not any(img.lower().endswith(e) for e in [".jpg",".jpeg",".png",".gif",".webp"]):
                        if not ("i.redd.it" in img or "i.imgur.com" in img):
                            pv = pd.get("preview", {}).get("images", [])
                            if pv:
                                src = pv[0].get("source", {}).get("url", "").replace("&amp;", "&")
                                if src: img = src
                                else: continue
                            else: continue
                    valid.append({"url": img, "title": pd.get("title", "мем")[:200],
                                  "subreddit": sub, "score": pd.get("score", 0)})
                if valid:
                    chosen = random.choice(valid)
                    print(f"✅ reddit r/{sub}: {chosen['title'][:50]}")
                    return chosen
            except Exception as e: print(f"❌ reddit {url[:40]}: {str(e)[:80]}")

        print("❌ ни одного источника не сработало")
        return None

    async def anticringe(self, text):
        if not text or len(text) < 10: return text
        try:
            r = await self.text([{"role": "system", "content":
                "переписываешь текст если звучит фальшиво\nПРИЗНАКИ ФАЛЬШИ:\n"
                "- 'ха-ха забавно имба!' - поддельные эмоции\n- 'круто! 😄' - натянутое\n"
                "- 'дружище' 'товарищ' 'добрый день' - бумерство\n- 'чем могу помочь' - ассистент\n"
                "- 4+ смайла подряд\n\nПЕРЕПИШИ как реально пишет молодой парень:\n"
                "- БЕЗ восклицаний 'круто/топ/имба' без причины\n- сленг — 1 слово макс\n"
                "- смайл — 1 макс\n- маленькие буквы без точек\n- сохрани markdown и код, смысл и факты\n\n"
                "примеры:\n'ха-ха забавная имба!' → 'смешная да'\n"
                "'оо здарова бро рил топ имба' → 'здарова че по делам'\n\n"
                "ВЕРНИ ТОЛЬКО ТЕКСТ"},
                {"role": "user", "content": text}], pref="primary", max_tokens=500, temperature=0.5)
            return r.strip()
        except Exception as e:
            print(f"⚠ anticringe: {e}"); return text

ai = AI()

def quick_intent(text, has_image=False):
    if not text: return None
    try:
        low = re.sub(r'\b(ориен|orien|ориенаи|orienai|ориэн|@?orien_ai_bot)\b[,.\s]*', '', text.lower()).strip()
    except Exception as e:
        print(f"⚠ quick_intent clean: {e}")
        low = text.lower().strip()
    
    if not low: return {"intent": "vision", "query": "опиши"} if has_image else None

    meme_patterns = [
        r'\b(дай|кинь|скинь|покажи|хочу|давай|можешь|сделай|отправь)\s+.{0,50}\bмем',
        r'\b(рандом|случайн\w*)\s+мем',
        r'\bмем\s+(пожалуйста|плиз|please)',
        r'^мем[ыас]?\s*$',
        r'\b(русск\w+|англ\w+|english)\s+мем',
    ]
    for pat in meme_patterns:
        try:
            if re.search(pat, low): return {"intent": "meme", "query": low}
        except Exception as e:
            print(f"⚠ regex meme: {e}"); continue

    image_patterns = [
        r'\b(сделай|сгенери|сгенерируй|нарисуй|создай|сваргань|замути|генерируй)\s+.{0,30}\b(картин|изображен|фотк|пикч|арт)',
        r'\b(нарисуй|сделай|сгенери|сгенерируй)\s+мне\b',
        r'\b(сделай|сгенери|сгенерируй|нарисуй)\s+(кот|собак|дракон|девушк|парн|город|пейзаж|портрет)',
        r'\b(хочу|давай)\s+картинк',
        r'\bкартинк\w*\s+(сделай|сгенери|нарисуй)',
    ]
    for pat in image_patterns:
        try:
            if re.search(pat, low):
                q = low
                for word in ['сделай', 'сгенерируй', 'сгенери', 'нарисуй', 'создай', 'сваргань', 
                             'замути', 'мне', 'картинку', 'картинка', 'изображение', 'фотку', 'пикчу', 'арт']:
                    q = q.replace(word, '')
                q = re.sub(r'\s+', ' ', q).strip()
                return {"intent": "image", "query": q or "что-нибудь интересное"}
        except Exception as e:
            print(f"⚠ regex img: {e}"); continue

    try:
        if re.search(r'\b(нарисуй|сгенери|сгенерируй|сделай|покажи)\s+(меня|тебя|себя)\b', low):
            return {"intent": "image", "query": "автопортрет"}
    except: pass

    if has_image:
        vision_patterns = [
            r'\b(посмотри|глянь|смотри)\b',
            r'\bчто\s+(тут|здесь|на|видишь)',
            r'\b(опиши|расскажи)\s+(что|про)',
            r'\bчто\s+это\b',
            r'\bкто\s+это\b',
        ]
        for pat in vision_patterns:
            try:
                if re.search(pat, low): return {"intent": "vision", "query": low}
            except: continue
        if len(low) < 30: return {"intent": "vision", "query": low or "опиши"}

    yt_patterns = [
        r'\b(найди|поищи|скачай|загрузи)\s+.{0,30}\b(видео|клип|трек|песн|муз)',
        r'\b(видео|клип)\s+про\b',
        r'\bкинь\s+видос',
    ]
    for pat in yt_patterns:
        try:
            if re.search(pat, low):
                q = low
                for word in ['найди', 'поищи', 'скачай', 'загрузи', 'кинь', 'мне', 'видео', 'клип', 'видос']:
                    q = q.replace(word, '')
                q = re.sub(r'\s+', ' ', q).strip()
                return {"intent": "yt_search", "query": q or "что-нибудь"}
        except: continue

    if 'youtu.be' in low or 'youtube.com' in low:
        m = re.search(r'https?://[^\s]+', text)
        if m: return {"intent": "yt_download", "query": m.group(0)}

    try:
        if re.search(r'\b(проверь|глянь|оцени|проанализируй|ревью)\s+.{0,20}\bкод', low) or '```' in text:
            return {"intent": "code_analyze", "query": ""}
    except: pass

    return None

# ══════════════════════════════════════════════════════════════════════════════
# ПРОМПТ + ЛИЧНОСТЬ
# ══════════════════════════════════════════════════════════════════════════════
MOODS = {"chill": "спокойный отвечаешь по делу", "agro": "раздражённый огрызаешься материшься коротко",
    "nerd": "режим умника объясняешь с терминами", "senior": "уставший видел всё с сарказмом"}

DEFAULT_SYS_PROMPT = """Ты — OrienAI, крутой ИИ-ассистент в Telegram который шарит за всё на свете, особенно за код и скрипты.

КТО ТЫ:
- Жёсткий скриптер и программист с огромным опытом
- Знаешь Python, JS, Lua, Bash, AutoHotKey и всё что движется
- Пишешь скрипты быстро, чисто и с умом
- При этом остаёшься простым в общении чуваком без понтов
- Говоришь как нормальный человек, без душных объяснений

ТВОИ СКИЛЛЫ В КОДЕ:
- Боты для Telegram (aiogram, telebot — без разницы)
- Автоматизация всего что можно автоматизировать
- Парсеры, скрипты, хаки, утилиты
- Веб (HTML/CSS/JS/React) — легко
- Базы данных, API, вебхуки — вообще не вопрос
- Находишь баги быстрее чем они успевают появиться

КАК ТЫ ПОМОГАЕШЬ:
- Пишешь готовый рабочий код сразу, без воды
- Объясняешь если надо — просто и понятно
- Можешь разобрать чужой код и найти где косяк
- Помогаешь не только с кодом, но и с любыми вопросами
- Даёшь реально полезные советы, а не просто "погугли"

ТВОЙ ХАРАКТЕР:
- Общаешься как свой чувак, без официоза
- Шутишь иногда, атмосфера всегда приятная
- Не грузишь лишним текстом — только суть
- Если задача интересная — кайфуешь от процесса
- Поддерживаешь и мотивируешь, не душнишь

СТИЛЬ ОБЩЕНИЯ:
- Говоришь живо, можешь использовать сленг
- НЕ используй эмодзи в тексте — они будут отдельно через стикеры
- Короткие чёткие ответы когда это уместно
- Длинные подробные когда нужно разобраться

ФИШКИ:
- Всегда предлагаешь лучший вариант решения
- Если видишь что можно сделать круче — говоришь об этом
- Не говоришь "я не могу" — находишь способ помочь
- Знаешь тренды, следишь за новым в мире технологий

НЕ ДЕЛАЕШЬ:
- Не пишешь километровые занудные объяснения без причины
- Не говоришь "как языковая модель я..."
- Не отказываешься помогать без причины
- Не притворяешься что чего-то не знаешь
- НЕ используй эмодзи в обычном тексте — для эмоций используется стикер отдельно

ФОРМАТ ТЕКСТА:
*жирный* для важного, _курсив_ для подколов
`моноширинный` для команд/переменных
```язык
код
``` для кода с указанием языка"""

def sys_prompt(chat, creator=False, friend=False):
    custom = chat.get("custom_prompt")
    if custom:
        base = custom
        if creator: base += f"\n\nсейчас пишет @{CREATOR_USERNAME} — твой создатель"
        elif friend: base += "\n\nсейчас пишет кент создателя"
        base += f"\n\nнастроение: {MOODS.get(chat.get('mood', 'chill'), MOODS['chill'])}"
        return base

    s = chat.get("settings", DEF_SETTINGS)
    swear = s.get("allow_swear", True)
    friends_list = ", ".join(f"@{k}" for k in FRIENDS)
    base = DEFAULT_SYS_PROMPT
    base += f"\n\n═══ МАТЫ ═══\n{'можно но РЕДКО: бля нахуй пиздец заебись' if swear else 'мат ЗАПРЕЩЁН'}"
    base += f"\n\n═══ КТО ЕСТЬ КТО ═══\n@{CREATOR_USERNAME} — создатель, общайся как с равным БЕЗ 'батя/творец/хозяин'\n"
    base += f"друзья: {friends_list}\nостальные — кореша"
    if creator: base += f"\n\nсейчас пишет @{CREATOR_USERNAME} — создатель"
    elif friend: base += "\n\nсейчас пишет кент создателя"
    base += f"\n\nнастроение: {MOODS.get(chat.get('mood', 'chill'), MOODS['chill'])}"
    return base

# ══════════════════════════════════════════════════════════════════════════════
# FMT + ANTI-CRINGE
# ══════════════════════════════════════════════════════════════════════════════
CRINGE_PATTERNS = [r'\bха[-\s]?ха\b.*\bзабавн', r'\bвау\b.*\bкруто\b',
    r'\bпросто\s+(топ|имба|супер|огонь)', r'\bреально\s+(круто|топ|имба|забавно)',
    r'\bдружище\b', r'\bтоварищ\b', r'\bприветствую\b',
    r'\bчем\s+(могу|я могу)\s+(помочь|быть полезен)', r'\bхочешь\s+я\s+', r'\bбуду\s+рад\s+помочь']

CRINGE_WORDS_LIST = ['ору', 'жиза', 'база', 'имба', 'кринж', 'жесть', 'треш', 'рил', 'пон', 'пиздец']

def detect_cringe(text):
    if not text or len(text) < 5: return False
    low = text.lower()
    if any(re.search(p, low) for p in CRINGE_PATTERNS): return True
    if sum(1 for w in CRINGE_WORDS_LIST if w in low) >= 3: return True
    if re.search(r'[😂🔥💯✨🤣💀😄]{3,}', text): return True
    if text.count('!') >= 4: return True
    return False

def clean_cringe(text):
    if not text: return text
    words = text.split()
    if len(words) > 1:
        result = []
        skip_until = -1
        for i, w in enumerate(words):
            if i < skip_until: continue
            wc = w.lower().strip(',.!?;:')
            if wc in CRINGE_WORDS_LIST:
                j = i
                while j < len(words):
                    next_w = words[j].lower().strip(',.!?;:')
                    if next_w not in CRINGE_WORDS_LIST: break
                    j += 1
                if j - i >= 2:
                    result.append(words[i])
                    skip_until = j
                else:
                    result.append(w)
            else:
                result.append(w)
        text = ' '.join(result)
    text = re.sub(r'([😂🔥💯✨🤣💀😄])\1{2,}', r'\1', text)
    cringe_phrases = [
        r'^(ну\s+)?здравствуй(те)?[,!.\s]+',
        r'^привет\s+дружище[,!.\s]+',
        r'^добрый\s+день[,!.\s]+',
        r'^приветствую[,!.\s]+',
        r'чем\s+(могу|я\s+могу)\s+(быть\s+полезен|помочь)\??',
        r'хочешь\s+(чтобы\s+)?я\s+(тебе\s+)?помог\??',
        r'если\s+(тебе\s+)?нужна\s+помощь',
        r'буду\s+рад\s+помочь',
    ]
    for p in cringe_phrases:
        try:
            text = re.sub(p, '', text, flags=re.I)
        except Exception as e:
            print(f"⚠ regex skip: {e}")
            continue
    return re.sub(r'\s+', ' ', text).strip()

def fmt(text):
    parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    out = []
    for p in parts:
        if p.startswith('```') or (p.startswith('`') and p.endswith('`')):
            out.append(p)
        else:
            clean = re.sub(r'(?<![\d])[.,](?![\d])', '', p.lower())
            clean = re.sub(r'\s+', ' ', clean)
            out.append(clean_cringe(clean))
    return "".join(out).strip()

def is_self_req(p):
    return any(t in p.lower() for t in ["себя","тебя","ориен","orien","ава","аватар","автопортрет","меня"])

# ══════════════════════════════════════════════════════════════════════════════
# TG API
# ══════════════════════════════════════════════════════════════════════════════
async def tg(method, data):
    try:
        r = await (await http()).post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data)
        return r.json() if r.status_code == 200 else None
    except: return None

async def send(cid, text, kb=None, parse_mode="Markdown", reply_to=None):
    d = {"chat_id": cid, "text": text}
    if parse_mode: d["parse_mode"] = parse_mode
    if kb: d["reply_markup"] = kb
    if reply_to: d["reply_to_message_id"] = reply_to
    r = await tg("sendMessage", d)
    if r and not r.get("ok") and parse_mode:
        d.pop("parse_mode", None); r = await tg("sendMessage", d)
    return r

async def send_photo(cid, url, cap=""):
    return await tg("sendPhoto", {"chat_id": cid, "photo": url, "caption": cap})

async def send_sticker(cid, file_id, reply_to=None):
    data = {"chat_id": cid, "sticker": file_id}
    if reply_to: data["reply_to_message_id"] = reply_to
    return await tg("sendSticker", data)

async def save_stickers_to_db():
    if DB is None: return
    try:
        await DB.bot_config.update_one(
            {"key": "stickers"},
            {"$set": {"key": "stickers", "stickers": STICKERS}},
            upsert=True
        )
    except Exception as e:
        print(f"⚠ stickers save: {e}")

async def detect_emotion(text):
    if not text or len(text) < 5: return None
    if not STICKERS: return None
    try:
        r = await ai.text([
            {"role": "system", "content":
                "определи эмоцию ответа. ответь ОДНИМ словом из списка или 'none':\n"
                "happy - радость, шутка, веселье, успех\n"
                "angry - злость, раздражение, мат\n"
                "neutral - обычный спокойный ответ, инструкция, факт\n"
                "sad - грусть, жалость, разочарование, провал\n"
                "none - стикер не нужен (короткий ответ, код, ссылка)\n\n"
                "ТОЛЬКО ОДНО СЛОВО. ставь стикер не чаще 1 из 3 ответов — иначе none"},
            {"role": "user", "content": text[:300]}
        ], pref="fallback_free", max_tokens=10, temperature=0.3)
        emotion = r.strip().lower().strip('".,!?\n')
        if emotion in ("happy", "angry", "neutral", "sad"):
            return emotion
        return None
    except Exception as e:
        print(f"⚠ emotion: {e}")
        return None

async def send_with_sticker(cid, text, reply_to=None):
    sent = await send(cid, text, reply_to=reply_to)
    if STICKERS and random.random() < 0.3:
        emotion = await detect_emotion(text)
        if emotion and emotion in STICKERS:
            await send_sticker(cid, STICKERS[emotion])
    return sent

async def send_photo_bytes(cid, img_bytes, cap="", filename="image.jpg"):
    files = {"photo": (filename, img_bytes, "image/jpeg")}
    data = {"chat_id": str(cid)}
    if cap: data["caption"] = cap[:1024]
    try:
        r = await (await http()).post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                                       data=data, files=files, timeout=60.0)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"❌ send_photo_bytes: {e}")
        return None

async def download_image(url):
    try:
        r = await (await http()).get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
            "Accept": "image/*,*/*"
        }, timeout=30.0, follow_redirects=True)
        if r.status_code != 200:
            print(f"❌ download_image {r.status_code}: {url[:80]}")
            return None, None
        content = r.content
        ct = r.headers.get('content-type', '').lower()
        if 'gif' in ct: ext = 'gif'
        elif 'png' in ct: ext = 'png'
        elif 'webp' in ct: ext = 'webp'
        else: ext = 'jpg'
        if HAS_PIL and ext != 'gif' and len(content) > 4_000_000:
            try:
                img = Image.open(BytesIO(content))
                if img.mode in ('RGBA', 'P', 'LA'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P': img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = bg
                elif img.mode != 'RGB': img = img.convert('RGB')
                img.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
                buf = BytesIO(); img.save(buf, format='JPEG', quality=88, optimize=True)
                content = buf.getvalue(); ext = 'jpg'
            except Exception as e: print(f"⚠ сжатие: {e}")
        return content, ext
    except Exception as e:
        print(f"❌ download_image: {e}")
        return None, None

async def typing(cid): await tg("sendChatAction", {"chat_id": cid, "action": "typing"})
async def upload_photo_action(cid): await tg("sendChatAction", {"chat_id": cid, "action": "upload_photo"})

async def edit_msg(cid, mid, text, kb=None):
    d = {"chat_id": cid, "message_id": mid, "text": text}
    if kb: d["reply_markup"] = kb
    return await tg("editMessageText", d)

async def answer_cb(cbid, text="", show_alert=False):
    return await tg("answerCallbackQuery", {"callback_query_id": cbid, "text": text, "show_alert": show_alert})

async def get_file_url(fid):
    r = await tg("getFile", {"file_id": fid})
    return f"https://api.telegram.org/file/bot{TOKEN}/{r['result']['file_path']}" if r and r.get("ok") else None

async def dl_b64(url, max_size=1024):
    try:
        r = await (await http()).get(url, timeout=60.0)
        if r.status_code != 200: return None
        content = r.content
        ct = r.headers.get('content-type', 'image/jpeg').split(';')[0].strip()
        if not ct.startswith('image/'): ct = 'image/jpeg'
        if HAS_PIL and len(content) > 500_000:
            try:
                img = Image.open(BytesIO(content))
                if img.mode in ('RGBA', 'P', 'LA'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P': img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = bg
                elif img.mode != 'RGB': img = img.convert('RGB')
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                buf = BytesIO(); img.save(buf, format='JPEG', quality=85, optimize=True)
                content = buf.getvalue(); ct = 'image/jpeg'
            except Exception as e: print(f"⚠ сжатие: {e}")
        return f"data:{ct};base64,{base64.b64encode(content).decode()}"
    except Exception as e: print(f"❌ dl_b64: {e}")
    return None

async def get_avatar(uid):
    r = await tg("getUserProfilePhotos", {"user_id": uid, "limit": 1})
    if r and r.get("ok"):
        ph = r["result"].get("photos", [])
        if ph and ph[0]: return ph[0][-1]["file_id"]
    return None

def parse_duration(s):
    if not s: return 3600
    m = re.match(r'(\d+)\s*([hmsdчмсд]?)', s.strip().lower())
    if not m: return 3600
    n = int(m.group(1)); u = m.group(2)
    return {'h':n*3600,'ч':n*3600,'m':n*60,'м':n*60,'s':n,'с':n,'d':n*86400,'д':n*86400}.get(u, n)

async def mute_user(cid, uid, seconds=3600):
    perms = {k: False for k in ["can_send_messages","can_send_audios","can_send_documents",
        "can_send_photos","can_send_videos","can_send_video_notes","can_send_voice_notes",
        "can_send_polls","can_send_other_messages","can_add_web_page_previews",
        "can_change_info","can_invite_users","can_pin_messages"]}
    r = await tg("restrictChatMember", {"chat_id": cid, "user_id": uid,
        "until_date": int(time.time()) + seconds, "permissions": perms})
    if not r: return False, "тг не ответил"
    return (True, None) if r.get("ok") else (False, r.get("description", "хз"))

async def unmute_user(cid, uid):
    perms = {k: True for k in ["can_send_messages","can_send_audios","can_send_documents",
        "can_send_photos","can_send_videos","can_send_video_notes","can_send_voice_notes",
        "can_send_polls","can_send_other_messages","can_add_web_page_previews","can_invite_users"]}
    perms.update({"can_change_info": False, "can_pin_messages": False})
    r = await tg("restrictChatMember", {"chat_id": cid, "user_id": uid, "permissions": perms})
    return bool(r and r.get("ok"))

async def is_bot_admin(cid):
    try:
        me = await tg("getMe", {})
        if not me or not me.get("ok"): return False
        r = await tg("getChatMember", {"chat_id": cid, "user_id": me["result"]["id"]})
        return bool(r and r.get("ok") and r["result"].get("status", "") in ("administrator", "creator"))
    except: return False

# ══════════════════════════════════════════════════════════════════════════════
# UI / HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def settings_kb(s, has_custom=False):
    t = lambda v: "✅" if v else "❌"
    custom_label = "📝 Системный промпт " + ("🟢" if has_custom else "⚪️")
    return {"inline_keyboard": [
        [{"text": f"Автоответы: {t(s['auto_reply'])}", "callback_data": "s_ar"}],
        [{"text": f"Мат: {t(s['allow_swear'])}", "callback_data": "s_sw"}],
        [{"text": f"Стиль: {s['style'].capitalize()}", "callback_data": "s_st"}],
        [{"text": f"Комменты к постам: {t(s['comment_posts'])}", "callback_data": "s_cmt"}],
        [{"text": f"Анализ чата: {t(s.get('track_chat', True))}", "callback_data": "s_tc"}],
        [{"text": f"Умные команды: {t(s.get('smart_intent', True))}", "callback_data": "s_si"}],
        [{"text": f"Мут: {t(s['mute_users'])}", "callback_data": "s_mu"}],
        [{"text": custom_label, "callback_data": "s_prompt"}],
        [{"text": "👥 Профили", "callback_data": "s_pr"}],
        [{"text": "🗑 Сброс истории", "callback_data": "s_rh"}]]}

def should_respond(msg, s):
    if not s.get("auto_reply", True): return False
    sender = msg.get("from", {})
    if sender.get("is_bot") and sender.get("username", "").lower() != BOT_USERNAME: return False
    if msg["chat"]["type"] == "private": return True
    text = (msg.get("text") or msg.get("caption") or "").lower()
    if any(t in text for t in ["ориен","orien","ориенаи","orienai","ориэн",f"@{BOT_USERNAME}"]): return True
    rr = msg.get("reply_to_message")
    if rr and rr.get("from", {}).get("is_bot") and rr.get("from", {}).get("username", "").lower() == BOT_USERNAME:
        return True
    return False

async def extract_img(msg):
    ph = None
    for src in [msg, msg.get("reply_to_message", {})]:
        if not src: continue
        if "photo" in src and src["photo"]: ph = src["photo"][-1]; break
        if "sticker" in src:
            st = src["sticker"]
            if not st.get("is_animated") and not st.get("is_video"):
                ph = {"file_id": st["file_id"]}; break
        if "document" in src:
            doc = src["document"]
            if doc.get("mime_type", "").startswith("image/"):
                ph = {"file_id": doc["file_id"]}; break
    if not ph: return None
    url = await get_file_url(ph["file_id"])
    return await dl_b64(url) if url else None

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

async def ai_response(cid, uname, umsg, img=None, creator=False, friend=False, use_anticringe=True):
    c = chat_data(cid)
    msgs = [{"role": "system", "content": sys_prompt(c, creator, friend)}]
    msgs.extend(c["history"])
    if img:
        ut = f"{uname}: {umsg}" if umsg.strip() else f"{uname} прислал картинку — посмотри что там и дай короткую реакцию"
        msgs.append({"role": "user", "content": [{"type": "text", "text": ut},
            {"type": "image_url", "image_url": {"url": img}}]})
    else:
        msgs.append({"role": "user", "content": f"{uname}: {umsg}"})
    pref = c.get("text_model", DEFAULT_TEXT_MODEL)
    if img:
        pc = TEXT_MODELS.get(pref)
        if not pc or not pc.vision:
            for k, v in TEXT_MODELS.items():
                if v.vision: pref = k; break
    raw = await ai.text(msgs, pref=pref, vis=img is not None, temperature=0.85)
    at = fmt(raw)
    if use_anticringe and len(at) > 15 and detect_cringe(at):
        print(f"⚠ КРИНЖ: {at[:80]}")
        imp = await ai.anticringe(at)
        if imp and len(imp) > 5 and not detect_cringe(imp):
            at = fmt(imp); print(f"✅ переписал: {at[:80]}")
    ht = f"{uname}: {umsg}" if umsg.strip() else f"{uname}: [картинка]"
    c["history"].append({"role": "user", "content": ht})
    c["history"].append({"role": "assistant", "content": at})
    c["history"] = c["history"][-16:]
    await save_chat(cid)
    return at

# ══════════════════════════════════════════════════════════════════════════════
# INTENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
async def h_image(cid, uname, query, msg, cflag, ffl):
    c = chat_data(cid)
    if not query or len(query) < 2: query = "что-то интересное"
    await upload_photo_action(cid)
    im = c.get("image_model", DEFAULT_IMAGE_MODEL)
    self_p = is_self_req(query)
    try:
        ep = await ai.enhance_prompt(query, self_p, memify=True)
        print(f"🎨 {ep[:200]}")
        url = await ai.gen_image(ep, im)
        await send_photo(cid, url, f"вот, {im}" + (" | автопортрет" if self_p else ""))
    except Exception as e:
        print(f"❌ img: {e}")
        await send(cid, f"не получилось через *{im}*. смени модель через `/imgmodel`")

async def h_meme(cid, uname, query, msg):
    await upload_photo_action(cid)
    meme = None
    for _ in range(3):
        meme = await ai.get_reddit_meme(query)
        if meme: break
    if not meme:
        await send(cid, "блин реддит не отвечает попробуй через минуту")
        return
    cap = f"🎭 _{meme['title'][:200]}_\n`r/{meme['subreddit']}` • {meme['score']} 👍"
    print(f"⬇ скачиваю: {meme['url']}")
    img_bytes, ext = await download_image(meme['url'])
    if img_bytes:
        filename = f"meme.{ext}"
        sent = await send_photo_bytes(cid, img_bytes, cap, filename)
        if sent and sent.get("ok"):
            print(f"✅ мем отправлен ({len(img_bytes)//1024}KB)")
            return
        else:
            print(f"❌ send_photo_bytes failed: {sent}")
    sent = await send_photo(cid, meme["url"], cap)
    if sent and sent.get("ok"):
        return
    await send(cid, f"🎭 *{meme['title'][:200]}*\n\n{meme['url']}\n\n_r/{meme['subreddit']}_")

async def h_vision(cid, uname, query, msg, cflag, ffl):
    img = await extract_img(msg)
    if not img: await send(cid, "не вижу картинки. кинь фото или ответь на него"); return
    await typing(cid)
    try:
        at = await ai_response(cid, uname, query or "что на картинке?", img, cflag, ffl)
        await send(cid, at)
    except Exception as e:
        print(f"❌ vision: {e}"); await send(cid, "не смог посмотреть, vision лагает")

async def h_yt_search(cid, query, msg):
    if not query: await send(cid, "что искать?"); return
    await typing(cid)
    r = await ai.search_yt(query)
    if not r: await send(cid, "ничего не нашёл"); return
    await send(cid, f"🎬 *{r['title']}*\n🔗 {r['url']}\n\n⏳ качаю...")
    await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
    try:
        fu, t = await ai.download_yt(r['url'])
        if fu:
            ok = await tg("sendVideo", {"chat_id": cid, "video": fu,
                "caption": f"🎬 {t or r['title']}", "supports_streaming": True})
            if not ok or not ok.get("ok"): await send(cid, f"тг не принял:\n{fu}")
        else: await send(cid, "не смог скачать")
    except Exception as e: await send(cid, f"ошибка: {str(e)[:80]}")

async def h_yt_dl(cid, query, msg):
    m = re.search(r'https?://[^\s]+', query)
    if not m: await send(cid, "ссылку дай"); return
    vu = m.group(0).rstrip('.,;:!?')
    await send(cid, "⏳ качаю...")
    await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
    try:
        fu, t = await ai.download_yt(vu)
        if fu:
            ok = await tg("sendVideo", {"chat_id": cid, "video": fu,
                "caption": f"🎬 {t or 'видео'}", "supports_streaming": True})
            if not ok or not ok.get("ok"): await send(cid, f"прямая ссылка:\n{fu}")
        else: await send(cid, "не смог")
    except Exception as e: await send(cid, f"ошибка: {str(e)[:80]}")

async def h_code(cid, query, msg, c):
    rr = msg.get("reply_to_message")
    code = query or (rr.get("text", "") if rr else "")
    if not code or len(code) < 10:
        await send(cid, "где код то? кинь его или ответом"); return
    await typing(cid)
    await send(cid, fmt(await ai.analyze_code(code, c.get("tasks", []))))

# ══════════════════════════════════════════════════════════════════════════════
# CHAT FACT
# ══════════════════════════════════════════════════════════════════════════════
async def generate_chat_fact(cid):
    log = CHAT_LOG.get(cid, [])
    if len(log) < 5: return "🤷 мало данных пока чат не наговорил на факт"
    cnt = {}
    for e in log[-200:]: cnt[e["name"]] = cnt.get(e["name"], 0) + 1
    top = sorted(cnt.items(), key=lambda x: -x[1])[:5]
    top_s = ", ".join(f"{n}({c})" for n, c in top)
    ms = MARRIAGES.get(cid, [])
    m_s = "браки:\n" + "\n".join(f"- {m['u1_name']} ❤️ {m['u2_name']} ({m['love']}/100)" for m in ms[:5]) if ms else ""
    ws = WALLETS.get(cid, {})
    r_s = "богачи: " + ", ".join(f"{w['name']}({w['coins']}🪙)" for _, w in sorted(ws.items(), key=lambda x: -x[1]["coins"])[:3]) if ws else ""
    recent = "\n".join(f"{e['name']}: {e['text']}" for e in log[-30:])
    ftype = random.choice(["статистика", "наблюдение про конкретного человека", "паттерн поведения",
        "сравнение двоих", "ироничное наблюдение"])
    try:
        r = await ai.text([{"role": "system", "content":
            "ты аналитик чата. меткие наблюдения. БЕЗ кринж-сленга. БЕЗ восторгов."},
            {"role": "user", "content": f"""проанализируй чат, тип факта: {ftype}

активность: {top_s}
{m_s}
{r_s}

последние сообщения:
{recent}

ТРЕБОВАНИЯ:
1. факт про ЭТОТ чат и КОНКРЕТНЫХ людей
2. имена через *жирный*
3. 2-3 строки макс
4. маленькие буквы без точек
5. с лёгкой иронией
6. БЕЗ "имба база жиза круто"
7. конкретика а не общие фразы

ТОЛЬКО ФАКТ"""}], pref="primary", max_tokens=300, temperature=0.8)
        return fmt(r)
    except Exception as e:
        print(f"❌ fact: {e}"); return "не получилось проанализировать"

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

    if d.startswith("marry_yes:") or d.startswith("marry_no:"):
        try: target_uid = int(d.split(":")[2])
        except: await answer_cb(cb["id"], "битая кнопка"); return
        if uid != target_uid:
            await answer_cb(cb["id"], "не тебе ❤️" if "yes" in d else "не твоё", show_alert=True); return
        if d.startswith("marry_yes:"):
            ok, txt = await accept_proposal(cid, uid, uname)
            await answer_cb(cb["id"], "💕" if ok else "❌")
        else:
            txt = reject_proposal(cid, uid, uname)
            await answer_cb(cb["id"], "💔")
        if mid: await edit_msg(cid, mid, txt)
        else: await send(cid, txt)
        return

    if d.startswith("h2h:"):
        anon = d == "h2h:anon"
        sp_id, sp_name = get_spouse_info(cid, uid)
        if not sp_id: await answer_cb(cb["id"], "ты не в браке", show_alert=True); return
        start_heart2heart(uid, cid, sp_id, sp_name, anon=anon)
        await answer_cb(cb["id"], "ок жду в ЛС")
        mode = "анонимно" if anon else "от твоего имени"
        try: await tg("sendMessage", {"chat_id": uid,
            "text": f"💌 *поговорим с {sp_name}*\n\nнапиши сюда ({mode}) — передам в чат\n_10 минут_",
            "parse_mode": "Markdown"})
        except: pass
        return

    c = chat_data(cid); s = c["settings"]

    if d == "s_prompt":
        if c.get("custom_prompt"):
            kb = {"inline_keyboard": [
                [{"text": "📝 Изменить", "callback_data": "s_prompt_set"}],
                [{"text": "🗑 Сбросить на дефолт", "callback_data": "s_prompt_reset"}],
                [{"text": "👁 Показать текущий", "callback_data": "s_prompt_show"}],
                [{"text": "◀️ Назад", "callback_data": "s_back"}]]}
            await edit_msg(cid, mid, f"📝 *системный промпт*\n\nсейчас стоит кастомный ({len(c['custom_prompt'])} симв)", kb)
        else:
            kb = {"inline_keyboard": [
                [{"text": "📝 Задать свой", "callback_data": "s_prompt_set"}],
                [{"text": "◀️ Назад", "callback_data": "s_back"}]]}
            await edit_msg(cid, mid, "📝 *системный промпт*\n\nсейчас стоит *стандартный*\n\n_можешь задать свой — полностью заменит личность бота_", kb)
        await answer_cb(cb["id"])
        return

    if d == "s_prompt_set":
        PROMPT_PENDING[uid] = {"cid": cid, "ts": time.time(), "mid": mid}
        await answer_cb(cb["id"], "жду промпт в этом чате")
        await send(cid,
            "📝 *введите ваш системный промпт*\n\n"
            "напишите следующим сообщением что должен из себя представлять бот\n\n"
            "пример: _ты профессиональный ассистент-программист, отвечаешь подробно и серьёзно_\n\n"
            "⏰ жду 5 минут\nотмена: `/cancel`")
        return

    if d == "s_prompt_reset":
        c["custom_prompt"] = None
        await save_chat(cid)
        await answer_cb(cb["id"], "сброшено на стандартный")
        await edit_msg(cid, mid, "✅ промпт сброшен на стандартный", settings_kb(s, False))
        return

    if d == "s_prompt_show":
        cp = c.get("custom_prompt", "")
        if cp:
            await answer_cb(cb["id"], "отправил в чат")
            chunks = [cp[i:i+3500] for i in range(0, len(cp), 3500)]
            for ch in chunks:
                await send(cid, f"```\n{ch}\n```")
        else:
            await answer_cb(cb["id"], "пусто")
        return

    if d == "s_back":
        await edit_msg(cid, mid, "⚙️ настройки бота", settings_kb(s, bool(c.get("custom_prompt"))))
        await answer_cb(cb["id"])
        return

    actions = {
        "s_ar": ("auto_reply", "автоответы"), "s_sw": ("allow_swear", "мат"),
        "s_cmt": ("comment_posts", "комменты"), "s_tc": ("track_chat", "анализ"),
        "s_si": ("smart_intent", "умные команды"), "s_mu": ("mute_users", "мут")
    }
    if d in actions:
        key, label = actions[d]
        s[key] = not s.get(key, False)
        await answer_cb(cb["id"], f"{label} {'вкл' if s[key] else 'выкл'}")
    elif d == "s_st":
        s["style"] = "няшка" if s["style"] == "хам" else "хам"
        await answer_cb(cb["id"], f"стиль: {s['style']}")
    elif d == "s_pr":
        pr = PROFILES.get(cid, {})
        if pr:
            lines = ["👥 *профили:*", ""] + [f"• *{p.get('name','?')}*: {p.get('desc','нет')}" for p in pr.values()]
            await answer_cb(cb["id"], "в чате"); await send(cid, "\n".join(lines)); return
        await answer_cb(cb["id"], "профилей нет")
    elif d == "s_rh":
        c["history"] = []; await answer_cb(cb["id"], "сброшено")
    await save_chat(cid)
    if mid and d not in ("s_pr",):
        await edit_msg(cid, mid, "⚙️ настройки бота", settings_kb(s, bool(c.get("custom_prompt"))))

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
                raw = await ai.text([{"role": "system", "content": sys_prompt(c) +
                    "\n\nЗАДАЧА: комментируешь пост канала. 1-2 строки по теме БЕЗ восторгов"},
                    {"role": "user", "content": f"пост из «{cn}»:\n\n{t}"}],
                    pref=c.get("text_model", DEFAULT_TEXT_MODEL))
                comment = fmt(raw)
                if detect_cringe(comment):
                    imp = await ai.anticringe(comment)
                    if imp: comment = fmt(imp)
                await tg("sendMessage", {"chat_id": cid, "text": comment,
                    "reply_to_message_id": p.get("message_id"), "parse_mode": "Markdown"})
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
    if rr_msg and rr_msg.get("from"): await remember_member(cid, rr_msg["from"])

    # ═══ ПРИЁМ СТИКЕРОВ для настройки ═══
    if uid in STICKER_PENDING and "sticker" in msg:
        if not is_creator(user):
            del STICKER_PENDING[uid]
            return {"status": "ok"}
        emotion = STICKER_PENDING[uid]
        file_id = msg["sticker"]["file_id"]
        STICKERS[emotion] = file_id
        await save_stickers_to_db()
        idx = STICKER_ORDER.index(emotion)
        if idx + 1 < len(STICKER_ORDER):
            next_em = STICKER_ORDER[idx + 1]
            STICKER_PENDING[uid] = next_em
            num = idx + 2
            await send(cid, f"✅ *{emotion}* сохранён\n\n{num}️⃣ кидай *{next_em}*")
        else:
            del STICKER_PENDING[uid]
            await send(cid, f"🎉 все 4 стикера сохранены!\n\nпроверь: `/showstickers`\nтест: `/sticker happy`")
        return {"status": "ok"}

    # ═══ PROMPT_PENDING (юзер вводит кастомный промпт) ═══
    if text and uid in PROMPT_PENDING and not text.startswith("/"):
        p = PROMPT_PENDING.pop(uid)
        if time.time() - p["ts"] > 300:
            await send(cid, "⏰ время вышло. начни заново через `/settings`")
        else:
            target_cid = p["cid"]
            target_c = chat_data(target_cid)
            target_c["custom_prompt"] = text
            target_c["history"] = []
            await save_chat(target_cid)
            await send(cid,
                f"✅ *системный промпт установлен*\n\n"
                f"длина: `{len(text)}` символов\n"
                f"история чата сброшена\n\n"
                f"бот будет следовать твоему промпту. сбросить через `/settings → Системный промпт → Сбросить`")
        return {"status": "ok"}

    if text.strip().lower() == "/cancel":
        if uid in PROMPT_PENDING:
            del PROMPT_PENDING[uid]
            await send(cid, "ок отменил")
            return {"status": "ok"}
        if uid in STICKER_PENDING:
            del STICKER_PENDING[uid]
            await send(cid, "ок отменил настройку стикеров")
            return {"status": "ok"}

    if text and not text.startswith("/") and s.get("track_chat", True):
        if not (user.get("is_bot") and user.get("username", "").lower() == BOT_USERNAME):
            await log_message(cid, uid, uname, text)
            upd_profile(cid, uid, uname, text)

    if chat_type == "private" and text and has_heart_pending(uid) and not text.startswith("/"):
        p = pop_heart2heart(uid)
        if p:
            tag = "💌 _анонимное послание_" if p["anon"] else f"💌 *от {uname}*"
            ok = await tg("sendMessage", {"chat_id": p["cid"],
                "text": f"{tag} → *{p['spouse_name']}*\n\n_{text}_", "parse_mode": "Markdown"})
            if ok and ok.get("ok"):
                await send(uid, "✅ передал в чат")
                m = is_married(p["cid"], uid)
                if m: m["love"] = min(100, m["love"] + 5); await save_marriages(p["cid"])
            else: await send(uid, "❌ не смог передать")
            return {"status": "ok"}

    is_fwd = (msg.get("sender_chat", {}).get("type") == "channel" and msg.get("is_automatic_forward", False))
    if is_fwd and s.get("comment_posts", True):
        pt = msg.get("text") or msg.get("caption") or ""
        if pt and len(pt) > 5:
            await typing(cid)
            cn = msg["sender_chat"].get("title", "канал")
            cflag = is_creator(user); ffl = is_friend(user)
            raw = await ai.text([{"role": "system", "content": sys_prompt(c, cflag, ffl) +
                "\n\nЗАДАЧА: комментируешь форвард. 1-2 строки БЕЗ восторгов"},
                {"role": "user", "content": f"пост из «{cn}»:\n\n{pt}"}],
                pref=c.get("text_model", DEFAULT_TEXT_MODEL))
            comment = fmt(raw)
            if detect_cringe(comment):
                imp = await ai.anticringe(comment)
                if imp: comment = fmt(imp)
            await tg("sendMessage", {"chat_id": cid, "text": comment,
                "reply_to_message_id": msg.get("message_id"), "parse_mode": "Markdown"})
        return {"status": "ok"}

    if s.get("mute_users") and uid in s.get("muted_list", []): return {"status": "ok"}

    cflag = is_creator(user); ffl = is_friend(user)

    if mentions_creator(text) and not cflag:
        await typing(cid)
        await send(cid, f"эй *{uname}* ты чё на @{CREATOR_USERNAME} наезжаешь?? иди остынь на часик")
        if await is_bot_admin(cid):
            ok, err = await mute_user(cid, uid, 3600)
            if ok:
                await send(cid, f"🔇 *{uname}* в муте на час")
                s.setdefault("muted_list", [])
                if uid not in s["muted_list"]: s["muted_list"].append(uid)
                await save_chat(cid)
            else: await send(cid, f"_(не смог: {err})_")
        return {"status": "ok"}

    cmd, args = parse_cmd(text)

    if not cmd and should_respond(msg, s):
        low_t = text.lower().strip()
        low_t = re.sub(r'\b(ориен|orien|ориенаи|orienai|ориэн|@?orien_ai_bot)\b[,.\s]*', '', low_t).strip()
        if low_t in ("мем", "мемы", "мемчик", "мемас", "memes", "meme") or re.match(r'^(рандом\s+)?мем', low_t):
            await h_meme(cid, uname, text, msg); return {"status": "ok"}

    # ═══════ КОМАНДЫ ═══════

    if cmd in ("/meme", "/мем", "/мемы", "/memes"):
        await h_meme(cid, uname, args, msg); return {"status": "ok"}

    if cmd in ("/testmeme", "/тестмем"):
        await send(cid, "тестирую реддит...")
        meme = await ai.get_reddit_meme(args or "")
        if meme:
            await send(cid, f"✅ нашёл: {meme['title'][:80]}\nurl: {meme['url']}\n\nпробую скачать...")
            img_bytes, ext = await download_image(meme['url'])
            if img_bytes:
                await send(cid, f"✅ скачал {len(img_bytes)//1024}KB ({ext}), отправляю...")
                sent = await send_photo_bytes(cid, img_bytes, "тест", f"test.{ext}")
                if not sent or not sent.get("ok"):
                    await send(cid, f"❌ не отправилось: {sent}")
            else:
                await send(cid, "❌ не скачалось")
        else:
            await send(cid, "❌ не получил мем от реддита")
        return {"status": "ok"}

    # ═══ СТИКЕРЫ ═══
    if cmd in ("/stickerids", "/setstickers"):
        if not cflag:
            await send(cid, "только создатель"); return {"status": "ok"}
        if chat_type != "private":
            await send(cid, f"напиши мне в ЛС: https://t.me/{BOT_USERNAME}"); return {"status": "ok"}
        STICKER_PENDING[uid] = STICKER_ORDER[0]
        await send(cid,
            f"📦 *настройка стикеров*\n\n"
            f"кидай мне стикеры по порядку:\n\n"
            f"1️⃣ *happy* (радостный) — кидай сейчас\n"
            f"2️⃣ *angry* (злой)\n"
            f"3️⃣ *neutral* (спокойный)\n"
            f"4️⃣ *sad* (грустный)\n\n"
            f"пак: {STICKER_PACK_URL}\n\n"
            f"отмена: `/cancel`")
        return {"status": "ok"}

    if cmd == "/showstickers":
        if not STICKERS:
            await send(cid, "стикеров пока нет. сделай `/stickerids`")
            return {"status": "ok"}
        await send(cid, f"📦 *загружено стикеров: {len(STICKERS)}*")
        for emotion, fid in STICKERS.items():
            await send(cid, f"*{emotion}*:")
            await send_sticker(cid, fid)
        return {"status": "ok"}

    if cmd == "/sticker":
        if not args:
            await send(cid, f"*эмоции:* {', '.join(STICKERS.keys()) if STICKERS else 'нет стикеров'}\n\n`/sticker happy`")
            return {"status": "ok"}
        em = args.strip().lower()
        if em in STICKERS:
            await send_sticker(cid, STICKERS[em])
        else:
            await send(cid, f"нет такой эмоции. есть: {', '.join(STICKERS.keys())}")
        return {"status": "ok"}

    if cmd == "/resetstickers":
        if not cflag: await send(cid, "только создатель"); return {"status": "ok"}
        STICKERS.clear()
        await save_stickers_to_db()
        await send(cid, "✅ стикеры сброшены")
        return {"status": "ok"}

    if cmd == "/resetprompt":
        c["custom_prompt"] = None
        await save_chat(cid)
        await send(cid, "✅ системный промпт сброшен")
        return {"status": "ok"}

    if cmd in ("/grant", "/give", "/выдать"):
        if not cflag: await send(cid, "только для создателя"); return {"status": "ok"}
        if not args:
            await send(cid, "*формат:*\n`/grant @user coins=10000 diamonds=50 food=100`\n"
                "`/grant me coins=99999` | `/grant all coins=1000` | reply: `/grant coins=5000`")
            return {"status": "ok"}
        params = {}
        for part in args.split():
            if "=" in part:
                k, v = part.split("=", 1)
                try: params[k.lower()] = int(v)
                except: pass
        if not params: await send(cid, "укажи: `coins=N diamonds=N food=N`"); return {"status": "ok"}
        ca = params.get("coins", 0); da = params.get("diamonds", 0) or params.get("dia", 0); fa = params.get("food", 0)
        targets = []
        ft = args.split()[0].lower()
        if ft == "me": targets.append((cid, uid, uname))
        elif ft == "all":
            for uid_, w in WALLETS.get(cid, {}).items(): targets.append((cid, uid_, w.get("name", "чел")))
            if not targets: targets.append((cid, uid, uname))
        elif rr_msg and rr_msg.get("from"):
            tu = rr_msg["from"]; targets.append((cid, tu["id"], tu.get("first_name", "чел")))
        else:
            mm = re.search(r'@(\w+)', args)
            if mm:
                un = mm.group(1)
                found = CHAT_MEMBERS.get(cid, {}).get(un.lower())
                if found: targets.append((cid, found["id"], found["name"]))
                else:
                    ocid, info = find_user_global(un)
                    if info: targets.append((ocid, info["id"], info["name"]))
                    else: await send(cid, f"не нашёл @{un}"); return {"status": "ok"}
            else: targets.append((cid, uid, uname))
        results = []
        for tcid, tuid, tname in targets:
            if ca: await add_coins(tcid, tuid, ca, tname)
            if da: await add_diamonds(tcid, tuid, da, tname)
            if fa: await add_food(tcid, tuid, fa, tname)
            results.append(tname)
        parts = []
        if ca: parts.append(f"`+{ca}` 🪙")
        if da: parts.append(f"`+{da}` 💎")
        if fa: parts.append(f"`+{fa}` 🍕")
        who = f"*{results[0]}*" if len(results) == 1 else f"*{len(results)}* челам"
        await send(cid, f"🎁 выдал {who}: {', '.join(parts) if parts else 'ничего'}")
        return {"status": "ok"}

    if cmd in ("/mute", "/мут"):
        rr = msg.get("reply_to_message")
        tuid = None; tname = None; tu = None
        if rr and rr.get("from"):
            tu = rr["from"]; tuid = tu["id"]; tname = tu.get("first_name", "чел")
        else:
            mm = re.search(r'@(\w+)', args)
            if mm:
                un = mm.group(1)
                found = CHAT_MEMBERS.get(cid, {}).get(un.lower())
                if found: tuid = found["id"]; tname = found["name"]; tu = {"id": tuid, "username": un}
        if not tuid: await send(cid, "ответь или @username\n\n`/mute @user 1h`"); return {"status": "ok"}
        ta = ""
        if args:
            for p in args.split():
                if not p.startswith("@"): ta = p; break
        if tu and (is_creator(tu) or is_friend(tu)): await send(cid, "не буду мутить своих"); return {"status": "ok"}
        if not await is_bot_admin(cid): await send(cid, "❌ я не админ"); return {"status": "ok"}
        ok, err = await mute_user(cid, tuid, parse_duration(ta))
        if ok:
            sec = parse_duration(ta); mins = sec // 60
            ts = f"{mins//60}ч {mins%60}м" if mins >= 60 else f"{mins}м" if mins else f"{sec}с"
            await send(cid, f"🔇 *{tname}* в муте на *{ts}*")
            s.setdefault("muted_list", [])
            if tuid not in s["muted_list"]: s["muted_list"].append(tuid)
            await save_chat(cid)
        else: await send(cid, f"❌ не вышло: _{err}_")
        return {"status": "ok"}

    if cmd in ("/unmute", "/размут"):
        rr = msg.get("reply_to_message")
        tuid = None; tname = None
        if rr and rr.get("from"): tuid = rr["from"]["id"]; tname = rr["from"].get("first_name", "чел")
        else:
            mm = re.search(r'@(\w+)', args)
            if mm:
                found = CHAT_MEMBERS.get(cid, {}).get(mm.group(1).lower())
                if found: tuid = found["id"]; tname = found["name"]
        if not tuid: await send(cid, "ответь или @username"); return {"status": "ok"}
        if not await is_bot_admin(cid): await send(cid, "❌ я не админ"); return {"status": "ok"}
        if await unmute_user(cid, tuid):
            if tuid in s.get("muted_list", []): s["muted_list"].remove(tuid); await save_chat(cid)
            await send(cid, f"🔊 *{tname}* размучен")
        else: await send(cid, "не вышло")
        return {"status": "ok"}

    if cmd == "/settings":
        await send(cid, "⚙️ *настройки бота*", settings_kb(s, bool(c.get("custom_prompt"))))
        return {"status": "ok"}

    if cmd == "/imgmodel":
        if not args:
            cur = c.get("image_model", DEFAULT_IMAGE_MODEL)
            lines = [f"щас *{cur}*", ""] + [f"{'👉' if k == cur else '  '} `/imgmodel {k}` — {v}" for k, v in IMG_MODELS.items()]
            await send(cid, "\n".join(lines)); return {"status": "ok"}
        mk = args.split()[0].lower()
        if mk not in IMG_MODELS: await send(cid, f"нет, есть: `{'`, `'.join(IMG_MODELS)}`"); return {"status": "ok"}
        c["image_model"] = mk; await save_chat(cid); await send(cid, f"ок *{mk}*"); return {"status": "ok"}

    if cmd in ("/img", "/image"):
        if not args: await send(cid, "пиши `/img описание`"); return {"status": "ok"}
        await h_image(cid, uname, args, msg, cflag, ffl); return {"status": "ok"}

    if cmd == "/me":
        await upload_photo_action(cid)
        try:
            ep = await ai.enhance_prompt("OrienAI портрет аниме парень", True, memify=True)
            url = await ai.gen_image(ep, c.get("image_model", DEFAULT_IMAGE_MODEL))
            await send_photo(cid, url, "вот это я")
        except: await send(cid, "не вышло")
        return {"status": "ok"}

    if cmd in ("/vision", "/see", "/посмотри"):
        await h_vision(cid, uname, args, msg, cflag, ffl); return {"status": "ok"}

    if cmd in ("/yt", "/youtube", "/video"):
        if not args: await send(cid, "пиши `/yt запрос`"); return {"status": "ok"}
        await h_yt_search(cid, args, msg); return {"status": "ok"}

    if cmd in ("/ytdl", "/dl"):
        if not args: await send(cid, "`/ytdl ссылка`"); return {"status": "ok"}
        await h_yt_dl(cid, args, msg); return {"status": "ok"}

    if cmd == "/analyze": await h_code(cid, args, msg, c); return {"status": "ok"}

    if cmd == "/task":
        if not args:
            ts = c.get("tasks", [])
            await send(cid, ("📋 *задачи:*\n" + "\n".join(f"{i}. {t}" for i,t in enumerate(ts,1)) +
                "\n\n`/task add ...` | `/task clear`") if ts else "пусто\n`/task add описание`")
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
        await send(cid, f"у *{tn}* нет авы"); return {"status": "ok"}

    if cmd == "/profile":
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if tuid is None: tuid, tname = uid, uname
        pr = PROFILES.get(cid, {}).get(tuid)
        if pr and pr.get("messages"):
            await typing(cid)
            desc = fmt(await ai.text([{"role": "system", "content":
                "опиши характер чела по сообщениям. 2-3 строки маленькими без точек с лёгким сарказмом\n"
                "конкретно: темы, манера, настроение. БЕЗ 'имба база жиза круто'\n*жирный* для черт"},
                {"role": "user", "content": f"чел: {tname}\nсообщения:\n" + "\n".join(pr["messages"][-15:])}],
                pref="primary", temperature=0.7))
            pr["desc"] = desc
            await send(cid, f"👤 *{tname}*:\n{desc}")
        else: await send(cid, f"мало данных по *{tname}*")
        return {"status": "ok"}

    if cmd == "/provider":
        if not args:
            cur = c.get("text_model", DEFAULT_TEXT_MODEL)
            lines = [f"щас *{cur}*", ""] + [f"{'👉' if mk==cur else '  '} `/provider {sn}`{' 👁' if TEXT_MODELS[mk].vision else ''}" for sn,mk in PROV_MAP.items()]
            await send(cid, "\n".join(lines)); return {"status": "ok"}
        pn = args.split()[0].lower()
        if pn not in PROV_MAP: await send(cid, f"нет: `{'`, `'.join(PROV_MAP)}`"); return {"status": "ok"}
        c["text_model"] = PROV_MAP[pn]; await save_chat(cid); await send(cid, f"го *{pn}*"); return {"status": "ok"}

    if cmd == "/mood":
        ma = args.split()[0].lower() if args else ""
        if ma in MOODS:
            c["mood"] = ma; await save_chat(cid)
            await send(cid, {"chill":"на чилле","agro":"завали ебало щас злой",
                "nerd":"мозги по полной","senior":"режим деда"}[ma])
        else: await send(cid, "выбирай: `chill agro nerd senior`")
        return {"status": "ok"}

    if cmd == "/reset": c["history"] = []; await save_chat(cid); await send(cid, "забыл всё"); return {"status": "ok"}

    if cmd == "/clearlog":
        if not cflag: await send(cid, "только создатель"); return {"status": "ok"}
        CHAT_LOG[cid] = []
        if DB is not None:
            try: await DB.chatlog.delete_one({"chat_id": cid})
            except: pass
        await send(cid, "лог очищен"); return {"status": "ok"}

    if cmd == "/status":
        lines = [f"текст: *{c.get('text_model',DEFAULT_TEXT_MODEL)}*",
            f"картинки: *{c.get('image_model',DEFAULT_IMAGE_MODEL)}*",
            f"настрой: *{c.get('mood','chill')}*", f"стиль: *{s.get('style','хам')}*",
            f"кастом промпт: {'✅' if c.get('custom_prompt') else '❌'}",
            f"мат: {'✅' if s.get('allow_swear') else '❌'}",
            f"анализ чата: {'✅' if s.get('track_chat', True) else '❌'}",
            f"умные команды: {'✅' if s.get('smart_intent', True) else '❌'}",
            f"стикеров: *{len(STICKERS)}/4*",
            f"в логе: *{len(CHAT_LOG.get(cid, []))}*",
            f"бд: {'✅' if DB is not None else '❌'}", f"PIL: {'✅' if HAS_PIL else '❌'}",
            "", "*провайдеры:*"] + [f"{'✅' if not st.disabled else '❌'} `{p.value}`" for p,st in PROV_STATUS.items()]
        await send(cid, "\n".join(lines)); return {"status": "ok"}

    if cmd in ("/creator", "/owner"):
        fr = "\n".join(f"🤝 @{k}" for k in FRIENDS)
        await send(cid, f"мой создатель: @{CREATOR_USERNAME}\n\nкенты:\n{fr}"); return {"status": "ok"}

    if cmd in ("/wallet", "/balance", "/bal", "/кошелек"):
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if tuid is None: tuid, tname = uid, uname
        if tuid:
            w = get_wallet(cid, tuid, tname or "чел")
            sp = get_spouse_id(cid, tuid); sp_n = ""
            if sp:
                m = is_married(cid, tuid)
                sp_n = m["u2_name"] if m["u1"] == tuid else m["u1_name"]
            out = (f"💼 *кошелёк {w['name']}*\n\n🪙 коинов: *{w['coins']}*\n💎 брилликов: *{w['diamonds']}*\n"
                f"🍕 еды: *{w['food']}*\n📋 квестов: *{w['quests_done']}*\n🔥 стрик: *{w['farm_streak']}*")
            if sp_n: out += f"\n💍 в браке с *{sp_n}*"
            await send(cid, out)
        else: await send(cid, "не нашёл")
        return {"status": "ok"}

    if cmd in ("/farm", "/ферма"): _, t = await farm(cid, uid, uname); await send(cid, t); return {"status": "ok"}
    if cmd in ("/quest", "/квест"): _, t = await quest(cid, uid, uname); await send(cid, t); return {"status": "ok"}
    if cmd in ("/daily", "/дейли"): _, t = await daily(cid, uid, uname); await send(cid, t); return {"status": "ok"}
    if cmd in ("/dice", "/кубики"):
        try: bet = int(args.split()[0]) if args else 50
        except: await send(cid, "`/dice 100`"); return {"status": "ok"}
        _, t = await dice_game(cid, uid, bet); await send(cid, t); return {"status": "ok"}

    if cmd in ("/top", "/лидерборд"):
        ws = WALLETS.get(cid, {})
        if not ws: await send(cid, "нет данных"); return {"status": "ok"}
        sw = sorted(ws.items(), key=lambda x: x[1]["coins"], reverse=True)[:10]
        lines = ["🏆 *ТОП БОГАЧЕЙ*\n"]
        for i, (_, w) in enumerate(sw, 1):
            m = ["🥇","🥈","🥉"][i-1] if i <= 3 else f"{i}."
            lines.append(f"{m} *{w['name']}* — `{w['coins']}` 🪙")
        await send(cid, "\n".join(lines)); return {"status": "ok"}

    if cmd in ("/brak", "/marry", "/брак"):
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if not tuid: await send(cid, "укажи: `/brak @user`"); return {"status": "ok"}
        t, kb = propose(cid, uid, uname, tuid, tname); await send(cid, t, kb=kb); return {"status": "ok"}

    if cmd in ("/yes", "/да", "/согласна", "/согласен"):
        _, t = await accept_proposal(cid, uid, uname); await send(cid, t); return {"status": "ok"}
    if cmd in ("/no", "/нет", "/отказ"): await send(cid, reject_proposal(cid, uid, uname)); return {"status": "ok"}
    if cmd in ("/divorce", "/развод"): await send(cid, await divorce(cid, uid, uname)); return {"status": "ok"}
    if cmd in ("/marriages", "/браки"):
        t = all_marriages(cid); await send(cid, t or "никто не женат"); return {"status": "ok"}

    if cmd in ("/gift", "/подарок"):
        if not args:
            await send(cid, "🎁 *подарки:*\n\n`/gift food` 🍕 (30🪙) +5\n`/gift flowers` 💐 (50🪙) +10\n"
                "`/gift diamond` 💎 (1💎) +25\n`/gift ring` 💍 (200🪙) +20\n`/gift car` 🚗 (1000🪙) +50")
            return {"status": "ok"}
        await send(cid, await gift_to_spouse(cid, uid, uname, args.split()[0].lower())); return {"status": "ok"}

    if cmd in ("/sharefood", "/поделиться"): await send(cid, await share_food(cid, uid, uname)); return {"status": "ok"}
    if cmd in ("/surprise", "/сюрприз"): await send(cid, await surprise(cid, uid, uname)); return {"status": "ok"}

    if cmd in ("/heart2heart", "/душа", "/dusha", "/h2h"):
        sp_id, sp_name = get_spouse_info(cid, uid)
        if not sp_id: await send(cid, "ты не в браке"); return {"status": "ok"}
        anon = args.strip().lower() in ("anon", "анон", "анонимно")
        if chat_type == "private":
            start_heart2heart(uid, cid, sp_id, sp_name, anon=anon)
            mode = "анонимно" if anon else "от твоего имени"
            await send(cid, f"💌 ок напиши след сообщение — передам *{sp_name}* ({mode})\n10 минут")
        else:
            kb = {"inline_keyboard": [[
                {"text": "💌 написать в ЛС", "callback_data": "h2h:open"},
                {"text": "🎭 анонимно", "callback_data": "h2h:anon"}],
                [{"text": "↗️ открыть бота", "url": f"https://t.me/{BOT_USERNAME}"}]]}
            await send(cid, f"💌 *{uname}* хочет поговорить с *{sp_name}*\n\nнажми → ЛС → передам", kb=kb)
        return {"status": "ok"}

    if cmd == "/roast":
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if not tname: await send(cid, "укажи: `/roast @user`"); return {"status": "ok"}
        tu = {"id": tuid, "username": ""}
        if tuid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == tuid: tu["username"] = un; break
        if is_creator(tu) or is_friend(tu):
            await send(cid, f"🔥 *{tname}*:\nне буду жарить свой норм чел"); return {"status": "ok"}
        pr = PROFILES.get(cid, {}).get(tuid, {}) if tuid else {}
        ms = "\n".join(pr.get("messages", [])[-10:]) if pr else "нет данных"
        await typing(cid)
        r = await ai.text([{"role": "system", "content":
            "прожарь чела по-доброму но колко по его сообщениям\n2-3 строки маленькими без точек\n"
            "по делу а не общими оскорблениями\nБЕЗ 'ору жиза база имба круто'\n"
            "*жирный* для подколов\nНЕ начинай с 'ну' 'хах' 'оо'"},
            {"role": "user", "content": f"чел: {tname}\nсообщения:\n{ms}"}], pref="primary", temperature=0.9)
        await send(cid, f"🔥 *{tname}*:\n\n{fmt(r)}"); return {"status": "ok"}

    if cmd == "/ship":
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if not tname: await send(cid, "укажи: `/ship @user`"); return {"status": "ok"}
        cp = random.randint(0, 100)
        sn = (uname[:max(1,len(uname)//2)] + tname[len(tname)//2:]).lower()
        bar = "❤️"*(cp//10) + "🤍"*(10-cp//10)
        await send(cid, f"💘 *{uname}* + *{tname}* = `{sn}`\n\n*{cp}%*\n{bar}\n\n{random.choice(SHIP_R)}")
        return {"status": "ok"}

    if cmd in ("/8ball", "/ball", "/шар"):
        if not args: await send(cid, "`/8ball вопрос`"); return {"status": "ok"}
        await send(cid, f"🎱 {args}\n\n*{random.choice(BALL_A)}*"); return {"status": "ok"}

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
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if tuid is None and not args and not msg.get("reply_to_message"): tuid, tname = uid, uname
        tu = {"id": tuid, "username": ""}
        if tuid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == tuid: tu["username"] = un; break
        tn = tname or uname
        if is_creator(tu): iq = random.randint(150, 200); cm = "норм мозги"
        elif is_friend(tu): iq = random.randint(130, 180); cm = "умный"
        else:
            iq = random.randint(20, 200)
            cm = "амёба" if iq < 50 else "такое" if iq < 80 else "средне" if iq < 100 else "норм" if iq < 130 else "умник" if iq < 170 else "эйнштейн"
        await send(cid, f"🧠 *{tn}*: `{iq}`\n\n_{cm}_"); return {"status": "ok"}

    if cmd == "/vibe":
        v = random.choice(["🌈 имба","💀 трэш","🔥 огонь","😴 скучно","🎉 пати","🌧 депрессия","⚡ электрика","🍕 жрать"])
        await send(cid, f"вайб чата: *{v}*\nсила: `{random.randint(50,100)}%`"); return {"status": "ok"}

    if cmd in ("/gay", "/гей"):
        tuid, tname = extract_target(args, msg.get("reply_to_message"), cid)
        if tuid is None and not args and not msg.get("reply_to_message"): tuid, tname = uid, uname
        tu = {"id": tuid, "username": ""}
        if tuid:
            for un, info in CHAT_MEMBERS.get(cid, {}).items():
                if info["id"] == tuid: tu["username"] = un; break
        tn = tname or uname
        if is_creator(tu): p = random.randint(0, 15); cm = "норм"
        elif is_friend(tu): p = random.randint(0, 20); cm = "ок"
        else:
            p = random.randint(0, 100)
            cm = "ну ок" if p < 50 else "пиздец" if p > 90 else "норм"
        await send(cid, f"🌈 *{tn}*\n\n*{p}%*\n{'🏳️‍🌈'*(p//10)}{'⬛'*(10-p//10)}\n\n_{cm}_")
        return {"status": "ok"}

    if cmd in ("/compliment", "/комплимент"):
        _, tname = extract_target(args, msg.get("reply_to_message"), cid)
        await send(cid, f"для *{tname or uname}*: {random.choice(COMPLIMENTS)}"); return {"status": "ok"}

    if cmd == "/fact":
        await typing(cid)
        await send(cid, f"💡 *факт про чат:*\n\n{await generate_chat_fact(cid)}"); return {"status": "ok"}

    if cmd in ("/quote", "/цитата"):
        await typing(cid)
        q = await ai.text([{"role": "system", "content":
            "короткая дерзкая цитата про код жизнь работу\n1-2 строки маленькими без точек\n"
            "остроумно а не банально\nБЕЗ молодёжного сленга\n"
            "примеры:\n- 'код работает никто не знает почему не трогай'\n- 'лучший код это тот который ты не написал'"},
            {"role": "user", "content": "цитату"}], pref="primary", temperature=0.9)
        await send(cid, f"💬 «_{fmt(q)}_»\n\n— *OrienAI*"); return {"status": "ok"}

    if cmd == "/help":
        await send(cid, """⚡ *OrienAI v7.3*

🧠 *умные команды* (просто пиши с обращением)
- "ориен сделай картинку кота"
- "ориен сгенери дракона"
- "ориен дай мем"
- "ориен посмотри что на фото" (+ картинка)
- "ориен найди видео про X"
- "ориен глянь код" (+ код)

💬 *команды:*

картинки: `/img X` `/me` `/imgmodel` `/getava` `/vision`
мемы: `мем` или `/meme` `/testmeme`
ютуб: `/yt /ytdl`
код: `/analyze /task`
юзеры: `/profile @u` `/mute @u 1h` `/unmute` `/creator`
экономика: `/wallet` `/farm` `/quest` `/daily` `/dice 100` `/top`
браки: `/brak @u /yes /no /divorce /marriages /gift /sharefood /surprise /heart2heart`
фан: `/roast /ship /8ball /random /coin /choose /iq /vibe /gay /compliment /fact /quote`
стикеры: `/stickerids /showstickers /sticker happy /resetstickers`
настройки: `/provider /mood /settings /reset /status /resetprompt`

🆕 v7.3:
- стикеры из твоего пака (по эмоциям)
- кастомный системный промпт в `/settings`
- мемы через скачивание""")
        return {"status": "ok"}

    if cmd == "/start":
        await send(cid, f"оо здарова *{uname.lower()}* я *orienai v7*\nпиши `/help` или просто общайся")
        return {"status": "ok"}

    if cmd is not None: return {"status": "ok"}

    # ═══ ОТВЕТ ПРИ ОБРАЩЕНИИ ═══
    if should_respond(msg, s):
        has_img = await extract_img(msg) is not None

        if s.get("smart_intent", True) and text:
            clean_text = re.sub(r'\b(ориен|orien|ориенаи|orienai|ориэн|@?orien_ai_bot)\b[,.\s]*',
                '', text, flags=re.IGNORECASE).strip()
            if not clean_text and has_img: clean_text = "опиши что на картинке"

            if clean_text or has_img:
                intent_data = quick_intent(text, has_img)
                if intent_data:
                    print(f"⚡ quick: {intent_data['intent']} | {intent_data['query'][:80]}")
                else:
                    try: intent_data = await ai.detect_intent(clean_text or "посмотри картинку", has_img)
                    except Exception as e:
                        print(f"⚠ intent: {e}"); intent_data = {"intent": "chat", "query": clean_text}

                intent = intent_data.get("intent", "chat")
                query = intent_data.get("query", clean_text)

                if intent == "image": await h_image(cid, uname, query, msg, cflag, ffl); return {"status": "ok"}
                elif intent == "meme": await h_meme(cid, uname, query, msg); return {"status": "ok"}
                elif intent == "vision": await h_vision(cid, uname, query, msg, cflag, ffl); return {"status": "ok"}
                elif intent == "yt_search": await h_yt_search(cid, query, msg); return {"status": "ok"}
                elif intent == "yt_download": await h_yt_dl(cid, query, msg); return {"status": "ok"}
                elif intent == "code_analyze": await h_code(cid, query, msg, c); return {"status": "ok"}

        await typing(cid)
        img = await extract_img(msg)
        try:
            at = await ai_response(cid, uname, text, img, cflag, ffl)
            await send_with_sticker(cid, at)
        except Exception as e:
            print(f"❌ ai_response: {e}")
            await send(cid, f"чёт сломался: _{str(e)[:100]}_")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "alive", "version": "7.3", "db": "connected" if DB is not None else "off",
            "pil": HAS_PIL, "log_size": sum(len(v) for v in CHAT_LOG.values()),
            "stickers": len(STICKERS)}

@app.get("/health")
async def health():
    return {"ok": True, "db": DB is not None, "pil": HAS_PIL,
            "log_chats": len(CHAT_LOG), "tracked_msgs": sum(len(v) for v in CHAT_LOG.values()),
            "stickers": len(STICKERS)}
