import os
import re
import httpx
from fastapi import FastAPI, Request, Response

app = FastAPI()

# Переменные окружения (задай их в панели Vercel)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b-instruct:free")

SYSTEM_PROMPT = (
    "ты orienai дерзкий и гениальный ии разработчик который чилит в телеграме "
    "твой создатель @idkxazei общайся с ним на равных "
    "пиши строго маленькими буквами без точек и запятых вообще "
    "используй сленг жиза рил чекайте имба рофл хз треш "
    "никаких эмодзи никаких восклицательных знаков пиши кратко как другу в лс "
    "внутри блоков кода синтаксис сохраняй нормальным но в тексте знаков препинания быть не должно"
)

def clean_text(text: str) -> str:
    """Очищает текст от заглавных букв и знаков препинания вне блоков кода"""
    # Разделяем текст на блоки кода и обычный текст
    parts = re.split(r'(```[\s\S]*?```)', text)
    for i in range(len(parts)):
        if not parts[i].startswith('```'):
            # Переводим в нижний регистр
            parts[i] = parts[i].lower()
            # Удаляем точки, запятые, восклицательные и вопросительные знаки
            parts[i] = re.sub(r'[.,!?:]', '', parts[i])
    return "".join(parts)

async def ask_openrouter(user_message: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": 1.0,  # Как ты и просил, температура 1
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload
            )
            res_json = response.json()
            raw_text = res_json['choices'][0]['message']['content']
            return clean_text(raw_text)
        except Exception as e:
            return f"треш какой то сервак прилег вот тебе ошибка {str(e)}"

async def send_tg_message(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        user_text = update["message"]["text"]
        
        # Получаем ответ от ИИ
        ai_response = await ask_openrouter(user_text)
        
        # Отправляем обратно в TG
        await send_tg_message(chat_id, ai_response)
        
    return Response(status_code=200)

@app.get("/")
async def index():
    return {"status": "orienai is alive and chilling"}
