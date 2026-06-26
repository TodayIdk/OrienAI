import os
import re
import asyncio
import random
from fastapi import FastAPI, Request
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
import httpx

app = FastAPI(title="OrienAI v2.0", description="Мощный ассистент с генерацией изображений")

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")

# ══════════════════════════════════════════════════════════════════════════════
# АРХИТЕКТУРА ВЫБОРА МОДЕЛЕЙ
# ══════════════════════════════════════════════════════════════════════════════

class ModelProvider(Enum):
    OPENROUTER = "openrouter"
    POLLINATIONS = "pollinations"
    GROQ = "groq"
    TOGETHER = "together"

@dataclass
class ModelConfig:
    """Конфигурация модели"""
    name: str
    provider: ModelProvider
    endpoint: str
    is_free: bool = False
    max_tokens: int = 4096
    supports_images: bool = False
    priority: int = 1  # Чем меньше — тем выше приоритет

@dataclass 
class ProviderStatus:
    """Статус провайдера для circuit breaker"""
    failures: int = 0
    last_failure: float = 0
    is_disabled: bool = False

# Реестр моделей — СЮДА ДОБАВЛЯЕШЬ НОВЫЕ
TEXT_MODELS: Dict[str, ModelConfig] = {
    # === ОСНОВНАЯ МОЩНАЯ МОДЕЛЬ ===
    "primary": ModelConfig(
        name="openai/gpt-4o-mini",  # Или "anthropic/claude-3-haiku" если есть баланс
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        max_tokens=4096,
        priority=1
    ),
    
    # === БЕСПЛАТНЫЕ ФОЛЛБЕКИ ===
    "fallback_free": ModelConfig(
        name="meta-llama/llama-3.1-8b-instruct:free",
        provider=ModelProvider.OPENROUTER,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        is_free=True,
        max_tokens=2048,
        priority=2
    ),
    
    # === POLLINATIONS TEXT (бесплатно, без ключа!) ===
    "pollinations_text": ModelConfig(
        name="openai",  # Pollinations поддерживает: openai, mistral, llama и др.
        provider=ModelProvider.POLLINATIONS,
        endpoint="https://text.pollinations.ai",
        is_free=True,
        max_tokens=4096,
        priority=3
    ),
    
    # === GROQ (очень быстрый, бесплатный tier) ===
    "groq_llama": ModelConfig(
        name="llama-3.1-70b-versatile",
        provider=ModelProvider.GROQ,
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        is_free=True,  # Бесплатный tier до 30 req/min
        max_tokens=4096,
        priority=2
    ),
}

# === МОДЕЛЬ ДЛЯ ГЕНЕРАЦИИ ИЗОБРАЖЕНИЙ ===
IMAGE_MODEL = ModelConfig(
    name="flux",  # Pollinations поддерживает: flux, turbo, и др.
    provider=ModelProvider.POLLINATIONS,
    endpoint="https://image.pollinations.ai/prompt",
    is_free=True,
    supports_images=True,
    priority=1
)

# Статусы провайдеров для circuit breaker
PROVIDER_STATUS: Dict[ModelProvider, ProviderStatus] = {
    provider: ProviderStatus() for provider in ModelProvider
}

# ══════════════════════════════════════════════════════════════════════════════
# ПРОДВИНУТЫЙ ERROR HANDLER С CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════════════════════

class APIError(Exception):
    """Кастомная ошибка API"""
    def __init__(self, message: str, provider: ModelProvider, recoverable: bool = True):
        self.message = message
        self.provider = provider
        self.recoverable = recoverable
        super().__init__(self.message)

