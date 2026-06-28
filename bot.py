import os
import telebot
import zipfile
import io
import requests
import time
import json
import re # ট্যাগ খুঁজে বের করার জন্য নতুন লাইব্রেরি
from flask import Flask
from threading import Thread

# --- কনফিগারেশন ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
ALLOWED_USER_ID = 5062314716 

# --- Cloudflare AI কনফিগারেশন ---
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   
CF_MODEL = "@cf/zai-org/glm-5.2" 

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
        file_stream.name = "response.txt"
        bot.send_document(chat_id, file_stream, caption="Output is too long, sending as file.")

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID:
        return

    chat_id = message.chat.id
    prompt_text = message.text or message.caption or "Analyze the attached file(s)."
    
    bot.send_message(chat_id, "Processing your request with Cloudflare AI... Please wait.")

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

        # --- এআই-কে ফাইল বানানোর স্পেশাল ইন্সট্রাকশন দেওয়া হলো ---
        system_instruction = (
            "You are an expert AI coding assistant. "
            "CRITICAL INSTRUCTION: If the user asks for a specific file format (e.g., .dart, .html, .py), "
            "or asks for multiple files, or a ZIP file, you MUST output the files using this exact XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete file content here]\n</file>\n'
            "You can generate multiple <file> blocks if needed. Do NOT use markdown code blocks (```) inside or outside the <file> tags."
        )

        api_url = f"[https://api.cloudflare.com/client/v4/accounts/](https://api.cloudflare.com/client/v4/accounts/){CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
        headers = {
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt_text}
            ]
        }

        response = requests.post(api_url, headers=headers, json=payload, timeout=300)

        if response.status_code == 200:
            result_data = response.json()
            
            try:
                final_response_text = ""
                
                # Cloudflare/OpenAI/Claude রেসপন্স এক্সট্র্যাক্ট করা
                if "result" in result_data and isinstance(result_data["result"], dict) and "choices" in result_data["result"]:
                    final_response_text = result_data['result']['choices'][0]['message']['content']
                elif "result" in result_data and isinstance(result_data["result"], dict) and "response" in result_data["result"]:
                    final_response_text = result_data['result']['response']
                elif "choices" in result_data:
                    final_response_text = result_data['choices'][0]['message']['content']
                elif "content" in result_data:
                    final_response_text = result_data['content'][0]['text']
                else:
                    final_response_text = f"Unknown API Format:\n{json.dumps(result_data, indent=2)[:1000]}"
                
                # --- ফাইল এবং ZIP পার্সিং লজিক ---
                file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)</file>', final_response_text, re.IGNORECASE)
                
                if file_matches:
                    user_wants_zip = 'zip' in prompt_text.lower()
                    
                    if len(file_matches) > 1 or user_wants_zip:
                        # একাধিক ফাইল বা জিপ চাইলে ZIP ফাইল তৈরি করবে
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                            for filename, content in file_matches:
                                content = content.strip()
                                if content.startswith("
http://googleusercontent.com/immersive_entry_chip/0
http://googleusercontent.com/immersive_entry_chip/1
http://googleusercontent.com/immersive_entry_chip/2
http://googleusercontent.com/immersive_entry_chip/3

**কোডটি যেভাবে টেস্ট করবেন:**
গিটহাবে আপডেট হওয়ার পর বটকে ২ ভাবে প্রম্পট দিয়ে দেখতে পারেন:
১. `"Create a modern login screen in HTML. I need it in a .html file."` (বট সরাসরি একটি `.html` ফাইল পাঠাবে)।
২. `"Create an e-commerce site with HTML, CSS, and JS. Zip them and give me."` (বট ফাইলগুলো তৈরি করে একটি `.zip` ফোল্ডার হিসেবে আপনাকে পাঠাবে)।
