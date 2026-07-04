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

# --- কনফিগারেশন ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
ALLOWED_USER_ID = 5062314716 

# --- Cloudflare AI কনফিগারেশন ---
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   
CF_MODEL = "@cf/zai-org/glm-5.2" 

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

# --- পার্মানেন্ট মেমোরি ডেটাবেস ---
DB_FILE = "chat_database.json"

def load_memory():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_memory(data):
    with open(DB_FILE, 'w') as f: json.dump(data, f)

user_chat_history = load_memory()

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

@bot.message_handler(commands=['clear', 'reset'])
def clear_memory(message):
    # যে কেউ তার নিজের চ্যাট ক্লিয়ার করতে পারবে
    chat_id = str(message.chat.id)
    if chat_id in user_chat_history:
        del user_chat_history[chat_id]
        save_memory(user_chat_history)
    bot.send_message(chat_id, "🧹 আপনার চ্যাট এবং মেমোরি ক্লিয়ার করা হয়েছে! নতুন প্রজেক্ট শুরু করতে পারেন।")

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    chat_id = str(message.chat.id)
    
    # ক্যাপশন বা টেক্সট নেওয়া
    prompt_text = message.text or message.caption or ""

    try:
        # --- ১. ফাইল প্রসেসিং ---
        file_context = ""
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in TEXT_EXTENSIONS:
                file_context += process_text_file(downloaded_file, file_name)
            elif file_ext == '.zip':
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir() and os.path.splitext(zip_info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(zip_info) as extracted_file:
                                file_context += process_text_file(extracted_file.read(), zip_info.filename)
                                
        if file_context:
            prompt_text += f"\n\n[USER UPLOADED FILES]:\n{file_context}"
            if not message.text and not message.caption:
                prompt_text += "\n\nAnalyze the uploaded files. If they contain a prompt/instruction, execute it immediately. If it's just raw data/code, acknowledge that you have saved it to memory and wait for my next command."

        if not prompt_text.strip():
            return

        bot.send_message(chat_id, "Processing your request... (Large tasks will auto-continue in background) ⏳")

        # --- ২. হিস্ট্রি ম্যানেজমেন্ট ---
        system_instruction = (
            "You are an expert AI coding assistant. "
            "You have a perfect memory of this chat. Always refer to the context provided. "
            "If the user asks for files or a ZIP, you MUST output the files using this exact XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete file content here]\n</file>\n'
            "Do NOT use markdown code blocks outside the XML tags. "
        )

        if chat_id not in user_chat_history:
            user_chat_history[chat_id] = [{"role": "system", "content": system_instruction}]
        
        user_chat_history[chat_id].append({"role": "user", "content": prompt_text})
        
        if len(user_chat_history[chat_id]) > 20: # মেমোরি উইন্ডো বড় করা হয়েছে
            user_chat_history[chat_id] = [user_chat_history[chat_id][0]] + user_chat_history[chat_id][-19:]

        # --- ৩. Auto-Continue Loop (Background) ---
        api_url = "https://api.cloudflare.com/client/v4/accounts/" + str(CF_ACCOUNT_ID) + "/ai/run/" + str(CF_MODEL)
        headers = {"Authorization": "Bearer " + str(CF_API_TOKEN), "Content-Type": "application/json"}
        
        final_full_response = ""
        current_payload_messages = user_chat_history[chat_id].copy()
        
        loop_count = 0
        MAX_AUTO_CONTINUE = 4 # সর্বোচ্চ ৪ বার ব্যাকগ্রাউন্ডে কন্টিনিউ করবে

        while loop_count <= MAX_AUTO_CONTINUE:
            payload = {"messages": current_payload_messages, "stream": True}
            chunk_text = ""
            
            response = requests.post(api_url, headers=headers, json=payload, stream=True, timeout=150)
            
            if response.status_code == 200:
                for line in response.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith("data: "):
                            data_str = decoded_line[6:]
                            if data_str == "[DONE]": break
                            try:
                                chunk = json.loads(data_str)
                                if "response" in chunk: chunk_text += chunk["response"]
                                elif "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    if "content" in delta: chunk_text += delta["content"]
                            except: pass
            else:
                bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")
                user_chat_history[chat_id].pop()
                return

            final_full_response += chunk_text
            
            # চেক করা হচ্ছে কোনো ফাইল ট্যাগ অসম্পূর্ণ আছে কি না
            unclosed_xml = final_full_response.count('<file name=') > final_full_response.count('</file>')
            
            if unclosed_xml and loop_count < MAX_AUTO_CONTINUE:
                loop_count += 1
                current_payload_messages.append({"role": "assistant", "content": chunk_text})
                current_payload_messages.append({"role": "user", "content": "continue exactly from where you stopped. Do not repeat previous text."})
            else:
                break # কাজ শেষ

        if not final_full_response.strip():
            bot.send_message(chat_id, "❌ কোনো ডেটা জেনারেট হয়নি। আবার চেষ্টা করুন।")
            user_chat_history[chat_id].pop()
            return

        # ফাইনাল রেসপন্স মেমোরিতে সেভ করা
        user_chat_history[chat_id].append({"role": "assistant", "content": final_full_response})
        save_memory(user_chat_history)

        # --- ৪. ফাইল পার্সিং এবং আউটপুট ---
        file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_full_response, re.IGNORECASE)
        MD_TICKS = chr(96) * 3 
        
        if file_matches:
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
                bot.send_document(chat_id, zip_buffer, caption="✅ Background task completed. Here is your ZIP file.")
                
            else:
                filename = file_matches[0][0]
                content = file_matches[0][1].strip()
                if content.startswith(MD_TICKS): content = content.split('\n', 1)[-1]
                if content.endswith(MD_TICKS): content = content.rsplit('\n', 1)[0]
                    
                file_buffer = io.BytesIO(content.strip().encode('utf-8'))
                file_buffer.name = filename
                bot.send_document(chat_id, file_buffer, caption=f"✅ Background task completed. Here is your {filename} file.")
        else:
            send_full_output(chat_id, final_full_response)
            
    except Exception as e:
        bot.send_message(chat_id, f"An error occurred: {str(e)}")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is securely running 24/7 with Memory & Auto-Continue!"

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
