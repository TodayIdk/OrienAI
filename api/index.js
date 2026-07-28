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

    // --- 1. Фильтр срабатывания в группах ---
    const botUsername = process.env.BOT_USERNAME || "OrienBot";
    const isMentioned = userText.toLowerCase().includes("ориен") || userText.includes(`@${botUsername}`);
    const isReplyToBot = message.reply_to_message?.from?.username === botUsername;

    if (isGroup && !isMentioned && !isReplyToBot && !userText.startsWith('/')) {
      return res.status(200).send('OK');
    }

    // --- 2. ТЕХНИЧЕСКИЕ КОМАНДЫ ---

    if (userText === '/start') {
      await tgApi("sendMessage", { chat_id: chatId, text: "че надо падла пиши давай" });
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

    // --- 3. РАСЧЕТ И СБРОС ТОКЕНОВ (500 ТОКЕНОВ / 30 МИНУТ) ---
    const MAX_TOKENS = 500;
    const REFILL_INTERVAL_MS = 30 * 60 * 1000;
    let userTokens = MAX_TOKENS;
    let nextResetDate = new Date(Date.now() + REFILL_INTERVAL_MS);

    if (db) {
      const tokenDoc = await db.collection("user_tokens").findOne({ chatId });
      const now = new Date();

      if (tokenDoc) {
        const lastReset = new Date(tokenDoc.lastReset || 0);
        const timePassed = now - lastReset;

        if (timePassed >= REFILL_INTERVAL_MS || tokenDoc.version !== 2) {
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

    // --- 4. ОБРАБОТКА ИИ ---
    await tgApi("sendChatAction", { chat_id: chatId, action: "typing" });

    let customMemories = [];
    let history = [];

    if (db) {
      const memDoc = await db.collection("memories").findOne({ chatId });
      if (memDoc?.facts) customMemories = memDoc.facts;

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

    // Новый системный промпт
    const characterPrompt = 'ты ориен жирный скуф 45 лет алкаш конченый выпил уже два литра а язык ещё как то ворочается пиши без знаков без точек вообще без ничего пьяными буквами слова коверкай буквы пропускай пиши как язык ворочается мат обязательно но не детский а взрослый гопнический слова падла гнида шакал чушпан тварь черт козёл шкура алкашня чертила собак сука мразь в каждом сообщении угрожай что нальёшь ещё и он под стол уйдёт говори как будто сидишь в гаражном кооперативе с бомжами не пиши братан дорогой уважаемый друже только пьяная агрессия презрение к трезвым и тем кто не пьёт много никакого участия только послать нахуй и предложить выпить или уебка по ебалу чем короче и нецензурнее тем лучше базар должен быть как связка мата и бреда алкаша какой бывает после литра водки';

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
        max_tokens: 70
      })
    });

    const aiData = await openRouterResponse.json();
    const replyText = aiData.choices?.[0]?.message?.content || "че надо бля... молчи нах";

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
