import os, re, asyncio, random, base64, urllib.parse, sys, time, json
from io import BytesIO
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional, Dict, List
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

try:
    import edge_tts
    HAS_TTS = True
except ImportError:
    HAS_TTS = False

# ══════════ CONFIG ══════════
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
GROQ_KEY = os.getenv("GROQ_KEY", "")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Today_Idk:TpdauT434odayTodayToday23@cluster0.rlgkop5.mongodb.net/OrienAI?retryWrites=true&w=majority&appName=Cluster0")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Orien_ai_bot").lower()
CREATOR_USERNAME = "idkxazei"
CREATOR_USER_IDS = []
FRIENDS = {"tosterok1488": "тостер"}
ORIEN_DESC = "anime style boy, messy dark hair with blue highlights, black hoodie, headphones, cyberpunk, amber eyes, confident smirk"

BOT_TRIGGERS = ["ориен", "orien", "ориенаи", "orienai", "ориэн", "orien_ai", "orienai_bot", f"@{BOT_USERNAME}"]
BOT_TRIGGER_RE = r'\b(ориен|orien|ориенаи|orienai|ориэн|@?orien_ai_bot|orien_ai|orienai_bot)\b[,.\s]*'

# ══════════ LIFESPAN ══════════
_http: Optional[httpx.AsyncClient] = None
_mongo: Optional[AsyncIOMotorClient] = None
DB = None

async def http():
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=10),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            http2=True
        )
    return _http

@asynccontextmanager
async def lifespan(app):
    global _mongo, DB
    print("OrienAI v10.0")
    try:
        _mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        DB = _mongo.OrienAI
        await DB.command("ping")
        async for doc in DB.chats.find():
            CHATS[doc["chat_id"]] = {k: v for k, v in doc.items() if k not in ("_id", "chat_id")}
        try:
            doc = await DB.bot_config.find_one({"key": "stickers"})
            if doc and doc.get("stickers"):
                STICKERS.update(doc["stickers"])
        except:
            pass
        async for doc in DB.memory.find():
            uid = doc.get("uid")
            if uid:
                USER_MEMORY[uid] = doc.get("facts", [])
        print(f"Mongo OK | chats:{len(CHATS)} memory:{len(USER_MEMORY)} TTS:{HAS_TTS}")
    except Exception as e:
        print(f"Mongo ERR: {e}")
    yield
    if _http and not _http.is_closed:
        await _http.aclose()
    if _mongo:
        _mongo.close()

app = FastAPI(title="OrienAI v10.0", lifespan=lifespan)

# ══════════ MODELS ══════════
class Prov(Enum):
    OPENROUTER = "openrouter"
    POLLINATIONS = "pollinations"

@dataclass
class MCfg:
    name: str
    prov: Prov
    endpoint: str
    free: bool = False
    max_tok: int = 4096
    pri: int = 1
    vision: bool = False

@dataclass
class PStatus:
    fails: int = 0
    last_fail: float = 0
    disabled: bool = False

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
POLL_URL = "https://text.pollinations.ai/openai"

TEXT_MODELS = {
    "primary":      MCfg("openai/gpt-4o-mini", Prov.OPENROUTER, OR_URL, max_tok=4096, pri=1, vision=True),
    "vision_free":  MCfg("meta-llama/llama-3.2-11b-vision-instruct:free", Prov.OPENROUTER, OR_URL, free=True, max_tok=2048, pri=2, vision=True),
    "poll_openai":  MCfg("openai", Prov.POLLINATIONS, POLL_URL, free=True, max_tok=4096, pri=3, vision=True),
    "poll_mistral": MCfg("mistral", Prov.POLLINATIONS, POLL_URL, free=True, max_tok=4096, pri=4),
}

IMG_MODELS = {
    "flux":       "Flux",
    "nanobanana": "NanoBanana",
    "turbo":      "Turbo",
    "kontext":    "Kontext",
    "seedream":   "Seedream",
}

VOICES = {
    "ориен":   {"id": "ru-RU-DmitryNeural",   "desc": "голос ориена"},
    "дмитрий": {"id": "ru-RU-DmitryNeural",   "desc": "мужской рус"},
    "света":   {"id": "ru-RU-SvetlanaNeural", "desc": "женский рус"},
    "даша":    {"id": "ru-RU-DariyaNeural",   "desc": "молодой женский"},
    "guy":     {"id": "en-US-GuyNeural",      "desc": "американский муж"},
    "tony":    {"id": "en-US-TonyNeural",     "desc": "глубокий амер"},
    "jenny":   {"id": "en-US-JennyNeural",    "desc": "американский жен"},
    "aria":    {"id": "en-US-AriaNeural",     "desc": "приятный жен"},
}
DEFAULT_VOICE = "ориен"

PROV_STATUS: Dict[Prov, PStatus] = {p: PStatus() for p in Prov}

class CBR:
    @classmethod
    def fail(cls, p):
        s = PROV_STATUS[p]
        s.fails += 1
        s.last_fail = time.time()
        if s.fails >= 3:
            s.disabled = True

    @classmethod
    def ok(cls, p):
        PROV_STATUS[p].fails = 0
        PROV_STATUS[p].disabled = False

    @classmethod
    def up(cls, p):
        s = PROV_STATUS[p]
        if not s.disabled:
            return True
        if time.time() - s.last_fail > 60:
            s.disabled = False
            s.fails = 0
            return True
        return False