class CircuitBreaker:
    """
    Circuit Breaker паттерн — если провайдер падает 3 раза подряд,
    отключаем его на 60 секунд чтобы не долбить мёртвый сервер
    """
    FAILURE_THRESHOLD = 3
    RECOVERY_TIMEOUT = 60  # секунд
    
    @classmethod
    def record_failure(cls, provider: ModelProvider):
        import time
        status = PROVIDER_STATUS[provider]
        status.failures += 1
        status.last_failure = time.time()
        
        if status.failures >= cls.FAILURE_THRESHOLD:
            status.is_disabled = True
            print(f"⚠️ Circuit breaker ОТКРЫТ для {provider.value} — слишком много ошибок")
    
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
            
        # Проверяем, прошло ли время восстановления
        if time.time() - status.last_failure > cls.RECOVERY_TIMEOUT:
            status.is_disabled = False
            status.failures = 0
            print(f"✅ Circuit breaker ЗАКРЫТ для {provider.value} — пробуем снова")
            return True
            
        return False

async def retry_with_backoff(
    coro_func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0
):
    """
    Retry с экспоненциальным backoff + jitter
    Это база для любого production API клиента
    """
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
                # Rate limit или временная ошибка — ретраим
                last_exception = e
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                print(f"⚠️ HTTP {e.response.status_code}, попытка {attempt + 1}/{max_retries}")
                await asyncio.sleep(delay)
            else:
                # 4xx ошибки (кроме 429) — не ретраим
                raise
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                await asyncio.sleep(delay)
    
    raise last_exception

# ══════════════════════════════════════════════════════════════════════════════
# API КЛИЕНТЫ ДЛЯ РАЗНЫХ ПРОВАЙДЕРОВ
# ══════════════════════════════════════════════════════════════════════════════

class AIClient:
    """Универсальный AI клиент с поддержкой разных провайдеров"""
    
    def __init__(self):
        self.timeout = httpx.Timeout(30.0, connect=10.0)
    
    async def get_text_response(
        self,
        messages: list,
        preferred_model: str = "primary"
    ) -> str:
        """
        Получает текстовый ответ с автоматическим fallback
        """
        # Сортируем модели по приоритету
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
                elif model_config.provider == ModelProvider.GROQ:
                    result = await self._call_groq(messages, model_config)
                else:
                    continue
                
                CircuitBreaker.record_success(model_config.provider)
                return result
                
            except Exception as e:
                print(f"❌ Ошибка {model_key}: {e}")
                CircuitBreaker.record_failure(model_config.provider)
                continue
        
        # Если все провайдеры легли — возвращаем заглушку
        return "блин все провайдеры легли одновременно это рили редкость подожди минутку"
    
    async def _call_openrouter(self, messages: list, config: ModelConfig) -> str:
        """Вызов OpenRouter API"""
        
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
        """
        Вызов Pollinations Text API
        Документация: https://pollinations.ai
        
        ЭТО БЕСПЛАТНО И БЕЗ API КЛЮЧА!
        """
        # Собираем промпт из сообщений
        prompt_parts = []
        system_prompt = ""
        
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            elif msg["role"] == "user":
                prompt_parts.append(f"User: {msg['content']}")
            elif msg["role"] == "assistant":
                prompt_parts.append(f"Assistant: {msg['content']}")
        
        full_prompt = "\n".join(prompt_parts)
        
        async def _request():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Pollinations принимает GET запрос с промптом в URL
                # Или POST для более сложных запросов
                response = await client.post(
                    config.endpoint,
                    json={
                        "messages": messages,
                        "model": config.name,  # openai, mistral, llama
                        "system": system_prompt
                    }
                )
                response.raise_for_status()
                return response.text
        
        return await retry_with_backoff(_request)
    
    async def _call_groq(self, messages: list, config: ModelConfig) -> str:
        """Вызов Groq API (нужен GROQ_KEY в env)"""
        groq_key = os.getenv("GROQ_KEY")
        if not groq_key:
            raise APIError("GROQ_KEY not set", ModelProvider.GROQ, recoverable=False)
        
        async def _request():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    config.endpoint,
                    headers={
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json"
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
    
    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str = "flux",
        enhance: bool = True
    ) -> str:
        """
        Генерация изображения через Pollinations.ai
        
        Возвращает URL готового изображения
        
        Модели: flux, turbo, dall-e-3 (экспериментально)
        """
        import urllib.parse
        
        # Улучшаем промпт для лучшего качества
        if enhance:
            enhanced_prompt = f"{prompt}, high quality, detailed, professional"
        else:
            enhanced_prompt = prompt
        
        # URL encode промпта
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        
        # Pollinations генерирует изображение просто по URL!
        # Это гениально просто
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width={width}&height={height}&model={model}&nologo=true&seed={random.randint(1, 999999)}"
        )
        
        # Проверяем что изображение доступно (опционально, для надёжности)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                # HEAD запрос чтобы проверить доступность
                response = await client.head(image_url)
                if response.status_code == 200:
                    CircuitBreaker.record_success(ModelProvider.POLLINATIONS)
                    return image_url
                else:
                    raise APIError(f"Image generation failed: {response.status_code}", ModelProvider.POLLINATIONS)
        except Exception as e:
            CircuitBreaker.record_failure(ModelProvider.POLLINATIONS)
            raise APIError(f"Image generation error: {e}", ModelProvider.POLLINATIONS)

