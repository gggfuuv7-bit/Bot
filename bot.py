import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
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
DEFAULT_CF_MODEL = "@cf/zai-org/glm-5.2" 

bot = telebot.TeleBot(BOT_TOKEN)
TEXT_EXTENSIONS = ['.txt', '.html', '.css', '.js', '.php', '.sql', '.dart', '.json', '.xml', '.md', '.csv']

# --- ডেটাবেস (মেমোরি এবং মডেল সিলেকশন) ---
user_chat_history = {}
user_active_model = {}

# --- বিশাল মডেল কালেকশন ---
# যদি কোনো মডেলের আইডিতে সমস্যা হয়, Cloudflare ড্যাশবোর্ড থেকে Copy ID করে এখানে বসিয়ে দেবেন
AVAILABLE_MODELS = {
    # --- Google ---
    "gemini_35_flash": {"name": "Gemini 3.5 Flash", "id": "@cf/google/gemini-3.5-flash"},
    "gemini_35_pro": {"name": "Gemini 3.5 Pro", "id": "@cf/google/gemini-3.5-pro"},
    "gemini_3_flash": {"name": "Gemini 3 Flash", "id": "@cf/google/gemini-3-flash"},
    
    # --- Anthropic ---
    "opus_48": {"name": "Claude Opus 4.8", "id": "@cf/anthropic/claude-opus-4.8"},
    "fable_5": {"name": "Claude Fable 5", "id": "@cf/anthropic/claude-fable-5"},
    "sonnet_46": {"name": "Claude 4.6 Sonnet", "id": "@cf/anthropic/claude-sonnet-4.6"},
    
    # --- Grok (xAI) ---
    "grok_3": {"name": "Grok 3", "id": "@cf/xai/grok-3"},
    "grok_2": {"name": "Grok 2", "id": "@cf/xai/grok-2"},
    
    # --- OpenAI ---
    "gpt_55_pro": {"name": "GPT-5.5 Pro", "id": "@cf/openai/gpt-5.5-pro"},
    "gpt_55": {"name": "GPT-5.5", "id": "@cf/openai/gpt-5.5"},
    
    # --- অন্যান্য সেরা কোডিং ও বেঞ্চমার্ক মডেল ---
    "deepseek_v4": {"name": "DeepSeek V4", "id": "@cf/deepseek/deepseek-v4"},
    "kimi_k25": {"name": "Kimi K2.5", "id": "@cf/moonshot/kimi-k2.5"},
    "glm_52": {"name": "Z.ai (GLM-5.2)", "id": "@cf/zai-org/glm-5.2"}
}

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

