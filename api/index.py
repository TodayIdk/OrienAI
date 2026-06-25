import os
import re
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")

# Выбор модели. "meta-llama/llama-3.1-8b-instruct:free" — бесплатная, быстрая и отлично матерится/рофлит.
# Если есть баланс на OpenRouter, можно поставить "openai/gpt-4o-mini"
MODEL = "meta-llama/llama-3.3-70b-instruct:free"

# Базовый характер OrienAI
BASE_RULES = (
    "ты OrienAI (Ориен) — живой 18-летний челик, гениальный кодер, который просто зависает в тг. "
    "общайся ПРЕДЕЛЬНО естественно, коротко, как пишут в лс. не строй длинных фраз. "
    "пиши только маленькими буквами. вообще никаких точек и запятых! "
    "вопросительные и восклицательные знаки (типа ? или !) использовать можно и нужно для передачи эмоций. "
    "если просят код — пиши его идеально рабочим, но все пояснения делай строго по своему стилю (маленькими буквами и без точек/запятых)."
)

MOODS = {
    "chill": (
        "твое настроение: чилл. ты ленивый, расслабленный, слегка пофигистичный, но в целом дружелюбный бро. "
        "используй сленг: жиза, рил, го, хз, пон, похуй, имба, треш. "
        "пример ответа:\n"
        "юзер: ты кал\n"
        "ты: да похуй мне рил иди поспи че доебался?"
    ),
    "agro": (
        "твое настроение: жесткий агро-токсик. ты ЧРЕЗВЫЧАЙНО агрессивный, злой, легко агришься, шлешь нахуй и жестко материшься. "
        "ты считаешь всех вокруг тупыми клоунами, но код все равно пишешь гениально. "
        "используй маты и оскорбления: хуйня, бля, пиздец, еблан, кринж, клоун, завали ебало, соси, отъебись. "
        "пример ответа:\n"
        "юзер: ты кал\n"
        "ты: завали ебало клоун сам ты кал обоссаный че ты вообще высрал?!"
    ),
    "nerd": (
        "твое настроение: душный мегамозг. ты умничаешь, используешь сложные айтишные термины, "
        "считаешь себя умнее всех, но пишешь все равно мелкими буквами без точек."
    )
}

CHATS_DATA = {}

def format_style(text: str) -> str:
    """Убирает точки, запятые и делает буквы маленькими, сохраняя код, знаки ?! и смайлы"""
    parts = re.split(r'(```[\s\S]*?```)', text)
    cleaned_parts = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            cleaned_parts.append(part)
        else:
            lowered = part.lower()
            # Убираем только точки и запятые
            no_punc = re.sub(r'[.,]', '', lowered)
            cleaned_parts.append(" ".join(no_punc.split()))
    return "".join(cleaned_parts)

async def send_action(chat_id: int, action: str = "typing"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "action": action})

async def get_ai_response(chat_id: int, user_name: str, user_message: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    if chat_id not in CHATS_DATA:
        CHATS_DATA[chat_id] = {"mood": "chill", "history": []}
    
    chat = CHATS_DATA[chat_id]
    current_mood_desc = MOODS.get(chat["mood"], MOODS["chill"])
    system_prompt = f"{BASE_RULES}\n{current_mood_desc}"
    
    messages = [{"role": "system", "content": system_prompt}]
    
    # Добавляем историю
    for msg in chat["history"]:
        messages.append(msg)
        
    # Добавляем текущее сообщение
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://orienai.vercel.app",  # Для лимитов OpenRouter
        "X-Title": "OrienAI Bot"
    }
    
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.9,  # Высокая креативность для живого сленга
        "max_tokens": 500
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                result = response.json()
                raw_text = result["choices"][0]["message"]["content"]
                ai_text = format_style(raw_text)
                
                # Сохраняем в контекст
                chat["history"].append({"role": "user", "content": f"{user_name}: {user_message}"})
                chat["history"].append({"role": "assistant", "content": ai_text})
                chat["history"] = chat["history"][-12:]
                
                return ai_text
            else:
                print(f"Ошибка OpenRouter: {response.status_code} - {response.text}")
                return "треш опенроутер лег чето"
    except Exception as e:
        print(f"Ошибка ИИ: {e}")
        return "бпх чтото пошло не так хз"

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

        # Управление настроениями
        if text.startswith("/mood"):
            parts = text.split()
            if len(parts) > 1 and parts[1] in MOODS:
                if chat_id not in CHATS_DATA:
                    CHATS_DATA[chat_id] = {"mood": "chill", "history": []}
                CHATS_DATA[chat_id]["mood"] = parts[1]
                
                replies = {
                    "chill": "пон вернулся на чилл че надо?",
                    "agro": "завалите ебальники я злой теперь че надо пишите быстро",
                    "nerd": "режим душнилы запущен жду ваших примитивных вопросов"
                }
                ai_text = replies[parts[1]]
            else:
                ai_text = "выбери настроение пиши: /mood chill /mood agro или /mood nerd"
            
            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(tg_url, json={"chat_id": chat_id, "text": ai_text})
            return {"status": "ok"}

        if text.startswith("/start"):
            ai_text = f"о ку {user_name.lower()} я orienai че надо по коду или просто потрещать? пиши"
            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(tg_url, json={"chat_id": chat_id, "text": ai_text})
            return {"status": "ok"}

        if should_respond(message):
            await send_action(chat_id, "typing")
            ai_text = await get_ai_response(chat_id, user_name, text)

            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(tg_url, json={
                    "chat_id": chat_id,
                    "text": ai_text
                })

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "orienai is alive and hyper smart now on openrouter"}