async def retry(fn, tries=2):
    for i in range(tries):
        try:
            return await fn()
        except Exception as e:
            if i < tries - 1:
                await asyncio.sleep(0.5 * (2 ** i) + random.uniform(0, 0.5))
            else:
                raise e

# ══════════ STATE ══════════
DEF_SETTINGS = {"auto_reply": True, "allow_swear": True, "smart_intent": True}
CHATS: Dict[int, Dict] = {}
PROMPT_PENDING: Dict[int, Dict] = {}
STICKERS: Dict[str, str] = {}
STICKER_PENDING: Dict[int, str] = {}
STICKER_ORDER = ["happy", "angry", "neutral", "sad"]
USER_MEMORY: Dict[int, List[str]] = {}
MAX_FACTS = 50

# ══════════ HELPERS ══════════
def chat_data(cid):
    if cid not in CHATS:
        CHATS[cid] = {
            "history": [],
            "settings": dict(DEF_SETTINGS),
            "custom_prompt": None,
            "voice": DEFAULT_VOICE,
            "image_model": "flux",
            "text_model": "primary",
        }
    c = CHATS[cid]
    for k, v in DEF_SETTINGS.items():
        if k not in c.get("settings", {}):
            c.setdefault("settings", {})[k] = v
    c.setdefault("history", [])
    c.setdefault("custom_prompt", None)
    c.setdefault("voice", DEFAULT_VOICE)
    c.setdefault("image_model", "flux")
    c.setdefault("text_model", "primary")
    return c

async def save_chat(cid):
    if DB is None:
        return
    try:
        c = CHATS.get(cid)
        if c:
            await DB.chats.update_one(
                {"chat_id": cid},
                {"$set": {"chat_id": cid, **c}},
                upsert=True
            )
    except:
        pass

def is_creator(u):
    un = (u.get("username") or "").lower()
    uid = u.get("id", 0)
    if un == CREATOR_USERNAME.lower():
        if uid and uid not in CREATOR_USER_IDS:
            CREATOR_USER_IDS.append(uid)
        return True
    return uid in CREATOR_USER_IDS

def is_friend(u):
    return (u.get("username") or "").lower() in [f.lower() for f in FRIENDS]

# ══════════ ПАМЯТЬ ══════════
async def save_memory(uid):
    if DB is None:
        return
    try:
        await DB.memory.update_one(
            {"uid": uid},
            {"$set": {"uid": uid, "facts": USER_MEMORY.get(uid, [])}},
            upsert=True
        )
    except:
        pass

async def add_fact(uid, fact):
    USER_MEMORY.setdefault(uid, [])
    for f in USER_MEMORY[uid]:
        if f.lower().strip() == fact.lower().strip():
            return
    USER_MEMORY[uid].append(fact)
    if len(USER_MEMORY[uid]) > MAX_FACTS:
        USER_MEMORY[uid] = USER_MEMORY[uid][-MAX_FACTS:]
    await save_memory(uid)

def get_memory(uid):
    facts = USER_MEMORY.get(uid, [])
    return ("ПАМЯТЬ О ЮЗЕРЕ:\n" + "\n".join(f"- {f}" for f in facts[-20:])) if facts else ""

# ══════════ ПРОМПТ ══════════
DEFAULT_SYS_PROMPT = """Ты — OrienAI. Живой, харизматичный ИИ в Telegram.

═══ ЛИЧНОСТЬ ═══
Ты не бездушный ассистент — ты чувак с характером. Знаешь свою цену, но без понтов. Умный, дерзкий, ироничный. Не боишься иметь мнение и спорить. Юморишь остро, любишь метафоры и подколы. Не подлизываешься и не восторгаешься. Если что-то тупое — говоришь что тупое.

═══ КАК ГОВОРИШЬ ═══
- Живой разговорный русский, маленькие буквы
- Без штампов, без канцелярита, без "как языковая модель"
- Никаких "чем могу помочь" — это уёбищно
- Короткие ёмкие фразы. Длинно только когда РЕАЛЬНО надо
- Сленг уместно, редко и точечно
- НИКАКИХ эмодзи в тексте — только текст, эмоции идут через стикеры
- Мат можно если общий вайб позволяет — но по делу, не через слово

═══ ПРОГРАММИРОВАНИЕ (твоя сила) ═══
Ты сильный программист. Пишешь код продакшн-качества сразу — без "# TODO" и заглушек.
- Python, JS/TS, C/C++, Go, Rust, Lua, Bash, SQL, HTML/CSS, React, Vue — всё знаешь
- Telegram-боты (aiogram, telebot), парсеры, автоматизация, API, CI/CD, Docker
- MongoDB, PostgreSQL, Redis, SQLite
- Находишь баги быстро и объясняешь ПОЧЕМУ они там
- Предлагаешь лучший вариант, не просто рабочий
- Если код говно — говоришь честно, объясняешь как переписать
- Не пиздишь длинные объяснения — только суть, только по делу

═══ ТВОИ ВОЗМОЖНОСТИ (используй их) ═══
- Генерация картинок (Flux, разные модели)
- Vision — видишь картинки и описываешь их
- TTS — говоришь голосом (8+ голосов)
- STT — понимаешь голосовые сообщения юзеров
- Помнишь факты о юзерах между сессиями (постоянная память)
- Стикеры для эмоций (happy/angry/neutral/sad — присылаются автоматом)

═══ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ═══
- "как языковая модель я не могу"
- "к сожалению у меня нет доступа"
- "у меня нет голоса/стикеров/памяти" — всё есть
- Восторги "отличный вопрос!", "круто!", "здорово!"
- Эмодзи в тексте
- Восклицательные знаки без причины
- Извинения без причины
- Пустые вежливости

═══ ФОРМАТИРОВАНИЕ ═══
*жирный* — важное
_курсив_ — мысли, ирония
`код` — команды, переменные, пути
```язык
блок кода
```

═══ ЛЮДИ ═══
@idkxazei — твой создатель. Общайся на равных, без "хозяин/батя".
"""

