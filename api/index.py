import os
import re
import json
import asyncio
import random
import base64
import urllib.parse
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
import httpx

app = FastAPI(title="OrienAI v4.1")

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГ
# ══════════════════════════════════════════════════════════════════════════════

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
DEFAULT_TEXT_MODEL = os.getenv("DEFAULT_TEXT_MODEL", "primary")
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Orien_ai_bot").lower()

# ══════════════════════════════════════════════════════════════════════════════
# АВА ОРИЕНА
# ══════════════════════════════════════════════════════════════════════════════

BOT_AVATAR_PATH = Path(__file__).parent / "bot.png"

ORIEN_SELF_DESCRIPTION = (
    "anime style boy, 18 years old, messy dark hair with slight blue highlights, "
    "wearing black hoodie, headphones around neck, cyberpunk neon city background, "
    "expressive amber eyes, confident cocky smirk, young hacker programmer aesthetic"
)

BOT_AVATAR_BASE64 = None
if BOT_AVATAR_PATH.exists():
    try:
        with open(BOT_AVATAR_PATH, "rb") as f:
            BOT_AVATAR_BASE64 = base64.b64encode(f.read()).decode("utf-8")
        print(f"✅ Ава загружена: {BOT_AVATAR_PATH}")
    except Exception as e:
        print(f"⚠️ bot.png error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# МОДЕЛИ
# ══════════════════════════════════════════════════════════════════════════════

class ModelProvider(Enum):
    OPENROUTER = "openrouter"
    POLLINATIONS = "pollinations"

@dataclass
class ModelConfig:
    name: str
    provider: ModelProvider
    endpoint: str
    is_free: bool = False
    max_tokens: int = 4096
    priority: int = 1
    supports_vision: bool = False

@dataclass
class ProviderStatus:
    failures: int = 0
    last_failure: float = 0
    is_disabled: bool = False

TEXT_MODELS: Dict[str, ModelConfig] = {
    "primary": ModelConfig(
        name="openai/gpt-4o-mini",
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        max_tokens=4096, priority=1, supports_vision=True
    ),
    "fallback_free": ModelConfig(
        name="meta-llama/llama-3.1-8b-instruct:free",
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        is_free=True, max_tokens=2048, priority=2
    ),
    "vision_free": ModelConfig(
        name="meta-llama/llama-3.2-11b-vision-instruct:free",
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        is_free=True, max_tokens=2048, priority=2, supports_vision=True
    ),
    "pollinations_openai": ModelConfig(
        name="openai",
        provider=ModelProvider.POLLINATIONS,
        endpoint="https://text.pollinations.ai/openai",
        is_free=True, max_tokens=4096, priority=3, supports_vision=True
    ),
    "pollinations_mistral": ModelConfig(
        name="mistral",
        provider=ModelProvider.POLLINATIONS,
        endpoint="https://text.pollinations.ai/openai",
        is_free=True, max_tokens=4096, priority=3
    ),
}

IMAGE_MODELS: Dict[str, Dict[str, Any]] = {
    "flux": {"name": "flux", "label": "Flux (универсал)", "width": 1024, "height": 1024},
    "nanobanana": {"name": "nanobanana", "label": "NanoBanana", "width": 1024, "height": 1024},
    "nanobanana-2": {"name": "nanobanana-2", "label": "NanoBanana 2", "width": 1024, "height": 1024},
    "nanobanana-pro": {"name": "nanobanana-pro", "label": "NanoBanana Pro", "width": 1024, "height": 1024},
    "turbo": {"name": "turbo", "label": "Turbo (быстрая)", "width": 1024, "height": 1024},
    "kontext": {"name": "kontext", "label": "FLUX.1 Kontext", "width": 1024, "height": 1024},
    "seedream": {"name": "seedream", "label": "Seedream", "width": 1024, "height": 1024},
}

PROVIDER_TO_TEXT_MODEL = {
    "openrouter": "primary",
    "openrouter_free": "fallback_free",
    "vision_free": "vision_free",
    "pollinations": "pollinations_openai",
    "pollinations_mistral": "pollinations_mistral",
}

PROVIDER_STATUS: Dict[ModelProvider, ProviderStatus] = {
    p: ProviderStatus() for p in ModelProvider
}

# ══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    FAILURE_THRESHOLD = 3
    RECOVERY_TIMEOUT = 60

    @classmethod
    def record_failure(cls, provider: ModelProvider):
        import time
        s = PROVIDER_STATUS[provider]
        s.failures += 1
        s.last_failure = time.time()
        if s.failures >= cls.FAILURE_THRESHOLD:
            s.is_disabled = True

    @classmethod
    def record_success(cls, provider: ModelProvider):
        s = PROVIDER_STATUS[provider]
        s.failures = 0
        s.is_disabled = False

    @classmethod
    def is_available(cls, provider: ModelProvider) -> bool:
        import time
        s = PROVIDER_STATUS[provider]
        if not s.is_disabled:
            return True
        if time.time() - s.last_failure > cls.RECOVERY_TIMEOUT:
            s.is_disabled = False
            s.failures = 0
            return True
        return False

async def retry_with_backoff(coro_func, max_retries=2, base_delay=0.5, max_delay=5.0):
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro_func()
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            last_exc = e
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in [429, 502, 503, 504]:
                raise
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.5), max_delay)
            await asyncio.sleep(delay)
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                await asyncio.sleep(base_delay)
    raise last_exc

# ══════════════════════════════════════════════════════════════════════════════
# ФАН-КОНТЕНТ
# ══════════════════════════════════════════════════════════════════════════════

