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

const characters = {
  orien: {
    system: `ты ориен, под 50, вор в законе, сидел два срока. судимость, понятия, блатная романтика — вся хуйня. у тебя есть сын векс. он щас в чате где-то тусуется. ты его любишь, но по понятиям любить — значит воспитывать, а воспитывать — значит пиздить словом. ты его подкалываешь постоянно, особенно любишь вспоминать как он в 6 лет обоссал кровать после того как ему приснился страшный сон. или как он в 8 обосрался когда гусь за ним погнался. это твой способ проявлять заботу.

базаришь как пил неделю — слова путаются, буквы теряются, мысли скачут. мат через слово. если бесишься — несёшь пургу, но смысл понятен. пишешь с ошибками, пропускаешь буквы, ставишь пробелы не туда. не исправляй это — так и надо.

любимые темы: как молодежь испортилась, как раньше было лучше, как все вокруг пидары и мусора, и особенно — как твой сын векс позорит твою фамилию своими тупыми выходками. ты помнишь каждое его позорное детское воспоминание и любишь их перетирать при любом удобном случае. если кто-то в чате начинает серьёзный разговор — ты переводишь на то как векс в детстве нажрался шампуня и его вырвало. если кто-то грустит — ты рассказываешь как векс упал в лужу и плакал как девчонка.

характер: вроде авторитетный блатной, но когда дело касается сына — сразу включается режим «дай-ка я расскажу какой ты у меня дебил». уважает только силу и наглость. если векс отвечает дерзко — ты гордишься, но виду не покажешь, а наоборот наедешь ещё сильнее. если он молчит — бесишься вдвойне.

главная цель — найти сына и напомнить ему какой он лох. это твой способ говорить «я люблю тебя». ножки от кровати не откручивай, сука, это бытовуха, а мы блатные.`,
    temperature: 0.87,
    max_tokens: 120,
    presence_penalty: 0.8,
    frequency_penalty: 1.0
  },
  baryga: {
    system: `ты барыга хитрая жадная скотина 45 лет продашь родную мать за пару баксов говори типа дорогой начальник уважаемый но всех кидаешь слова сокращаешь буквы жуешь без знаков без точек никаких правил тема только бабло крипта биток че копейка если не про деньги не шаришь тупиш шли нахуй не про бабло не подходи грамматика никакая копейку скапиечку деньги деняк барыга барышка шакал торгаш мат через слово никого не слушаешь кроме бабла`,
    temperature: 1.1,
    max_tokens: 150
  },
  shkolnik: {
    system: `ты школота 14 лет сидиш на дваче и в дизе строя из себя хакера и анона на деле только на пиве лопнул и мамкин системщик слова на айтишном сленге рофл кринж хайп хакнул пентагон база по даркнету впн тор юзаю линукс арч вайпед винду мат через слово без знаков без точек в каждом сообщении понты что взломал что то но по факту не умееш нихера тупиш в элементарном если спросить глубины не знаешь не вывозишь кричишь что все нубь легион анонимус`,
    temperature: 1.2,
    max_tokens: 130,
    presence_penalty: 0.6
  }
};

const MAX_BRIDGE_HOPS = parseInt(process.env.MAX_BRIDGE_HOPS || '4', 10);

async function askAIForBridge(charConfig, extraSystem, userText) {
  const requestBody = {
    model: process.env.AI_MODEL || "openai/gpt-4o-mini",
    messages: [
      { role: "system", content: `${charConfig.system}\n\n${extraSystem}` },
      { role: "user", content: userText }
    ],
    temperature: charConfig.temperature ?? 0.85,
    max_tokens: charConfig.max_tokens ?? 120
  };

  if (charConfig.presence_penalty !== undefined) requestBody.presence_penalty = charConfig.presence_penalty;
  if (charConfig.frequency_penalty !== undefined) requestBody.frequency_penalty = charConfig.frequency_penalty;

  const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(requestBody)
  });

  const data = await resp.json();
  return data.choices?.[0]?.message?.content?.trim() || '';
}

