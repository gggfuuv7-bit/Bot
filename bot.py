import os
import telebot
import base64
import zipfile
import io
import requests
import json

# --- আপনার কনফিগারেশন ---
API_KEY = "fe_oa_c1b46d08269cb2874f0e82fdecb91d79dde771dcbfc00280"
BASE_URL = "https://cc.freemodel.dev"
BOT_TOKEN = "8836794590:AAGDA3S4ePZI1MTHWZM9ka1NO_BdddCFp20"
ALLOWED_USER_ID = 5062314716 # আপনার টেলিগ্রাম ইউজার আইডি

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
    print("\n--- New Message Received ---")
    if message.from_user.id != ALLOWED_USER_ID:
        print("Unauthorized user blocked.")
        return

    chat_id = message.chat.id
    prompt_text = message.text or message.caption or "Analyze this file(s) and provide the complete code/output."
    
    bot.send_message(chat_id, "Processing your request with GPT-5.5... Please wait.")
    print("Sent 'Processing...' message to user.")
    
    content_blocks = []

    try:
        # ফাইল প্রসেসিং
        if message.photo:
            print("Processing Photo...")
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            image_base64 = base64.b64encode(downloaded_file).decode('utf-8')
            content_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})

        elif message.document:
            print(f"Processing Document: {message.document.file_name}")
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in ['.jpg', '.jpeg', '.png']:
                media_type = 'jpeg' if file_ext in ['.jpg', '.jpeg'] else 'png'
                image_base64 = base64.b64encode(downloaded_file).decode('utf-8')
                content_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/{media_type};base64,{image_base64}"}})
            
            elif file_ext in TEXT_EXTENSIONS:
                extracted_text = process_text_file(downloaded_file, file_name)
                prompt_text += f"\n\n{extracted_text}"
            
            elif file_ext == '.zip':
                prompt_text += "\n\n--- Extracted ZIP Contents ---\n"
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir() and os.path.splitext(zip_info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(zip_info) as extracted_file:
                                prompt_text += process_text_file(extracted_file.read(), zip_info.filename)

        # টেক্সট প্রম্পট যোগ করা (GPT ফরম্যাট অনুযায়ী)
        content_blocks.insert(0, {"type": "text", "text": prompt_text})
        
        # --- GPT-5.5 API Call (OpenAI-compatible format) ---
        api_url = f"{BASE_URL}/v1/chat/completions" # GPT এর জন্য এন্ডপয়েন্ট পরিবর্তন করা হয়েছে
        
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-5.5", # মডেল নাম পরিবর্তন করে GPT-5.5 করা হয়েছে
            "messages": [{"role": "user", "content": content_blocks}],
            "temperature": 0.7
        }

        print(f"Sending request to FreeModel API... Model: {payload['model']}")
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        
        print(f"API Response Status Code: {response.status_code}")

        if response.status_code == 200:
            result_data = response.json()
            final_response_text = result_data['choices'][0]['message']['content']
            print("Successfully got response from GPT-5.5. Sending to Telegram...")
            send_full_output(chat_id, final_response_text)
            print("Message sent to Telegram successfully!")
        else:
            print(f"API Error Body: {response.text}")
            bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")

    except requests.exceptions.Timeout:
        print("Error: FreeModel API Request Timed Out.")
        bot.send_message(chat_id, "Error: The AI took too long to respond (Timeout).")
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")
        bot.send_message(chat_id, f"An error occurred:\n{str(e)}")

from flask import Flask
from threading import Thread

# Render-এর জন্য ডামি ওয়েবসাইট তৈরি
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is successfully running 24/7 on Render!"

def run_server():
    # Render নিজে থেকে একটি PORT দেবে, সেটি ব্যবহার করতে হবে
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    print("Starting Web Server for Render...")
    # ওয়েব সার্ভারটিকে আলাদাভাবে চালু করা
    Thread(target=run_server).start()
    
    print("Bot is successfully running... Waiting for messages.")
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=60)
        except Exception as e:
            print(f"Telegram Network Error, restarting in 3 seconds...: {e}")
            import time
            time.sleep(3)