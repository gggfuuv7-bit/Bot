import os
import telebot
import zipfile
import io
import requests
import time
from flask import Flask
from threading import Thread

# --- কনফিগারেশন ---
# এখানে আপনার আসল টেলিগ্রাম বটের টোকেনটি দিন
BOT_TOKEN = "8836794590:AAGDA3S4ePZI1MTHWZM9ka1NO_BdddCFp20" 
ALLOWED_USER_ID = 5062314716 # আপনার টেলিগ্রাম ইউজার আইডি বসিয়ে দিন

# --- G0I.AI কনফিগারেশন (সরাসরি API) ---
G0I_API_KEY = "sk-abed438aa07ec4fe1f5e1bf60e2979b9fdd8780bfc0a684984a9f4d0a5e4439d"
BASE_URL = "https://api.g0i.ai"
MODEL_NAME = "claude-opus-4-8" # আপনার সিলেক্ট করা মডেল

bot = telebot.TeleBot(BOT_TOKEN)

TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

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
        file_stream.name = "full_response.txt"
        bot.send_document(chat_id, file_stream, caption="Output is too long, sending as file.")

@bot.message_handler(content_types=['text', 'document', 'photo'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID:
        return

    chat_id = message.chat.id
    prompt_text = message.text or message.caption or "Analyze the attached file(s)."
    
    bot.send_message(chat_id, f"Processing your request with {MODEL_NAME}... Please wait.")

    content_blocks = []

    try:
        # ফাইল প্রসেসিং
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in TEXT_EXTENSIONS:
                prompt_text += process_text_file(downloaded_file, file_name)
            
            elif file_ext == '.zip':
                prompt_text += "\n\n--- Extracted ZIP Contents ---\n"
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir() and os.path.splitext(zip_info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(zip_info) as extracted_file:
                                prompt_text += process_text_file(extracted_file.read(), zip_info.filename)

        content_blocks.append({"type": "text", "text": prompt_text})
        
        # --- API Call (G0I.AI - Anthropic Format) ---
        api_url = f"{BASE_URL}/v1/messages"
        
        headers = {
            "x-api-key": G0I_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        payload = {
            "model": MODEL_NAME,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": content_blocks}]
        }

        response = requests.post(api_url, headers=headers, json=payload, timeout=60)

        if response.status_code == 200:
            result_data = response.json()
            final_response_text = result_data['content'][0]['text']
            send_full_output(chat_id, final_response_text)
        else:
            bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")

    except requests.exceptions.Timeout:
        bot.send_message(chat_id, "Error: The AI took too long to respond (Timeout).")
    except Exception as e:
        bot.send_message(chat_id, f"An error occurred: {str(e)}")

# --- Render 24/7 Web Server ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running perfectly with G0I.AI!"

def run_bot():
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=60)
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