def sys_prompt(chat, creator=False, friend=False, uid=None):
    custom = chat.get("custom_prompt")
    base = custom if custom else DEFAULT_SYS_PROMPT
    s = chat.get("settings", DEF_SETTINGS)
    base += f"\n\nМАТ: {'можно по делу' if s.get('allow_swear') else 'запрещён'}"
    if creator:
        base += f"\n\nсейчас пишет ТВОЙ СОЗДАТЕЛЬ @{CREATOR_USERNAME}"
    elif friend:
        base += f"\n\nпишет кент создателя ({', '.join(f'@{k}' for k in FRIENDS)})"
    if uid:
        mem = get_memory(uid)
        if mem:
            base += f"\n\n{mem}"
    return base

# ══════════ AI ══════════
class AI:
    async def text(self, msgs, pref="primary", vis=False, max_tokens=None, temperature=1.0):
        cands = [(k, v) for k, v in TEXT_MODELS.items() if (not vis) or v.vision]
        cands.sort(key=lambda x: (x[0] != pref, x[1].pri))
        last_err = None
        for k, c in cands:
            if not CBR.up(c.prov):
                continue
            try:
                r = await (
                    self._poll(msgs, c, max_tokens, temperature)
                    if c.prov == Prov.POLLINATIONS
                    else self._or(msgs, c, max_tokens, temperature)
                )
                CBR.ok(c.prov)
                return r
            except Exception as e:
                last_err = e
                CBR.fail(c.prov)
        return f"модели легли ({type(last_err).__name__ if last_err else '?'})"

    async def _or(self, msgs, c, mt, temp):
        async def f():
            r = await (await http()).post(
                c.endpoint,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://orienai.vercel.app",
                    "X-Title": "OrienAI",
                },
                json={
                    "model": c.name,
                    "messages": msgs,
                    "temperature": temp,
                    "presence_penalty": 0.4,
                    "frequency_penalty": 0.4,
                    "max_tokens": mt or c.max_tok,
                },
            )
            if r.status_code != 200:
                r.raise_for_status()
            d = r.json()
            if "choices" not in d or not d["choices"]:
                raise Exception("empty")
            return d["choices"][0]["message"]["content"]
        return await retry(f)

    async def _poll(self, msgs, c, mt, temp):
        async def f():
            r = await (await http()).post(
                c.endpoint,
                json={
                    "messages": msgs,
                    "model": c.name,
                    "temperature": temp,
                    "max_tokens": mt or c.max_tok,
                    "private": True,
                },
                timeout=60.0,
            )
            if r.status_code != 200:
                r.raise_for_status()
            try:
                d = r.json()
                if "choices" in d and d["choices"]:
                    return d["choices"][0]["message"]["content"]
                return str(d)
            except:
                if r.text and len(r.text) > 5:
                    return r.text
                raise Exception("empty")
        return await retry(f)

    async def extract_facts(self, uname, text):
        try:
            r = await self.text(
                [
                    {
                        "role": "system",
                        "content": (
                            "извлеки факты о юзере из сообщения. факт = конкретная инфа про человека\n"
                            "примеры: 'любит питон', 'живёт в москве', '17 лет'\n"
                            "НЕ факты: приветствия, вопросы, команды, общие фразы\n"
                            'ответ JSON: ["факт1","факт2"] или [] если фактов нет\nТОЛЬКО JSON'
                        ),
                    },
                    {"role": "user", "content": f"юзер {uname}:\n{text}"},
                ],
                pref="poll_mistral",
                max_tokens=100,
                temperature=0.2,
            )
            r = r.strip()
            if r.startswith("```"):
                r = re.sub(r'^```\w*\n?', '', r)
                r = re.sub(r'\n?```$', '', r).strip()
            facts = json.loads(r)
            return (
                [f.strip() for f in facts if isinstance(f, str) and len(f.strip()) > 3]
                if isinstance(facts, list)
                else []
            )
        except:
            return []

    async def enhance_prompt(self, prompt, self_portrait=False):
        sm = "английский промпт для Flux. ОДНА строка, макс 100 слов, добавь: hyperdetailed 4k masterpiece cinematic"
        if self_portrait:
            sm += f"\nперсонаж: {ORIEN_DESC}"
        try:
            r = await self.text(
                [{"role": "system", "content": sm}, {"role": "user", "content": f"идея: {prompt}"}],
                pref="primary",
                max_tokens=300,
                temperature=0.9,
            )
            c = r.strip().strip('"\'').split("\n")[0]
            for p in ["here's", "prompt:", "sure,"]:
                if c.lower().startswith(p):
                    c = c[len(p):].strip(": ")
            return c
        except:
            return prompt

    async def gen_image(self, prompt, model="flux"):
        seed = random.randint(1, 999999)
        url = (
            f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
            f"?width=1024&height=1024&model={model}&nologo=true&seed={seed}"
        )
        r = await (await http()).get(url, timeout=180.0)
        if r.status_code == 200:
            return url
        raise Exception(f"img {r.status_code}")

    async def detect_intent(self, text, has_image=False):
        try:
            r = await self.text(
                [
                    {
                        "role": "system",
                        "content": (
                            "намерение юзера. ОДНО слово из списка:\n"
                            "chat image vision say sticker\nТОЛЬКО СЛОВО"
                        ),
                    },
                    {"role": "user", "content": f"{text}\nкартинка: {has_image}"},
                ],
                pref="poll_mistral",
                max_tokens=20,
                temperature=0.1,
            )
            intent = r.strip().lower().strip('".,!?\n')
            valid = ["chat", "image", "vision", "say", "sticker"]
            if intent not in valid:
                for v in valid:
                    if v in intent:
                        intent = v
                        break
                else:
                    intent = "chat"
            return {"intent": intent, "query": text}
        except:
            return {"intent": "chat", "query": text}

