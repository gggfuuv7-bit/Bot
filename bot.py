import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import zipfile
import io
import requests
import time
import json
import re
from flask import Flask
from threading import Thread

# --- কনফিগারেশন ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
ALLOWED_USER_ID = 5062314716 
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   
DEFAULT_CF_MODEL = "@cf/zai-org/glm-5.2" 

bot = telebot.TeleBot(BOT_TOKEN)

# --- মেমোরি এবং মডেল স্টোরেজ ---
user_chat_history = {}
# গ্লোবাল ডিকশনারি ব্যবহার করছি যা Render-এ রিস্টার্ট না হওয়া পর্যন্ত থাকবে
user_active_model = {} 

AVAILABLE_MODELS = {
    "gemini": {"name": "Gemini 3.5 Flash", "id": "@cf/google/gemini-3.5-flash"},
    "opus": {"name": "Claude Opus 4.8", "id": "@cf/anthropic/claude-opus-4.8"},
    "grok": {"name": "Grok 3", "id": "@cf/xai/grok-3"},
    "gpt": {"name": "GPT-5.5 Pro", "id": "@cf/openai/gpt-5.5-pro"},
    "deepseek": {"name": "DeepSeek V4 Pro", "id": "@cf/deepseek/deepseek-v4"},
    "glm": {"name": "Z.ai (GLM-5.2)", "id": "@cf/zai-org/glm-5.2"}
}

# --- স্ট্যাবল বাটন মেনু ---
@bot.message_handler(commands=['model'])
def change_model_menu(message):
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(data['name'], callback_data=f"model_{key}") for key, data in AVAILABLE_MODELS.items()]
    markup.add(*buttons)
    bot.send_message(message.chat.id, "🤖 **মডেল নির্বাচন করুন:**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('model_'))
def handle_model_selection(call):
    model_key = call.data.split('_')[1]
    if model_key in AVAILABLE_MODELS:
        # মডেলটি সরাসরি সেভ করছি
        user_active_model[call.message.chat.id] = AVAILABLE_MODELS[model_key]['id']
        bot.answer_callback_query(call.id, f"{AVAILABLE_MODELS[model_key]['name']} সিলেক্ট হয়েছে!")
        bot.edit_message_text(f"✅ এখন থেকে **{AVAILABLE_MODELS[model_key]['name']}** দিয়ে চ্যাট হবে।", 
                              call.message.chat.id, call.message.message_id)

@bot.message_handler(content_types=['text', 'document'])
def handle_message(message):
    chat_id = message.chat.id
    
    # মডেল চেক: যদি সেট না থাকে তবে ডিফল্ট GLM
    model_id = user_active_model.get(chat_id, DEFAULT_CF_MODEL)
    
    bot.send_message(chat_id, f"Processing with: `{model_id.split('/')[-1]}`", parse_mode="Markdown")

    try:
        # API কল
        api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{model_id}"
        headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
        
        # প্রম্পট হ্যান্ডলিং
        prompt = message.text or message.caption or "Help me with coding."
        
        # মেমোরি এবং সিস্টেম ইনস্ট্রাকশন
        if chat_id not in user_chat_history: 
            user_chat_history[chat_id] = [{"role": "system", "content": "You are a coding expert. Output code in XML tags: <file name='x.y'>content</file>"}]
        
        user_chat_history[chat_id].append({"role": "user", "content": prompt})
        
        payload = {"messages": user_chat_history[chat_id][-10:], "stream": True}
        
        final_text = ""
        response = requests.post(api_url, headers=headers, json=payload, stream=True, timeout=180)
        
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line.decode('utf-8').replace('data: ', ''))
                    if 'response' in chunk: final_text += chunk['response']
                    elif 'choices' in chunk: final_text += chunk['choices'][0].get('delta', {}).get('content', '')
            
            user_chat_history[chat_id].append({"role": "assistant", "content": final_text})
            
            # আউটপুট সেন্ড
            bot.send_message(chat_id, final_text[:4000] if len(final_text) < 4000 else "Output too long, check file.")
        else:
            bot.send_message(chat_id, f"API Error ({response.status_code}): {response.text}")
            
    except Exception as e:
        bot.send_message(chat_id, f"Error: {str(e)}")

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Alive!"

if __name__ == "__main__":
    Thread(target=lambda: bot.infinity_polling(timeout=60, long_polling_timeout=60), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
