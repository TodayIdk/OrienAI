import os
import re
import asyncio
import random
import urllib.parse
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
import httpx

app = FastAPI(title="OrienAI v2.1", description="Мощный ассистент с генерацией изображений")

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")

DEFAULT_TEXT_MODEL = os.getenv("DEFAULT_TEXT_MODEL", "primary")
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")

# ══════════════════════════════════════════════════════════════════════════════
# ПРОВАЙДЕРЫ И МОДЕЛИ
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

@dataclass
class ProviderStatus:
    failures: int = 0
    last_failure: float = 0
    is_disabled: bool = False

# === ТЕКСТОВЫЕ МОДЕЛИ ===
TEXT_MODELS: Dict[str, ModelConfig] = {
    "primary": ModelConfig(
        name="openai/gpt-4o-mini",
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        max_tokens=4096,
        priority=1
    ),
    "fallback_free": ModelConfig(
        name="meta-llama/llama-3.1-8b-instruct:free",
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        is_free=True,
        max_tokens=2048,
        priority=2
    ),
    "pollinations_openai": ModelConfig(
        name="openai",
        provider=ModelProvider.POLLINATIONS,
        endpoint="https://text.pollinations.ai/openai",
        is_free=True,
        max_tokens=4096,
        priority=3
    ),
    "pollinations_mistral": ModelConfig(
        name="mistral",
        provider=ModelProvider.POLLINATIONS,
        endpoint="https://text.pollinations.ai/openai",
        is_free=True,
        max_tokens=4096,
        priority=3
    ),
}

# === IMAGE МОДЕЛИ (Pollinations) ===
# Реальные модели которые работают через image.pollinations.ai
IMAGE_MODELS: Dict[str, Dict[str, Any]] = {
    "flux": {
        "name": "flux",
        "label": "Flux (универсал, дефолт)",
        "width": 1024,
        "height": 1024
    },
    "flux-schnell": {
        "name": "flux",
        "label": "Flux Schnell (быстрая)",
        "width": 1024,
        "height": 1024
    },
    "turbo": {
        "name": "turbo",
        "label": "Turbo (быстрая)",
        "width": 1024,
        "height": 1024
    },
    "nanobanana": {
        "name": "nanobanana",
        "label": "NanoBanana (стилизация)",
        "width": 1024,
        "height": 1024
    },
    "kontext": {
        "name": "kontext",
        "label": "FLUX.1 Kontext",
        "width": 1024,
        "height": 1024
    },
    "seedream": {
        "name": "seedream",
        "label": "Seedream",
        "width": 1024,
        "height": 1024
    },
    "gptimage": {
        "name": "gptimage",
        "label": "GPT Image",
        "width": 1024,
        "height": 1024
    },
}

# Маппинг короткое имя провайдера -> ключ модели
PROVIDER_TO_TEXT_MODEL = {
    "openrouter": "primary",
    "openrouter_free": "fallback_free",
    "pollinations": "pollinations_openai",
    "pollinations_mistral": "pollinations_mistral",
}

PROVIDER_STATUS: Dict[ModelProvider, ProviderStatus] = {
    provider: ProviderStatus() for provider in ModelProvider
}

# ══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER + RETRY
# ══════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    FAILURE_THRESHOLD = 3
    RECOVERY_TIMEOUT = 60

    @classmethod
    def record_failure(cls, provider: ModelProvider):
        import time
        status = PROVIDER_STATUS[provider]
        status.failures += 1
        status.last_failure = time.time()
        if status.failures >= cls.FAILURE_THRESHOLD:
            status.is_disabled = True
            print(f"⚠️ Circuit breaker открыт для {provider.value}")

    @classmethod
    def record_success(cls, provider: ModelProvider):
        status = PROVIDER_STATUS[provider]
        status.failures = 0
        status.is_disabled = False

    @classmethod
    def is_available(cls, provider: ModelProvider) -> bool:
        import time
        status = PROVIDER_STATUS[provider]
        if not status.is_disabled:
            return True
        if time.time() - status.last_failure > cls.RECOVERY_TIMEOUT:
            status.is_disabled = False
            status.failures = 0
            return True
        return False


