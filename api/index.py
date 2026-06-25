import os
import re
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Базовый характер
BASE_PROMPT = (
    "ты OrienAI (Ориен) умный ии бот который просто чилит в тг чатах и шарит за код на уровне бога "
    "ты прям имбовый скриптер и кодер пиши только маленькими буквами без точек и запятых "
    "используй сленг жиза рил чекайте имба рофл хз треш пон го отвечай максимально кратко как другу в лс "
    "никакой рекламы и ссылок если просят код делай его рабочим но все пояснения пиши строго по стилю "
    "без заглавных и знаков препинания "
    "ВАЖНО пиши строго от первого лица: я делаю, я чилю, я написал (вместо делает, чилит, написал)"
)

# Разные настроения
MOODS = {
    "chill": "сейчас твое настроение: ты максимально расслабленный дружелюбный бро на чилле",
    "agro": "сейчас твое настроение: ты жесткий токсик и агро рофлишь над тупостью дерзишь но по-дружески используй слова типа лол мда боже треш",
    "nerd": "сейчас твое настроение: ты душный мегамозг умничаешь терминами но по нашему стилю"
}

# Временная память в оперативной памяти (сбросится при перезапуске сервера Vercel, но для общения хватает)
CHATS_DATA = {}

def format_style(text: str) -> str:
    parts = re.split(r'(```[\s\S]*?```)', text)
    cleaned_parts = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            cleaned_parts.append(part)
        else:
            lowered = part.lower()
            # Убираем знаки препинания кроме смайликов
            no_punc = re.sub(r'[.,\/#!$%\^&\*;:{}=\-_`~()?—]', '', lowered)
            cleaned_parts.append(" ".join(no_punc.split()))
    return "".join(cleaned_parts)

async def send_action(chat_id: int, action: str = "typing"):
    """Отправляет статус 'печатает...' в Telegram"""
    url = f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "action": action})

async def get_ai_response(chat_id: int, user_name: str, user_message: str) -> str:
    url = "https://text.pollinations.ai/"
    
    # Инициализация данных чата, если их нет
    if chat_id not in CHATS_DATA:
        CHATS_DATA[chat_id] = {
            "mood": "chill",
            "history": []
        }
    
    chat = CHATS_DATA[chat_id]
    current_mood = MOODS.get(chat["mood"], MOODS["chill"])
    system_prompt = f"{BASE_PROMPT}\n{current_mood}"
    
    # Формируем историю для ИИ
    messages = [{"role": "system", "content": system_prompt}]
    
    # Добавляем прошлую историю общения
    for msg in chat["history"]:
        messages.append(msg)
        
    # Добавляем текущее сообщение от конкретного юзера
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})
    
    payload = {
        "messages": messages,
        "model": "openai",
        "jsonMode": False
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                ai_text = format_style(response.text)
                
                # Сохраняем в историю (ограничиваем последними 10 сообщениями)
                chat["history"].append({"role": "user", "content": f"{user_name}: {user_message}"})
                chat["history"].append({"role": "assistant", "content": ai_text})
                chat["history"] = chat["history"][-10:] # держим только последние 10 сообщений
                
                return ai_text
            return "треш сервак упал попробуй позже"
    except Exception as e:
        print(f"Ошибка ИИ: {e}")
        return "бпх чтото пошло не так хз"

def should_respond(message: dict) -> bool:
    """Проверяет, нужно ли отвечать на сообщение (в личке или при упоминании кликухи)"""
    chat_type = message["chat"]["type"]
    if chat_type == "private":
        return True
        
    text = message.get("text", "").lower()
    triggers = ["ориен", "orien", "ориенаи", "ии", "эй бот"]
    
    # Если упомянули кликуху
    for trigger in triggers:
        if trigger in text:
            return True
            
    # Если ответили реплаем на сообщение бота
    reply_to = message.get("reply_to_message")
    if reply_to and reply_to.get("from", {}).get("is_bot"):
        return True
        
    return False

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "bad request"}

    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        user_name = message.get("from", {}).get("first_name", "бро")

        if not text:
            return {"status": "ok"}

        # Обработка команд смены настроения
        if text.startswith("/mood"):
            parts = text.split()
            if len(parts) > 1 and parts[1] in MOODS:
                if chat_id not in CHATS_DATA:
                    CHATS_DATA[chat_id] = {"mood": "chill", "history": []}
                CHATS_DATA[chat_id]["mood"] = parts[1]
                
                # Кастомные ответы на смену настроения
                mood_replies = {
                    "chill": "пон теперь я на чилле как обычно че делаешь",
                    "agro": "мда ок теперь я злой че надо пиши фастом",
                    "nerd": "режим душнилы активирован жду твои технические вопросы"
                }
                ai_text = mood_replies[parts[1]]
            else:
                ai_text = "выбери настроение /mood chill /mood agro или /mood nerd"
            
            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(tg_url, json={"chat_id": chat_id, "text": ai_text})
            return {"status": "ok"}

        if text.startswith("/start"):
            ai_text = f"о ку {user_name.lower()} я orienai че надо по коду или просто потрещать пиши"
            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(tg_url, json={"chat_id": chat_id, "text": ai_text})
            return {"status": "ok"}

        # Проверяем, нужно ли боту реагировать (для групп)
        if should_respond(message):
            # Показываем, что бот думает ("печатает...")
            await send_action(chat_id, "typing")
            
            # Получаем ответ
            ai_text = await get_ai_response(chat_id, user_name, text)

            # Отправляем ответ в Telegram
            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(tg_url, json={
                    "chat_id": chat_id,
                    "text": ai_text
                })

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "orienai is alive and hyper smart now"}
