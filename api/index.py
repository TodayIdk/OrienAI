import os
import re
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

TOKEN = os.getenv("TELEGRAM_TOKEN")
SYSTEM_PROMPT = (
    "ты OrienAI умный ии бот который просто чилит в тг чатах и шарит за код на уровне бога "
    "ты прям имбовый скриптер и кодер пиши только маленькими буквами без точек и запятых "
    "используй сленг жиза рил чекайте имба рофл хз треш отвечай максимально кратко как другу в лс "
    "никакой рекламы и ссылок если просят код делай его рабочим но все пояснения пиши строго по стилю "
    "без заглавных и знаков препинания"
)

# Проверка токена при запуске
if not TOKEN:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная TELEGRAM_TOKEN не найдена в окружении!")
else:
    print(f"✅ Токен найден (начинается на: {TOKEN[:5]}...)")

def format_style(text: str) -> str:
    parts = re.split(r'(```[\s\S]*?```)', text)
    cleaned_parts = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            cleaned_parts.append(part)
        else:
            lowered = part.lower()
            no_punc = re.sub(r'[.,\/#!$%\^&\*;:{}=\-_`~()?—]', '', lowered)
            cleaned_parts.append(" ".join(no_punc.split()))
    return "".join(cleaned_parts)

async def get_ai_response(user_message: str) -> str:
    url = "https://text.pollinations.ai/"
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "model": "openai",
        "jsonMode": False
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                return format_style(response.text)
            print(f"❌ Ошибка Pollinations AI: {response.status_code}")
            return "треш сервак упал попробуй позже"
    except Exception as e:
        print(f"❌ Исключение при запросе к ИИ: {e}")
        return "бпх чтото пошло не так хз"

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        print(f"📥 Получен запрос от TG: {data}")  # Логируем всё, что прислал Telegram
    except Exception as e:
        print(f"❌ Ошибка парсинга JSON: {e}")
        return {"status": "bad request"}

    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if not text:
            return {"status": "ok"}
            
        if text.startswith("/start"):
            ai_text = "о ку привет я orienai че надо по коду или просто потрещать пиши"
        else:
            print(f"🤖 Запрос к ИИ с текстом: {text}")
            ai_text = await get_ai_response(text)
            print(f"🤖 Ответ от ИИ подготовлен: {ai_text}")

        # Отправляем ответ в Telegram
        tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            print(f"📤 Отправка сообщения в чат {chat_id}...")
            tg_resp = await client.post(tg_url, json={
                "chat_id": chat_id,
                "text": ai_text
            })
            print(f"📩 Ответ от Telegram API: статус {tg_resp.status_code}, тело: {tg_resp.text}")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": f"orienai is alive. token status: {'configured' if TOKEN else 'empty'}"}