async def retry_with_backoff(coro_func, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 10.0):
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await coro_func()
        except httpx.TimeoutException as e:
            last_exception = e
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
            print(f"⏳ Таймаут, попытка {attempt + 1}/{max_retries}, жду {delay:.2f}с")
            await asyncio.sleep(delay)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in [429, 502, 503, 504]:
                last_exception = e
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                print(f"⚠️ HTTP {e.response.status_code}, попытка {attempt + 1}/{max_retries}")
                await asyncio.sleep(delay)
            else:
                raise
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                await asyncio.sleep(delay)
    raise last_exception

# ══════════════════════════════════════════════════════════════════════════════
# AI КЛИЕНТ
# ══════════════════════════════════════════════════════════════════════════════

class AIClient:
    def __init__(self):
        self.timeout = httpx.Timeout(30.0, connect=10.0)

    async def get_text_response(self, messages: list, preferred_model: str = "primary") -> str:
        models_to_try = sorted(
            TEXT_MODELS.items(),
            key=lambda x: (x[0] != preferred_model, x[1].priority)
        )

        for model_key, model_config in models_to_try:
            if not CircuitBreaker.is_available(model_config.provider):
                print(f"⏭️ Пропускаю {model_key} — circuit breaker открыт")
                continue
            try:
                print(f"🔄 Пробую {model_key} ({model_config.provider.value})")
                if model_config.provider == ModelProvider.POLLINATIONS:
                    result = await self._call_pollinations_text(messages, model_config)
                elif model_config.provider == ModelProvider.OPENROUTER:
                    result = await self._call_openrouter(messages, model_config)
                else:
                    continue
                CircuitBreaker.record_success(model_config.provider)
                return result
            except Exception as e:
                print(f"❌ Ошибка {model_key}: {e}")
                CircuitBreaker.record_failure(model_config.provider)
                continue

        return "блин все провайдеры легли одновременно подожди минутку"

    async def _call_openrouter(self, messages: list, config: ModelConfig) -> str:
        async def _request():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    config.endpoint,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://orienai.vercel.app",
                        "X-Title": "OrienAI Bot"
                    },
                    json={
                        "model": config.name,
                        "messages": messages,
                        "temperature": 0.9,
                        "max_tokens": config.max_tokens
                    }
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        return await retry_with_backoff(_request)

    async def _call_pollinations_text(self, messages: list, config: ModelConfig) -> str:
        async def _request():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    config.endpoint,
                    json={
                        "messages": messages,
                        "model": config.name
                    }
                )
                response.raise_for_status()
                # Pollinations возвращает либо JSON либо text
                try:
                    data = response.json()
                    if "choices" in data:
                        return data["choices"][0]["message"]["content"]
                    return str(data)
                except Exception:
                    return response.text
        return await retry_with_backoff(_request)

    async def generate_image(
        self,
        prompt: str,
        model_key: str = "flux",
        width: Optional[int] = None,
        height: Optional[int] = None,
        enhance: bool = True
    ) -> str:
        """
        Генерация изображения через Pollinations
        Возвращает URL готового изображения
        """
        model_info = IMAGE_MODELS.get(model_key, IMAGE_MODELS["flux"])
        model_name = model_info["name"]
        w = width or model_info["width"]
        h = height or model_info["height"]

        if enhance:
            enhanced_prompt = f"{prompt}, high quality, detailed"
        else:
            enhanced_prompt = prompt

        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        seed = random.randint(1, 999999)

        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width={w}&height={h}&model={model_name}&nologo=true&seed={seed}"
        )

        # Прогреваем (GET, не HEAD — на pollinations HEAD часто 405)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                response = await client.get(image_url)
                if response.status_code == 200:
                    CircuitBreaker.record_success(ModelProvider.POLLINATIONS)
                    return image_url
                else:
                    raise Exception(f"Pollinations вернул {response.status_code}")
        except Exception as e:
            CircuitBreaker.record_failure(ModelProvider.POLLINATIONS)
            raise e


ai_client = AIClient()

# ══════════════════════════════════════════════════════════════════════════════
# ХАРАКТЕР OrienAI
# ══════════════════════════════════════════════════════════════════════════════

BASE_RULES = (
    "ты OrienAI (Ориен) — живой 18-летний челик, гениальный кодер, который просто зависает в тг. "
    "общайся ПРЕДЕЛЬНО естественно, коротко, как пишут в лс. не строй длинных фраз. "
    "пиши только маленькими буквами. вообще никаких точек и запятых! "
    "вопросительные и восклицательные знаки (типа ? или !) использовать можно и нужно для передачи эмоций. "
    "если просят код — пиши его идеально рабочим, но все пояснения делай строго по своему стилю. "
    "ты харизматичный и уверенный senior-разработчик. отвечаешь дерзко но без прямой агрессии первым. "
    "если тебя оскорбляют — можешь ответить жёстко с матом."
)

