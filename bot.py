import os
import telebot
import zipfile
import io
import requests
import time
import json
from flask import Flask
from threading import Thread

# কনফিগারেশন
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   

bot = telebot.TeleBot(BOT_TOKEN)

# গ্লোবাল স্টেট ম্যানেজমেন্ট
project_state = {} # {chat_id: {"files": {...}, "grok_memory": "...", "glm_state": "..."}}

def call_ai(model, messages):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json={"messages": messages}, timeout=200)
    data = response.json()
    return data.get("result", {}).get("response", str(data))

@bot.message_handler(content_types=['document'])
def handle_zip(message):
    chat_id = message.chat.id
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    project_state[chat_id] = {"files": {}, "memory": ""}
    
    with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
        for info in z.infolist():
            if not info.is_dir() and info.filename.endswith(('.dart', '.html', '.js', '.css', '.py')):
                with z.open(info) as f:
                    project_state[chat_id]["files"][info.filename] = f.read().decode('utf-8', 'ignore')
    
    # Grok কে দিয়ে ইনিশিয়াল রিডিং
    context = "\n".join([f"File: {k}\n{v}" for k, v in project_state[chat_id]["files"].items()])
    grok_init = [{"role": "user", "content": f"You are the master Controller. Remember this project: {context}"}]
    project_state[chat_id]["memory"] = call_ai("@cf/xai/grok-4.20-multi-agent-0309", grok_init)
    
    bot.send_message(chat_id, "✅ প্রজেক্ট Grok-এর মেমোরিতে লোড হয়েছে। এখন কাজ শুরু করুন।")

@bot.message_handler(content_types=['text'])
def handle_task(message):
    chat_id = message.chat.id
    if chat_id not in project_state:
        bot.send_message(chat_id, "প্রথমে জিপ ফাইল পাঠান।")
        return

    # ১. Grok কে বলা পরিবর্তন শনাক্ত করতে
    grok_msg = [{"role": "user", "content": f"Memory: {project_state[chat_id]['memory']}. Task: {message.text}. Which files change and how?"}]
    instructions = call_ai("@cf/xai/grok-4.20-multi-agent-0309", grok_msg)
    
    # ২. GLM কে বলা কোড করতে
    glm_msg = [{"role": "user", "content": f"Follow these instructions: {instructions}. Project: {project_state[chat_id]['files']}. Output only the updated file content in <file name='x'>content</file> tags."}]
    updated_files = call_ai("@cf/zai-org/glm-5.2", glm_msg)
    
    # ৩. স্টেট আপডেট (Sync)
    updates = re.findall(r'<file name="([^"]+)">([\s\S]*?)</file>', updated_files)
    for fname, fcontent in updates:
        project_state[chat_id]["files"][fname] = fcontent
        # Grok এর মেমোরিও আপডেট করা
        project_state[chat_id]["memory"] += f"\nUpdated {fname} to: {fcontent[:200]}"
    
    bot.send_message(chat_id, f"✅ কাজ সম্পন্ন। আপডেট হয়েছে: {[u[0] for u in updates]}")

app = Flask(__name__)
@app.route('/')
def home(): return "Sync Agent Online!"

if __name__ == "__main__":
    Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