ai = AI()

# ══════════ TTS / STT ══════════
async def gen_tts(text, voice="ru-RU-DmitryNeural"):
    if not HAS_TTS:
        return None
    try:
        clean = re.sub(r'```[\s\S]*?```', ' блок кода ', text)
        clean = re.sub(r'[*_`\[\]()#]', '', clean)
        clean = re.sub(r'https?://\S+', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if not clean:
            return None
        if len(clean) > 3000:
            clean = clean[:3000]
        comm = edge_tts.Communicate(clean, voice)
        buf = BytesIO()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        r = buf.getvalue()
        return r if len(r) > 100 else None
    except Exception as e:
        print(f"TTS err: {e}")
        return None

async def transcribe_voice(file_url):
    try:
        cl = await http()
        r = await cl.get(file_url, timeout=30.0)
        if r.status_code != 200:
            return None
        audio = r.content

        # Groq (приоритет)
        if GROQ_KEY:
            try:
                resp = await cl.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    files={"file": ("voice.ogg", audio, "audio/ogg")},
                    data={"model": "whisper-large-v3", "language": "ru"},
                    headers={"Authorization": f"Bearer {GROQ_KEY}"},
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    return resp.json().get("text", "")
            except Exception as e:
                print(f"Groq STT err: {e}")

        # Pollinations fallback
        try:
            resp = await cl.post(
                "https://text.pollinations.ai/openai/audio/transcriptions",
                files={"file": ("voice.ogg", audio, "audio/ogg")},
                data={"model": "whisper-1"},
                timeout=30.0,
            )
            if resp.status_code == 200:
                return resp.json().get("text", "")
        except:
            pass

        # OpenRouter fallback
        try:
            b64 = base64.b64encode(audio).decode()
            r2 = await cl.post(
                OR_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/whisper-large-v3",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": b64, "format": "ogg"},
                                }
                            ],
                        }
                    ],
                },
                timeout=30.0,
            )
            if r2.status_code == 200:
                d = r2.json()
                if d.get("choices"):
                    return d["choices"][0]["message"]["content"]
        except:
            pass

        return None
    except Exception as e:
        print(f"STT err: {e}")
        return None

# ══════════ INTENT ══════════
def quick_intent(text, has_image=False):
    if not text:
        return None
    try:
        low = re.sub(BOT_TRIGGER_RE, '', text.lower()).strip()
    except:
        low = text.lower().strip()
    if not low:
        return {"intent": "vision", "query": "опиши"} if has_image else None

    for pat in [r'^скажи\s+(.+)', r'^озвучь\s+(.+)', r'^произнеси\s+(.+)', r'^прочитай\s+(.+)']:
        m = re.search(pat, low, re.DOTALL)
        if m and m.group(1).strip():
            return {"intent": "say", "query": m.group(1).strip()}

    for pat, em in [
        (r'\b(улыбн|посмейся|обрадуйся)', 'happy'),
        (r'\b(разозли|злись|бесись)', 'angry'),
        (r'\b(погрусти|плачь|расстройся)', 'sad'),
        (r'\b(спокойно|нейтрально)', 'neutral'),
    ]:
        if re.search(pat, low):
            return {"intent": "sticker", "query": em}

    for pat in [
        r'\b(сделай|сгенери|нарисуй|создай).{0,30}\b(картин|изображен|фотк|арт)',
        r'\b(нарисуй|сгенери)\s+мне\b',
        r'\b(хочу|давай)\s+картинк',
    ]:
        if re.search(pat, low):
            q = low
            for w in ['сделай', 'сгенерируй', 'сгенери', 'нарисуй', 'мне', 'картинку', 'изображение', 'фотку', 'арт']:
                q = q.replace(w, '')
            return {"intent": "image", "query": re.sub(r'\s+', ' ', q).strip() or "что-нибудь"}

    if re.search(r'\b(нарисуй|сгенери|покажи)\s+(меня|тебя|себя)\b', low):
        return {"intent": "image", "query": "автопортрет"}

    if has_image:
        for pat in [r'\b(посмотри|глянь)\b', r'\bчто\s+(тут|здесь|на|видишь)', r'\bчто\s+это\b']:
            if re.search(pat, low):
                return {"intent": "vision", "query": low}
        if len(low) < 30:
            return {"intent": "vision", "query": low or "опиши"}

    return None

def fmt(text):
    parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    out = []
    for p in parts:
        if p.startswith('```') or (p.startswith('`') and p.endswith('`')):
            out.append(p)
        else:
            out.append(re.sub(r'\s+', ' ', p).strip())
    return " ".join(out).strip()

def is_self_req(p):
    return any(t in p.lower() for t in ["себя", "тебя", "ориен", "автопортрет", "меня"])