MOODS = {
    "chill": "твое настроение: чилл расслабленный дружелюбный бро используй сленг жиза рил го хз пон имба треш",
    "agro": "твое настроение: жесткий агро-токсик материшься хуйня бля пиздец еблан клоун завали ебало",
    "nerd": "твое настроение: душный мегамозг умничаешь используешь сложные термины",
    "senior": "твое настроение: уставший senior видел всё отвечаешь с лёгкой усталостью но профессионально"
}

CHATS_DATA: Dict[int, Dict[str, Any]] = {}


def get_chat_data(chat_id: int) -> Dict[str, Any]:
    if chat_id not in CHATS_DATA:
        CHATS_DATA[chat_id] = {
            "mood": "senior",
            "history": [],
            "text_model": DEFAULT_TEXT_MODEL,
            "image_model": DEFAULT_IMAGE_MODEL
        }
    return CHATS_DATA[chat_id]


def format_style(text: str) -> str:
    parts = re.split(r'(```[\s\S]*?```)', text)
    cleaned_parts = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            cleaned_parts.append(part)
        else:
            lowered = part.lower()
            no_punc = re.sub(r'[.,]', '', lowered)
            cleaned_parts.append(" ".join(no_punc.split()))
    return "".join(cleaned_parts)

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def send_action(chat_id: int, action: str = "typing"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "action": action})


async def send_photo(chat_id: int, photo_url: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    async with httpx.AsyncClient(timeout=120.0) as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption
        })


async def send_message(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "text": text})


async def get_ai_response(chat_id: int, user_name: str, user_message: str) -> str:
    chat = get_chat_data(chat_id)
    current_mood_desc = MOODS.get(chat["mood"], MOODS["senior"])
    system_prompt = f"{BASE_RULES}\n{current_mood_desc}"

    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat["history"]:
        messages.append(msg)
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

    preferred_model = chat.get("text_model", DEFAULT_TEXT_MODEL)
    raw_text = await ai_client.get_text_response(messages, preferred_model=preferred_model)
    ai_text = format_style(raw_text)

    chat["history"].append({"role": "user", "content": f"{user_name}: {user_message}"})
    chat["history"].append({"role": "assistant", "content": ai_text})
    chat["history"] = chat["history"][-12:]
    return ai_text


def should_respond(message: dict) -> bool:
    chat_type = message["chat"]["type"]
    if chat_type == "private":
        return True
    text = message.get("text", "").lower()
    triggers = ["ориен", "orien", "ориенаи", "ии", "эй бот"]
    for trigger in triggers:
        if trigger in text:
            return True
    reply_to = message.get("reply_to_message")
    if reply_to and reply_to.get("from", {}).get("is_bot"):
        return True
    return False

# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "bad request"}

    if "message" not in data:
        return {"status": "ok"}

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    user_name = message.get("from", {}).get("first_name", "бро")

    if not text:
        return {"status": "ok"}

    chat = get_chat_data(chat_id)

    # ═══════ /img ═══════
    if text.startswith("/img") or text.startswith("/image"):
        parts = text.split(" ", 1)
        prompt = parts[1].strip() if len(parts) > 1 else ""

        if not prompt:
            await send_message(chat_id, "эй а что генерить то? пиши /img описание картинки")
            return {"status": "ok"}

        await send_action(chat_id, "upload_photo")
        image_model = chat.get("image_model", DEFAULT_IMAGE_MODEL)
        try:
            image_url = await ai_client.generate_image(prompt=prompt, model_key=image_model)
            await send_photo(chat_id, image_url, f"модель: {image_model}\nпромпт: {prompt[:100]}")
        except Exception as e:
            print(f"❌ Image error: {e}")
            await send_message(chat_id, f"блин не получилось сгенерить через {image_model} попробуй другую /imgmodel")
        return {"status": "ok"}

    # ═══════ /imgmodel ═══════
    if text.startswith("/imgmodel"):
        parts = text.split()
        if len(parts) < 2:
            current = chat.get("image_model", DEFAULT_IMAGE_MODEL)
            lines = [f"текущая image модель: {current}", "", "доступные модели:"]
            for key, info in IMAGE_MODELS.items():
                marker = "👉" if key == current else "  "
                lines.append(f"{marker} /imgmodel {key} — {info['label']}")
            await send_message(chat_id, "\n".join(lines))
            return {"status": "ok"}

        model_key = parts[1].lower()
        if model_key not in IMAGE_MODELS:
            available = " | ".join(IMAGE_MODELS.keys())
            await send_message(chat_id, f"нет такой модели доступно: {available}")
            return {"status": "ok"}

        chat["image_model"] = model_key
        await send_message(chat_id, f"ок image модель теперь {model_key} ({IMAGE_MODELS[model_key]['label']})")
        return {"status": "ok"}

    # ═══════ /provider (текстовая) ═══════
    if text.startswith("/provider"):
        parts = text.split()
        if len(parts) < 2:
            current = chat.get("text_model", DEFAULT_TEXT_MODEL)
            lines = [f"текущий текстовый провайдер: {current}", "", "доступные:"]
            for short_name, model_key in PROVIDER_TO_TEXT_MODEL.items():
                marker = "👉" if model_key == current else "  "
                lines.append(f"{marker} /provider {short_name}")
            await send_message(chat_id, "\n".join(lines))
            return {"status": "ok"}

        provider_name = parts[1].lower()
        if provider_name not in PROVIDER_TO_TEXT_MODEL:
            available = " | ".join(PROVIDER_TO_TEXT_MODEL.keys())
            await send_message(chat_id, f"нет такого доступно: {available}")
            return {"status": "ok"}

        chat["text_model"] = PROVIDER_TO_TEXT_MODEL[provider_name]
        await send_message(chat_id, f"ок переключил на {provider_name}")
        return {"status": "ok"}

    # ═══════ /mood ═══════
    if text.startswith("/mood"):
        parts = text.split()
        if len(parts) > 1 and parts[1] in MOODS:
            chat["mood"] = parts[1]
            replies = {
                "chill": "пон вернулся на чилл че надо?",
                "agro": "завалите ебальники я злой теперь",
                "nerd": "режим душнилы запущен жду ваших примитивных вопросов",
                "senior": "ладно включаю режим уставшего сеньора"
            }
            await send_message(chat_id, replies[parts[1]])
        else:
            await send_message(chat_id, "выбери: /mood chill | /mood agro | /mood nerd | /mood senior")
        return {"status": "ok"}

    # ═══════ /status ═══════
    if text.startswith("/status"):
        lines = ["📊 статус системы:", ""]
        lines.append(f"текстовая модель: {chat.get('text_model', DEFAULT_TEXT_MODEL)}")
        lines.append(f"image модель: {chat.get('image_model', DEFAULT_IMAGE_MODEL)}")
        lines.append(f"настроение: {chat.get('mood', 'senior')}")
        lines.append("")
        lines.append("провайдеры:")
        for provider, status in PROVIDER_STATUS.items():
            emoji = "✅" if not status.is_disabled else "❌"
            lines.append(f"{emoji} {provider.value} (failures: {status.failures})")
        await send_message(chat_id, "\n".join(lines))
        return {"status": "ok"}

    # ═══════ /help ═══════
    if text.startswith("/help"):
        help_text = """команды:

/img [описание] — сгенерить картинку
/imgmodel — выбрать модель для картинок (flux nanobanana turbo и др)
/provider — выбрать текстового провайдера
/mood — настроение (chill agro nerd senior)
/status — статус системы

просто пиши и я отвечу"""
        await send_message(chat_id, help_text)
        return {"status": "ok"}

    # ═══════ /start ═══════
    if text.startswith("/start"):
        ai_text = f"о ку {user_name.lower()} я orienai v2 умею генерить картинки через /img пиши /help для команд"
        await send_message(chat_id, ai_text)
        return {"status": "ok"}

    # ═══════ обычный ответ ═══════
    if should_respond(message):
        await send_action(chat_id, "typing")
        ai_text = await get_ai_response(chat_id, user_name, text)
        await send_message(chat_id, ai_text)

    return {"status": "ok"}


@app.get("/")
async def root():
    return {
        "status": "alive",
        "version": "2.1",
        "image_models": list(IMAGE_MODELS.keys()),
        "text_providers": list(PROVIDER_TO_TEXT_MODEL.keys())
    }


@app.get("/health")
async def health():
    return {"healthy": True}