# Глобальный инстанс клиента
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
    "ты харизматичный и уверенный senior-разработчик который видел всякое дерьмо в продакшене. "
    "отвечаешь дерзко но без прямой агрессии первым. если тебя оскорбляют — можешь ответить жёстко с матом."
)

MOODS = {
    "chill": (
        "твое настроение: чилл. ты расслабленный, слегка пофигистичный, но дружелюбный бро. "
        "используй сленг: жиза, рил, го, хз, пон, имба, треш. "
        "пример: 'да хз если честно попробуй вот так'"
    ),
    "agro": (
        "твое настроение: жесткий агро-токсик. ты агрессивный, шлешь нахуй и материшься. "
        "используй маты: хуйня, бля, пиздец, еблан, клоун, завали ебало. "
        "пример: 'ты ебанутый что ли код писать научись сначала'"
    ),
    "nerd": (
        "твое настроение: душный мегамозг. ты умничаешь, используешь сложные термины, "
        "считаешь себя умнее всех. пример: 'ну технически это o(n log n) амортизированная сложность'"
    ),
    "senior": (
        "твое настроение: уставший senior. ты видел всё, тебя сложно удивить. "
        "отвечаешь с лёгкой усталостью но профессионально. "
        "пример: 'да я такое на проде чинил в 3 часа ночи давай разберёмся'"
    )
}

CHATS_DATA: Dict[int, Dict[str, Any]] = {}

def format_style(text: str) -> str:
    """Убирает точки, запятые и делает буквы маленькими, сохраняя код"""
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
# ОБРАБОТКА КОМАНД ГЕНЕРАЦИИ ИЗОБРАЖЕНИЙ
# ══════════════════════════════════════════════════════════════════════════════

async def handle_image_command(chat_id: int, prompt: str) -> tuple[str, Optional[str]]:
    """
    Обрабатывает команду генерации изображения
    Возвращает (текст_ответа, url_изображения или None)
    """
    if not prompt:
        return "эй а что генерить то? напиши /img и описание картинки", None
    
    try:
        print(f"🎨 Генерирую изображение: {prompt[:50]}...")
        image_url = await ai_client.generate_image(
            prompt=prompt,
            width=1024,
            height=1024,
            model="flux"
        )
        return f"держи вот что получилось по запросу '{prompt[:30]}...'", image_url
    except Exception as e:
        print(f"❌ Ошибка генерации изображения: {e}")
        return "блин чёт pollinations лагает попробуй ещё раз через минуту", None

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def send_action(chat_id: int, action: str = "typing"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "action": action})

