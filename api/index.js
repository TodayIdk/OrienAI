import { MongoClient } from 'mongodb';

let cachedClient = null;

async function connectToDatabase(uri) {
  if (cachedClient) return cachedClient;
  const client = new MongoClient(uri);
  await client.connect();
  cachedClient = client;
  return client;
}

async function tgApi(method, payload) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  return fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(res => res.json());
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(200).json({ status: 'OrienAI is active' });
  }

  try {
    const update = req.body;

    // --- 1. Inline-кнопки (/settings) ---
    if (update.callback_query) {
      const cb = update.callback_query;
      const chatId = cb.message.chat.id;
      const data = cb.data;

      const mongoUri = process.env.MONGODB_URI;
      if (mongoUri) {
        const client = await connectToDatabase(mongoUri);
        const db = client.db("orien_bot_db");
        
        if (data.startsWith("set_char_")) {
          const newChar = data.replace("set_char_", "");
          await db.collection("settings").updateOne(
            { chatId },
            { $set: { persona: newChar, updatedAt: new Date() } },
            { upsert: true }
          );

          await tgApi("answerCallbackQuery", { callback_query_id: cb.id, text: "Характер изменен!" });
          await tgApi("sendMessage", { chat_id: chatId, text: `Характер сменен на: **${newChar.toUpperCase()}**` });
        }
      }
      return res.status(200).send('OK');
    }

    const message = update.message || update.channel_post;
    if (!message || (!message.text && !message.caption)) {
      return res.status(200).send('OK');
    }

    const chatId = message.chat.id;
    const userText = (message.text || message.caption || "").trim();
    const isGroup = message.chat.type === 'group' || message.chat.type === 'supergroup';

    const userId = message.from?.id || 'неизвестно';
    const username = message.from?.username ? `@${message.from.username}` : '';
    const firstName = message.from?.first_name || 'Чувак';

    const mongoUri = process.env.MONGODB_URI;
    let db = null;

    if (mongoUri) {
      try {
        const client = await connectToDatabase(mongoUri);
        db = client.db("orien_bot_db");
      } catch (dbErr) {
        console.error("MongoDB Error:", dbErr);
      }
    }

    // --- 2. Фильтр срабатывания в группах ---
    const botUsername = process.env.BOT_USERNAME || "OrienBot";
    const isMentioned = userText.toLowerCase().includes("ориен") || userText.includes(`@${botUsername}`);
    const isReplyToBot = message.reply_to_message?.from?.username === botUsername;

    if (isGroup && !isMentioned && !isReplyToBot && !userText.startsWith('/')) {
      return res.status(200).send('OK');
    }

    // --- 3. ТЕХНИЧЕСКИЕ КОМАНДЫ (БЕСПЛАТНЫЕ) ---

    if (userText === '/start') {
      await tgApi("sendMessage", { chat_id: chatId, text: "че надо падла пиши давай или жми /settings" });
      return res.status(200).send('OK');
    }

    if (userText.startsWith('/settings')) {
      const buttons = [
        [
          { text: "Быдло", callback_data: "set_char_bydlo" },
          { text: "Барыга", callback_data: "set_char_baryga" },
          { text: "Очкарик", callback_data: "set_char_ochkarik" }
        ]
      ];
      await tgApi("sendMessage", {
        chat_id: chatId,
        text: `⚙️ **НАСТРОЙКИ ОРИЕНА**\n\nВыбери характер:`,
        parse_mode: "Markdown",
        reply_markup: { inline_keyboard: buttons }
      });
      return res.status(200).send('OK');
    }

    if (userText.toLowerCase().includes("мут") && isGroup) {
      if (!message.reply_to_message) {
        await tgApi("sendMessage", { chat_id: chatId, text: "ответь на соо кого мутить надо падла" });
        return res.status(200).send('OK');
      }
      const targetUserId = message.reply_to_message.from.id;
      const match = userText.match(/\d+/);
      const minutes = match ? parseInt(match[0]) : 10;
      const untilDate = Math.floor(Date.now() / 1000) + minutes * 60;

      try {
        await tgApi("restrictChatMember", {
          chat_id: chatId,
          user_id: targetUserId,
          permissions: { can_send_messages: false },
          until_date: untilDate
        });
        await tgApi("sendMessage", { chat_id: chatId, text: `заткнул падлу на ${minutes} минут` });
      } catch (err) {
        await tgApi("sendMessage", { chat_id: chatId, text: "не могу замутить прав дай сперва админских" });
      }
      return res.status(200).send('OK');
    }

    if (userText.toLowerCase().includes("ориен запомни")) {
      const factToRemember = userText.replace(/ориен запомни/i, "").trim();
      if (factToRemember && db) {
        await db.collection("memories").updateOne(
          { chatId },
          { $push: { facts: factToRemember } },
          { upsert: true }
        );
        await tgApi("sendMessage", { chat_id: chatId, text: "запомнил бля" });
      }
      return res.status(200).send('OK');
    }

    if (userText.toLowerCase().includes("ориен сброс памяти") || userText.startsWith("/reset")) {
      if (db) {
        await db.collection("memories").deleteOne({ chatId });
        await db.collection("chat_history").deleteMany({ chatId });
      }
      await tgApi("sendMessage", { chat_id: chatId, text: "все забыл чистый лист нах" });
      return res.status(200).send('OK');
    }

    // --- 4. РАСЧЕТ И СБРОС ТОКЕНОВ (500 ТОКЕНОВ / 30 МИНУТ) ---
    // Установлен лимит в 500 сообщений/запросов (считается по сообщениям, чтобы тратилось 1:1)
    const MAX_TOKENS = 500;
    const REFILL_INTERVAL_MS = 30 * 60 * 1000; // 30 минут
    let userTokens = MAX_TOKENS;
    let nextResetDate = new Date(Date.now() + REFILL_INTERVAL_MS);

    if (db) {
      const tokenDoc = await db.collection("user_tokens").findOne({ chatId });
      const now = new Date();

      if (tokenDoc) {
        const lastReset = new Date(tokenDoc.lastReset || 0);
        const timePassed = now - lastReset;

        if (timePassed >= REFILL_INTERVAL_MS || tokenDoc.version !== 2) {
          // Автоматический сброс для всех и пополнение до 500 токенов
          userTokens = MAX_TOKENS;
          nextResetDate = new Date(now.getTime() + REFILL_INTERVAL_MS);
          await db.collection("user_tokens").updateOne(
            { chatId },
            { $set: { tokens: MAX_TOKENS, lastReset: now, version: 2 } }
          );
        } else {
          userTokens = tokenDoc.tokens;
          nextResetDate = new Date(lastReset.getTime() + REFILL_INTERVAL_MS);
        }
      } else {
        await db.collection("user_tokens").insertOne({
          chatId,
          tokens: MAX_TOKENS,
          lastReset: now,
          version: 2
        });
      }
    }

    if (userText.startsWith('/tokens')) {
      const minutesLeft = Math.ceil((nextResetDate - new Date()) / (1000 * 60));
      await tgApi("sendMessage", {
        chat_id: chatId,
        text: `📊 **Баланс токенов:** ${userTokens} / ${MAX_TOKENS}\n⏳ Восстановление через: ${minutesLeft > 0 ? minutesLeft : 0} мин.`,
        parse_mode: "Markdown"
      });
      return res.status(200).send('OK');
    }

    if (userTokens <= 0) {
      const minutesLeft = Math.ceil((nextResetDate - new Date()) / (1000 * 60));
      await tgApi("sendMessage", {
        chat_id: chatId,
        text: `кончились токены бля жди еще ${minutesLeft} мин или вали`
      });
      return res.status(200).send('OK');
    }

    // --- 5. ОБРАБОТКА ИИ МАКСИМАЛЬНО ЭКОНОМНО ---
    await tgApi("sendChatAction", { chat_id: chatId, action: "typing" });

    let personaType = "bydlo";
    let customMemories = [];
    let history = [];

    if (db) {
      const setDoc = await db.collection("settings").findOne({ chatId });
      if (setDoc?.persona) personaType = setDoc.persona;

      const memDoc = await db.collection("memories").findOne({ chatId });
      if (memDoc?.facts) customMemories = memDoc.facts;

      // Загружаем только последние 3 сообщения для экономии
      const previousMessages = await db.collection("chat_history")
        .find({ chatId })
        .sort({ timestamp: -1 })
        .limit(3)
        .toArray();

      history = previousMessages.reverse().map(doc => ({
        role: doc.role,
        content: doc.content
      }));
    }

    // Сокращенные промпты (для экономии токенов)
    let characterPrompt = "";
    if (personaType === "baryga") {
      characterPrompt = `ты барыга торгаш 40 лет жадный говорит быстро без знаков сленг: братан начальник. своди все к деньгам крипте нефти.`;
    } else if (personaType === "ochkarik") {
      characterPrompt = `ты очкарик задрот 25 лет мнит себя гением айтишник пиши без знаков с английскими терминами говоря про линукс и арч.`;
    } else {
      characterPrompt = `ты ориен 50 лет алкаш гопник без знаков мат через слово ленивый тупой агрессивный чем короче ответ тем лучше.`;
    }

    const SYSTEM_PROMPT = `${characterPrompt}\nСобеседник: ${firstName} (${username}). Память: ${customMemories.join(",")}`;

    const openRouterResponse = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: process.env.AI_MODEL || "openai/gpt-4o-mini",
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          ...history,
          { role: "user", content: userText }
        ],
        temperature: 0.85,
        max_tokens: 70 // Не дает модели расписывать длинные тексты
      })
    });

    const aiData = await openRouterResponse.json();
    const replyText = aiData.choices?.[0]?.message?.content || "че надо бля... молчи нах";

    // Списываем РОВНО 1 токен за сообщение (чтобы 500 токенов = 500 сообщений)
    if (db) {
      await db.collection("user_tokens").updateOne(
        { chatId },
        { $inc: { tokens: -1 } }
      );

      await db.collection("chat_history").insertMany([
        { chatId, userId, role: "user", content: userText, timestamp: new Date() },
        { chatId, userId, role: "assistant", content: replyText, timestamp: new Date() }
      ]);
    }

    await tgApi("sendMessage", {
      chat_id: chatId,
      text: replyText
    });

  } catch (error) {
    console.error("Internal Server Error:", error);
  }

  return res.status(200).send('OK');
}
