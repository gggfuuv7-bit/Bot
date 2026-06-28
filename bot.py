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

        system_instruction = (
            "You are an expert AI coding assistant. "
            "CRITICAL INSTRUCTION: If the user asks for a specific file format (e.g., .dart, .html, .py), "
            "or asks for multiple files, or a ZIP file, you MUST output the files using this exact XML structure:\n"
            '<file name="exact_filename.extension">\n[write the complete file content here]\n</file>\n'
            "You can generate multiple <file> blocks if needed. Do NOT use markdown code blocks inside or outside the <file> tags."
        )

        # URL তৈরি করার সবচেয়ে নিরাপদ পদ্ধতি (ভুলেও এখানে কোনো লিংক পেস্ট করবেন না)
        api_url = "https://api.cloudflare.com/client/v4/accounts/" + str(CF_ACCOUNT_ID) + "/ai/run/" + str(CF_MODEL)
        
        headers = {
            "Authorization": "Bearer " + str(CF_API_TOKEN),
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
                
                file_matches = re.findall(r'<file name="([^"]+)">([\s\S]*?)</file>', final_response_text, re.IGNORECASE)
                
                # সিনট্যাক্স এরর এড়ানোর জন্য সিক্রেট ট্রিক
                MD_TICKS = chr(96) * 3 
                
                if file_matches:
                    user_wants_zip = 'zip' in prompt_text.lower()
                    
                    if len(file_matches) > 1 or user_wants_zip:
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                            for filename, content in file_matches:
                                content = content.strip()
                                if content.startswith(MD_TICKS):
                                    content = content.split('\n', 1)[-1]
                                if content.endswith(MD_TICKS):
                                    content = content.rsplit('\n', 1)[0]
                                
                                zip_file.writestr(filename, content.strip())
                        
                        zip_buffer.seek(0)
                        zip_buffer.name = "project_files.zip"
                        bot.send_document(chat_id, zip_buffer, caption="Here is your ZIP file.")
                        
                    else:
                        filename = file_matches[0][0]
                        content = file_matches[0][1].strip()
                        
                        if content.startswith(MD_TICKS):
                            content = content.split('\n', 1)[-1]
                        if content.endswith(MD_TICKS):
                            content = content.rsplit('\n', 1)[0]
                            
                        file_buffer = io.BytesIO(content.strip().encode('utf-8'))
                        file_buffer.name = filename
                        bot.send_document(chat_id, file_buffer, caption=f"Here is your {filename} file.")
                else:
                    send_full_output(chat_id, final_response_text)
                
            except Exception as e:
                bot.send_message(chat_id, f"Parsing Error: {e}\nData: {str(result_data)[:500]}")
                
        else:
            bot.send_message(chat_id, f"API Error: {response.status_code}\n{response.text}")

    except requests.exceptions.Timeout:
        bot.send_message(chat_id, "Error: The AI took too long (Timeout after 5 mins).")
    except Exception as e:
        bot.send_message(chat_id, f"An error occurred: {str(e)}")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is securely running 24/7 with Zip & File Formatting Support!"

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
