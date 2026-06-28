import os
import telebot
import base64
import zipfile
import io
import requests
import time
from flask import Flask
from threading import Thread

# --- কনফিগারেশন ---
API_KEY = "fe_oa_c1b46d08269cb2874f0e82fdecb91d79dde771dcbfc00280" # আপনার API Key দিন
BASE_URL = "https://api.freemodel.dev"
BOT_TOKEN = "8836794590:AAGDA3S4ePZI1MTHWZM9ka1NO_BdddCFp20" # আপনার Bot Token দিন
ALLOWED_USER_ID = 5062314716 # আপনার টেলিগ্রাম ইউজার আইডি দিন

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
    prompt_text = message.text or message.caption or "Analyze this file(s) and provide the complete code/output."
    
    bot.send_message(chat_id, "Processing your request with GLM... Please wait.")
    content_blocks = []

    try:
        if message.photo:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            image_base64 = base64.b64encode(downloaded_file).decode('utf-8')
            content_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})

        elif message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in ['.jpg', '.jpeg', '.png']:
                media_type = 'jpeg' if file_ext in ['.jpg', '.jpeg'] else 'png'
                image_base64 = base64.b64encode(downloaded_file).decode('utf-8')
                content_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/{media_type};base64,{image_base64}"}})
            
            elif file_ext in TEXT_EXTENSIONS:
                prompt_text += process_text_file(downloaded_file, file_name)
            
            elif file_ext == '.zip':
                prompt_text += "\n\n--- Extracted ZIP Contents ---\n"
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir() and os.path.splitext(zip_info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(zip_info) as extracted_file:
                                prompt_text += process_text_file(extracted_file.read(), zip_info.filename)

        content_blocks.insert(0, {"type": "text", "text": prompt_text})
        
        # --- API Call using Requests (OpenAI format for GLM) ---
        api_url = f"{BASE_URL}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        # মডেলের নাম GLM দেওয়া হয়েছে
        payload = {
            "model": "glm-5.2", 
            "messages": [{"role": "user", "content": content_blocks}]
        }

        response = requests.post(api_url, headers=headers, json=payload, timeout=60)

        if response.status_code == 200:
            result_data = response.json()
            final_response_text = result_data['choices'][0]['message']['content']
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
    return "Bot is running perfectly with GLM!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    Thread(target=run_server).start()
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=60)
        except Exception as e:
            time.sleep(3)
