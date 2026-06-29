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

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") 
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")   
CF_MODEL = "@cf/zai-org/glm-5.2" 
ARCHITECT_MODEL = "@cf/xai/grok-4.20-multi-agent-0309"

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

# --- মেমোরি ডেটাবেস ---
user_chat_history = {}
project_state = {} 

def process_text_file(file_content, filename):
    try: return f"\n--- File: {filename} ---\n{file_content.decode('utf-8')}\n"
    except: return f"\n[Error reading {filename}]\n"

def send_full_output(chat_id, text, is_partial=False):
    caption = "⚠️ লিমিট শেষ! বাকিটুকু পেতে 'continue' বা 'চালিয়ে যাও' লিখুন।" if is_partial else "Output is too long, sending as file."
    if len(text) <= 4000:
        bot.send_message(chat_id, text + ("\n\n" + caption if is_partial else ""))
    else:
        file_stream = io.BytesIO(text.encode('utf-8'))
        file_stream.name = "partial_response.txt" if is_partial else "response.txt"
        bot.send_document(chat_id, file_stream, caption=caption)

def call_grok_sync(messages):
    """Grok-কে ব্যাকগ্রাউন্ডে কল করার জন্য সিকিউর ফাংশন (NoneType Error ফিক্সড)"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{ARCHITECT_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json={"messages": messages}, timeout=200)
        data = response.json()
        if "result" in data and "response" in data["result"]: return str(data["result"]["response"])
        if "result" in data and "choices" in data["result"]: return str(data["result"]["choices"][0]["message"]["content"])
        return str(data)
    except Exception as e:
        return f"API_ERROR: {str(e)}"

@bot.message_handler(commands=['clear', 'reset'])
def clear_memory(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    if chat_id in user_chat_history: del user_chat_history[chat_id]
    if chat_id in project_state: del project_state[chat_id]
    bot.send_message(chat_id, "🧹 বটের মেমোরি সম্পূর্ণ ক্লিয়ার করা হয়েছে!")

@bot.message_handler(content_types=['document'])
def handle_project_upload(message):
    """GLM ফাইল রিসিভ করে ব্যাকগ্রাউন্ডে Grok-কে পড়তে দেবে"""
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    
    bot.send_message(chat_id, "📂 GLM: প্রজেক্ট ফাইল পেয়েছি! আমি এটি ব্যাকগ্রাউন্ডে Grok-এর মেমোরিতে সেভ করছি... Please wait.")
    
    if chat_id not in project_state:
        project_state[chat_id] = {"files": {}, "grok_history": []}
        
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        file_name = message.document.file_name.lower()
        
        if file_name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                for info in z.infolist():
                    if not info.is_dir() and os.path.splitext(info.filename)[1].lower() in TEXT_EXTENSIONS:
                        with z.open(info) as f:
                            project_state[chat_id]["files"][info.filename] = f.read().decode('utf-8', 'ignore')
        elif os.path.splitext(file_name)[1] in TEXT_EXTENSIONS:
            project_state[chat_id]["files"][file_name] = downloaded_file.decode('utf-8', 'ignore')
        
        # Grok-কে প্রজেক্ট মনে রাখতে বলা
        project_context = "\n".join([f"--- File: {k} ---\n{v}" for k, v in project_state[chat_id]["files"].items()])
        sys_msg = "You are Grok, a background memory module. Memorize the project files. You will act as a database for GLM. Do not write code."
        project_state[chat_id]["grok_history"] = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": f"Project Code:\n{project_context}\n\nAcknowledge that you have memorized this."}
        ]
        
        call_grok_sync(project_state[chat_id]["grok_history"]) # Silent call
        bot.send_message(chat_id, "✅ GLM: প্রজেক্ট রেডি! এখন আমাকে বলুন কী করতে হবে।")

    except Exception as e:
        bot.send_message(chat_id, f"Error processing file: {str(e)}")

@bot.message_handler(content_types=['text'])
def handle_text_task(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    prompt_text = message.text
    is_continuing = prompt_text.strip().lower() in ['continue', 'চালিয়ে যাও']
    
    # === A. Project Mode ===
    if chat_id in project_state and project_state[chat_id].get("files"):
        if not is_continuing:
            bot.send_message(chat_id, "🔍 GLM: আমি ব্যাকগ্রাউন্ডে Grok-এর থেকে প্রয়োজনীয় ফাইলগুলো চেয়ে নিচ্ছি...")
            
            # GLM-এর হয়ে Grok-কে ব্যাকগ্রাউন্ডে প্রশ্ন করা হচ্ছে
            grok_prompt = f"GLM asks: The user requested '{prompt_text}'. Based on the project memory, list exactly which file paths I need to edit. Do not write code, just give me the file names."
            project_state[chat_id]["grok_history"].append({"role": "user", "content": grok_prompt})
            
            grok_plan = call_grok_sync(project_state[chat_id]["grok_history"])
            if not grok_plan: grok_plan = "" # NoneType Error Fix
            
            project_state[chat_id]["grok_history"].append({"role": "assistant", "content": grok_plan})
            
            # Grok-এর কথা অনুযায়ী ফাইল বের করা
            target_files = {k: v for k, v in project_state[chat_id]["files"].items() if k in grok_plan or k.split('/')[-1] in grok_plan}
            
            if not target_files:
                bot.send_message(chat_id, "⚠️ GLM: প্রজেক্টে এডিট করার মতো কোনো ফাইল পাওয়া যায়নি বা ইনস্ট্রাকশনটি পরিষ্কার নয়।")
                return
                
            bot.send_message(chat_id, f"✅ GLM: আমি ফাইলগুলো পেয়ে গেছি! এডিট হচ্ছে: {list(target_files.keys())}\n\n✍️ কোড লেখা শুরু করছি...")
            
            # GLM-এর মেইন প্রম্পট তৈরি (সে এখন কাজ করবে)
            glm_sys = (
                "You are GLM 5.2, the front-end Coder. You interact directly with the user. "
                "You just consulted your background memory (Grok) and retrieved the necessary files. "
                "Fulfill the user's request. Output modified files in XML: <file name='filename.ext'>content</file>."
            )
            files_context = "\n".join([f"--- File: {k} ---\n{v}" for k, v in target_files.items()])
            glm_prompt = f"User Request: {prompt_text}\n\nFiles retrieved from memory:\n{files_context}\n\nWrite the updated code now."
            
            current_messages = [{"role": "system", "content": glm_sys}, {"role": "user", "content": glm_prompt}]
            project_state[chat_id]["current_glm_stream"] = current_messages
        else:
            bot.send_message(chat_id, "✍️ GLM কন্টিনিউ করছে...")
            current_messages = project_state[chat_id].get("current_glm_stream", [])
            current_messages.append({"role": "user", "content": "continue"})
            
    # === B. Normal Mode ===
    else:
        bot.send_message(chat_id, "Processing your request with GLM-5.2...")
        glm_sys = "You are GLM 5.2. If asked for files/ZIP, output in XML: <file name='x.ext'>code</file>. Do NOT use markdown outside tags."
        if chat_id not in user_chat_history: user_chat_history[chat_id] = [{"role": "system", "content": glm_sys}]
        
        user_chat_history[chat_id].append({"role": "user", "content": prompt_text})
        if len(user_chat_history[chat_id]) > 15: user_chat_history[chat_id] = [user_chat_history[chat_id][0]] + user_chat_history[chat_id][-14:]
        current_messages = user_chat_history[chat_id]

    # --- GLM Streaming Execution ---
    api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"messages": current_messages, "stream": True}

    final_response_text = ""
    is_cut_off = False

    try:
        response = requests.post(api_url, headers=headers, json=payload, stream=True, timeout=120)
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
            bot.send_message(chat_id, f"GLM API Error: {response.status_code}\n{response.text}")
            return
    except Exception:
        is_cut_off = True

    if not final_response_text.strip():
        bot.send_message(chat_id, "❌ GLM কোনো ডেটা জেনারেট করেনি।")
        return

    current_messages.append({"role": "assistant", "content": final_response_text})

    # --- File Parsing & Syncing ---
    file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_response_text, re.IGNORECASE)
    MD_TICKS = chr(96) * 3 
    looks_incomplete = is_cut_off or ("<file" in final_response_text and "</file>" not in final_response_text)

    if file_matches and not looks_incomplete and not is_continuing:
        user_wants_zip = 'zip' in prompt_text.lower()
        
        # প্রজেক্ট মোড হলে ব্যাকগ্রাউন্ডে Grok-কে আপডেট দেওয়া (Sync)
        if chat_id in project_state and project_state[chat_id].get("files"):
            sync_msg = "GLM says: I have modified these files. Update your memory:\n"
            for fn, fc in file_matches:
                fc = fc.strip()
                if fc.startswith(MD_TICKS): fc = fc.split('\n', 1)[-1]
                if fc.endswith(MD_TICKS): fc = fc.rsplit('\n', 1)[0]
                project_state[chat_id]["files"][fn] = fc.strip()
                sync_msg += f"--- {fn} ---\n{fc.strip()[:200]}...\n"
            project_state[chat_id]["grok_history"].append({"role": "user", "content": sync_msg})
            call_grok_sync(project_state[chat_id]["grok_history"][-1:]) # Silent sync
        
        # Output to user
        if len(file_matches) > 1 or user_wants_zip:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for fn, fc in file_matches:
                    fc = fc.strip()
                    if fc.startswith(MD_TICKS): fc = fc.split('\n', 1)[-1]
                    if fc.endswith(MD_TICKS): fc = fc.rsplit('\n', 1)[0]
                    zip_file.writestr(fn, fc.strip())
            zip_buffer.seek(0)
            zip_buffer.name = "project_files.zip"
            bot.send_document(chat_id, zip_buffer, caption="Here is your ZIP file.")
        else:
            fn = file_matches[0][0]
            fc = file_matches[0][1].strip()
            if fc.startswith(MD_TICKS): fc = fc.split('\n', 1)[-1]
            if fc.endswith(MD_TICKS): fc = fc.rsplit('\n', 1)[0]
            file_buffer = io.BytesIO(fc.strip().encode('utf-8'))
            file_buffer.name = fn
            bot.send_document(chat_id, file_buffer, caption=f"Here is your {fn} file.")
    else:
        send_full_output(chat_id, final_response_text, is_partial=looks_incomplete)

app = Flask(__name__)
@app.route('/')
def home(): return "GLM as Front-End, Grok as Memory - Online!"

if __name__ == "__main__":
    Thread(target=lambda: bot.infinity_polling(timeout=60, long_polling_timeout=60), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
