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
ARCHITECT_MODEL = "@cf/xai/grok-4.20-multi-agent-0309" # শুধু বড় প্রজেক্টের জন্য

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

# --- মেমোরি ডেটাবেস ---
user_chat_history = {} # সাধারণ চ্যাটের জন্য
project_state = {}     # বড় প্রজেক্ট/জিপ ফাইলের জন্য

def process_text_file(file_content, filename):
    try:
        text = file_content.decode('utf-8')
        return f"\n--- File: {filename} ---\n{text}\n"
    except:
        return f"\n[Error reading {filename} as text]\n"

def send_full_output(chat_id, text, is_partial=False):
    caption = "⚠️ লিমিট শেষ! বাকিটুকু পেতে 'continue' বা 'চালিয়ে যাও' লিখুন।" if is_partial else "Output is too long, sending as file."
    
    if len(text) <= 4000:
        bot.send_message(chat_id, text + ("\n\n" + caption if is_partial else ""))
    else:
        file_stream = io.BytesIO(text.encode('utf-8'))
        file_stream.name = "partial_response.txt" if is_partial else "response.txt"
        bot.send_document(chat_id, file_stream, caption=caption)

def call_grok_sync(messages):
    """Grok-কে কল করার জন্য ফাংশন"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{ARCHITECT_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json={"messages": messages}, timeout=200)
        data = response.json()
        if "result" in data and "response" in data["result"]: return data["result"]["response"]
        if "result" in data and "choices" in data["result"]: return data["result"]["choices"][0]["message"]["content"]
        return str(data)
    except Exception as e:
        return f"Error: {str(e)}"

# --- মেমোরি ক্লিয়ার করার কমান্ড ---
@bot.message_handler(commands=['clear', 'reset'])
def clear_memory(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    if chat_id in user_chat_history: del user_chat_history[chat_id]
    if chat_id in project_state: del project_state[chat_id]
    bot.send_message(chat_id, "🧹 বটের মেমোরি ক্লিয়ার করা হয়েছে! (সাধারণ চ্যাট এবং প্রজেক্ট মেমোরি দুটোই)")

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id

    # -------------------------------------------------------------
    # ধাপ ১: ফাইল বা জিপ আপলোড হলে (Project Mode Active হবে)
    # -------------------------------------------------------------
    if message.document:
        bot.send_message(chat_id, "📂 প্রজেক্ট ফাইল প্রসেস করে Grok-এর মেমোরিতে দেওয়া হচ্ছে... Please wait.")
        if chat_id not in project_state:
            project_state[chat_id] = {"files": {}, "grok_history": []}
            
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in TEXT_EXTENSIONS:
                project_state[chat_id]["files"][file_name] = downloaded_file.decode('utf-8', 'ignore')
            elif file_ext == '.zip':
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for info in z.infolist():
                        if not info.is_dir() and os.path.splitext(info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(info) as f:
                                project_state[chat_id]["files"][info.filename] = f.read().decode('utf-8', 'ignore')
            
            # Grok-কে প্রজেক্ট মনে রাখতে বলা
            project_context = "\n".join([f"--- File: {k} ---\n{v}" for k, v in project_state[chat_id]["files"].items()])
            sys_msg = "You are Grok, the Chief Architect. Memorize the project files. You don't write code, you analyze tasks and tell the Coder which files to edit and how."
            project_state[chat_id]["grok_history"] = [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": f"Project Code:\n{project_context}\n\nAcknowledge that you have memorized this."}
            ]
            
            ack = call_grok_sync(project_state[chat_id]["grok_history"])
            project_state[chat_id]["grok_history"].append({"role": "assistant", "content": ack})
            bot.send_message(chat_id, "✅ Grok প্রজেক্ট বুঝে মেমোরিতে সেভ করেছে! এখন আপনার ইনস্ট্রাকশন দিন। (Dual-Agent Mode Active)")
            return # ফাইল প্রসেসিং শেষ
            
        except Exception as e:
            bot.send_message(chat_id, f"Error processing file: {str(e)}")
            return

    # -------------------------------------------------------------
    # ধাপ ২: টেক্সট মেসেজ প্রসেসিং
    # -------------------------------------------------------------
    prompt_text = message.text or "Analyze."
    is_continuing = prompt_text.strip().lower() in ['continue', 'চালিয়ে যাও']
    
    # === A. Project Mode (Dual-Agent) ===
    if chat_id in project_state and project_state[chat_id].get("files"):
        if not is_continuing:
            bot.send_message(chat_id, "🧠 Grok অ্যানালাইসিস করে প্ল্যান তৈরি করছে...")
            # 1. Grok Plan
            project_state[chat_id]["grok_history"].append({"role": "user", "content": f"Task: {prompt_text}. Which files need changing and how?"})
            grok_plan = call_grok_sync(project_state[chat_id]["grok_history"])
            project_state[chat_id]["grok_history"].append({"role": "assistant", "content": grok_plan})
            
            # Find targeted files
            target_files = {k: v for k, v in project_state[chat_id]["files"].items() if k in grok_plan or k.split('/')[-1] in grok_plan}
            
            if not target_files:
                bot.send_message(chat_id, f"Grok-এর প্ল্যান:\n{grok_plan}\n\n⚠️ কোনো ফাইল এডিট করার প্রয়োজন নেই বা Grok ফাইলের নাম উল্লেখ করেনি।")
                return
                
            bot.send_message(chat_id, f"📋 প্ল্যান রেডি! এডিট হবে: {list(target_files.keys())}\n\n✍️ GLM 5.2 এখন কোড লিখছে...")
            
            # 2. Setup GLM
            glm_sys = "You are GLM 5.2, an expert Coder. Follow the Architect's plan. Output modified files in XML: <file name='exact_filename.ext'>content</file>."
            files_context = "\n".join([f"--- File: {k} ---\n{v}" for k, v in target_files.items()])
            glm_prompt = f"Plan: {grok_plan}\n\nFiles:\n{files_context}\n\nTask: {prompt_text}"
            
            current_messages = [{"role": "system", "content": glm_sys}, {"role": "user", "content": glm_prompt}]
            project_state[chat_id]["current_glm_stream"] = current_messages
        else:
            bot.send_message(chat_id, "✍️ GLM 5.2 কন্টিনিউ করছে...")
            current_messages = project_state[chat_id].get("current_glm_stream", [])
            current_messages.append({"role": "user", "content": "continue"})
            
    # === B. Normal Mode (আপনার দেওয়া আগের লজিক) ===
    else:
        bot.send_message(chat_id, "Processing your request with GLM-5.2... Please wait.")
        system_instruction = (
            "You are an expert AI coding assistant. "
            "If the user asks for files or a ZIP, you MUST output the files using this exact XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete file content here]\n</file>\n'
            "Do NOT use markdown code blocks. "
            "CRITICAL: If the user types 'continue', you MUST seamlessly continue exactly from where your last response stopped."
        )

        if chat_id not in user_chat_history:
            user_chat_history[chat_id] = [{"role": "system", "content": system_instruction}]
        
        user_chat_history[chat_id].append({"role": "user", "content": prompt_text})
        if len(user_chat_history[chat_id]) > 15:
            user_chat_history[chat_id] = [user_chat_history[chat_id][0]] + user_chat_history[chat_id][-14:]
            
        current_messages = user_chat_history[chat_id]

    # -------------------------------------------------------------
    # ধাপ ৩: GLM Streaming (উভয় মোডের জন্য একই লজিক)
    # -------------------------------------------------------------
    api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"messages": current_messages, "stream": True}

    final_response_text = ""
    is_cut_off = False

    try:
        response = requests.post(api_url, headers=headers, json=payload, stream=True, timeout=120)
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str == "[DONE]": break
                        try:
                            chunk = json.loads(data_str)
                            if "response" in chunk: final_response_text += chunk["response"]
                            elif "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta: final_response_text += delta["content"]
                        except: pass
        else:
            bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")
            return
    except Exception as e:
        is_cut_off = True

    if not final_response_text.strip():
        bot.send_message(chat_id, "❌ কোনো ডেটা জেনারেট হয়নি। আবার চেষ্টা করুন।")
        return

    # হিস্ট্রি আপডেট
    current_messages.append({"role": "assistant", "content": final_response_text})

    # -------------------------------------------------------------
    # ধাপ ৪: ফাইল পার্সিং এবং আউটপুট
    # -------------------------------------------------------------
    file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_response_text, re.IGNORECASE)
    MD_TICKS = chr(96) * 3 
    looks_incomplete = is_cut_off or ("<file" in final_response_text and "</file>" not in final_response_text)

    if file_matches and not looks_incomplete and not is_continuing:
        user_wants_zip = 'zip' in prompt_text.lower()
        
        # প্রজেক্ট মোড হলে মেমোরি আপডেট (Sync) করতে হবে
        if chat_id in project_state and project_state[chat_id].get("files"):
            sync_msg = "SYSTEM UPDATE: Files modified:\n"
            for fn, fc in file_matches:
                fc = fc.strip()
                if fc.startswith(MD_TICKS): fc = fc.split('\n', 1)[-1]
                if fc.endswith(MD_TICKS): fc = fc.rsplit('\n', 1)[0]
                project_state[chat_id]["files"][fn] = fc.strip()
                sync_msg += f"--- {fn} ---\n{fc.strip()[:200]}...\n"
            project_state[chat_id]["grok_history"].append({"role": "user", "content": sync_msg})
            call_grok_sync(project_state[chat_id]["grok_history"][-1:]) # দ্রুত আপডেট
            bot.send_message(chat_id, "🔄 প্রজেক্ট ফাইল ও মেমোরি আপডেট করা হয়েছে।")
        
        # ফাইল সেন্ড করা
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
            bot.send_document(chat_id, zip_buffer, caption="Here is your ZIP file.")
        else:
            filename = file_matches[0][0]
            content = file_matches[0][1].strip()
            if content.startswith(MD_TICKS): content = content.split('\n', 1)[-1]
            if content.endswith(MD_TICKS): content = content.rsplit('\n', 1)[0]
                
            file_buffer = io.BytesIO(content.strip().encode('utf-8'))
            file_buffer.name = filename
            bot.send_document(chat_id, file_buffer, caption=f"Here is your {filename} file.")
    else:
        send_full_output(chat_id, final_response_text, is_partial=looks_incomplete)

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Hybrid Mode (Normal Chat + Dual-Agent Project)!"

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