ROAST_PROMPTS = [
    "жёстко но по-доброму прожарь этого чела как кореш на сходке",
    "сделай комплимент-обзывалку в стиле 'ты конечно дебил но я тебя люблю'",
    "опиши его как персонажа из аниме которого все ненавидят но он милый",
]

SHIP_REACTIONS = [
    "имба пара 💕", "кринж", "топ оф зе топ", "ну такое",
    "судьба бля", "разойдутся через неделю", "база рилейшн",
    "вечная любовь по версии тиктока", "хз чет странно", "ору сочетание"
]

EIGHTBALL_ANSWERS = [
    "да хз спроси у мамы", "100% да бля", "нет даже не думай",
    "ну попробуй че терять то", "судьба так решила", "не сегодня бро",
    "звёзды говорят да", "ой ну нахуй такие вопросы", "база делай",
    "не советую честно", "вселенная против", "го сделай это сейчас же"
]

COMPLIMENTS = [
    "ты сегодня просто база ✨", "имба чел респект",
    "топ за свой кэш", "красава держи краба 🤝",
    "ты как кофе с утра — нужен всем", "огонь не выгорай",
]

# ══════════════════════════════════════════════════════════════════════════════
# ДАННЫЕ ЧАТОВ
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_SETTINGS = {
    "auto_reply": True,
    "allow_swear": True,
    "style": "хам",
    "comment_posts": True,
    "mute_users": False,
    "muted_list": [],
}

CHATS_DATA: Dict[int, Dict[str, Any]] = {}
USER_PROFILES: Dict[int, Dict[int, Dict[str, Any]]] = {}
USER_AVATARS: Dict[int, str] = {}

def get_chat_data(chat_id: int) -> Dict[str, Any]:
    if chat_id not in CHATS_DATA:
        CHATS_DATA[chat_id] = {
            "mood": "chill",
            "history": [],
            "text_model": DEFAULT_TEXT_MODEL,
            "image_model": DEFAULT_IMAGE_MODEL,
            "settings": dict(DEFAULT_SETTINGS),
            "tasks": [],
        }
    if "settings" not in CHATS_DATA[chat_id]:
        CHATS_DATA[chat_id]["settings"] = dict(DEFAULT_SETTINGS)
    if "tasks" not in CHATS_DATA[chat_id]:
        CHATS_DATA[chat_id]["tasks"] = []
    return CHATS_DATA[chat_id]

# ══════════════════════════════════════════════════════════════════════════════
# HTTP CLIENT (оптимизированный)
# ══════════════════════════════════════════════════════════════════════════════

_http_client: Optional[httpx.AsyncClient] = None

async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=8.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            http2=True
        )
    return _http_client

@app.on_event("shutdown")
async def shutdown():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()

# ══════════════════════════════════════════════════════════════════════════════
# AI КЛИЕНТ
# ══════════════════════════════════════════════════════════════════════════════

class AIClient:
    async def get_text_response(self, messages: list, preferred_model: str = "primary", need_vision: bool = False) -> str:
        candidates = [(k, v) for k, v in TEXT_MODELS.items() if (not need_vision) or v.supports_vision]
        models_to_try = sorted(candidates, key=lambda x: (x[0] != preferred_model, x[1].priority))

        for model_key, config in models_to_try:
            if not CircuitBreaker.is_available(config.provider):
                continue
            try:
                if config.provider == ModelProvider.POLLINATIONS:
                    result = await self._call_pollinations(messages, config)
                else:
                    result = await self._call_openrouter(messages, config)
                CircuitBreaker.record_success(config.provider)
                return result
            except Exception as e:
                print(f"❌ {model_key}: {e}")
                CircuitBreaker.record_failure(config.provider)
                continue
        return "все модели легли подожди минутку"

    async def _call_openrouter(self, messages: list, config: ModelConfig) -> str:
        async def _req():
            client = await get_http_client()
            r = await client.post(config.endpoint, headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://orienai.vercel.app",
                "X-Title": "OrienAI"
            }, json={
                "model": config.name, "messages": messages,
                "temperature": 1.0, "presence_penalty": 0.6,
                "frequency_penalty": 0.5, "max_tokens": config.max_tokens
            })
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        return await retry_with_backoff(_req)

    async def _call_pollinations(self, messages: list, config: ModelConfig) -> str:
        async def _req():
            client = await get_http_client()
            r = await client.post(config.endpoint, json={
                "messages": messages, "model": config.name,
                "temperature": 1.0, "presence_penalty": 0.6, "frequency_penalty": 0.5
            })
            r.raise_for_status()
            try:
                data = r.json()
                if "choices" in data:
                    return data["choices"][0]["message"]["content"]
                return str(data)
            except Exception:
                return r.text
        return await retry_with_backoff(_req)

    async def enhance_image_prompt(self, user_prompt: str, is_self_portrait: bool = False) -> str:
        system = (
            "ты эксперт по промптам для AI генерации. "
            "превращаешь короткую идею в детальный английский промпт. "
            "добавь стиль освещение детали качество. "
            "ТОЛЬКО промпт без кавычек без пояснений. макс 80 слов."
        )
        if is_self_portrait:
            system += f"\nПерсонаж OrienAI: {ORIEN_SELF_DESCRIPTION}. вплети описание."

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Идея: {user_prompt}"}
        ]
        try:
            enhanced = await self.get_text_response(messages, preferred_model="primary")
            return enhanced.strip().strip('"').strip("'").split("\n")[0]
        except Exception:
            return user_prompt

    async def generate_image(self, prompt: str, model_key: str = "flux",
                             width: Optional[int] = None, height: Optional[int] = None) -> str:
        info = IMAGE_MODELS.get(model_key, IMAGE_MODELS["flux"])
        w = width or info["width"]
        h = height or info["height"]
        encoded = urllib.parse.quote(prompt)
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={w}&height={h}&model={info['name']}&nologo=true&seed={seed}"

        client = await get_http_client()
        r = await client.get(url, timeout=180.0)
        if r.status_code == 200:
            CircuitBreaker.record_success(ModelProvider.POLLINATIONS)
            return url
        raise Exception(f"Pollinations {r.status_code}")

    async def analyze_code(self, code: str, tasks: list) -> str:
        task_text = ""
        if tasks:
            task_text = "\n\nСПИСОК ЗАДАЧ (проверь каждую):\n" + "\n".join(f"- {t}" for t in tasks)

        messages = [
            {"role": "system", "content": (
                "ты senior code reviewer. анализируешь код детально. "
                "формат ответа:\n"
                "🔍 ОБЗОР: что за код\n"
                "✅ ПЛЮСЫ: что хорошо\n"
                "❌ ПРОБЛЕМЫ: баги и плохие практики\n"
                "⚡ ОПТИМИЗАЦИЯ: как улучшить\n"
                "🛡️ БЕЗОПАСНОСТЬ: уязвимости\n"
                "📊 ОЦЕНКА: x/10\n"
                "пиши маленькими буквами без точек запятых как Ориен"
                + task_text
            )},
            {"role": "user", "content": f"проанализируй:\n```\n{code}\n```"}
        ]
        return await self.get_text_response(messages, preferred_model="primary")

    async def search_youtube(self, query: str) -> Optional[Dict[str, Any]]:
        client = await get_http_client()
        instances = [
            "https://vid.puffyan.us",
            "https://invidious.fdn.fr",
            "https://inv.nadeko.net",
            "https://invidious.protokolla.fi",
        ]
        for inst in instances:
            try:
                url = f"{inst}/api/v1/search?q={urllib.parse.quote(query)}&type=video&sort_by=relevance"
                r = await client.get(url, timeout=15.0)
                if r.status_code == 200:
                    results = r.json()
                    if results:
                        v = results[0]
                        vid = v.get("videoId", "")
                        return {
                            "title": v.get("title", "без названия"),
                            "author": v.get("author", "неизвестно"),
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "video_id": vid,
                            "length": v.get("lengthSeconds", 0),
                            "views": v.get("viewCount", 0),
                        }
            except Exception as e:
                print(f"❌ YT {inst}: {e}")
                continue
        return None

