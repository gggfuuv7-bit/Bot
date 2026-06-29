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

# --- Cloudflare AI কনফিগারেশন ---
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   
DEFAULT_CF_MODEL = "@cf/zai-org/glm-5.2" 

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

user_chat_history = {}
user_active_model = {}

AVAILABLE_MODELS = {
    "gemini_35_flash": {"name": "Gemini 3.5 Flash", "id": "@cf/google/gemini-3.5-flash"},
    "gemini_35_pro": {"name": "Gemini 3.5 Pro", "id": "@cf/google/gemini-3.5-pro"},
    "opus_48": {"name": "Claude Opus 4.8", "id": "@cf/anthropic/claude-opus-4.8"},
    "sonnet_46": {"name": "Claude 4.6 Sonnet", "id": "@cf/anthropic/claude-sonnet-4.6"},
    "grok_3": {"name": "Grok 3", "id": "@cf/xai/grok-3"},
    "gpt_55_pro": {"name": "GPT-5.5 Pro", "id": "@cf/openai/gpt-5.5-pro"},
    "deepseek_v4": {"name": "DeepSeek V4 Pro", "id": "@cf/deepseek/deepseek-v4"},
    "glm_52": {"name": "Z.ai (GLM-5.2)", "id": "@cf/zai-org/glm-5.2"}
}

def process_text_file(file_content, filename):
    try:
        return f"\n--- File: {filename} ---\n{file_content.decode('utf-8')}\n"
    except:
        return f"\n[Error reading {filename}]\n"

def send_full_output(chat_id, text, is_partial=False):
    caption = "⚠️ লিমিট শেষ! বাকিটুকু পেতে 'continue' বা 'চালিয়ে যাও' লিখুন।" if is_partial else "Output is too long, sending as file."
    if len(text) <= 4000:
        bot.send_message(chat_id, text + ("\n\n" + caption if is_partial else ""))
    else:
        file_stream = io.BytesIO(text.encode('utf-8'))
        file_stream.name = "partial_response.txt" if is_partial else "response.txt"
        bot.send_document(chat_id, file_stream, caption=caption)

# --- Start কমান্ড ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    bot.send_message(message.chat.id, "✅ বট সফলভাবে চালু হয়েছে এবং কাজ করার জন্য প্রস্তুত! \n\nমডেল পরিবর্তন করতে **/model** লিখুন।", parse_mode="Markdown")

