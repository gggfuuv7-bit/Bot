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

# --- এআই মডেল ---
GLM_WORKER = "@cf/zai-org/glm-5.2"
GROK_ARCHITECT = "@cf/xai/grok-4.20-multi-agent-0309"

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

# --- প্রোজেক্টের গ্লোবাল স্টেট এবং মেমোরি ---
# এর ভেতরে ফাইল এবং Grok-এর লং-টার্ম মেমোরি সেভ থাকবে
project_state = {}

def call_ai_sync(model, messages):
    """Grok-এর মতো নন-স্ট্রিমিং কাজের জন্য সাধারণ এপিআই কল"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json={"messages": messages}, timeout=200)
        if response.status_code == 200:
            data = response.json()
            if "result" in data and "response" in data["result"]: return data["result"]["response"]
            if "result" in data and "choices" in data["result"]: return data["result"]["choices"][0]["message"]["content"]
        return f"Error: {response.status_code} - {response.text}"
    except Exception as e:
        return f"Exception: {str(e)}"

def send_full_output(chat_id, text, is_partial=False):
    caption = "⚠️ লিমিট শেষ! বাকিটুকু পেতে 'continue' বা 'চালিয়ে যাও' লিখুন।" if is_partial else "Output is too long, sending as file."
    
    if len(text) <= 4000:
        bot.send_message(chat_id, text + ("\n\n" + caption if is_partial else ""))
    else:
        file_stream = io.BytesIO(text.encode('utf-8'))
        file_stream.name = "partial_response.txt" if is_partial else "response.txt"
        bot.send_document(chat_id, file_stream, caption=caption)

@bot.message_handler(commands=['clear', 'reset'])
def clear_memory(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    if chat_id in project_state:
        del project_state[chat_id]
    bot.send_message(chat_id, "🧹 প্রজেক্ট এবং বটের মেমোরি সম্পূর্ণ ক্লিয়ার করা হয়েছে! নতুন প্রজেক্ট শুরু করতে পারেন।")

@bot.message_handler(content_types=['document'])
def handle_project_upload(message):
    """ফাইল বা জিপ আপলোড হলে সেটি আনজিপ করে Grok-কে পড়তে দেওয়া হবে"""
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    
    bot.send_message(chat_id, "📂 ফাইল রিসিভ করেছি। আনজিপ করে Grok-এর মেমোরিতে সেট করা হচ্ছে... Please wait.")
    
    if chat_id not in project_state:
        project_state[chat_id] = {"files": {}, "grok_history": []}
        
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        file_name = message.document.file_name.lower()
        file_ext = os.path.splitext(file_name)[1]

        added_files = []
        if file_ext in TEXT_EXTENSIONS:
            content = downloaded_file.decode('utf-8', 'ignore')
            project_state[chat_id]["files"][file_name] = content
            added_files.append(file_name)
            
        elif file_ext == '.zip':
            with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                for info in z.infolist():
                    if not info.is_dir() and os.path.splitext(info.filename)[1].lower() in TEXT_EXTENSIONS:
                        with z.open(info) as f:
                            content = f.read().decode('utf-8', 'ignore')
                            project_state[chat_id]["files"][info.filename] = content
                            added_files.append(info.filename)
        
        # Grok-এর মেমোরিতে পুরো প্রজেক্ট ইনজেক্ট করা
        project_context = "\n".join([f"--- File: {k} ---\n{v}" for k, v in project_state[chat_id]["files"].items()])
        sys_msg = "You are Grok, the Chief Architect. You hold the complete structure and code of this project in your memory. You must analyze user tasks and dictate exactly which files need to change and how."
        
        project_state[chat_id]["grok_history"] = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": f"Here is the full project code. Memorize it:\n{project_context}\n\nAcknowledge that you have memorized the project."}
        ]
        
        ack = call_ai_sync(GROK_ARCHITECT, project_state[chat_id]["grok_history"])
        project_state[chat_id]["grok_history"].append({"role": "assistant", "content": ack})
        
        bot.send_message(chat_id, f"✅ Grok সফলভাবে {len(added_files)} টি ফাইল পড়েছে এবং প্রজেক্ট মেমোরিতে সেভ করেছে! এখন তাকে ইনস্ট্রাকশন দিন।")

    except Exception as e:
        bot.send_message(chat_id, f"ফাইল প্রসেস করতে এরর হয়েছে: {str(e)}")

@bot.message_handler(content_types=['text'])
def handle_text_task(message):
    """ইউজারের টেক্সট প্রম্পট প্রসেস করা"""
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    task = message.text

    if chat_id not in project_state or not project_state[chat_id]["files"]:
        bot.send_message(chat_id, "⚠️ আপনার মেমোরিতে কোনো প্রজেক্ট নেই। অনুগ্রহ করে প্রথমে একটি প্রজেক্টের .zip বা ফাইল পাঠান।")
        return

    is_continuing = task.strip().lower() in ['continue', 'চালিয়ে যাও']

    if not is_continuing:
        # -------------------------------------------------------------
        # ধাপ ১: Grok (Architect) এর কাছে প্ল্যান চাওয়া
        # -------------------------------------------------------------
        bot.send_message(chat_id, "🧠 Grok প্রজেক্ট অ্যানালাইসিস করে প্ল্যান তৈরি করছে...")
        
        grok_prompt = f"The user wants to do this: '{task}'. Based on the project in your memory, list EXACTLY which files need to be modified and write a detailed instruction for the Coder (GLM) on what to change."
        project_state[chat_id]["grok_history"].append({"role": "user", "content": grok_prompt})
        
        grok_plan = call_ai_sync(GROK_ARCHITECT, project_state[chat_id]["grok_history"])
        project_state[chat_id]["grok_history"].append({"role": "assistant", "content": grok_plan})
        
        # কোন কোন ফাইলের নাম Grok বলেছে, তা ম্যাপ করা
        target_files_content = {}
        for fname, fcontent in project_state[chat_id]["files"].items():
            if fname in grok_plan or fname.split('/')[-1] in grok_plan:
                target_files_content[fname] = fcontent
                
        if not target_files_content:
            bot.send_message(chat_id, f"Grok-এর প্ল্যান:\n{grok_plan}\n\n⚠️ Grok কোনো নির্দিষ্ট ফাইলের নাম উল্লেখ করেনি। GLM-কে কল করা বাতিল করা হলো।")
            return
            
        bot.send_message(chat_id, f"📋 **Grok-এর প্ল্যান প্রস্তুত!**\nএডিট হবে: {list(target_files_content.keys())}\n\n✍️ GLM 5.2 এখন কোড জেনারেট করছে...")

        # -------------------------------------------------------------
        # ধাপ ২: GLM (Worker) এর কাছে কাজ হস্তান্তর
        # -------------------------------------------------------------
        glm_sys = (
            "You are GLM 5.2, an expert Coder. You will receive an Architect's plan and only the specific files needed for the task. "
            "Write the updated code. You MUST output the modified files using this XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete updated file content here]\n</file>\n'
            "Do NOT use markdown code blocks outside the tags."
        )
        
        files_to_edit_str = "\n".join([f"--- File: {k} ---\n{v}" for k, v in target_files_content.items()])
        glm_prompt = f"Architect's Plan & Instructions: {grok_plan}\n\nTarget Files Context:\n{files_to_edit_str}\n\nImplement the changes perfectly based on the plan."
        
        # GLM-এর জন্য টেম্পরারি হিস্ট্রি (যাতে এর কনটেক্সট লিমিট বেঁচে যায়)
        project_state[chat_id]["glm_current_task"] = [
            {"role": "system", "content": glm_sys},
            {"role": "user", "content": glm_prompt}
        ]
        
    else:
        bot.send_message(chat_id, "✍️ GLM 5.2 আগের জায়গা থেকে কন্টিনিউ করছে...")
        project_state[chat_id]["glm_current_task"].append({"role": "user", "content": "continue"})

    # -------------------------------------------------------------
    # ধাপ ৩: GLM-এর স্ট্রিমিং রেসপন্স প্রসেসিং
    # -------------------------------------------------------------
    api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{GLM_WORKER}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"messages": project_state[chat_id]["glm_current_task"], "stream": True}

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
            bot.send_message(chat_id, f"GLM API Error: {response.status_code}\n{response.text}")
            return
    except Exception as e:
        is_cut_off = True

    if not final_response_text.strip():
        bot.send_message(chat_id, "❌ GLM কোনো ডেটা জেনারেট করেনি।")
        return

    project_state[chat_id]["glm_current_task"].append({"role": "assistant", "content": final_response_text})

    # -------------------------------------------------------------
    # ধাপ ৪: ফাইল পার্সিং এবং Grok ও গ্লোবাল স্টেট সিঙ্ক (Sync)
    # -------------------------------------------------------------
    file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_response_text, re.IGNORECASE)
    MD_TICKS = chr(96) * 3 
    looks_incomplete = is_cut_off or ("<file" in final_response_text and "</file>" not in final_response_text)

    if file_matches and not looks_incomplete and not is_continuing:
        updated_file_names = []
        
        zb = io.BytesIO()
        with zipfile.ZipFile(zb, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for filename, content in file_matches:
                content = content.strip()
                if content.startswith(MD_TICKS): content = content.split('\n', 1)[-1]
                if content.endswith(MD_TICKS): content = content.rsplit('\n', 1)[0]
                content = content.strip()
                
                # ১. গ্লোবাল প্রজেক্ট স্টেট আপডেট করা
                project_state[chat_id]["files"][filename] = content
                updated_file_names.append(filename)
                
                zip_file.writestr(filename, content)
        
        # ২. Grok-এর মেমোরি আপডেট করা (Sync)
        sync_msg = f"SYSTEM UPDATE: The Coder (GLM) has successfully updated the following files based on your plan:\n"
        for fn in updated_file_names:
            sync_msg += f"--- {fn} ---\n{project_state[chat_id]['files'][fn]}\n"
        sync_msg += "Acknowledge this update and overwrite your memory with this new code."
        
        project_state[chat_id]["grok_history"].append({"role": "user", "content": sync_msg})
        sync_ack = call_ai_sync(GROK_ARCHITECT, project_state[chat_id]["grok_history"][-1:]) # দ্রুত একনলেজমেন্ট
        project_state[chat_id]["grok_history"].append({"role": "assistant", "content": "Acknowledged. Memory synced."})
        
        # ৩. ইউজারকে ফাইল পাঠানো
        if len(file_matches) > 1 or 'zip' in task.lower():
            zb.seek(0)
            zb.name = "updated_project.zip"
            bot.send_document(chat_id, zb, caption=f"🔄 সিঙ্ক্রোনাইজেশন সম্পন্ন! \nএই ফাইলগুলো আপডেট হয়েছে: {updated_file_names}")
        else:
            filename = file_matches[0][0]
            file_buffer = io.BytesIO(project_state[chat_id]["files"][filename].encode('utf-8'))
            file_buffer.name = filename
            bot.send_document(chat_id, file_buffer, caption=f"🔄 সিঙ্ক্রোনাইজেশন সম্পন্ন! \n{filename} আপডেট করা হয়েছে।")
            
    else:
        send_full_output(chat_id, final_response_text, is_partial=looks_incomplete)

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is securely running 24/7 with Dual-Agent Architecture!"

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