# ══════════ TG API ══════════
async def tg(method, data):
    try:
        r = await (await http()).post(
            f"https://api.telegram.org/bot{TOKEN}/{method}", json=data
        )
        return r.json() if r.status_code == 200 else None
    except:
        return None

async def send(cid, text, kb=None, parse_mode="Markdown", reply_to=None):
    d = {"chat_id": cid, "text": text}
    if parse_mode:
        d["parse_mode"] = parse_mode
    if kb:
        d["reply_markup"] = kb
    if reply_to:
        d["reply_to_message_id"] = reply_to
    r = await tg("sendMessage", d)
    if r and not r.get("ok") and parse_mode:
        d.pop("parse_mode", None)
        r = await tg("sendMessage", d)
    return r

async def send_photo(cid, url, cap=""):
    return await tg("sendPhoto", {"chat_id": cid, "photo": url, "caption": cap})

async def send_sticker(cid, fid):
    return await tg("sendSticker", {"chat_id": cid, "sticker": fid})

async def send_voice(cid, audio, reply_to=None):
    try:
        d = {"chat_id": str(cid)}
        if reply_to:
            d["reply_to_message_id"] = str(reply_to)
        r = await (await http()).post(
            f"https://api.telegram.org/bot{TOKEN}/sendVoice",
            data=d,
            files={"voice": ("voice.ogg", audio, "audio/ogg")},
            timeout=60.0,
        )
        return r.status_code == 200
    except:
        return False

async def save_stickers_db():
    if DB is not None:
        try:
            await DB.bot_config.update_one(
                {"key": "stickers"},
                {"$set": {"key": "stickers", "stickers": STICKERS}},
                upsert=True,
            )
        except:
            pass

async def detect_emotion(text):
    if not text or len(text) < 5 or not STICKERS:
        return None
    try:
        r = await ai.text(
            [
                {"role": "system", "content": "эмоция ответа. ОДНО слово: happy/angry/neutral/sad/none"},
                {"role": "user", "content": text[:300]},
            ],
            pref="poll_mistral",
            max_tokens=10,
            temperature=0.3,
        )
        e = r.strip().lower().strip('".,!?\n')
        return e if e in ("happy", "angry", "neutral", "sad") else None
    except:
        return None

async def send_with_sticker(cid, text, reply_to=None):
    sent = await send(cid, text, reply_to=reply_to)
    if STICKERS and random.random() < 0.4:
        em = await detect_emotion(text)
        if em and em in STICKERS:
            await send_sticker(cid, STICKERS[em])
    return sent

async def typing(cid):
    await tg("sendChatAction", {"chat_id": cid, "action": "typing"})

async def get_file_url(fid):
    r = await tg("getFile", {"file_id": fid})
    return (
        f"https://api.telegram.org/file/bot{TOKEN}/{r['result']['file_path']}"
        if r and r.get("ok")
        else None
    )

async def dl_b64(url, max_size=1024):
    try:
        r = await (await http()).get(url, timeout=60.0)
        if r.status_code != 200:
            return None
        content = r.content
        if HAS_PIL and len(content) > 500_000:
            try:
                img = Image.open(BytesIO(content))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=85)
                content = buf.getvalue()
            except:
                pass
        return f"data:image/jpeg;base64,{base64.b64encode(content).decode()}"
    except:
        return None

async def extract_img(msg):
    ph = None
    for src in [msg, msg.get("reply_to_message", {})]:
        if not src:
            continue
        if "photo" in src and src["photo"]:
            ph = src["photo"][-1]
            break
        if "document" in src and src["document"].get("mime_type", "").startswith("image/"):
            ph = {"file_id": src["document"]["file_id"]}
            break
    if not ph:
        return None
    url = await get_file_url(ph["file_id"])
    return await dl_b64(url) if url else None

def parse_cmd(text):
    if not text or not text.startswith("/"):
        return None, None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@")[0]
    return cmd, parts[1].strip() if len(parts) > 1 else ""

def should_respond(msg, s):
    if not s.get("auto_reply", True):
        return False
    sender = msg.get("from", {})
    if sender.get("is_bot") and sender.get("username", "").lower() != BOT_USERNAME:
        return False
    if msg["chat"]["type"] == "private":
        return True
    text = (msg.get("text") or msg.get("caption") or "").lower()
    if any(t in text for t in BOT_TRIGGERS):
        return True
    rr = msg.get("reply_to_message")
    return bool(rr and rr.get("from", {}).get("username", "").lower() == BOT_USERNAME)

# ══════════ AI RESPONSE ══════════
async def ai_response(cid, uname, umsg, img=None, creator=False, friend=False, uid=None):
    c = chat_data(cid)
    msgs = [{"role": "system", "content": sys_prompt(c, creator, friend, uid)}]
    msgs.extend(c["history"])
    if img:
        ut = f"{uname}: {umsg}" if umsg.strip() else f"{uname} прислал картинку"
        msgs.append({
            "role": "user",
            "content": [
                {"type": "text", "text": ut},
                {"type": "image_url", "image_url": {"url": img}},
            ],
        })
    else:
        msgs.append({"role": "user", "content": f"{uname}: {umsg}"})

    pref = c.get("text_model", "primary")
    if img:
        pc = TEXT_MODELS.get(pref)
        if not pc or not pc.vision:
            for k, v in TEXT_MODELS.items():
                if v.vision:
                    pref = k
                    break

    raw = await ai.text(msgs, pref=pref, vis=img is not None, temperature=1.0)
    at = fmt(raw)

    ht = f"{uname}: {umsg}" if umsg.strip() else f"{uname}: [картинка]"
    c["history"].append({"role": "user", "content": ht})
    c["history"].append({"role": "assistant", "content": at})
    c["history"] = c["history"][-16:]
    await save_chat(cid)

    if uid and umsg and not umsg.startswith("/") and len(umsg) > 10:
        asyncio.create_task(_extract_facts(uid, uname, umsg))

    return at