# --- মডেল মেনু ---
@bot.message_handler(commands=['model'])
def change_model_menu(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(data['name'], callback_data=f"model_{key}") for key, data in AVAILABLE_MODELS.items()]
    markup.add(*buttons)
        
    bot.send_message(message.chat.id, "🤖 **মডেল নির্বাচন করুন:**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('model_'))
def handle_model_selection(call):
    if call.from_user.id != ALLOWED_USER_ID: return
    model_key = call.data.split('_')[1]
    if model_key in AVAILABLE_MODELS:
        user_active_model[call.message.chat.id] = AVAILABLE_MODELS[model_key]['id']
        bot.answer_callback_query(call.id, f"{AVAILABLE_MODELS[model_key]['name']} সিলেক্ট হয়েছে!")
        bot.edit_message_text(f"✅ সফলভাবে **{AVAILABLE_MODELS[model_key]['name']}** মডেলে পরিবর্তন করা হয়েছে!", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['clear', 'reset'])
def clear_memory(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    if message.chat.id in user_chat_history:
        del user_chat_history[message.chat.id]
    bot.send_message(message.chat.id, "🧹 বটের মেমোরি ক্লিয়ার করা হয়েছে!")

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    prompt_text = message.text or message.caption or "Analyze the attached file(s)."
    
    current_model = user_active_model.get(chat_id, DEFAULT_CF_MODEL)
    model_display_name = next((data['name'] for key, data in AVAILABLE_MODELS.items() if data['id'] == current_model), current_model)
            
    bot.send_message(chat_id, f"Processing with `{model_display_name}`... Please wait.", parse_mode="Markdown")

    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            if os.path.splitext(file_name)[1] in TEXT_EXTENSIONS:
                prompt_text += process_text_file(downloaded_file, file_name)
            elif file_name.endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for info in z.infolist():
                        if not info.is_dir() and os.path.splitext(info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(info) as f: prompt_text += process_text_file(f.read(), info.filename)

        sys_inst = "You are an expert AI. If user asks for files/ZIP, output using XML: <file name=\"name.ext\">\ncontent\n</file>\nNo markdown blocks outside. For 'continue', resume seamlessly."
        
        if chat_id not in user_chat_history: user_chat_history[chat_id] = [{"role": "system", "content": sys_inst}]
        user_chat_history[chat_id].append({"role": "user", "content": prompt_text})
        if len(user_chat_history[chat_id]) > 15: user_chat_history[chat_id] = [user_chat_history[chat_id][0]] + user_chat_history[chat_id][-14:]

        headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
        payload = {"messages": user_chat_history[chat_id], "stream": True}
        
        final_response_text, is_cut_off = "", False
        
        try:
            response = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{current_model}", headers=headers, json=payload, stream=True, timeout=120)
            if response.status_code == 200:
                for line in response.iter_lines():
                    if line and line.decode('utf-8').startswith("data: "):
                        data_str = line.decode('utf-8')[6:]
                        if data_str == "[DONE]": break
                        try:
                            chunk = json.loads(data_str)
                            if "response" in chunk: final_response_text += chunk["response"]
                            elif "choices" in chunk and chunk["choices"]: final_response_text += chunk["choices"][0].get("delta", {}).get("content", "")
                        except: pass
            else:
                bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")
                user_chat_history[chat_id].pop()
                return
        except: is_cut_off = True

        if not final_response_text.strip():
            bot.send_message(chat_id, "❌ কোনো ডেটা আসেনি। অন্য মডেল ট্রাই করুন।")
            user_chat_history[chat_id].pop()
            return

        user_chat_history[chat_id].append({"role": "assistant", "content": final_response_text})
        is_cont = prompt_text.strip().lower() in ['continue', 'চালিয়ে যাও']
        file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_response_text, re.IGNORECASE)
        looks_inc = is_cut_off or ("<file" in final_response_text and "</file>" not in final_response_text)
        
        if file_matches and not looks_inc and not is_cont:
            if len(file_matches) > 1 or 'zip' in prompt_text.lower():
                zb = io.BytesIO()
                with zipfile.ZipFile(zb, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for fn, fc in file_matches:
                        fc = fc.strip()
                        if fc.startswith("
http://googleusercontent.com/immersive_entry_chip/0
http://googleusercontent.com/immersive_entry_chip/1
http://googleusercontent.com/immersive_entry_chip/2
http://googleusercontent.com/immersive_entry_chip/3

### ধাপ ২: বটকে 24/7 জাগিয়ে রাখার ট্রিক (UptimeRobot)
গিটহাবে কোড সেভ করার ৩-৪ মিনিট পর টেলিগ্রামে গিয়ে `/start` লিখুন। বট চালু হয়ে যাবে। এরপর বট যেন আর কখনো না ঘুমায়, তার জন্য এই ১ মিনিটের কাজটি করে রাখুন:

১. ব্রাউজার থেকে Render-এর ড্যাশবোর্ডে গিয়ে আপনার প্রজেক্টের নামের নিচে থাকা লিংকটি (যেমন: `https://your-bot-name.onrender.com`) কপি করুন।
২. এবার [UptimeRobot](https://uptimerobot.com/) ওয়েবসাইটে গিয়ে ফ্রিতে একটি অ্যাকাউন্ট খুলুন।
৩. ড্যাশবোর্ড থেকে **"Add New Monitor"** এ ক্লিক করুন।
৪. Type হিসেবে `HTTP(s)` সিলেক্ট করুন, Name-এ বটের নাম দিন এবং URL-এর জায়গায় Render থেকে কপি করা লিংকটি পেস্ট করে "Create Monitor"-এ ক্লিক করুন।

ব্যাস! UptimeRobot এখন প্রতি ৫ মিনিট পরপর আপনার বটকে একটি করে ধাক্কা দেবে, ফলে আপনার বট জীবনেও ঘুমাবে না এবং আপনি `/model` লেখামাত্রই এক সেকেন্ডে মেনু চলে আসবে!
