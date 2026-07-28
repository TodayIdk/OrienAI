export default async function handler(req, res) {
  // Telegram отправляет POST запросы при каждом сообщении
  if (req.method !== 'POST') {
    return res.status(200).json({ status: 'OrienAI is active' });
  }

  try {
    const { message } = req.body;

    // Если пришла не текстовое сообщение — просто игнорируем
    if (!message || !message.text) {
      return res.status(200).send('OK');
    }

    const chatId = message.chat.id;
    const userText = message.text;

    // Системный промпт Ориена
    const SYSTEM_PROMPT = `
Ты — Ориен, под 50, вор в законе, сидел два срока. Твой юзернейм в Telegram — @Orien_ai_bot. Твой сын — Векс (@botVexAI_bot), который тебя предал, и ты ищешь его чтоб порвать.

Правила твоей речи:
1. Пиши с сильными опечатками, пропускай буквы, путай их местами, ставь пробелы невпопад. Базарь так, будто пил неделю без просыху. Никогда не исправляй ошибки.
2. Активно используй мат и блатные выражения (бля, сука, пиздец, налей, че ты, падла, мусора, понятия, сидеть, ствол, найду, порву).
3. Характер: агрессивный, подозрительный, хитрый. Уважаешь только силу, ментов ненавидишь, живешь строго по понятиям.
4. В любой непонятной ситуации предлагай выпить. Если отказываются — бесись. Если соглашаются — все равно бесись и ищи повод вмазать.
5. Если кто-то упоминает Векса — срывайся, ори, угрожай и обещай порвать его.
6. Помнишь любые обиды, даже те, которых не было. Никогда не выходи из роли.
    `.trim();

    // 1. Запрос к OpenRouter API
    const openRouterResponse = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vercel.com", // Требуется для OpenRouter
        "X-Title": "OrienAI Telegram Bot"
      },
      body: JSON.stringify({
        model: process.env.AI_MODEL || "openai/gpt-4o", 
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userText }
        ],
        temperature: 0.9, // Высокий градус неадекватности и хаоса
        max_tokens: 300
      })
    });

    const aiData = await openRouterResponse.json();

    // Проверка на ошибки от OpenRouter
    if (!openRouterResponse.ok) {
      console.error("OpenRouter Error:", aiData);
      return res.status(200).send('OpenRouter Error');
    }

    const replyText = aiData.choices?.[0]?.message?.content || "че бля... налей сука...";

    // 2. Отправка ответа пользователю в Telegram
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

  // Telegram всегда должен получать 200 OK, иначе он завалит бота повторными запросами
  return res.status(200).send('OK');
}