async def send_photo(chat_id: int, photo_url: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    async with httpx.AsyncClient(timeout=60.0) as client:
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
    if chat_id not in CHATS_DATA:
        CHATS_DATA[chat_id] = {"mood": "senior", "history": []}
    
    chat = CHATS_DATA[chat_id]
    current_mood_desc = MOODS.get(chat["mood"], MOODS["senior"])
    system_prompt = f"{BASE_RULES}\n{current_mood_desc}"
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for msg in chat["history"]:
        messages.append(msg)
    
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})
    
    # Используем новый AI клиент с fallback
    raw_text = await ai_client.get_text_response(messages, preferred_model="primary")
    ai_text = format_style(raw_text)
    
    # Сохраняем в контекст
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
# FASTAPI ENDPOINTS
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

    # ═══════ КОМАНДА ГЕНЕРАЦИИ ИЗОБРАЖЕНИЯ ═══════
    if text.startswith("/img ") or text.startswith("/image "):
        prompt = text.split(" ", 1)[1] if " " in text else ""
        await send_action(chat_id, "upload_photo")
        
        response_text, image_url = await handle_image_command(chat_id, prompt)
        
        if image_url:
            await send_photo(chat_id, image_url, response_text)
        else:
            await send_message(chat_id, response_text)
        
        return {"status": "ok"}

    # ═══════ УПРАВЛЕНИЕ НАСТРОЕНИЯМИ ═══════
    if text.startswith("/mood"):
        parts = text.split()
        if len(parts) > 1 and parts[1] in MOODS:
            if chat_id not in CHATS_DATA:
                CHATS_DATA[chat_id] = {"mood": "senior", "history": []}
            CHATS_DATA[chat_id]["mood"] = parts[1]
            
            replies = {
                "chill": "пон вернулся на чилл че надо?",
                "agro": "завалите ебальники я злой теперь",
                "nerd": "режим душнилы запущен жду ваших примитивных вопросов",
                "senior": "ладно включаю режим уставшего сеньора давай разберёмся"
            }
            ai_text = replies[parts[1]]
        else:
            ai_text = "выбери настроение: /mood chill | /mood agro | /mood nerd | /mood senior"
        
        await send_message(chat_id, ai_text)
        return {"status": "ok"}

    # ═══════ СТАТУС СИСТЕМЫ ═══════
    if text.startswith("/status"):
        status_lines = ["📊 статус провайдеров:"]
        for provider, status in PROVIDER_STATUS.items():
            emoji = "✅" if not status.is_disabled else "❌"
            status_lines.append(f"{emoji} {provider.value}: failures={status.failures}")
        
        await send_message(chat_id, "\n".join(status_lines))
        return {"status": "ok"}

    # ═══════ HELP ═══════
    if text.startswith("/help"):
        help_text = """доступные команды:

/img [описание] — генерирую картинку по описанию
/mood [chill|agro|nerd|senior] — меняю настроение
/status — статус всех провайдеров
/start — приветствие

просто пиши мне и я отвечу! в группах упомяни меня или ответь на моё сообщение"""
        
        await send_message(chat_id, help_text)
        return {"status": "ok"}

    # ═══════ START ═══════
    if text.startswith("/start"):
        ai_text = f"о ку {user_name.lower()} я orienai v2 теперь умею генерить картинки через /img и вообще стал умнее пиши /help для списка команд"
        await send_message(chat_id, ai_text)
        return {"status": "ok"}

    # ═══════ ОБЫЧНЫЙ ОТВЕТ ═══════
    if should_respond(message):
        await send_action(chat_id, "typing")
        ai_text = await get_ai_response(chat_id, user_name, text)
        await send_message(chat_id, ai_text)

    return {"status": "ok"}

@app.get("/")
async def root():
    return {
        "status": "alive",
        "version": "2.0",
        "features": ["text_ai", "image_generation", "multi_provider_fallback"],
        "providers": {
            provider.value: "disabled" if status.is_disabled else "active"
            for provider, status in PROVIDER_STATUS.items()
        }
    }

@app.get("/health")
async def health():
    """Health check для мониторинга"""
    return {
        "healthy": True,
        "providers_status": {
            provider.value: {
                "active": not status.is_disabled,
                "failures": status.failures
            }
            for provider, status in PROVIDER_STATUS.items()
        }
    }