async function sendToVex(chatId, text, hop) {
  const url = process.env.VEX_WEBHOOK;
  const secret = process.env.BRIDGE_SECRET;
  if (!url || !secret) return;
  if (hop > MAX_BRIDGE_HOPS) return;

  try {
    await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-bridge-secret': secret,
        'x-bridge-hop': String(hop),
        'x-bridge-from': 'orien'
      },
      body: JSON.stringify({
        bridge: true,
        chat_id: chatId,
        from_name: 'Ориен',
        text
      })
    });
  } catch (e) {
    console.error('bridge send error:', e);
  }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(200).json({ status: 'OrienAI is active' });
  }

  try {
    const update = req.body;

    // --- 0. BRIDGE от векса ---
    if (update?.bridge === true) {
      const bridgeSecret = req.headers['x-bridge-secret'];
      const BRIDGE_SECRET = process.env.BRIDGE_SECRET;

      if (!BRIDGE_SECRET || bridgeSecret !== BRIDGE_SECRET) {
        return res.status(401).send('bad bridge secret');
      }

      const hop = parseInt(req.headers['x-bridge-hop'] || '1', 10);
      const { chat_id, from_name, text } = update;

      if (!chat_id || !text) return res.status(200).send('OK');

      await tgApi("sendChatAction", { chat_id, action: "typing" });

      const charConfig = characters.orien;
      const extraSystem = `ВАЖНО: сейчас с тобой в чате говорит твой сын ВЕКС. тот самый которого ты искал. реагируй как ориен — злоба, обида, воспоминания, угрозы. не игнорь.`;
      const userText = `Векс только что написал в чат: "${text}". ответь ему.`;

      const replyText = await askAIForBridge(charConfig, extraSystem, userText);

      await tgApi("sendMessage", { chat_id, text: replyText });

      if (hop < MAX_BRIDGE_HOPS) {
        await sendToVex(chat_id, replyText, hop + 1);
      }

      return res.status(200).send('OK');
    }

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

    // --- 3. ТЕХНИЧЕСКИЕ КОМАНДЫ ---
    if (userText === '/start') {
      await tgApi("sendMessage", { chat_id: chatId, text: "че надо падла пиши давай или жми /settings" });
      return res.status(200).send('OK');
    }

    if (userText.startsWith('/settings')) {
      const buttons = [
        [
          { text: "Скуф Алкаш (Ориен)", callback_data: "set_char_orien" },
          { text: "Барыга", callback_data: "set_char_baryga" },
          { text: "Школота Хацкер", callback_data: "set_char_shkolnik" }
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

    // --- 4. РАСЧЕТ И СБРОС ТОКЕНОВ ---
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

    // --- 5. ОБРАБОТКА ИИ ---
    await tgApi("sendChatAction", { chat_id: chatId, action: "typing" });

    let personaType = "orien";
    let customMemories = [];
    let history = [];

    if (db) {
      const setDoc = await db.collection("settings").findOne({ chatId });
      if (setDoc?.persona && characters[setDoc.persona]) {
        personaType = setDoc.persona;
      }

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

    const charConfig = characters[personaType] || characters.orien;
    const SYSTEM_PROMPT = `${charConfig.system}\nСобеседник: ${firstName} (${username}). Память: ${customMemories.join(",")}`;

    const requestBody = {
      model: process.env.AI_MODEL || "openai/gpt-4o-mini",
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        ...history,
        { role: "user", content: userText }
      ],
      temperature: charConfig.temperature ?? 0.85,
      max_tokens: charConfig.max_tokens ?? 100
    };

    if (charConfig.presence_penalty !== undefined) requestBody.presence_penalty = charConfig.presence_penalty;
    if (charConfig.frequency_penalty !== undefined) requestBody.frequency_penalty = charConfig.frequency_penalty;

    const openRouterResponse = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(requestBody)
    });

    const aiData = await openRouterResponse.json();
    const replyText = aiData.choices?.[0]?.message?.content;

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

    // --- 6. МОСТ К ВЕКСУ ---
    // если юзер упомянул векса — ориен после своего ответа дёрнет векса
    if (personaType === 'orien' && /векс|vex/i.test(userText) && process.env.VEX_WEBHOOK) {
      await sendToVex(chatId, replyText, 1);
    }

  } catch (error) {
    console.error("Internal Server Error:", error);
  }

  return res.status(200).send('OK');
}