# --- মডেল পরিবর্তনের কমান্ড এবং বাটন মেনু ---
@bot.message_handler(commands=['model'])
def change_model_menu(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    
    markup = InlineKeyboardMarkup()
    buttons = []
    
    # বাটনগুলো তৈরি করা
    for key, data in AVAILABLE_MODELS.items():
        buttons.append(InlineKeyboardButton(data['name'], callback_data=f"model_{key}"))
    
    # বাটনগুলোকে ২ কলামে (২টি করে এক লাইনে) সাজানো
    for i in range(0, len(buttons), 2):
        markup.add(*buttons[i:i+2])
        
    bot.send_message(message.chat.id, "🤖 **মডেল নির্বাচন করুন:**\nআপনি যে মডেলটি ব্যবহার করতে চান, সেটি নিচের তালিকা থেকে সিলেক্ট করুন:", reply_markup=markup, parse_mode="Markdown")

# --- বাটন ক্লিকের রেসপন্স হ্যান্ডলার ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('model_'))
def handle_model_selection(call):
    if call.from_user.id != ALLOWED_USER_ID: return
    
    model_key = call.data.split('_')[1]
    if model_key in AVAILABLE_MODELS:
        selected_model_id = AVAILABLE_MODELS[model_key]['id']
        selected_model_name = AVAILABLE_MODELS[model_key]['name']
        
        user_active_model[call.message.chat.id] = selected_model_id
        
        bot.answer_callback_query(call.id, f"{selected_model_name} অ্যাক্টিভ হয়েছে!")
        bot.edit_message_text(f"✅ সফলভাবে **{selected_model_name}** মডেলে পরিবর্তন করা হয়েছে!\n\nএখন থেকে আপনার সব প্রজেক্ট এই এআই হ্যান্ডেল করবে।", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# --- মেমোরি ক্লিয়ার করার কমান্ড ---
@bot.message_handler(commands=['clear', 'reset'])
def clear_memory(message):
    if message.from_user.id != ALLOWED_USER_ID: return
    chat_id = message.chat.id
    if chat_id in user_chat_history:
        del user_chat_history[chat_id]
    bot.send_message(chat_id, "🧹 বটের মেমোরি ক্লিয়ার করা হয়েছে! নতুন প্রজেক্ট শুরু করতে পারেন।")

@bot.message_handler(content_types=['text', 'document'])
def handle_all_messages(message):
    if message.from_user.id != ALLOWED_USER_ID: return

    chat_id = message.chat.id
    prompt_text = message.text or message.caption or "Analyze the attached file(s)."
    
    current_model = user_active_model.get(chat_id, DEFAULT_CF_MODEL)
    
    model_display_name = current_model
    for key, data in AVAILABLE_MODELS.items():
        if data['id'] == current_model:
            model_display_name = data['name']
            break
            
    bot.send_message(chat_id, f"Processing your request with `{model_display_name}`... Please wait.", parse_mode="Markdown")

    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name.lower()
            file_ext = os.path.splitext(file_name)[1]

            if file_ext in TEXT_EXTENSIONS:
                prompt_text += process_text_file(downloaded_file, file_name)
            elif file_ext == '.zip':
                with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                    for zip_info in z.infolist():
                        if not zip_info.is_dir() and os.path.splitext(zip_info.filename)[1].lower() in TEXT_EXTENSIONS:
                            with z.open(zip_info) as extracted_file:
                                prompt_text += process_text_file(extracted_file.read(), zip_info.filename)

        system_instruction = (
            "You are an expert AI coding assistant. "
            "If the user asks for files or a ZIP, you MUST output the files using this exact XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete file content here]\n</file>\n'
            "Do NOT use markdown code blocks. "
            "CRITICAL: If the user types 'continue', you MUST seamlessly continue exactly from where your last response stopped. Do not repeat anything."
        )

        if chat_id not in user_chat_history:
            user_chat_history[chat_id] = [{"role": "system", "content": system_instruction}]
        
        user_chat_history[chat_id].append({"role": "user", "content": prompt_text})
        
        if len(user_chat_history[chat_id]) > 15:
            user_chat_history[chat_id] = [user_chat_history[chat_id][0]] + user_chat_history[chat_id][-14:]

        api_url = "https://api.cloudflare.com/client/v4/accounts/" + str(CF_ACCOUNT_ID) + "/ai/run/" + str(current_model)
        headers = {
            "Authorization": "Bearer " + str(CF_API_TOKEN),
            "Content-Type": "application/json"
        }
        
        payload = {
            "messages": user_chat_history[chat_id],
            "stream": True 
        }

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
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                if "response" in chunk:
                                    final_response_text += chunk["response"]
                                elif "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        final_response_text += delta["content"]
                            except:
                                pass
            else:
                bot.send_message(chat_id, f"API Error ({model_display_name}): {response.status_code}\n{response.text}")
                user_chat_history[chat_id].pop()
                return

        except requests.exceptions.Timeout:
            is_cut_off = True
        except requests.exceptions.ConnectionError:
            is_cut_off = True
        except Exception as e:
            is_cut_off = True

        if not final_response_text.strip():
            bot.send_message(chat_id, "❌ কোনো ডেটা জেনারেট হয়নি। অন্য কোনো মডেল সিলেক্ট করে চেষ্টা করুন।")
            user_chat_history[chat_id].pop()
            return

        user_chat_history[chat_id].append({"role": "assistant", "content": final_response_text})

        is_continuing = prompt_text.strip().lower() in ['continue', 'চালিয়ে যাও']
        
        file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)(?:</file>|$)', final_response_text, re.IGNORECASE)
        MD_TICKS = chr(96) * 3 
        
        looks_incomplete = is_cut_off or ("<file" in final_response_text and "</file>" not in final_response_text)
        
        if file_matches and not looks_incomplete and not is_continuing:
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
            
    except Exception as e:
        bot.send_message(chat_id, f"An error occurred: {str(e)}")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is securely running 24/7 with Continuous Streaming & Multi-Model Switcher!"

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