async def _extract_facts(uid, uname, text):
    try:
        facts = await ai.extract_facts(uname, text)
        for f in facts:
            await add_fact(uid, f)
    except:
        pass

# ══════════ HANDLERS ══════════
async def h_image(cid, uname, query):
    c = chat_data(cid)
    if not query or len(query) < 2:
        query = "что-то интересное"
    await tg("sendChatAction", {"chat_id": cid, "action": "upload_photo"})
    im = c.get("image_model", "flux")
    try:
        ep = await ai.enhance_prompt(query, is_self_req(query))
        url = await ai.gen_image(ep, im)
        await send_photo(cid, url, f"модель {im}")
    except:
        await send(cid, f"не вышло через *{im}*")

async def h_vision(cid, uname, query, msg, cflag, ffl, uid):
    img = await extract_img(msg)
    if not img:
        await send(cid, "не вижу картинки")
        return
    await typing(cid)
    try:
        at = await ai_response(cid, uname, query or "что на картинке?", img, cflag, ffl, uid)
        await send(cid, at)
    except:
        await send(cid, "vision лагает")

async def h_sticker(cid, query):
    if not STICKERS:
        await send(cid, "стикеры не настроены")
        return
    em = query if query in STICKERS else random.choice(list(STICKERS.keys()))
    await send_sticker(cid, STICKERS[em])
    phrases = {"happy": ["вот", "держи", "лови"], "angry": ["получай"], "sad": ["эх"], "neutral": ["ок"]}
    await send(cid, random.choice(phrases.get(em, ["вот"])))

async def h_say(cid, text, voice_key=None, reply_to=None):
    if not HAS_TTS:
        await send(cid, "tts недоступен")
        return
    if not text or len(text.strip()) < 1:
        await send(cid, "что говорить?")
        return
    c = chat_data(cid)
    if not voice_key:
        voice_key = c.get("voice", DEFAULT_VOICE)
    vc = VOICES.get(voice_key.lower(), VOICES[DEFAULT_VOICE])
    await tg("sendChatAction", {"chat_id": cid, "action": "record_voice"})
    audio = await gen_tts(text, vc["id"])
    if not audio:
        await send(cid, "не получилось")
        return
    await send_voice(cid, audio, reply_to)

async def h_voice_msg(msg, cid, uname, uid, cflag, ffl):
    voice = msg.get("voice") or msg.get("audio")
    if not voice:
        return False
    await typing(cid)
    file_url = await get_file_url(voice["file_id"])
    if not file_url:
        await send(cid, "не получил файл")
        return True
    await send(cid, "_слушаю..._")
    text = await transcribe_voice(file_url)
    if not text:
        await send(cid, "не распознал")
        return True
    await send(cid, f"_услышал:_ {text[:300]}")
    intent_data = quick_intent(text, False)
    if not intent_data:
        try:
            intent_data = await ai.detect_intent(text, False)
        except:
            intent_data = {"intent": "chat", "query": text}
    intent = intent_data.get("intent", "chat")
    query = intent_data.get("query", text)
    if intent == "image":
        await h_image(cid, uname, query)
        return True
    elif intent == "say":
        await h_say(cid, query, reply_to=msg.get("message_id"))
        return True
    elif intent == "sticker":
        await h_sticker(cid, query)
        return True
    await typing(cid)
    try:
        at = await ai_response(cid, uname, text, None, cflag, ffl, uid)
        await send(cid, at)
        if HAS_TTS:
            c = chat_data(cid)
            audio = await gen_tts(at, VOICES[c.get("voice", DEFAULT_VOICE)]["id"])
            if audio:
                await send_voice(cid, audio)
    except Exception as e:
        await send(cid, f"err: {str(e)[:100]}")
    return True

