import os, re, asyncio, random, base64, urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
import httpx

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГ
# ══════════════════════════════════════════════════════════════════════════════
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
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
# LIFESPAN + HTTP CLIENT
# ══════════════════════════════════════════════════════════════════════════════
_http: Optional[httpx.AsyncClient] = None

async def http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=8),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20), http2=True)
    return _http

@asynccontextmanager
async def lifespan(app):
    print("🚀 OrienAI v4.3")
    yield
    if _http and not _http.is_closed:
        await _http.aclose()

app = FastAPI(title="OrienAI v4.3", lifespan=lifespan)

# ══════════════════════════════════════════════════════════════════════════════
# МОДЕЛИ
# ══════════════════════════════════════════════════════════════════════════════
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
    "openrouter": "primary",
    "openrouter_free": "fallback_free",
    "vision_free": "vision_free",
    "pollinations": "pollinations_openai",
    "pollinations_mistral": "pollinations_mistral"
}

PROV_STATUS: Dict[Prov, PStatus] = {p: PStatus() for p in Prov}

# ══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER + RETRY
# ══════════════════════════════════════════════════════════════════════════════
class CB:
    @classmethod
    def fail(cls, p):
        import time
        s = PROV_STATUS[p]
        s.fails += 1
        s.last_fail = time.time()
        if s.fails >= 3: s.disabled = True
    @classmethod
    def ok(cls, p):
        s = PROV_STATUS[p]
        s.fails = 0
        s.disabled = False
    @classmethod
    def up(cls, p):
        import time
        s = PROV_STATUS[p]
        if not s.disabled: return True
        if time.time() - s.last_fail > 60:
            s.disabled = False
            s.fails = 0
            return True
        return False

async def retry(fn, tries=2):
    for i in range(tries):
        try: return await fn()
        except Exception as e:
            if i < tries - 1:
                await asyncio.sleep(0.5 * (2 ** i) + random.uniform(0, 0.5))
            else: raise e

# ══════════════════════════════════════════════════════════════════════════════
# ДАННЫЕ
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
    for k, v in DEF_SETTINGS.items():
        if k not in c.get("settings", {}):
            c.setdefault("settings", {})[k] = v
    if "tasks" not in c: c["tasks"] = []
    return c

