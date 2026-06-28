import os
import telebot
import zipfile
import io
import requests
import time
from flask import Flask
from threading import Thread

# --- কনফিগারেশন (Environment Variables) ---
# এই মানগুলো এখন Render-এর ড্যাশবোর্ড থেকে অটোমেটিক নিয়ে নেবে। 
# গিটহাবে কোনো টোকেন দেখা যাবে না, তাই হ্যাক হওয়ারও ভয় নেই!

BOT_TOKEN = os.environ.get("BOT_TOKEN") 
ALLOWED_USER_ID = 5062314716 # এখানে শুধু আপনার টেলিগ্রাম ইউজার আইডি বসিয়ে দিন

# --- Cloudflare AI কনফিগারেশন ---
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   
CF_MODEL = "@cf/zai-org/glm-5.2" # Cloudflare-এ GLM 5.2 মডেল

# বট ইনিশিয়ালাইজেশন
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

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID:
        return

    chat_id = message.chat.id
    prompt_text = message.text or message.caption or "Analyze the attached file(s)."
    
    bot.send_message(chat_id, "Processing your request with Cloudflare AI (GLM-5.2)... Please wait.")

    try:
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

        # --- Cloudflare API Call ---
        api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
        
        headers = {
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messages": [
                {"role": "system", "content": "You are a helpful AI coding assistant."},
                {"role": "user", "content": prompt_text}
            ]
        }

        response = requests.post(api_url, headers=headers, json=payload, timeout=60)

        if response.status_code == 200:
            result_data = response.json()
            if result_data.get("success"):
                final_response_text = result_data['result']['response']
                send_full_output(chat_id, final_response_text)
            else:
                bot.send_message(chat_id, f"Cloudflare AI Error: {result_data.get('errors')}")
        else:
            bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")

    except requests.exceptions.Timeout:
        bot.send_message(chat_id, "Error: Cloudflare AI took too long to respond (Timeout).")
    except Exception as e:
        bot.send_message(chat_id, f"An error occurred: {str(e)}")

# --- Render 24/7 Web Server ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is securely running 24/7 with Cloudflare AI!"

def run_bot():
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=60)
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    # বটকে ব্যাকগ্রাউন্ডে পাঠানো হলো
    Thread(target=run_bot, daemon=True).start()
    
    # ওয়েব সার্ভার মেইন ফোকাসে রাখা হলো, যাতে Render সাথে সাথে পোর্ট খুঁজে পায়
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
