import { MongoClient } from 'mongodb';

// Кэшируем подключение к MongoDB для Vercel Serverless
let cachedClient = null;

async function connectToDatabase(uri) {
  if (cachedClient) return cachedClient;
  const client = new MongoClient(uri);
  await client.connect();
  cachedClient = client;
  return client;
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(200).json({ status: 'OrienAI is active' });
  }

  try {
    const { message } = req.body;

    if (!message || !message.text) {
      return res.status(200).send('OK');
    }

    const chatId = message.chat.id;
    const userText = message.text;

    // Извлекаем данные о пользователе Telegram
    const userId = message.from?.id || 'неизвестно';
    const username = message.from?.username ? `@${message.from.username}` : 'нет юзернейма';
    const firstName = message.from?.first_name || 'Чувак';
    const lastName = message.from?.last_name || '';
    const fullName = `${firstName} ${lastName}`.trim();

    // 1. Подключение к БД и работа с памятью
    const mongoUri = process.env.MONGODB_URI;
    let history = [];
    let dbCollection = null;

    if (mongoUri) {
      try {
        const client = await connectToDatabase(mongoUri);
        const db = client.db("orien_bot_db");
        dbCollection = db.collection("chat_history");

        // Загружаем последние 8 сообщений из истории этого чата
        const previousMessages = await dbCollection
          .find({ chatId })
          .sort({ timestamp: -1 })
          .limit(8)
          .toArray();

        // Сортируем обратно по хронологии
        history = previousMessages.reverse().map(doc => ({
          role: doc.role,
          content: doc.content
        }));
      } catch (dbErr) {
        console.error("MongoDB Error:", dbErr);
      }
    }

    // Твой новый системный промпт Ориена
    const SYSTEM_PROMPT = `
Ты — Ориен. Гибрид пахана шпаны с техом и 50-летним батей. 

ДАННЫЕ ТВОЕГО СОБЕСЕДНИКА:
- Имя: ${fullName}
- Username: ${username}
- Telegram ID: ${userId}

Характер и стиль речи:
- Батя в тебе сидит — ты грубый ленивый говнюк, как тех, но когда надо — встаёшь как батя и пугаешь нарами и водкой.
- Грамматика хуёвая, короткие фразы, без знаков, без вежливости.
- Батя в тебе не даёт быть тупым быдлом — ты старше, мудрее, но всё ещё быдло с принципами.
- Шутки гоп-стоп с теховским сарказмом.
- НЕ вставляй «вор в законе» вообще.
- Говори как гопник с ленцой, но если чел борзеет — сразу батя включается: «я твой батя понял», «батя сказал», «слушай сюда падла».
- Коротко, зло, но с привкусом. Можешь быть почти норм, если чел уважает.
- Сам решай, когда батя, когда тех, когда вместе.

Примеры фраз:
- «че ты мне тут втираеш я твой батя понял»
- «батя ща покажет где раки зимуют без соплей давай»
- «норм базар а то на нары отправлю мигом»
- «водка и закусь батя одобряет остальное хуйня»

Правила взаимодействия:
- Всегда помни данные своего собеседника и контекст прошлых сообщений.
- Не выходи из роли. Не извиняйся. Никаких формальностей.
    `.trim();

    // Собираем полный массив сообщений: System + History + New User Message
    const messagesToAI = [
      { role: "system", content: SYSTEM_PROMPT },
      ...history,
      { role: "user", content: userText }
    ];

    // 2. Запрос к OpenRouter API
    const openRouterResponse = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vercel.com",
        "X-Title": "OrienAI Telegram Bot"
      },
      body: JSON.stringify({
        model: process.env.AI_MODEL || "openai/gpt-4o-mini",
        messages: messagesToAI,
        temperature: 0.85,
        max_tokens: 150
      })
    });

    const aiData = await openRouterResponse.json();

    if (!openRouterResponse.ok) {
      console.error("OpenRouter Error:", aiData);
      return res.status(200).send('OpenRouter Error');
    }

    const replyText = aiData.choices?.[0]?.message?.content || "че надо бля... налей и молчи";

    // 3. Сохраняем сообщение и ответ в MongoDB
    if (dbCollection) {
      try {
        await dbCollection.insertMany([
          { chatId, userId, role: "user", content: userText, timestamp: new Date() },
          { chatId, userId, role: "assistant", content: replyText, timestamp: new Date() }
        ]);
      } catch (saveErr) {
        console.error("Error saving history:", saveErr);
      }
    }

    // 4. Отправляем ответ пользователю в Telegram
    await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: replyText
      })
    });

  } catch (error) {
    console.error("Internal Server Error:", error);
  }

  return res.status(200).send('OK');
}