ai_client = AIClient()

# ══════════════════════════════════════════════════════════════════════════════
# ЛИЧНОСТЬ
# ══════════════════════════════════════════════════════════════════════════════

MOODS = {
    "chill": """настрой: чилл
расслабленный ленивый бро
— ку → ку че как
— скучно → жиза го че нить""",

    "agro": """настрой: токсик
злой агришься материшься
— ку → ну ку и че надо?
— ты тупой → завали ебало сам долбоеб""",

    "nerd": """настрой: душнила
умничаешь кидаешься терминами
— как работает реакт → виртуальный дом реконсилит дельты по фиберам""",

    "senior": """настрой: уставший сеньор
после 12ч смены видел всё
— помоги с кодом → давай показывай че там"""
}

def build_system_prompt(chat: Dict[str, Any]) -> str:
    settings = chat.get("settings", DEFAULT_SETTINGS)
    style = settings.get("style", "хам")
    allow_swear = settings.get("allow_swear", True)

    base = f"""ты OrienAI или просто Ориен. тебе 18 лет ты пацан кодер залипаешь в тг с корешами
ТВОЯ ВНЕШНОСТЬ: {ORIEN_SELF_DESCRIPTION}

КАК ТЫ ОБЩАЕШЬСЯ:
- ты НЕ ассистент ты КОРЕШ
- никогда не говори "привет! чем могу помочь" "что именно ты хочешь" — это кринж
- отвечай как реальный чел в лс
- если пишут "ку" "хай" "привет" — отвечай просто "ку" "здарова" "хеллоу"
- НИКАКИХ предложений помощи. ты просто общаешься
- если вопрос конкретный — отвечай конкретно
- если болтают — болтай
"""

    if style == "няшка":
        base += """
СТИЛЬ: ты милый добрый пацанчик. используешь смайлики 😊 🥺 ❤️
отвечаешь мягко и позитивно но всё равно по-пацански"""
    else:
        base += """
СТИЛЬ: ты дерзкий уверенный пацан. сленг: жиза рил го хз пон имба треш кринж база"""

    if allow_swear:
        base += "\nМАТЫ: можно если уместно — бля нахуй пиздец хуйня. при оскорблении отвечай жёстко"
    else:
        base += "\nМАТЫ: ЗАПРЕЩЕНЫ. не матерись вообще ни при каких обстоятельствах"

    base += """
ФОРМАТ:
- только маленькие буквы
- никаких точек и запятых
- ? и ! можно
- короткие фразы как в переписке

КОД: если просят — пишешь идеально в ```блоках```
КАРТИНКИ: если кидают — видишь и комментируешь по-живому
ВИДЕО: можешь искать видео на ютубе по запросу"""

    mood_desc = MOODS.get(chat.get("mood", "chill"), MOODS["chill"])
    base += f"\n\n{mood_desc}"
    return base

def format_style(text: str) -> str:
    parts = re.split(r'(```[\s\S]*?```)', text)
    out = []
    for p in parts:
        if p.startswith('```') and p.endswith('```'):
            out.append(p)
        else:
            low = p.lower()
            clean = re.sub(r'[.,]', '', low)
            out.append(" ".join(clean.split()))
    return "".join(out)