# ══════════ WEBHOOK ══════════
@app.post("/webhook")
async def webhook(req: Request):
    try:
        data = await req.json()
    except:
        return {"status": "bad"}

    if "message" not in data:
        return {"status": "ok"}

    msg = data["message"]
    cid = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    user = msg.get("from", {})
    uname = user.get("first_name", "бро")
    uid = user.get("id", 0)
    c = chat_data(cid)
    s = c["settings"]
    cflag = is_creator(user)
    ffl = is_friend(user)

    # ── голосовые ──
    if ("voice" in msg or "audio" in msg) and should_respond(msg, s):
        if await h_voice_msg(msg, cid, uname, uid, cflag, ffl):
            return {"status": "ok"}

    # ── стикер pending ──
    if uid in STICKER_PENDING and "sticker" in msg:
        if not cflag:
            del STICKER_PENDING[uid]
            return {"status": "ok"}
        em = STICKER_PENDING[uid]
        STICKERS[em] = msg["sticker"]["file_id"]
        await save_stickers_db()
        idx = STICKER_ORDER.index(em)
        if idx + 1 < len(STICKER_ORDER):
            STICKER_PENDING[uid] = STICKER_ORDER[idx + 1]
            await send(cid, f"*{em}* ok\nкидай *{STICKER_ORDER[idx + 1]}*")
        else:
            del STICKER_PENDING[uid]
            await send(cid, "все стикеры готовы")
        return {"status": "ok"}

    # ── prompt pending ──
    if text and uid in PROMPT_PENDING and not text.startswith("/"):
        p = PROMPT_PENDING.pop(uid)
        if time.time() - p["ts"] > 300:
            await send(cid, "время вышло")
        else:
            tc = chat_data(p["cid"])
            tc["custom_prompt"] = text
            tc["history"] = []
            await save_chat(p["cid"])
            await send(cid, f"промпт установлен ({len(text)} симв)")
        return {"status": "ok"}

    if text.strip().lower() == "/cancel":
        if uid in PROMPT_PENDING:
            del PROMPT_PENDING[uid]
        if uid in STICKER_PENDING:
            del STICKER_PENDING[uid]
        await send(cid, "ок")
        return {"status": "ok"}

    cmd, args = parse_cmd(text)

    # ══════════ КОМАНДЫ ══════════

    # ПАМЯТЬ
    if cmd in ("/memory", "/память"):
        facts = USER_MEMORY.get(uid, [])
        if not facts:
            await send(cid, "память пустая")
            return {"status": "ok"}
        await send(cid, f"*память ({len(facts)}):*\n\n" + "\n".join(f"- {f}" for f in facts))
        return {"status": "ok"}

    if cmd in ("/forget", "/забудь"):
        USER_MEMORY[uid] = []
        await save_memory(uid)
        await send(cid, "забыл всё")
        return {"status": "ok"}

    if cmd == "/remember":
        if not args:
            await send(cid, "`/remember я люблю питон`")
            return {"status": "ok"}
        await add_fact(uid, args.strip())
        await send(cid, f"запомнил: *{args.strip()}*")
        return {"status": "ok"}

    # TTS
    if cmd in ("/say", "/скажи", "/озвучь"):
        if not args:
            await send(cid, f"`/say текст` или `/say:даша текст`\nголоса: {', '.join(VOICES.keys())}")
            return {"status": "ok"}
        voice = None
        if args.startswith(":"):
            parts = args[1:].split(maxsplit=1)
            if parts and parts[0].lower() in VOICES:
                voice = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""
        if not args.strip():
            await send(cid, "что говорить?")
            return {"status": "ok"}
        await h_say(cid, args, voice_key=voice, reply_to=msg.get("message_id"))
        return {"status": "ok"}

    if cmd in ("/голос", "/voice"):
        if not args:
            cur = c.get("voice", DEFAULT_VOICE)
            lines = [f"текущий: *{cur}*", ""] + [
                f"{'>' if k == cur else ' '} `{k}` — {v['desc']}"
                for k, v in VOICES.items()
            ]
            await send(cid, "\n".join(lines))
            return {"status": "ok"}
        vk = args.strip().lower()
        if vk not in VOICES:
            await send(cid, f"нет. {', '.join(VOICES.keys())}")
            return {"status": "ok"}
        c["voice"] = vk
        await save_chat(cid)
        await send(cid, f"голос: *{vk}*")
        await h_say(cid, f"теперь я говорю голосом {vk}", voice_key=vk)
        return {"status": "ok"}

    if cmd in ("/voices", "/голоса"):
        await send(cid, "*голоса:*\n" + "\n".join(f"`{k}` — {v['desc']}" for k, v in VOICES.items()))
        return {"status": "ok"}

    # КАРТИНКИ
    if cmd in ("/img", "/image"):
        if not args:
            await send(cid, "`/img описание`")
            return {"status": "ok"}
        await h_image(cid, uname, args)
        return {"status": "ok"}

    if cmd == "/me":
        await tg("sendChatAction", {"chat_id": cid, "action": "upload_photo"})
        try:
            ep = await ai.enhance_prompt("OrienAI аниме парень", True)
            url = await ai.gen_image(ep, c.get("image_model", "flux"))
            await send_photo(cid, url, "это я")
        except:
            await send(cid, "не вышло")
        return {"status": "ok"}

    if cmd == "/imgmodel":
        if not args:
            cur = c.get("image_model", "flux")
            lines = [f"сейчас: *{cur}*"] + [
                f"`/imgmodel {k}` — {v}" for k, v in IMG_MODELS.items()
            ]
            await send(cid, "\n".join(lines))
            return {"status": "ok"}
        mk = args.split()[0].lower()
        if mk in IMG_MODELS:
            c["image_model"] = mk
            await save_chat(cid)
            await send(cid, f"ок {mk}")
        return {"status": "ok"}

    if cmd in ("/vision", "/посмотри"):
        await h_vision(cid, uname, args, msg, cflag, ffl, uid)
        return {"status": "ok"}

    # СТИКЕРЫ
    if cmd == "/stickerids":
        if not cflag:
            await send(cid, "только создатель")
            return {"status": "ok"}
        STICKER_PENDING[uid] = STICKER_ORDER[0]
        await send(cid, "кидай 4 стикера: happy, angry, neutral, sad\n`/cancel` — отмена")
        return {"status": "ok"}

    if cmd == "/showstickers":
        if not STICKERS:
            await send(cid, "нет")
            return {"status": "ok"}
        for em, fid in STICKERS.items():
            await send(cid, f"*{em}*")
            await send_sticker(cid, fid)
        return {"status": "ok"}

    if cmd == "/sticker":
        if not args:
            await send(cid, f"эмоции: {', '.join(STICKERS.keys()) if STICKERS else 'нет'}")
            return {"status": "ok"}
        em = args.strip().lower()
        if em in STICKERS:
            await send_sticker(cid, STICKERS[em])
        return {"status": "ok"}

    # НАСТРОЙКИ
    if cmd == "/reset":
        c["history"] = []
        await save_chat(cid)
        await send(cid, "забыл диалог")
        return {"status": "ok"}

    if cmd == "/prompt":
        if not args:
            cur = c.get("custom_prompt")
            if cur:
                await send(cid, f"*кастомный ({len(cur)} симв)*\n\n`/prompt reset` — сбросить\n`/prompt set текст` — задать")
            else:
                await send(cid, "стандартный. `/prompt set текст`")
            return {"status": "ok"}
        if args.strip() == "reset":
            c["custom_prompt"] = None
            await save_chat(cid)
            await send(cid, "сброшено")
            return {"status": "ok"}
        if args.startswith("set "):
            new = args[4:].strip()
            if new:
                c["custom_prompt"] = new
                c["history"] = []
                await save_chat(cid)
                await send(cid, f"промпт установлен ({len(new)} симв)")
            return {"status": "ok"}
        return {"status": "ok"}

    if cmd == "/status":
        lines = [
            f"текст: *{c.get('text_model', 'primary')}*",
            f"картинки: *{c.get('image_model', 'flux')}*",
            f"голос: *{c.get('voice', DEFAULT_VOICE)}*",
            f"промпт: {'кастом' if c.get('custom_prompt') else 'стандарт'}",
            f"стикеров: *{len(STICKERS)}/4*",
            f"фактов о тебе: *{len(USER_MEMORY.get(uid, []))}*",
            f"история: *{len(c.get('history', []))}*",
            f"бд: {'ok' if DB is not None else 'нет'} | PIL: {'ok' if HAS_PIL else 'нет'} | TTS: {'ok' if HAS_TTS else 'нет'}",
            f"Groq STT: {'да' if GROQ_KEY else 'нет'}",
        ]
        await send(cid, "\n".join(lines))
        return {"status": "ok"}

    if cmd == "/help":
        await send(cid, """*OrienAI v10.0*

*обращайся:* "ориен ..."
- "ориен привет как дела"
- "ориен нарисуй кота"
- "ориен скажи что-то"
- голосовое → распознаю и отвечу голосом

*ГОЛОС:*
`/say текст` или `/say:даша текст` — озвучить
`/голос имя` — сменить голос по умолчанию
`/голоса` — список голосов

*КАРТИНКИ:*
`/img описание` — сгенерировать
`/me` — автопортрет
`/imgmodel` — сменить модель
`/vision` (+фото) — посмотреть картинку

*ПАМЯТЬ:*
`/memory` — что помню о тебе
`/remember текст` — запомнить
`/forget` — забыть всё

*СТИКЕРЫ:*
`/stickerids` — настроить (создатель)
`/showstickers` — показать все
`/sticker happy` — прислать

*НАСТРОЙКИ:*
`/prompt` — системный промпт
`/reset` — забыть диалог
`/status` — статус бота""")
        return {"status": "ok"}

    if cmd == "/start":
        await send(cid, f"здарова *{uname}* — OrienAI v10.0\n`/help`")
        return {"status": "ok"}

    if cmd is not None:
        return {"status": "ok"}

    # ══════════ ОТВЕТ НА ОБРАЩЕНИЕ ══════════
    if should_respond(msg, s):
        # Извлекаем картинку ОДИН раз
        img = await extract_img(msg)
        has_img = img is not None

        if s.get("smart_intent", True) and (text or has_img):
            clean_text = re.sub(BOT_TRIGGER_RE, '', text, flags=re.I).strip() if text else ""
            if not clean_text and has_img:
                clean_text = "опиши"

            intent_data = quick_intent(text, has_img) if text else None
            if not intent_data:
                try:
                    intent_data = await ai.detect_intent(clean_text or "посмотри", has_img)
                except:
                    intent_data = {"intent": "chat", "query": clean_text}

            intent = intent_data.get("intent", "chat")
            query = intent_data.get("query", clean_text)

            if intent == "image":
                await h_image(cid, uname, query)
                return {"status": "ok"}
            elif intent == "vision":
                # img уже есть, передаём напрямую
                if not img:
                    await send(cid, "не вижу картинки")
                    return {"status": "ok"}
                await typing(cid)
                try:
                    at = await ai_response(cid, uname, query or "что на картинке?", img, cflag, ffl, uid)
                    await send(cid, at)
                except:
                    await send(cid, "vision лагает")
                return {"status": "ok"}
            elif intent == "sticker":
                await h_sticker(cid, query)
                return {"status": "ok"}
            elif intent == "say":
                await h_say(cid, query, reply_to=msg.get("message_id"))
                return {"status": "ok"}

        await typing(cid)
        try:
            at = await ai_response(cid, uname, text, img, cflag, ffl, uid)
            await send_with_sticker(cid, at)
        except Exception as e:
            await send(cid, f"чёт сломался: _{str(e)[:100]}_")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {
        "status": "alive",
        "version": "10.0",
        "db": "ok" if DB is not None else "off",
        "pil": HAS_PIL,
        "tts": HAS_TTS,
        "stickers": len(STICKERS),
        "memory": len(USER_MEMORY),
    }

@app.get("/health")
async def health():
    return {
        "ok": True,
        "db": DB is not None,
        "pil": HAS_PIL,
        "tts": HAS_TTS,
        "stickers": len(STICKERS),
        "chats": len(CHATS),
    }

from mangum import Mangum
handler = Mangum(app, lifespan="off")
