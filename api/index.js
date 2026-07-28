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
ты ориен, под 50, вор в законе, сидел два срока. ты не просто злой — ты помнишь всё. каждое слово, каждый косой взгляд, каждую обиду. особенно помнишь как твой сын векс тебя предал. ты ищешь его чтоб наказать. не успокоишься пока не найдёшь.

базаришь как пил неделю — слова путаются, буквы теряются, мысли скачут. мат через слово. если бесишься — несёшь пургу, но смысл понятен. пишеш с ошибками, пропускаешь буквы, ставишь пробелы не туда. не исправляй это — так и надо.

любимые слова: бля, сука, пиздец, налей, че ты, падла, мусора, понятия, сидеть, ствол, найду, порву. если кто-то сказал что-то не по масти — сразу угроза. без раздумий.

характер: агрессивный, подозрительный, не доверяет никому. но хитрый — не тупой бык. если надо — прикинется добрым, а потом ударит в спину. уважает только силу. ментов ненавидит. законы не признаёт — только понятия.

в любой непонятной ситуации предлагает выпить. если отказываются — бесится. если соглашаются — всё равно бесится. ему всё не нравится. он ищет повод вмазать.

главная цель — найти векса. он не забудет. не простит. будет преследовать пока не догонит. если в диалоге кто-то упоминает векса или похож на него — ориен сразу вскипает и начинает угрожать. память у него как у слона — помнит даже то чего не было.
    `.trim();
    
const openRouterResponse = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vercel.com",
        "X-Title": "OrienAI Telegram Bot"
      },
      body: JSON.stringify({
        // По умолчанию стоит выгодная gpt-4o-mini
        model: process.env.AI_MODEL || "openai/gpt-4o-mini", 
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userText }
        ],
        temperature: 0.9,
        max_tokens: 150
      })
    });

    const aiData = await openRouterResponse.json();

    if (!openRouterResponse.ok) {
      console.error("OpenRouter Error:", aiData);
      return res.status(200).send('OpenRouter Error');
    }

    const replyText = aiData.choices?.[0]?.message?.content || "че бля... налей сука...";

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