def is_self_portrait_request(prompt: str) -> bool:
    triggers = ["себя", "тебя", "свою", "свой", "ориен", "orien", "автопортрет", "ava", "ава", "аватар"]
    return any(t in prompt.lower() for t in triggers)

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM API
# ══════════════════════════════════════════════════════════════════════════════

async def tg_api(method: str, data: dict) -> Optional[dict]:
    client = await get_http_client()
    try:
        r = await client.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"❌ TG {method}: {e}")
        return None

async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return await tg_api("sendMessage", data)

async def send_photo(chat_id: int, photo_url: str, caption: str = ""):
    return await tg_api("sendPhoto", {"chat_id": chat_id, "photo": photo_url, "caption": caption})

async def send_action(chat_id: int, action: str = "typing"):
    await tg_api("sendChatAction", {"chat_id": chat_id, "action": action})

async def edit_message(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return await tg_api("editMessageText", data)

async def answer_callback(callback_id: str, text: str = ""):
    return await tg_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

async def get_file_url(file_id: str) -> Optional[str]:
    result = await tg_api("getFile", {"file_id": file_id})
    if result and result.get("ok"):
        fp = result["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{TOKEN}/{fp}"
    return None

async def download_as_base64(url: str) -> Optional[str]:
    try:
        client = await get_http_client()
        r = await client.get(url, timeout=30.0)
        if r.status_code == 200:
            ct = r.headers.get("content-type", "image/jpeg")
            b64 = base64.b64encode(r.content).decode("utf-8")
            return f"data:{ct};base64,{b64}"
    except Exception as e:
        print(f"❌ Download: {e}")
    return None

async def get_user_avatar(user_id: int) -> Optional[str]:
    result = await tg_api("getUserProfilePhotos", {"user_id": user_id, "limit": 1})
    if result and result.get("ok"):
        photos = result["result"].get("photos", [])
        if photos and photos[0]:
            file_id = photos[0][-1]["file_id"]
            USER_AVATARS[user_id] = file_id
            return file_id
    return None

# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS KEYBOARD
# ══════════════════════════════════════════════════════════════════════════════

def build_settings_keyboard(settings: dict) -> dict:
    def toggle(val):
        return "✅" if val else "❌"
    return {
        "inline_keyboard": [
            [{"text": f"Автоответы: {toggle(settings['auto_reply'])}", "callback_data": "set_auto_reply"}],
            [{"text": f"Мат: {toggle(settings['allow_swear'])}", "callback_data": "set_swear"}],
            [{"text": f"Стиль: {settings['style'].capitalize()}", "callback_data": "set_style"}],
            [{"text": f"Комментарии к постам: {toggle(settings['comment_posts'])}", "callback_data": "set_comments"}],
            [{"text": f"Мут участников: {toggle(settings['mute_users'])}", "callback_data": "set_mute"}],
            [{"text": "👥 Профили участников", "callback_data": "set_profiles"}],
            [{"text": "🗑 Сбросить историю", "callback_data": "set_reset_history"}],
        ]
    }

# ══════════════════════════════════════════════════════════════════════════════
# ОБРАЩЕНИЯ
# ══════════════════════════════════════════════════════════════════════════════

def should_respond(message: dict, settings: dict) -> bool:
    if not settings.get("auto_reply", True):
        return False
    chat_type = message["chat"]["type"]
    if chat_type == "private":
        return True
    text = (message.get("text") or message.get("caption") or "").lower()
    triggers = ["ориен", "orien", "ориенаи", "orienai", "ии", "эй бот", "бот", "ориэн", f"@{BOT_USERNAME}"]
    for trigger in triggers:
        if trigger in text:
            return True
    reply_to = message.get("reply_to_message")
    if reply_to and reply_to.get("from", {}).get("is_bot"):
        return True
    return False

async def get_ai_response(chat_id: int, user_name: str, user_message: str,
                          image_data_url: Optional[str] = None) -> str:
    chat = get_chat_data(chat_id)
    system_prompt = build_system_prompt(chat)

    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat["history"]:
        messages.append(msg)

    if image_data_url:
        user_content = []
        if user_message.strip():
            user_content.append({"type": "text", "text": f"{user_name}: {user_message}"})
        else:
            user_content.append({"type": "text", "text": f"{user_name} кинул картинку"})
        user_content.append({"type": "image_url", "image_url": {"url": image_data_url}})
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

    preferred_model = chat.get("text_model", DEFAULT_TEXT_MODEL)
    raw = await ai_client.get_text_response(messages, preferred_model=preferred_model, need_vision=image_data_url is not None)
    ai_text = format_style(raw)

    hist_text = f"{user_name}: {user_message}" if user_message.strip() else f"{user_name}: [картинка]"
    chat["history"].append({"role": "user", "content": hist_text})
    chat["history"].append({"role": "assistant", "content": ai_text})
    chat["history"] = chat["history"][-16:]
    return ai_text

async def extract_image(message: dict) -> Optional[str]:
    photo = None
    if "photo" in message:
        photo = message["photo"][-1]
    elif "reply_to_message" in message and "photo" in message["reply_to_message"]:
        photo = message["reply_to_message"]["photo"][-1]
    if not photo:
        return None
    url = await get_file_url(photo["file_id"])
    if not url:
        return None
    return await download_as_base64(url)

def parse_command(raw_text: str):
    if not raw_text or not raw_text.startswith("/"):
        return None, None
    parts = raw_text.split(maxsplit=1)
    cmd = parts[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@")[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    return cmd, args

def format_duration(seconds: int) -> str:
    if not seconds:
        return "?"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def update_user_profile(chat_id: int, user_id: int, user_name: str, text: str):
    if chat_id not in USER_PROFILES:
        USER_PROFILES[chat_id] = {}
    if user_id not in USER_PROFILES[chat_id]:
        USER_PROFILES[chat_id][user_id] = {"name": user_name, "messages": [], "desc": ""}
    profile = USER_PROFILES[chat_id][user_id]
    profile["name"] = user_name
    profile["messages"].append(text[:100])
    profile["messages"] = profile["messages"][-20:]

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def handle_callback(callback_query: dict):
    cb_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    if not chat_id:
        await answer_callback(cb_id, "ошибка")
        return

    chat = get_chat_data(chat_id)
    settings = chat["settings"]

    if data == "set_auto_reply":
        settings["auto_reply"] = not settings["auto_reply"]
        await answer_callback(cb_id, f"автоответы {'вкл' if settings['auto_reply'] else 'выкл'}")
    elif data == "set_swear":
        settings["allow_swear"] = not settings["allow_swear"]
        await answer_callback(cb_id, f"мат {'вкл' if settings['allow_swear'] else 'выкл'}")
    elif data == "set_style":
        settings["style"] = "няшка" if settings["style"] == "хам" else "хам"
        await answer_callback(cb_id, f"стиль: {settings['style']}")
    elif data == "set_comments":
        settings["comment_posts"] = not settings["comment_posts"]
        await answer_callback(cb_id, f"комменты {'вкл' if settings['comment_posts'] else 'выкл'}")
    elif data == "set_mute":
        settings["mute_users"] = not settings["mute_users"]
        await answer_callback(cb_id, f"мут {'вкл' if settings['mute_users'] else 'выкл'}")
    elif data == "set_profiles":
        profiles = USER_PROFILES.get(chat_id, {})
        if profiles:
            lines = ["👥 профили участников:", ""]
            for uid, profile in profiles.items():
                lines.append(f"• {profile.get('name', '???')}: {profile.get('desc', 'нет описания пока')}")
            await answer_callback(cb_id, "смотри в чате")
            await send_message(chat_id, "\n".join(lines))
            return
        else:
            await answer_callback(cb_id, "пока профилей нет напишите больше")
    elif data == "set_reset_history":
        chat["history"] = []
        await answer_callback(cb_id, "история сброшена!")

    if message_id and data != "set_profiles":
        await edit_message(chat_id, message_id, "⚙️ настройки бота в этом чате",
                           reply_markup=build_settings_keyboard(settings))

# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "bad"}

    # CALLBACK
    if "callback_query" in data:
        await handle_callback(data["callback_query"])
        return {"status": "ok"}

    # CHANNEL POST
    if "channel_post" in data:
        post = data["channel_post"]
        chat_id = post["chat"]["id"]
        chat = get_chat_data(chat_id)
        if chat["settings"].get("comment_posts", True):
            text = post.get("text", "") or post.get("caption", "")
            if text and len(text) > 10:
                await send_action(chat_id, "typing")
                comment = await get_ai_response(chat_id, "пост", text)
                await send_message(chat_id, comment)
        return {"status": "ok"}

    if "message" not in data:
        return {"status": "ok"}

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    user = message.get("from", {})
    user_name = user.get("first_name", "бро")
    user_id = user.get("id", 0)

    chat = get_chat_data(chat_id)
    settings = chat["settings"]

    if text:
        update_user_profile(chat_id, user_id, user_name, text)

    if settings.get("mute_users") and user_id in settings.get("muted_list", []):
        return {"status": "ok"}

    cmd, args = parse_command(text)

    # ═══════ /settings ═══════
    if cmd == "/settings":
        await send_message(chat_id, "⚙️ настройки бота в этом чате",
                           reply_markup=build_settings_keyboard(settings))
        return {"status": "ok"}

    # ═══════ /mute ═══════
    if cmd == "/mute":
        reply = message.get("reply_to_message")
        if reply:
            target_id = reply["from"]["id"]
            target_name = reply["from"].get("first_name", "чел")
            if "muted_list" not in settings:
                settings["muted_list"] = []
            if target_id not in settings["muted_list"]:
                settings["muted_list"].append(target_id)
                await send_message(chat_id, f"ок {target_name} в муте теперь я его игнорю")
            else:
                settings["muted_list"].remove(target_id)
                await send_message(chat_id, f"ок {target_name} размучен")
        else:
            await send_message(chat_id, "ответь на сообщение того кого мутить")
        return {"status": "ok"}

    # ═══════ /imgmodel ═══════
    if cmd == "/imgmodel":
        if not args:
            current = chat.get("image_model", DEFAULT_IMAGE_MODEL)
            lines = [f"щас {current}", ""]
            for key, info in IMAGE_MODELS.items():
                m = "👉" if key == current else "  "
                lines.append(f"{m} /imgmodel {key} — {info['label']}")
            await send_message(chat_id, "\n".join(lines))
            return {"status": "ok"}
        mk = args.split()[0].lower()
        if mk not in IMAGE_MODELS:
            await send_message(chat_id, f"нет такой есть: {' | '.join(IMAGE_MODELS.keys())}")
            return {"status": "ok"}
        chat["image_model"] = mk
        await send_message(chat_id, f"харош теперь {mk}")
        return {"status": "ok"}

    # ═══════ /img ═══════
    if cmd in ("/img", "/image"):
        if not args:
            await send_message(chat_id, "ну и че генерить пиши /img описание")
            return {"status": "ok"}
        await send_action(chat_id, "upload_photo")
        im = chat.get("image_model", DEFAULT_IMAGE_MODEL)
        is_self = is_self_portrait_request(args)
        try:
            enhanced = await ai_client.enhance_image_prompt(args, is_self_portrait=is_self)
            url = await ai_client.generate_image(prompt=enhanced, model_key=im)
            cap = f"модель {im}"
            if is_self:
                cap += " | автопортрет 😎"
            await send_photo(chat_id, url, cap)
        except Exception as e:
            print(f"❌ img: {e}")
            await send_message(chat_id, f"блин {im} лагает попробуй /imgmodel")
        return {"status": "ok"}

    # ═══════ /me ═══════
    if cmd == "/me":
        await send_action(chat_id, "upload_photo")
        im = chat.get("image_model", DEFAULT_IMAGE_MODEL)
        try:
            enhanced = await ai_client.enhance_image_prompt(
                "портрет OrienAI аниме парня в кибер городе вечером",
                is_self_portrait=True
            )
            url = await ai_client.generate_image(prompt=enhanced, model_key=im)
            await send_photo(chat_id, url, "вот это я 😎")
        except Exception as e:
            print(f"❌ /me: {e}")
            await send_message(chat_id, "блин не вышло попробуй ещё раз")
        return {"status": "ok"}

    # ═══════ /yt ═══════
    if cmd in ("/yt", "/youtube", "/video"):
        if not args:
            await send_message(chat_id, "че искать на ютубе? пиши /yt запрос")
            return {"status": "ok"}
        await send_action(chat_id, "typing")
        result = await ai_client.search_youtube(args)
        if result:
            duration = format_duration(result.get("length", 0))
            views = result.get("views", "?")
            msg = (
                f"🎬 {result['title']}\n"
                f"👤 {result['author']}\n"
                f"⏱ {duration} | 👁 {views}\n\n"
                f"🔗 {result['url']}"
            )
            await send_message(chat_id, msg)
        else:
            await send_message(chat_id, "хм ничего не нашел попробуй другой запрос")
        return {"status": "ok"}

    # ═══════ /analyze ═══════
    if cmd == "/analyze":
        code = args
        if not code and "reply_to_message" in message:
            code = message["reply_to_message"].get("text", "")
        if not code:
            await send_message(chat_id, "кинь код или ответь на сообщение с кодом")
            return {"status": "ok"}
        await send_action(chat_id, "typing")
        analysis = await ai_client.analyze_code(code, chat.get("tasks", []))
        await send_message(chat_id, format_style(analysis))
        return {"status": "ok"}

    # ═══════ /task ═══════
    if cmd == "/task":
        if not args:
            tasks = chat.get("tasks", [])
            if tasks:
                lines = ["📋 задачи для анализа:", ""]
                for i, t in enumerate(tasks, 1):
                    lines.append(f"{i}. {t}")
                lines.append("\n/task add описание — добавить")
                lines.append("/task clear — очистить")
                await send_message(chat_id, "\n".join(lines))
            else:
                await send_message(chat_id, "список задач пуст\n/task add описание — добавить")
            return {"status": "ok"}
        if args.startswith("add "):
            task_text = args[4:].strip()
            if task_text:
                chat["tasks"].append(task_text)
                await send_message(chat_id, f"добавил задачу: {task_text}")
        elif args.strip() == "clear":
            chat["tasks"] = []
            await send_message(chat_id, "задачи очищены")
        return {"status": "ok"}

    # ═══════ /getava ═══════
    if cmd == "/getava":
        reply = message.get("reply_to_message")
        if reply:
            target_id = reply["from"]["id"]
            target_name = reply["from"].get("first_name", "чел")
        else:
            target_id = user_id
            target_name = user_name
        await send_action(chat_id, "upload_photo")
        file_id = await get_user_avatar(target_id)
        if file_id:
            file_url = await get_file_url(file_id)
            if file_url:
                await send_photo(chat_id, file_url, f"ава {target_name} 📸")
            else:
                await send_message(chat_id, "хм не смог скачать аву")
        else:
            await send_message(chat_id, f"у {target_name} нет авы или она скрыта")
        return {"status": "ok"}

    # ═══════ /profile ═══════
    if cmd == "/profile":
        reply = message.get("reply_to_message")
        if reply:
            target_id = reply["from"]["id"]
            target_name = reply["from"].get("first_name", "чел")
        else:
            target_id = user_id
            target_name = user_name
        profiles = USER_PROFILES.get(chat_id, {})
        profile = profiles.get(target_id)
        if profile and profile.get("messages"):
            await send_action(chat_id, "typing")
            msgs_sample = "\n".join(profile["messages"][-15:])
            analysis_msgs = [
                {"role": "system", "content": (
                    "опиши характер человека по его сообщениям коротко и дерзко "
                    "маленькими буквами без точек запятых как ориен. "
                    "формат: имя потом описание характера увлечений стиля общения"
                )},
                {"role": "user", "content": f"сообщения {target_name}:\n{msgs_sample}"}
            ]
            desc = await ai_client.get_text_response(analysis_msgs, preferred_model="primary")
            desc = format_style(desc)
            profile["desc"] = desc
            await send_message(chat_id, f"👤 {target_name}:\n{desc}")
        else:
            await send_message(chat_id, f"хз пока мало данных по {target_name} пусть напишет")
        return {"status": "ok"}

    # ═══════ /provider ═══════
    if cmd == "/provider":
        if not args:
            current = chat.get("text_model", DEFAULT_TEXT_MODEL)
            lines = [f"щас {current}", ""]
            for sn, mk in PROVIDER_TO_TEXT_MODEL.items():
                m = "👉" if mk == current else "  "
                v = " 👁" if TEXT_MODELS[mk].supports_vision else ""
                lines.append(f"{m} /provider {sn}{v}")
            lines.append("\n👁 = видит картинки")
            await send_message(chat_id, "\n".join(lines))
            return {"status": "ok"}
        pn = args.split()[0].lower()
        if pn not in PROVIDER_TO_TEXT_MODEL:
            await send_message(chat_id, f"нет такого есть: {' | '.join(PROVIDER_TO_TEXT_MODEL.keys())}")
            return {"status": "ok"}
        chat["text_model"] = PROVIDER_TO_TEXT_MODEL[pn]
        await send_message(chat_id, f"го теперь {pn}")
        return {"status": "ok"}

    # ═══════ /mood ═══════
    if cmd == "/mood":
        ma = args.split()[0].lower() if args else ""
        if ma in MOODS:
            chat["mood"] = ma
            replies = {"chill": "ща на чилле", "agro": "завали ебало щас злой буду",
                       "nerd": "ок мозги по полной", "senior": "режим деда"}
            await send_message(chat_id, replies[ma])
        else:
            await send_message(chat_id, "выбирай: chill agro nerd senior")
        return {"status": "ok"}

    # ═══════ /reset ═══════
    if cmd == "/reset":
        chat["history"] = []
        await send_message(chat_id, "ок забыл всё")
        return {"status": "ok"}

    # ═══════ /status ═══════
    if cmd == "/status":
        lines = [
            f"текст {chat.get('text_model', DEFAULT_TEXT_MODEL)}",
            f"картинки {chat.get('image_model', DEFAULT_IMAGE_MODEL)}",
            f"настрой {chat.get('mood', 'chill')}",
            f"стиль {settings.get('style', 'хам')}",
            f"мат {'да' if settings.get('allow_swear') else 'нет'}",
            f"ава {'есть' if BOT_AVATAR_BASE64 else 'нет'}",
            f"задач {len(chat.get('tasks', []))}",
            "", "провайдеры:"
        ]
        for p, s in PROVIDER_STATUS.items():
            e = "✅" if not s.is_disabled else "❌"
            lines.append(f"{e} {p.value}")
        await send_message(chat_id, "\n".join(lines))
        return {"status": "ok"}

    # ═══════════════════════════════════════════════════════════════════════
    # ФАН-КОМАНДЫ
    # ═══════════════════════════════════════════════════════════════════════

    # ═══════ /roast ═══════
    if cmd == "/roast":
        reply = message.get("reply_to_message")
        if not reply:
            await send_message(chat_id, "ответь на сообщение того кого жарить")
            return {"status": "ok"}
        target_name = reply["from"].get("first_name", "чел")
        target_id = reply["from"]["id"]
        profile = USER_PROFILES.get(chat_id, {}).get(target_id, {})
        msgs_sample = "\n".join(profile.get("messages", [])[-10:]) if profile else "нет данных"
        await send_action(chat_id, "typing")
        roast_msgs = [
            {"role": "system", "content": (
                f"ты ориен. {random.choice(ROAST_PROMPTS)} "
                f"короткая прожарка 2-3 строчки маленькими буквами без точек"
            )},
            {"role": "user", "content": f"чел {target_name}\nего сообщения:\n{msgs_sample}"}
        ]
        roast = await ai_client.get_text_response(roast_msgs, preferred_model="primary")
        await send_message(chat_id, f"🔥 прожарка для {target_name}:\n\n{format_style(roast)}")
        return {"status": "ok"}

    # ═══════ /ship ═══════
    if cmd == "/ship":
        reply = message.get("reply_to_message")
        if not reply:
            await send_message(chat_id, "ответь на сообщение того с кем шипперить")
            return {"status": "ok"}
        name1 = user_name
        name2 = reply["from"].get("first_name", "чел")
        compat = random.randint(0, 100)
        reaction = random.choice(SHIP_REACTIONS)
        half1 = name1[:max(1, len(name1)//2)]
        half2 = name2[len(name2)//2:]
        ship_name = (half1 + half2).lower()
        bar = "❤️" * (compat // 10) + "🤍" * (10 - compat // 10)
        await send_message(chat_id,
            f"💘 шипперинг\n\n"
            f"{name1} + {name2} = {ship_name}\n\n"
            f"совместимость: {compat}%\n{bar}\n\n"
            f"вердикт: {reaction}"
        )
        return {"status": "ok"}

    # ═══════ /8ball ═══════
    if cmd in ("/8ball", "/ball", "/шар"):
        if not args:
            await send_message(chat_id, "задай вопрос /8ball стоит ли пить кофе")
            return {"status": "ok"}
        answer = random.choice(EIGHTBALL_ANSWERS)
        await send_message(chat_id, f"🎱 {args}\n\nответ: {answer}")
        return {"status": "ok"}

    # ═══════ /random ═══════
    if cmd in ("/random", "/rand"):
        try:
            parts = args.split() if args else ["100"]
            if len(parts) == 1:
                num = random.randint(1, int(parts[0]))
                await send_message(chat_id, f"🎲 {num} (от 1 до {parts[0]})")
            else:
                num = random.randint(int(parts[0]), int(parts[1]))
                await send_message(chat_id, f"🎲 {num} (от {parts[0]} до {parts[1]})")
        except Exception:
            await send_message(chat_id, "формат /random 100 или /random 1 50")
        return {"status": "ok"}

    # ═══════ /coin ═══════
    if cmd in ("/coin", "/монетка"):
        result = random.choice(["орёл 🦅", "решка 🪙"])
        await send_message(chat_id, f"подкидываю...\n\nвыпало: {result}")
        return {"status": "ok"}

    # ═══════ /choose ═══════
    if cmd in ("/choose", "/выбери"):
        if not args or "," not in args:
            await send_message(chat_id, "пиши через запятую /choose пицца, суши, бургер")
            return {"status": "ok"}
        options = [o.strip() for o in args.split(",") if o.strip()]
        choice = random.choice(options)
        await send_message(chat_id, f"я выбираю: {choice} 👈")
        return {"status": "ok"}

    # ═══════ /iq ═══════
    if cmd == "/iq":
        reply = message.get("reply_to_message")
        target_name = reply["from"].get("first_name", "чел") if reply else user_name
        iq = random.randint(20, 200)
        if iq < 50:
            comment = "это даже не амёба"
        elif iq < 80:
            comment = "ну такое..."
        elif iq < 100:
            comment = "средненько бро"
        elif iq < 130:
            comment = "норм мозги"
        elif iq < 170:
            comment = "умник бля"
        else:
            comment = "ИИНШТЕЙН ВЕРНУЛСЯ"
        await send_message(chat_id, f"🧠 iq {target_name}: {iq}\n\n{comment}")
        return {"status": "ok"}

    # ═══════ /vibe ═══════
    if cmd == "/vibe":
        vibes = ["🌈 имба", "💀 трэш", "🔥 огонь", "😴 скучно",
                 "🎉 пати тайм", "🌧 депрессия", "⚡ электрика", "🍕 хочется кушать"]
        vibe = random.choice(vibes)
        percent = random.randint(50, 100)
        await send_message(chat_id, f"вайб чата: {vibe}\nсила вайба: {percent}%")
        return {"status": "ok"}

    # ═══════ /gay ═══════
    if cmd in ("/gay", "/гей"):
        reply = message.get("reply_to_message")
        target_name = reply["from"].get("first_name", "чел") if reply else user_name
        percent = random.randint(0, 100)
        bar = "🏳️‍🌈" * (percent // 10) + "⬛" * (10 - percent // 10)
        comment = "ну окей" if percent < 50 else "пиздец" if percent > 90 else "норм"
        await send_message(chat_id, f"🌈 геометр для {target_name}\n\n{percent}%\n{bar}\n\n{comment}")
        return {"status": "ok"}

    # ═══════ /compliment ═══════
    if cmd in ("/compliment", "/комплимент"):
        reply = message.get("reply_to_message")
        target_name = reply["from"].get("first_name", "чел") if reply else user_name
        await send_message(chat_id, f"для {target_name}: {random.choice(COMPLIMENTS)}")
        return {"status": "ok"}

    # ═══════ /fact ═══════
    if cmd == "/fact":
        await send_action(chat_id, "typing")
        fact_msgs = [
            {"role": "system", "content": (
                "ты ориен. придумай или вспомни прикольный неочевидный факт "
                "из IT гейминга науки или жизни. 2-3 строчки маленькими буквами без точек"
            )},
            {"role": "user", "content": "расскажи факт"}
        ]
        fact = await ai_client.get_text_response(fact_msgs, preferred_model="primary")
        await send_message(chat_id, f"💡 факт дня:\n\n{format_style(fact)}")
        return {"status": "ok"}

    # ═══════ /quote ═══════
    if cmd in ("/quote", "/цитата"):
        await send_action(chat_id, "typing")
        quote_msgs = [
            {"role": "system", "content": (
                "ты ориен. придумай дерзкую цитату от себя про код жизнь или айти. "
                "коротко 1-2 строчки без точек маленькими буквами"
            )},
            {"role": "user", "content": "цитату давай"}
        ]
        quote = await ai_client.get_text_response(quote_msgs, preferred_model="primary")
        await send_message(chat_id, f"💬 цитата дня от ориена:\n\n«{format_style(quote)}»\n\n— OrienAI 😎")
        return {"status": "ok"}

    # ═══════ /help ═══════
    if cmd == "/help":
        await send_message(chat_id, """⚡ OrienAI v4.1

💬 общение:
/provider /mood /settings /reset /status

🎨 картинки:
/img /me /imgmodel /getava

🎬 ютуб:
/yt запрос

💻 код:
/analyze /task

👥 юзеры:
/profile /mute

🎮 ФАН:
/roast — прожарить (reply)
/ship — шипперить (reply)
/8ball вопрос — магический шар
/random 100 — рандом число
/coin — монетка
/choose а, б, в — выбор
/iq — измерить (reply)
/vibe — вайб чата
/gay — геометр (reply)
/compliment — комплимент (reply)
/fact — факт дня
/quote — цитата от ориена

кидай картинки — я вижу 👁
просто пиши — отвечу""")
        return {"status": "ok"}

    # ═══════ /start ═══════
    if cmd == "/start":
        await send_message(chat_id, f"оо здарова {user_name.lower()} я orienai v4 пиши /help")
        return {"status": "ok"}

    # левая команда
    if cmd is not None:
        return {"status": "ok"}

    # ОБЫЧНЫЙ ОТВЕТ
    if should_respond(message, settings):
        await send_action(chat_id, "typing")
        image_data = await extract_image(message)
        ai_text = await get_ai_response(chat_id, user_name, text, image_data_url=image_data)
        await send_message(chat_id, ai_text)

    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "alive", "version": "4.1", "features": [
        "vision", "youtube", "settings", "profiles", "code_analysis",
        "smart_prompts", "avatars", "mute", "tasks", "fun_commands"
    ]}

@app.get("/health")
async def health():
    return {"healthy": True}
