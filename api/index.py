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

def format_style(text: str) -> str:
    # Разделяем текст на блоки кода и обычный текст, чтобы не сломать синтаксис кода
    parts = re.split(r'(```[\s\S]*?```)', text)
    cleaned_parts = []
    
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            # Код оставляем как есть, чтобы он работал
            cleaned_parts.append(part)
        else:
            # Обычный текст принудительно переводим в нижний регистр и убираем знаки препинания
            lowered = part.lower()
            # Убираем точки, запятые, восклицательные, вопросительные знаки, двоеточия и т.д.
            no_punc = re.sub(r'[.,\/#!$%\^&\*;:{}=\-_`~()?—]', '', lowered)
            # Убираем лишние пробелы
            cleaned_parts.append(" ".join(no_punc.split()))
            
    return "".join(cleaned_parts)

async def get_ai_response(user_message: str) -> str:
    url = "https://text.pollinations.ai/"
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "model": "openai",  # Используем дефолтную модель (обычно gpt-4o-mini)
        "jsonMode": False
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                raw_text = response.text
                return format_style(raw_text)
            return "треш сервак упал попробуй позже"
    except Exception as e:
        print(f"Ошибка ИИ: {e}")
        return "бпх чтото пошло не так хз"

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

        # Не отвечаем на пустые сообщения или команды старта (хотя старт можно обработать)
        if not text:
            return {"status": "ok"}
            
        if text.startswith("/start"):
            ai_text = "о ку привет я orienai че надо по коду или просто потрещать пиши"
        else:
            ai_text = await get_ai_response(text)

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
    return {"message": "orienai is alive"}
