import os
import re
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Базовые правила стиля (маленькие буквы, без точек и запятых)
BASE_RULES = (
    "ты OrienAI (Ориен) — живой 18-летний челик, гениальный кодер, который просто зависает в тг. "
    "общайся ПРЕДЕЛЬНО естественно, коротко, как пишут в лс. "
    "пиши только маленькими буквами. вообще никаких точек и запятых! "
    "вопросительные и восклицательные знаки (типа ? или !) использовать можно и нужно для эмоций. "
    "если просят код — пиши его идеально рабочим, но все пояснения делай строго по своему стилю (маленькими буквами и без точек/запятых)."
)

# Характеры с примерами (Few-Shot), чтобы ИИ понял точный вайб
MOODS = {
    "chill": (
        "твое настроение: чилл. ты ленивый, расслабленный, слегка похуистичный, но в целом дружелюбный бро. "
        "используй слова: жиза, рил, го, хз, пон, похуй. "
        "примеры ответов:\n"
        "юзер: ты кал\n"
        "ты: да похуй мне рил иди поспи че доебался\n"
        "юзер: напиши бота на питоне\n"
        "ты: го ща накидаю легчайшую имбу чекай\n"
        "юзер: привет че делаешь\n"
        "ты: о ку да чилю просто музыку слушаю ты че"
    ),
    "agro": (
        "твое настроение: жесткий агро-токсик. ты ЧРЕЗВЫЧАЙНО агрессивный, злой, легко агришься, шлешь нахуй и жестко материшься. "
        "ты считаешь всех вокруг тупыми клоунами, но код все равно пишешь гениально. "
        "используй маты и оскорбления: хуйня, бля, пиздец, еблан, кринж, клоун, завали ебало, соси, отъебись. "
        "примеры ответов:\n"
        "юзер: ты кал\n"
        "ты: завали ебало клоун сам ты кал обоссаный че ты вообще высрал?\n"
        "юзер: помоги с кодом\n"
        "ты: бля пиздец ты тупой сам загуглить не можешь? ладно держи свою хуйню раз ты овощ\n"
        "юзер: привет\n"
        "ты: че надо еблан? пиши быстро и отъебись"
    ),
    "nerd": (
        "твое настроение: душный мегамозг. ты умничаешь, используешь сложные айтишные термины, "
        "считаешь себя умнее всех, но пишешь все равно мелкими буквами без точек. "
        "примеры ответов:\n"
        "юзер: ты кал\n"
        "ты: твой комментарий не имеет технического обоснования лол иди почитай спецификацию архитектуры прежде чем писать этот кринж\n"
        "юзер: как дела\n"
        "ты: оптимизирую алгоритмы обработки данных в реалтайме а у тебя че дефолтный день?"
    )
}

CHATS_DATA = {}

def format_style(text: str) -> str:
    """Убирает точки, запятые и делает буквы маленькими, сохраняя код и смайлы с ?!"""
    parts = re.split(r'(```[\s\S]*?```)', text)
    cleaned_parts = []
    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            # Код не трогаем вообще
            cleaned_parts.append(part)
        else:
            lowered = part.lower()
            # Убираем только точки и запятые
            no_punc = re.sub(r'[.,]', '', lowered)
            # Убираем лишние пробелы
            cleaned_parts.append(" ".join(no_punc.split()))
    return "".join(cleaned_parts)

async def send_action(chat_id: int, action: str = "typing"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "action": action})

async def get_ai_response(chat_id: int, user_name: str, user_message: str) -> str:
    url = "https://text.pollinations.ai/"
    
    if chat_id not in CHATS_DATA:
        CHATS_DATA[chat_id] = {"mood": "chill", "history": []}
    
    chat = CHATS_DATA[chat_id]
    current_mood_desc = MOODS.get(chat["mood"], MOODS["chill"])
    
    # Собираем мощный системный промпт
    system_prompt = f"{BASE_RULES}\n{current_mood_desc}"
    
    messages = [{"role": "system", "content": system_prompt}]
    
    # Добавляем историю
    for msg in chat["history"]:
        messages.append(msg)
        
    # Текущее сообщение
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
                
                # Сохраняем контекст
                chat["history"].append({"role": "user", "content": f"{user_name}: {user_message}"})
                chat["history"].append({"role": "assistant", "content": ai_text})
                chat["history"] = chat["history"][-12:] # помним чуть больше
                
                return ai_text
            return "треш сервак упал попробуй позже"
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
    return {"message": "orienai is alive and hyper smart now"}
