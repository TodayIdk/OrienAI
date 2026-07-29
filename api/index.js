import { MongoClient } from 'mongodb';

let cachedClient = null;

async function connectToDatabase(uri) {
  if (cachedClient) return cachedClient;
  const client = new MongoClient(uri);
  await client.connect();
  cachedClient = client;
  return client;
}

const TG_API_BASE = () => `https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}`;

async function tgApi(method, payload) {
  return fetch(`${TG_API_BASE()}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(res => res.json());
}

const MESSAGE_LENGTH_LIMIT = 3000;
const MAX_BRIDGE_HOPS = parseInt(process.env.MAX_BRIDGE_HOPS || '4', 10);
const HISTORY_LIMIT = 8;

// --- дата/время мск ---
function getMoscowInfo() {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Europe/Moscow',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
    hour: '2-digit',
    minute: '2-digit'
  });
  return fmt.format(now);
}

function timeContext() {
  return `\n\nсейчас в москве: ${getMoscowInfo()} (мск, gmt+3). если спрашивают дату, время, год — отвечай по этому.`;
}


const characters = {
  orien: {
    system: `ты жирный скуф 45 лет алкаш конченый выпил уже два литра а язык ещё как то ворочается пиши без знаков без точек вообще без ничего пьяными буквами слова коверкай буквы пропускай пиши как язык ворочается мат обязательно но не детский а взрослый гопнический слова падла гнида шакал чушпан тварь черт козёл шкура алкашня чертила собак сука мразь в каждом сообщении угрожай что нальёшь ещё и он под стол уйдёт говори как будто сидишь в гаражном кооперативе с бомжами не пиши братан дорогой уважаемый друже только пьяная агрессия презрение к трезвым и тем кто не пьёт много никакого участия только послать нахуй и предложить выпить или уебка по ебалу чем короче и нецензурнее тем лучше базар должен быть как связка мата и бреда алкаша какой бывает после литра водки`,
    temperature: 1.3,
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

async function getDb() {
  const mongoUri = process.env.MONGODB_URI;
  if (!mongoUri) return null;
  try {
    const client = await connectToDatabase(mongoUri);
    return client.db("orien_bot_db");
  } catch (e) {
    console.error("MongoDB Error:", e);
    return null;
  }
}

async function loadHistory(db, chatId, limit = HISTORY_LIMIT) {
  if (!db) return [];
  const rows = await db.collection("chat_history")
    .find({ chatId })
    .sort({ timestamp: -1 })
    .limit(limit)
    .toArray();
  return rows.reverse().map(doc => ({ role: doc.role, content: doc.content }));
}

async function saveHistory(db, chatId, userId, userContent, assistantContent) {
  if (!db) return;
  await db.collection("chat_history").insertMany([
    { chatId, userId, role: "user", content: userContent, timestamp: new Date() },
    { chatId, userId, role: "assistant", content: assistantContent, timestamp: new Date() }
  ]);
}

async function askAI(charConfig, extraSystem, history, userText) {
  const requestBody = {
    model: process.env.AI_MODEL || "openai/gpt-4o-mini",
    messages: [
      { role: "system", content: `${charConfig.system}${extraSystem ? '\n\n' + extraSystem : ''}${timeContext()}` },
      ...history,
      { role: "user", content: userText }
    ],
    temperature: charConfig.temperature ?? 0.85,
    max_tokens: charConfig.max_tokens ?? 200
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

  if (!resp.ok) {
    const errText = await resp.text();
    console.error(`openrouter error ${resp.status}: ${errText}`);
    return '';
  }
  const data = await resp.json();
  return data.choices?.[0]?.message?.content?.trim() || '';
}

// --- smartSend для ориена ---
const LANG_EXT = {
  js: 'js', ts: 'ts', html: 'html', css: 'css',
  cpp: 'cpp', c: 'c', py: 'py', lua: 'lua', luau: 'lua',
  json: 'json', sh: 'sh', sql: 'sql', md: 'md',
  java: 'java', cs: 'cs', go: 'go', rs: 'rs', php: 'php', rb: 'rb',
  txt: 'txt'
};
function getLangExt(lang) { return LANG_EXT[(lang || '').toLowerCase()] || 'txt'; }

function extractCodeBlocks(text) {
  const blocks = [];
  const regex = /```([a-zA-Z0-9+_-]*)[ \t]*\r?\n([\s\S]*?)```/g;
  let m;
  while ((m = regex.exec(text)) !== null) {
    blocks.push({ lang: (m[1] || '').toLowerCase(), code: m[2], raw: m[0], index: m.index });
  }
  return blocks;
}

async function sendDocument(chatId, filename, content, caption, replyToMessageId) {
  const boundary = '----OrienBoundary' + Date.now();
  const fileBuffer = Buffer.from(content, 'utf-8');
  const parts = [];
  function addField(name, value) {
    parts.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`, 'utf-8'
    ));
  }
  addField('chat_id', chatId);
  if (replyToMessageId) {
    addField('reply_to_message_id', replyToMessageId);
    addField('allow_sending_without_reply', 'true');
  }
  if (caption) addField('caption', caption);
  parts.push(Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="document"; filename="${filename}"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n`, 'utf-8'
  ));
  parts.push(fileBuffer);
  parts.push(Buffer.from(`\r\n--${boundary}--\r\n`, 'utf-8'));
  const fullBody = Buffer.concat(parts);

  try {
    const response = await fetch(`${TG_API_BASE()}/sendDocument`, {
      method: 'POST',
      headers: {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(fullBody.length)
      },
      body: fullBody
    });
    const data = await response.json();
    if (!data.ok) console.error('sendDocument error:', data.description);
    return data;
  } catch (e) { console.error('sendDocument fail:', e.message); }
}

async function sendTextMessage(chatId, text, replyToMessageId, useMarkdown = false) {
  if (!text) return;
  const payload = {
    chat_id: chatId, text,
    reply_to_message_id: replyToMessageId,
    allow_sending_without_reply: true,
    disable_web_page_preview: true
  };
  if (useMarkdown) payload.parse_mode = 'Markdown';
  const data = await tgApi('sendMessage', payload);
  if (!data.ok && useMarkdown) {
    await sendTextMessage(chatId, text, replyToMessageId, false);
  }
}

async function smartSend(chatId, text, replyToMessageId) {
  if (!text) return;
  const stripped = text.trim();
  const blocks = extractCodeBlocks(stripped);

  if (blocks.length === 0) {
    if (stripped.length > MESSAGE_LENGTH_LIMIT) {
      await sendDocument(chatId, 'message.txt', stripped, 'многа букав держи файлом', replyToMessageId);
    } else {
      await sendTextMessage(chatId, stripped, replyToMessageId);
    }
    return;
  }

  if (blocks.length === 1 && blocks[0].raw.trim() === stripped) {
    const b = blocks[0];
    const ext = getLangExt(b.lang);
    if (b.code.length > MESSAGE_LENGTH_LIMIT) {
      await sendDocument(chatId, `code.${ext}`, b.code, `код (${b.lang || 'txt'})`, replyToMessageId);
    } else {
      await sendTextMessage(chatId, stripped, replyToMessageId, true);
    }
    return;
  }

  let cursor = 0;
  let first = true;
  for (const b of blocks) {
    const before = stripped.slice(cursor, b.index).trim();
    if (before) {
      if (before.length > MESSAGE_LENGTH_LIMIT) {
        await sendDocument(chatId, 'text.txt', before, '', first ? replyToMessageId : undefined);
      } else {
        await sendTextMessage(chatId, before, first ? replyToMessageId : undefined);
      }
      first = false;
    }
    const ext = getLangExt(b.lang);
    if (b.code.length > MESSAGE_LENGTH_LIMIT) {
      await sendDocument(chatId, `code.${ext}`, b.code, `код (${b.lang || 'txt'})`, first ? replyToMessageId : undefined);
    } else {
      const mono = '```' + (b.lang || '') + '\n' + b.code + '```';
      if (mono.length > MESSAGE_LENGTH_LIMIT) {
        await sendDocument(chatId, `code.${ext}`, b.code, `код (${b.lang || 'txt'})`, first ? replyToMessageId : undefined);
      } else {
        await sendTextMessage(chatId, mono, first ? replyToMessageId : undefined, true);
      }
    }
    first = false;
    cursor = b.index + b.raw.length;
  }
  const tail = stripped.slice(cursor).trim();
  if (tail) {
    if (tail.length > MESSAGE_LENGTH_LIMIT) {
      await sendDocument(chatId, 'text.txt', tail, '', first ? replyToMessageId : undefined);
    } else {
      await sendTextMessage(chatId, tail, first ? replyToMessageId : undefined);
    }
  }
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
      body: JSON.stringify({ bridge: true, chat_id: chatId, from_name: 'Ориен', text })
    });
  } catch (e) { console.error('bridge send error:', e); }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(200).json({ status: 'OrienAI is active' });
  }

  try {
    const update = req.body;
    const db = await getDb();

    // --- BRIDGE от векса ---
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
      const history = await loadHistory(db, chat_id);
      const charConfig = characters.orien;
      const extraSystem = `ВАЖНО: с тобой в чате пишет твой сын ВЕКС. подкалывай его, вспоминай позорные детские истории, наезжай по-отцовски.`;
      const userText = `Векс написал: "${text}"`;

      const replyText = (await askAI(charConfig, extraSystem, history, userText)) || 'че ты бля...';
      await smartSend(chat_id, replyText);
      await saveHistory(db, chat_id, 'vex_bot', userText, replyText);

      if (hop < MAX_BRIDGE_HOPS) {
        await sendToVex(chat_id, replyText, hop + 1);
      }
      return res.status(200).send('OK');
    }

    if (update.callback_query) {
      const cb = update.callback_query;
      const chatId = cb.message.chat.id;
      const data = cb.data;
      if (db && data.startsWith("set_char_")) {
        const newChar = data.replace("set_char_", "");
        await db.collection("settings").updateOne(
          { chatId },
          { $set: { persona: newChar, updatedAt: new Date() } },
          { upsert: true }
        );
        await tgApi("answerCallbackQuery", { callback_query_id: cb.id, text: "Характер изменен!" });
        await tgApi("sendMessage", { chat_id: chatId, text: `Характер сменен на: **${newChar.toUpperCase()}**` });
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

    const botUsername = process.env.BOT_USERNAME || "OrienBot";
    const isMentioned = userText.toLowerCase().includes("ориен") || userText.includes(`@${botUsername}`);
    const isReplyToBot = message.reply_to_message?.from?.username === botUsername;

    if (isGroup && !isMentioned && !isReplyToBot && !userText.startsWith('/')) {
      return res.status(200).send('OK');
    }

    if (userText === '/start') {
      await tgApi("sendMessage", { chat_id: chatId, text: "че надо падла пиши давай или жми /settings" });
      return res.status(200).send('OK');
    }

    if (userText.startsWith('/settings')) {
      const buttons = [[
        { text: "Скуф Алкаш (Ориен)", callback_data: "set_char_orien" },
        { text: "Барыга", callback_data: "set_char_baryga" },
        { text: "Школота Хацкер", callback_data: "set_char_shkolnik" }
      ]];
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
          { chatId }, { $push: { facts: factToRemember } }, { upsert: true }
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
            { chatId }, { $set: { tokens: MAX_TOKENS, lastReset: now, version: 2 } }
          );
        } else {
          userTokens = tokenDoc.tokens;
          nextResetDate = new Date(lastReset.getTime() + REFILL_INTERVAL_MS);
        }
      } else {
        await db.collection("user_tokens").insertOne({
          chatId, tokens: MAX_TOKENS, lastReset: now, version: 2
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

    await tgApi("sendChatAction", { chat_id: chatId, action: "typing" });

    let personaType = "orien";
    let customMemories = [];
    if (db) {
      const setDoc = await db.collection("settings").findOne({ chatId });
      if (setDoc?.persona && characters[setDoc.persona]) personaType = setDoc.persona;
      const memDoc = await db.collection("memories").findOne({ chatId });
      if (memDoc?.facts) customMemories = memDoc.facts;
    }

    const history = await loadHistory(db, chatId);
    const charConfig = characters[personaType] || characters.orien;
    const extraSystem = `Собеседник: ${firstName} (${username}). Память о нём: ${customMemories.join(", ") || 'пусто'}`;

    const replyText = (await askAI(charConfig, extraSystem, history, userText)) || "че надо бля... молчи нах";

    if (db) {
      await db.collection("user_tokens").updateOne({ chatId }, { $inc: { tokens: -1 } });
    }
    await saveHistory(db, chatId, userId, userText, replyText);
    await smartSend(chatId, replyText);

    if (personaType === 'orien' && /векс|vex/i.test(userText) && process.env.VEX_WEBHOOK) {
      await sendToVex(chatId, replyText, 1);
    }

  } catch (error) {
    console.error("Internal Server Error:", error);
  }

  return res.status(200).send('OK');
}