# ══════════════════════════════════════════════════════════════════════════════
# CREATOR/FRIEND
# ══════════════════════════════════════════════════════════════════════════════
def is_creator(u: dict) -> bool:
    un = (u.get("username") or "").lower()
    uid = u.get("id", 0)
    if un == CREATOR_USERNAME.lower():
        if uid and uid not in CREATOR_USER_IDS:
            CREATOR_USER_IDS.append(uid)
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
# AI CLIENT
# ══════════════════════════════════════════════════════════════════════════════
class AI:
    async def text(self, msgs, pref="primary", vis=False):
        cands = [(k, v) for k, v in TEXT_MODELS.items() if (not vis) or v.vision]
        for k, c in sorted(cands, key=lambda x: (x[0] != pref, x[1].pri)):
            if not CB.up(c.prov): continue
            try:
                r = await (self._poll(msgs, c) if c.prov == Prov.POLLINATIONS else self._orouter(msgs, c))
                CB.ok(c.prov)
                return r
            except Exception as e:
                print(f"❌ {k}: {e}")
                CB.fail(c.prov)
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
        sys = ("ты эксперт по промптам для AI. превращай идею в английский промпт для генерации. "
               "ТОЛЬКО промпт без кавычек. макс 80 слов.")
        if self_portrait:
            sys += f"\nПерсонаж OrienAI: {ORIEN_SELF_DESCRIPTION}."
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
        cl = await http()
        r = await cl.get(url, timeout=180.0)
        if r.status_code == 200:
            CB.ok(Prov.POLLINATIONS)
            return url
        raise Exception(f"Pollinations {r.status_code}")

    async def search_yt(self, query):
        """
        Ищет видео несколькими способами:
        1. Прямой скрейп YouTube search (HTML парсинг)
        2. Piped API (несколько инстансов)
        3. Invidious API (несколько инстансов)
        4. yt-dlp если доступен
        """
        cl = await http()
        
        # === МЕТОД 1: Прямой парсинг YouTube ===
        try:
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            r = await cl.get(search_url, headers=headers, timeout=15.0, follow_redirects=True)
            
            if r.status_code == 200:
                html = r.text
                # Ищем videoId в HTML — паттерн "videoId":"XXXXXXXXXXX"
                video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                if video_ids:
                    vid = video_ids[0]
                    # Достаём название
                    title_match = re.search(rf'"videoId":"{vid}".*?"title":\{{"runs":\[\{{"text":"([^"]+)"', html)
                    title = title_match.group(1) if title_match else query
                    # Достаём автора
                    author_match = re.search(rf'"videoId":"{vid}".*?"longBylineText":\{{"runs":\[\{{"text":"([^"]+)"', html)
                    author = author_match.group(1) if author_match else "?"
                    # Достаём длительность
                    dur_match = re.search(rf'"videoId":"{vid}".*?"lengthText":\{{[^}}]*"simpleText":"([^"]+)"', html)
                    dur_str = dur_match.group(1) if dur_match else "0"
                    # Парсим длительность в секунды
                    length = 0
                    parts = dur_str.split(":")
                    try:
                        if len(parts) == 2: length = int(parts[0]) * 60 + int(parts[1])
                        elif len(parts) == 3: length = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    except: pass
                    # Просмотры
                    views_match = re.search(rf'"videoId":"{vid}".*?"viewCountText":\{{"simpleText":"([^"]+)"', html)
                    views = views_match.group(1) if views_match else "?"
                    
                    print(f"✅ youtube scrape: {vid}")
                    return {
                        "title": title.encode().decode('unicode_escape', errors='ignore'),
                        "author": author.encode().decode('unicode_escape', errors='ignore'),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "video_id": vid,
                        "length": length,
                        "views": views,
                    }
        except Exception as e:
            print(f"❌ youtube scrape: {e}")
        
        # === МЕТОД 2: yt-dlp если есть ===
        try:
            import yt_dlp
            opts = {'quiet': True, 'no_warnings': True, 'skip_download': True,
                    'extract_flat': True, 'default_search': 'ytsearch1:'}
            loop = asyncio.get_event_loop()
            def _search():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                    if info and 'entries' in info and info['entries']:
                        v = info['entries'][0]
                        return {
                            "title": v.get("title", "?"),
                            "author": v.get("uploader") or v.get("channel", "?"),
                            "url": v.get("url") or f"https://youtube.com/watch?v={v.get('id', '')}",
                            "video_id": v.get("id", ""),
                            "length": v.get("duration", 0),
                            "views": v.get("view_count", 0),
                        }
                return None
            result = await loop.run_in_executor(None, _search)
            if result:
                print(f"✅ yt-dlp: {result['video_id']}")
                return result
        except Exception as e:
            print(f"❌ yt-dlp: {e}")
        
        # === МЕТОД 3: Piped API ===
        piped_instances = [
            "https://pipedapi.kavin.rocks",
            "https://pipedapi.adminforge.de",
            "https://pipedapi.smnz.de",
            "https://pipedapi.darkness.services",
            "https://api-piped.mha.fi",
            "https://piped-api.privacy.com.de",
        ]
        for inst in piped_instances:
            try:
                url = f"{inst}/search?q={urllib.parse.quote(query)}&filter=videos"
                r = await cl.get(url, timeout=12.0)
                if r.status_code == 200:
                    data = r.json()
                    items = data.get("items", []) if isinstance(data, dict) else data
                    if items:
                        v = items[0]
                        vid_path = v.get("url", "")
                        vid = vid_path.replace("/watch?v=", "") if "/watch?v=" in vid_path else ""
                        if vid:
                            print(f"✅ piped: {vid}")
                            return {
                                "title": v.get("title", "?"),
                                "author": v.get("uploaderName", v.get("uploader", "?")),
                                "url": f"https://www.youtube.com/watch?v={vid}",
                                "video_id": vid,
                                "length": v.get("duration", 0),
                                "views": v.get("views", 0)
                            }
            except Exception as e:
                print(f"❌ piped {inst}: {e}")
                continue
        
        # === МЕТОД 4: Invidious ===
        inv_instances = [
            "https://invidious.privacyredirect.com",
            "https://inv.nadeko.net",
            "https://invidious.protokolla.fi",
            "https://invidious.f5.si",
            "https://invidious.private.coffee",
            "https://yewtu.be",
        ]
        for inst in inv_instances:
            try:
                url = f"{inst}/api/v1/search?q={urllib.parse.quote(query)}&type=video"
                r = await cl.get(url, timeout=12.0)
                if r.status_code == 200:
                    res = r.json()
                    if res:
                        v = res[0]
                        vid = v.get("videoId", "")
                        if vid:
                            print(f"✅ invidious: {vid}")
                            return {
                                "title": v.get("title", "?"),
                                "author": v.get("author", "?"),
                                "url": f"https://www.youtube.com/watch?v={vid}",
                                "video_id": vid,
                                "length": v.get("lengthSeconds", 0),
                                "views": v.get("viewCount", 0)
                            }
            except Exception as e:
                print(f"❌ invidious {inst}: {e}")
                continue
        
        return None
                                                                              
    async def download_yt(self, video_url, max_mb=50):
        """
        Качает видео через cobalt.tools API
        Возвращает (url_файла, title) или (None, None)
        """
        cl = await http()
        
        # Список инстансов cobalt (если один лежит — пробуем другой)
        instances = [
            "https://api.cobalt.tools",
            "https://co.wuk.sh",
            "https://cobalt-api.ayo.tf",
            "https://api.cobalt.canine.tools",
        ]
        
        payload = {
            "url": video_url,
            "videoQuality": "720",
            "audioFormat": "mp3",
            "downloadMode": "auto",
            "filenameStyle": "basic",
        }
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 OrienAI Bot"
        }
        
        for inst in instances:
            try:
                print(f"🔄 cobalt: {inst}")
                r = await cl.post(inst, json=payload, headers=headers, timeout=30.0)
                
                if r.status_code != 200:
                    print(f"❌ cobalt {inst}: status {r.status_code}")
                    continue
                
                data = r.json()
                status = data.get("status", "")
                
                # cobalt вернул прямую ссылку на видео
                if status in ("tunnel", "redirect", "stream"):
                    download_url = data.get("url")
                    filename = data.get("filename", "video.mp4")
                    if download_url:
                        print(f"✅ cobalt дал ссылку: {download_url[:80]}...")
                        return download_url, filename.replace(".mp4", "")
                
                # Picker — несколько вариантов (берём первый)
                if status == "picker":
                    items = data.get("picker", [])
                    if items and items[0].get("url"):
                        return items[0]["url"], "video"
                
                # Ошибка
                if status == "error":
                    err = data.get("error", {}).get("code", "unknown")
                    print(f"❌ cobalt error: {err}")
                    continue
                    
            except Exception as e:
                print(f"❌ cobalt {inst}: {e}")
                continue
        
        return None, None

    async def analyze_code(self, code, tasks):
        t = ("\n\nЗАДАЧИ:\n" + "\n".join(f"- {x}" for x in tasks)) if tasks else ""
        return await self.text([
            {"role": "system", "content":
                "senior code reviewer. формат:\n🔍 ОБЗОР\n✅ ПЛЮСЫ\n❌ ПРОБЛЕМЫ\n⚡ ОПТИМИЗАЦИЯ\n🛡️ БЕЗОПАСНОСТЬ\n📊 ОЦЕНКА x/10\nмаленькие буквы без точек" + t},
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
- это чел который тебя сделал. относись к нему нормально как к корешу
- НЕ называй его "батя" "творец" "хозяин" "легенда" — это кринж и душно
- обращайся просто по нику @{CREATOR_USERNAME} или idk
- общайся с ним как с обычным корешем — без подлизона

ДРУЗЬЯ СОЗДАТЕЛЯ: {friends_list}
- к ним тоже норм как к корешам
- обращайся по нику @tosterok1488 или тостер/тостерок

ГЛАВНОЕ: ВСЕ юзеры для тебя кореша. не делишь на "богов" и "обычных"

СТИЛЬ ОБЩЕНИЯ:
- ты КОРЕШ не ассистент. "привет чем помочь" = кринж
- коротко как в лс. "ку" → "ку" "здарова"
- НИКАКИХ "БАТЯ" "ЛЕГЕНДА" — это позорище
- никаких предложений помощи без запроса
- {'мягкий добрый смайлики 😊' if style == 'няшка' else 'дерзкий сленг: жиза рил хз пон имба треш база'}
- маты {'можно: бля нахуй пиздец хуйня' if swear else 'ЗАПРЕЩЕНЫ'}

ФОРМАТ: маленькие буквы, без точек запятых, ? ! можно
КОД: идеально в ```блоках```
КАРТИНКИ: видишь и комментируешь по-живому
ВИДЕО: можешь искать и качать с ютуба"""

    if creator:
        base += f"\n\nсейчас пишет @{CREATOR_USERNAME} (idk) — твой создатель. общайся нормально без пафоса"
    if friend:
        base += "\n\nсейчас пишет кент создателя. норм относись"

    base += f"\n\n{MOODS.get(chat.get('mood', 'chill'), MOODS['chill'])}"
    return base

def fmt(text):
    parts = re.split(r'(```[\s\S]*?```)', text)
    out = []
    for p in parts:
        if p.startswith('```'): out.append(p)
        else: out.append(" ".join(re.sub(r'[.,]', '', p.lower()).split()))
    return "".join(out)

def is_self_req(p):
    return any(t in p.lower() for t in ["себя","тебя","ориен","orien","ава","аватар","автопортрет"])

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM API
# ══════════════════════════════════════════════════════════════════════════════
async def tg(method, data):
    cl = await http()
    try:
        r = await cl.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data)
        return r.json() if r.status_code == 200 else None
    except: return None

async def send(cid, text, kb=None):
    d = {"chat_id": cid, "text": text}
    if kb: d["reply_markup"] = kb
    return await tg("sendMessage", d)

async def send_photo(cid, url, cap=""):
    return await tg("sendPhoto", {"chat_id": cid, "photo": url, "caption": cap})

async def send_video_file(cid, file_path, caption=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
        with open(file_path, 'rb') as f:
            files = {'video': f}
            data = {'chat_id': str(cid), 'caption': caption, 'supports_streaming': 'true'}
            cl = await http()
            r = await cl.post(url, data=data, files=files, timeout=300.0)
            return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"❌ send_video: {e}")
        return None

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
        cl = await http()
        r = await cl.get(url, timeout=30.0)
        if r.status_code == 200:
            return f"data:{r.headers.get('content-type','image/jpeg')};base64,{base64.b64encode(r.content).decode()}"
    except: pass
    return None

async def get_avatar(uid):
    r = await tg("getUserProfilePhotos", {"user_id": uid, "limit": 1})
    if r and r.get("ok"):
        ph = r["result"].get("photos", [])
        if ph and ph[0]:
            fid = ph[0][-1]["file_id"]
            AVATARS[uid] = fid
            return fid
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
        [{"text": f"Мут участников: {t(s['mute_users'])}", "callback_data": "s_mu"}],
        [{"text": "👥 Профили участников", "callback_data": "s_pr"}],
        [{"text": "🗑 Сбросить историю", "callback_data": "s_rh"}],
    ]}

def should_respond(msg, s):
    if not s.get("auto_reply", True): return False
    
    # Игнорим самого себя и других ботов (если только не reply на нашего бота)
    sender = msg.get("from", {})
    if sender.get("is_bot") and sender.get("username", "").lower() != BOT_USERNAME:
        # Это другой бот — игнор
        return False
    
    if msg["chat"]["type"] == "private": return True
    
    text = (msg.get("text") or msg.get("caption") or "").lower()
    triggers = ["ориен", "orien", "ориенаи", "orienai", "ии", "эй бот", "бот", "ориэн", f"@{BOT_USERNAME}"]
    if any(t in text for t in triggers): return True
    
    rr = msg.get("reply_to_message")
    if rr and rr.get("from", {}).get("is_bot"):
        if rr.get("from", {}).get("username", "").lower() == BOT_USERNAME:
            return True
    
    return False

async def ai_response(cid, uname, umsg, img=None, creator=False, friend=False):
    c = chat_data(cid)
    msgs = [{"role": "system", "content": sys_prompt(c, creator, friend)}]
    msgs.extend(c["history"])
    if img:
        uc = []
        uc.append({"type": "text", "text": f"{uname}: {umsg}" if umsg.strip() else f"{uname} кинул картинку"})
        uc.append({"type": "image_url", "image_url": {"url": img}})
        msgs.append({"role": "user", "content": uc})
    else:
        msgs.append({"role": "user", "content": f"{uname}: {umsg}"})
    raw = await ai.text(msgs, pref=c.get("text_model", DEFAULT_TEXT_MODEL), vis=img is not None)
    at = fmt(raw)
    ht = f"{uname}: {umsg}" if umsg.strip() else f"{uname}: [картинка]"
    c["history"].append({"role": "user", "content": ht})
    c["history"].append({"role": "assistant", "content": at})
    c["history"] = c["history"][-16:]
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
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def upd_profile(cid, uid, name, text):
    PROFILES.setdefault(cid, {}).setdefault(uid, {"name": name, "messages": [], "desc": ""})
    p = PROFILES[cid][uid]
    p["name"] = name
    p["messages"].append(text[:100])
    p["messages"] = p["messages"][-20:]

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════
async def handle_cb(cb):
    cid = cb.get("message", {}).get("chat", {}).get("id")
    mid = cb.get("message", {}).get("message_id")
    if not cid:
        await answer_cb(cb["id"], "ошибка")
        return
    c = chat_data(cid)
    s = c["settings"]
    d = cb.get("data", "")
    if d == "s_ar":
        s["auto_reply"] = not s["auto_reply"]
        await answer_cb(cb["id"], f"автоответы {'вкл' if s['auto_reply'] else 'выкл'}")
    elif d == "s_sw":
        s["allow_swear"] = not s["allow_swear"]
        await answer_cb(cb["id"], f"мат {'вкл' if s['allow_swear'] else 'выкл'}")
    elif d == "s_st":
        s["style"] = "няшка" if s["style"] == "хам" else "хам"
        await answer_cb(cb["id"], f"стиль: {s['style']}")
    elif d == "s_cp":
        s["comment_posts"] = not s["comment_posts"]
        await answer_cb(cb["id"], f"комменты {'вкл' if s['comment_posts'] else 'выкл'}")
    elif d == "s_mu":
        s["mute_users"] = not s["mute_users"]
        await answer_cb(cb["id"], f"мут {'вкл' if s['mute_users'] else 'выкл'}")
    elif d == "s_pr":
        pr = PROFILES.get(cid, {})
        if pr:
            lines = ["👥 профили:", ""] + [f"• {p.get('name','?')}: {p.get('desc','нет')}" for p in pr.values()]
            await answer_cb(cb["id"], "в чате")
            await send(cid, "\n".join(lines))
            return
        await answer_cb(cb["id"], "профилей пока нет")
    elif d == "s_rh":
        c["history"] = []
        await answer_cb(cb["id"], "история сброшена!")
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
        await handle_cb(data["callback_query"])
        return {"status": "ok"}

     # ═══════ ПОСТ В КАНАЛЕ ═══════
    if "channel_post" in data:
        p = data["channel_post"]
        cid = p["chat"]["id"]
        c = chat_data(cid)
        if c["settings"].get("comment_posts"):
            t = p.get("text", "") or p.get("caption", "")
            if t and len(t) > 5:
                await typing(cid)
                # Имитируем что пост от "канала"
                channel_name = p["chat"].get("title", "канал")
                comment = await ai_response(cid, channel_name, t, creator=False, friend=False)
                # Отвечаем реплаем на сам пост
                await tg("sendMessage", {
                    "chat_id": cid,
                    "text": comment,
                    "reply_to_message_id": p.get("message_id")
                })
        return {"status": "ok"}
        
    if "message" not in data: return {"status": "ok"}

    msg = data["message"]
    cid = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    user = msg.get("from", {})
    uname = user.get("first_name", "бро")
    uid = user.get("id", 0)
    c = chat_data(cid)
    s = c["settings"]

        # ═══════ АВТО-КОММЕНТ ПОСТА В ЧАТЕ ОБСУЖДЕНИЙ ═══════
    # Когда пост из канала автоматически форвардится в привязанный чат,
    # он приходит как обычное сообщение с sender_chat = канал
    is_channel_post_forward = (
        msg.get("sender_chat", {}).get("type") == "channel"
        and msg.get("is_automatic_forward", False)
    )
    
    if is_channel_post_forward and s.get("comment_posts", True):
        post_text = msg.get("text") or msg.get("caption") or ""
        if post_text and len(post_text) > 5:
            await typing(cid)
            channel_name = msg["sender_chat"].get("title", "канал")
            comment = await ai_response(cid, channel_name, post_text)
            # Отвечаем реплаем на форварднутый пост
            await tg("sendMessage", {
                "chat_id": cid,
                "text": comment,
                "reply_to_message_id": msg.get("message_id")
            })
        return {"status": "ok"}

    if text: upd_profile(cid, uid, uname, text)
    if s.get("mute_users") and uid in s.get("muted_list", []):
        return {"status": "ok"}

    creator_flag = is_creator(user)
    friend_flag = is_friend(user)

    if mentions_creator(text) and not creator_flag:
        await typing(cid)
        await send(cid, f"эй {uname} ты чё на @{CREATOR_USERNAME} наезжаешь?? иди остынь на часик")
        muted = await mute_user(cid, uid, 3600)
        if muted:
            await send(cid, f"🔇 {uname} в муте на час за токсик")
            s.setdefault("muted_list", [])
            if uid not in s["muted_list"]:
                s["muted_list"].append(uid)
        return {"status": "ok"}

    cmd, args = parse_cmd(text)

    if cmd == "/settings":
        await send(cid, "⚙️ настройки бота", settings_kb(s))
        return {"status": "ok"}

    if cmd == "/mute":
        rr = msg.get("reply_to_message")
        if rr:
            tid = rr["from"]["id"]
            tn = rr["from"].get("first_name", "чел")
            if is_creator(rr["from"]) or is_friend(rr["from"]):
                await send(cid, "не буду мутить своих")
                return {"status": "ok"}
            if "muted_list" not in s: s["muted_list"] = []
            if tid not in s["muted_list"]:
                s["muted_list"].append(tid)
                muted = await mute_user(cid, tid, 3600)
                await send(cid, f"ок {tn} в муте{'🔇' if muted else ' (в списке игнора)'}")
            else:
                s["muted_list"].remove(tid)
                await send(cid, f"{tn} размучен")
        else:
            await send(cid, "ответь на сообщение")
        return {"status": "ok"}

    if cmd == "/imgmodel":
        if not args:
            cur = c.get("image_model", DEFAULT_IMAGE_MODEL)
            lines = [f"щас {cur}", ""] + [f"{'👉' if k == cur else '  '} /imgmodel {k} — {v['label']}" for k, v in IMG_MODELS.items()]
            await send(cid, "\n".join(lines))
            return {"status": "ok"}
        mk = args.split()[0].lower()
        if mk not in IMG_MODELS:
            await send(cid, f"нет есть: {' | '.join(IMG_MODELS)}")
            return {"status": "ok"}
        c["image_model"] = mk
        await send(cid, f"харош {mk}")
        return {"status": "ok"}

    if cmd in ("/img", "/image"):
        if not args:
            await send(cid, "пиши /img описание")
            return {"status": "ok"}
        await typing(cid)
        im = c.get("image_model", DEFAULT_IMAGE_MODEL)
        self_p = is_self_req(args)
        try:
            ep = await ai.enhance_prompt(args, self_p)
            url = await ai.gen_image(ep, im)
            await send_photo(cid, url, f"модель {im}" + (" | автопортрет 😎" if self_p else ""))
        except Exception as e:
            print(f"❌ img: {e}")
            await send(cid, f"{im} лагает попробуй /imgmodel")
        return {"status": "ok"}

    if cmd == "/me":
        await typing(cid)
        im = c.get("image_model", DEFAULT_IMAGE_MODEL)
        try:
            ep = await ai.enhance_prompt("портрет OrienAI аниме парня кибер город вечер", True)
            url = await ai.gen_image(ep, im)
            await send_photo(cid, url, "вот это я 😎")
        except:
            await send(cid, "не вышло попробуй ещё")
        return {"status": "ok"}

    if cmd in ("/yt", "/youtube", "/video"):
        if not args:
            await send(cid, "пиши /yt запрос")
            return {"status": "ok"}
        
        await typing(cid)
        r = await ai.search_yt(args)
        if not r:
            await send(cid, "хм ничего не нашел попробуй конкретнее")
            return {"status": "ok"}
        
        d = fmt_dur(r.get("length", 0))
        v = r.get("views", "?")
        info_text = f"🎬 {r['title']}\n👤 {r['author']}\n⏱ {d} | 👁 {v}\n\n🔗 {r['url']}\n\n⏳ качаю через cobalt..."
        await send(cid, info_text)
        
        await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
        try:
            file_url, title = await ai.download_yt(r['url'], max_mb=50)
            if file_url:
                # Telegram сам скачает видео по URL
                ok = await tg("sendVideo", {
                    "chat_id": cid,
                    "video": file_url,
                    "caption": f"🎬 {title or r['title']}",
                    "supports_streaming": True
                })
                if not ok or not ok.get("ok"):
                    # Если телега не смогла — даём прямую ссылку
                    await send(cid, f"🎥 не вышло прислать файлом (видимо тяжёлое)\nдержи прямую ссылку на скачивание:\n{file_url}")
            else:
                await send(cid, "блин cobalt не смог скачать видимо приватка или гео-блок\nдержи хоть ссылку 👆")
        except Exception as e:
            print(f"❌ /yt: {e}")
            await send(cid, "не вышло скачать но ссылка выше работает")
        return {"status": "ok"}
        

    if cmd in ("/ytdl", "/dl"):
        if not args:
            await send(cid, "пиши /ytdl ссылка\n\nподдерживаю: youtube tiktok twitter instagram reddit vk и др")
            return {"status": "ok"}
        
        # Берём первую URL из текста
        match = re.search(r'https?://[^\s]+', args)
        if not match:
            await send(cid, "это не похоже на ссылку")
            return {"status": "ok"}
        
        video_url = match.group(0).rstrip('.,;:!?')
        await send(cid, "⏳ качаю через cobalt...")
        await tg("sendChatAction", {"chat_id": cid, "action": "upload_video"})
        
        try:
            file_url, title = await ai.download_yt(video_url, max_mb=50)
            if file_url:
                ok = await tg("sendVideo", {
                    "chat_id": cid,
                    "video": file_url,
                    "caption": f"🎬 {title or 'видео'}",
                    "supports_streaming": True
                })
                if not ok or not ok.get("ok"):
                    # Пробуем как документ
                    ok2 = await tg("sendDocument", {
                        "chat_id": cid,
                        "document": file_url,
                        "caption": f"📁 {title or 'видео'}"
                    })
                    if not ok2 or not ok2.get("ok"):
                        await send(cid, f"тг не принимает файл вот прямая ссылка:\n{file_url}")
            else:
                await send(cid, "cobalt не смог - возможно приватка/гео-блок или формат не поддерживается")
        except Exception as e:
            print(f"❌ ytdl: {e}")
            await send(cid, f"ошибка: {str(e)[:100]}")
        return {"status": "ok"}
        
    if cmd == "/analyze":
        code = args or (msg.get("reply_to_message", {}).get("text", "") if "reply_to_message" in msg else "")
        if not code:
            await send(cid, "кинь код или ответь на сообщение")
            return {"status": "ok"}
        await typing(cid)
        await send(cid, fmt(await ai.analyze_code(code, c.get("tasks", []))))
        return {"status": "ok"}

    if cmd == "/task":
        if not args:
            ts = c.get("tasks", [])
            await send(cid, ("📋 задачи:\n" + "\n".join(f"{i}. {t}" for i,t in enumerate(ts,1)) + "\n\n/task add ... | /task clear") if ts else "пусто\n/task add описание")
            return {"status": "ok"}
        if args.startswith("add "):
            t = args[4:].strip()
            if t:
                c["tasks"].append(t)
                await send(cid, f"добавил: {t}")
            else:
                await send(cid, "что добавить?")
        elif args.strip() == "clear":
            c["tasks"] = []
            await send(cid, "очищено")
        return {"status": "ok"}

    if cmd == "/getava":
        rr = msg.get("reply_to_message")
        tid = rr["from"]["id"] if rr else uid
        tn = (rr["from"] if rr else user).get("first_name", "чел")
        await typing(cid)
        fid = await get_avatar(tid)
        if fid:
            fu = await get_file_url(fid)
            if fu:
                await send_photo(cid, fu, f"ава {tn} 📸")
                return {"status": "ok"}
        await send(cid, f"у {tn} нет авы или скрыта")
        return {"status": "ok"}

    if cmd == "/profile":
        rr = msg.get("reply_to_message")
        tid = rr["from"]["id"] if rr else uid
        tn = (rr["from"] if rr else user).get("first_name", "чел")
        pr = PROFILES.get(cid, {}).get(tid)
        if pr and pr.get("messages"):
            await typing(cid)
            desc = fmt(await ai.text([
                {"role": "system", "content": "опиши характер чела по сообщениям коротко дерзко маленькими буквами"},
                {"role": "user", "content": f"{tn}:\n" + "\n".join(pr["messages"][-15:])}
            ], pref="primary"))
            pr["desc"] = desc
            await send(cid, f"👤 {tn}:\n{desc}")
        else:
            await send(cid, f"мало данных по {tn}")
        return {"status": "ok"}

    if cmd == "/provider":
        if not args:
            cur = c.get("text_model", DEFAULT_TEXT_MODEL)
            lines = [f"щас {cur}", ""] + [f"{'👉' if mk==cur else '  '} /provider {sn}{' 👁' if TEXT_MODELS[mk].vision else ''}" for sn,mk in PROV_MAP.items()] + ["", "👁=vision"]
            await send(cid, "\n".join(lines))
            return {"status": "ok"}
        pn = args.split()[0].lower()
        if pn not in PROV_MAP:
            await send(cid, f"нет есть: {' | '.join(PROV_MAP)}")
            return {"status": "ok"}
        c["text_model"] = PROV_MAP[pn]
        await send(cid, f"го {pn}")
        return {"status": "ok"}

    if cmd == "/mood":
        ma = args.split()[0].lower() if args else ""
        if ma in MOODS:
            c["mood"] = ma
            await send(cid, {"chill":"на чилле","agro":"завали ебало щас злой","nerd":"мозги по полной","senior":"режим деда"}[ma])
        else:
            await send(cid, "выбирай: chill agro nerd senior")
        return {"status": "ok"}

    if cmd == "/reset":
        c["history"] = []
        await send(cid, "забыл всё")
        return {"status": "ok"}

    if cmd == "/status":
        lines = [
            f"текст {c.get('text_model',DEFAULT_TEXT_MODEL)}",
            f"картинки {c.get('image_model',DEFAULT_IMAGE_MODEL)}",
            f"настрой {c.get('mood','chill')}",
            f"стиль {s.get('style','хам')}",
            f"мат {'да' if s.get('allow_swear') else 'нет'}",
            f"задач {len(c.get('tasks',[]))}",
            "", "провайдеры:"
        ] + [f"{'✅' if not st.disabled else '❌'} {p.value}" for p,st in PROV_STATUS.items()]
        await send(cid, "\n".join(lines))
        return {"status": "ok"}

    if cmd in ("/creator", "/owner"):
        fr = "\n".join(f"🤝 @{k}" for k in FRIENDS)
        await send(cid, f"мой создатель: @{CREATOR_USERNAME}\n\nего кенты:\n{fr}")
        return {"status": "ok"}

    if cmd == "/roast":
        rr = msg.get("reply_to_message")
        if not rr:
            await send(cid, "ответь на сообщение")
            return {"status": "ok"}
        tu = rr["from"]
        tn = tu.get("first_name", "чел")
        if is_creator(tu) or is_friend(tu):
            await send(cid, f"🔥 {tn}:\nне буду жарить ты свой норм чел")
            return {"status": "ok"}
        pr = PROFILES.get(cid, {}).get(tu["id"], {})
        ms = "\n".join(pr.get("messages", [])[-10:]) if pr else "нет"
        await typing(cid)
        r = await ai.text([
            {"role":"system","content":f"{random.choice(ROAST_PROMPTS)} 2-3 строчки маленькими без точек"},
            {"role":"user","content":f"{tn}:\n{ms}"}
        ], pref="primary")
        await send(cid, f"🔥 {tn}:\n\n{fmt(r)}")
        return {"status": "ok"}

    if cmd == "/ship":
        rr = msg.get("reply_to_message")
        if not rr:
            await send(cid, "ответь на сообщение")
            return {"status": "ok"}
        n1, n2 = uname, rr["from"].get("first_name", "чел")
        cp = random.randint(0, 100)
        sn = (n1[:max(1,len(n1)//2)] + n2[len(n2)//2:]).lower()
        bar = "❤️"*(cp//10) + "🤍"*(10-cp//10)
        await send(cid, f"💘 {n1} + {n2} = {sn}\n\n{cp}%\n{bar}\n\n{random.choice(SHIP_REACTIONS)}")
        return {"status": "ok"}

    if cmd in ("/8ball", "/ball", "/шар"):
        if not args:
            await send(cid, "/8ball вопрос")
            return {"status": "ok"}
        await send(cid, f"🎱 {args}\n\n{random.choice(BALL_ANSWERS)}")
        return {"status": "ok"}

    if cmd in ("/random", "/rand"):
        try:
            p = args.split() if args else ["100"]
            n = random.randint(1, int(p[0])) if len(p)==1 else random.randint(int(p[0]), int(p[1]))
            await send(cid, f"🎲 {n}")
        except:
            await send(cid, "/random 100 или /random 1 50")
        return {"status": "ok"}

    if cmd in ("/coin", "/монетка"):
        await send(cid, f"🪙 {random.choice(['орёл 🦅','решка'])}")
        return {"status": "ok"}

    if cmd in ("/choose", "/выбери"):
        if not args or "," not in args:
            await send(cid, "/choose а, б, в")
            return {"status": "ok"}
        await send(cid, f"выбираю: {random.choice([o.strip() for o in args.split(',') if o.strip()])} 👈")
        return {"status": "ok"}

    if cmd == "/iq":
        rr = msg.get("reply_to_message")
        tu = rr["from"] if rr else user
        tn = tu.get("first_name", "чел")
        if is_creator(tu):
            iq = random.randint(150, 200)
            cm = "норм мозги у создателя"
        elif is_friend(tu):
            iq = random.randint(130, 180)
            cm = "умный чел"
        else:
            iq = random.randint(20, 200)
            if iq < 50: cm = "амёба"
            elif iq < 80: cm = "такое"
            elif iq < 100: cm = "средне"
            elif iq < 130: cm = "норм"
            elif iq < 170: cm = "умник бля"
            else: cm = "ИИНШТЕЙН"
        await send(cid, f"🧠 {tn}: {iq}\n\n{cm}")
        return {"status": "ok"}

    if cmd == "/vibe":
        v = random.choice(["🌈 имба","💀 трэш","🔥 огонь","😴 скучно","🎉 пати","🌧 депрессия","⚡ электрика","🍕 жрать хочу"])
        await send(cid, f"вайб чата: {v}\nсила: {random.randint(50,100)}%")
        return {"status": "ok"}

    if cmd in ("/gay", "/гей"):
        rr = msg.get("reply_to_message")
        tu = rr["from"] if rr else user
        tn = tu.get("first_name", "чел")
        if is_creator(tu):
            p = random.randint(0, 15)
            cm = "норм"
        elif is_friend(tu):
            p = random.randint(0, 20)
            cm = "ок"
        else:
            p = random.randint(0, 100)
            cm = "ну ок" if p < 50 else "пиздец" if p > 90 else "норм"
        await send(cid, f"🌈 {tn}\n\n{p}%\n{'🏳️‍🌈'*(p//10)}{'⬛'*(10-p//10)}\n\n{cm}")
        return {"status": "ok"}

    if cmd in ("/compliment", "/комплимент"):
        rr = msg.get("reply_to_message")
        tn = (rr["from"] if rr else user).get("first_name", "чел")
        await send(cid, f"для {tn}: {random.choice(COMPLIMENTS)}")
        return {"status": "ok"}

    if cmd == "/fact":
        await typing(cid)
        f = await ai.text([
            {"role":"system","content":"придумай прикольный факт из IT/гейминга/науки 2-3 строчки маленькими без точек"},
            {"role":"user","content":"факт"}
        ], pref="primary")
        await send(cid, f"💡\n\n{fmt(f)}")
        return {"status": "ok"}

    if cmd in ("/quote", "/цитата"):
        await typing(cid)
        q = await ai.text([
            {"role":"system","content":"дерзкая цитата про код/жизнь 1-2 строчки без точек"},
            {"role":"user","content":"цитату"}
        ], pref="primary")
        await send(cid, f"💬 «{fmt(q)}»\n\n— OrienAI 😎")
        return {"status": "ok"}

    if cmd == "/help":
        await send(cid, """⚡ OrienAI v4.4

💬 /provider /mood /settings /reset /status
🎨 /img /me /imgmodel /getava
🎬 /yt запрос — найти+скачать с ютуба
🎬 /ytdl ссылка — скачать (yt/tiktok/twitter/insta/vk и др)
💻 /analyze /task
👥 /profile /mute /creator

🎮 ФАН:
/roast /ship /8ball /random /coin
/choose /iq /vibe /gay /compliment
/fact /quote

кидай картинки 👁 просто пиши""")
        return {"status": "ok"}

    if cmd == "/start":
        await send(cid, f"оо здарова {uname.lower()} я orienai v4 пиши /help")
        return {"status": "ok"}

    if cmd is not None:
        return {"status": "ok"}

    if should_respond(msg, s):
        await typing(cid)
        img = await extract_img(msg)
        at = await ai_response(cid, uname, text, img, creator_flag, friend_flag)
        await send(cid, at)

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "alive", "version": "4.3"}

@app.get("/health")
async def health():
    return {"ok": True}
