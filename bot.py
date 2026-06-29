import os
import telebot
import zipfile
import io
import requests
import time
import json
import re
from flask import Flask
from threading import Thread

# --- টেলিগ্রাম কনফিগারেশন ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
ALLOWED_USER_ID = 5062314716 

# --- Kie.ai API কনফিগারেশন ---
KIE_API_TOKEN = "705642c427c89e3ec6cae822307facde" # আপনার দেওয়া API Key
KIE_API_URL = "https://api.kie.ai/claude/v1/messages"
KIE_MODEL = "claude-opus-4-8"

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv', '.py']

# --- মেমোরি ডেটাবেস ---
user_chat_history = {}

def process_text_file(file_content, filename):
    try:
        text = file_content.decode('utf-8')
        return f"\n--- File: {filename} ---\n{text}\n"
    except:
        return f"\n[Error reading {filename} as text]\n"

def send_full_output(chat_id, text):
    if len(text) <= 4000:
        bot.send_message(chat_id, text)
    else:
        file_stream = io.BytesIO(text.encode('utf-8'))
        file_stream.name = "response.txt"
        bot.send_document(chat_id, file_stream, caption="Output is too long, sending as file.")

@bot.message_handler(commands=['start', 'clear', 'reset'])
def clear_memory(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    if chat_id in user_chat_history:
        del user_chat_history[chat_id]
    bot.send_message(chat_id, f"✅ Kie.ai ({KIE_MODEL}) বট চালু হয়েছে এবং মেমোরি ক্লিয়ার করা হয়েছে! \n\nআপনার প্রজেক্ট ফাইল বা প্রম্পট দিন।")

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    
    prompt_text = message.text or message.caption or "Analyze the attached file(s)."
    
    bot.send_message(chat_id, f"Processing with `{KIE_MODEL}` (Thinking Mode Active)... Please wait.", parse_mode="Markdown")

    try:
        # --- ফাইল প্রসেসিং লজিক ---
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in TEXT_EXTENSIONS:
                prompt_text += process_text_file(downloaded_file, file_name)
            elif file_ext == '.zip':
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir() and os.path.splitext(zip_info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(zip_info) as extracted_file:
                                prompt_text += process_text_file(extracted_file.read(), zip_info.filename)

        # --- হিস্ট্রি ম্যানেজমেন্ট ---
        if chat_id not in user_chat_history:
            user_chat_history[chat_id] = []
            
        user_chat_history[chat_id].append({"role": "user", "content": prompt_text})
        
        # টোকেন লিমিট বাঁচাতে হিস্ট্রি সাইজ কন্ট্রোল
        if len(user_chat_history[chat_id]) > 10:
            user_chat_history[chat_id] = user_chat_history[chat_id][-10:]

        # --- Kie.ai (Claude Format) API Call ---
        headers = {
            "Authorization": f"Bearer {KIE_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        system_instruction = (
            "You are an expert AI coding assistant. "
            "If the user asks for files or a ZIP, you MUST output the files using this exact XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete file content here]\n</file>\n'
            "Do NOT use markdown code blocks outside the tags. "
            "CRITICAL: If the user types 'continue', seamlessly continue exactly from where your last response stopped."
        )

        payload = {
            "model": KIE_MODEL,
            "system": system_instruction, # Claude মডেলে system prompt আলাদা থাকে
            "messages": user_chat_history[chat_id],
            "thinkingFlag": True, # আপনার দেওয়া cURL অনুযায়ী Thinking ফ্লাগ অন করা হলো
            "stream": False,
            "max_tokens": 4096
        }

        response = requests.post(KIE_API_URL, headers=headers, json=payload, timeout=180)
        
        if response.status_code == 200:
            data = response.json()
            final_response_text = ""
            
            # Claude-এর রেসপন্স সাধারণত data["content"][0]["text"] এ থাকে
            if "content" in data and isinstance(data["content"], list) and len(data["content"]) > 0:
                final_response_text = data["content"][0].get("text", "")
            # যদি OpenAI স্টাইল ফলো করে থাকে
            elif "choices" in data:
                final_response_text = data["choices"][0]["message"]["content"]
            else:
                final_response_text = f"Unknown API Format:\n{json.dumps(data, indent=2)[:1000]}"
            
            # এআই-এর উত্তর মেমোরিতে যুক্ত করা
            user_chat_history[chat_id].append({"role": "assistant", "content": final_response_text})
            
            # --- ফাইল পার্সিং এবং আউটপুট ---
            file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_response_text, re.IGNORECASE)
            MD_TICKS = chr(96) * 3 
            
            if file_matches and not prompt_text.strip().lower() in ['continue', 'চালিয়ে যাও']:
                user_wants_zip = 'zip' in prompt_text.lower()
                
                if len(file_matches) > 1 or user_wants_zip:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        for filename, content in file_matches:
                            content = content.strip()
                            if content.startswith(MD_TICKS): content = content.split('\n', 1)[-1]
                            if content.endswith(MD_TICKS): content = content.rsplit('\n', 1)[0]
                            zip_file.writestr(filename, content.strip())
                    
                    zip_buffer.seek(0)
                    zip_buffer.name = "project_files.zip"
                    bot.send_document(chat_id, zip_buffer, caption="Here is your ZIP file from Claude Opus 4.8.")
                else:
                    filename = file_matches[0][0]
                    content = file_matches[0][1].strip()
                    if content.startswith(MD_TICKS): content = content.split('\n', 1)[-1]
                    if content.endswith(MD_TICKS): content = content.rsplit('\n', 1)[0]
                        
                    file_buffer = io.BytesIO(content.strip().encode('utf-8'))
                    file_buffer.name = filename
                    bot.send_document(chat_id, file_buffer, caption=f"Here is your {filename} file.")
            else:
                send_full_output(chat_id, final_response_text)
                
        else:
            bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")

    except Exception as e:
        bot.send_message(chat_id, f"An error occurred: {str(e)}")

# --- Render 24/7 Web Server ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is securely running 24/7 with Kie.ai (Claude Opus 4.8)!"

def run_bot():
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
