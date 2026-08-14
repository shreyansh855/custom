import os
import telebot
import requests
import re
import time

BOT_TOKEN = os.getenv("8651849879:AAFJ5PahVQ_NIPNIM5KD5nMfS_1hrmeI5ZI")
ADMIN_ID = int(os.getenv("ADMIN_ID", 7423951207))

bot = telebot.TeleBot(BOT_TOKEN)

# Webhook remove (conflict se bachne ke liye)
try:
    bot.remove_webhook()
except:
    pass
time.sleep(1)

# ===== STRONG VALIDATION =====
def clean_number(number):
    # Sirf digits aur + rakho
    cleaned = re.sub(r'[\s\-\(\)]', '', number.strip())
    return cleaned

def is_valid_number(number):
    cleaned = clean_number(number)
    
    # Case 1: +91XXXXXXXXXX (13 chars)
    if cleaned.startswith('+91') and len(cleaned) == 13:
        rest = cleaned[3:]
        if rest.isdigit() and len(rest) == 10:
            return True
    
    # Case 2: 91XXXXXXXXXX (12 chars)
    if cleaned.startswith('91') and len(cleaned) == 12:
        rest = cleaned[2:]
        if rest.isdigit() and len(rest) == 10:
            return True
    
    # Case 3: XXXXXXXXXX (10 chars)
    if len(cleaned) == 10 and cleaned.isdigit():
        return True
    
    return False

def format_number(number):
    cleaned = clean_number(number)
    
    # Already has +91
    if cleaned.startswith('+91'):
        return cleaned
    
    # 10 digits -> add +91
    if len(cleaned) == 10 and cleaned.isdigit():
        return '+91' + cleaned
    
    # 91XXXXXXXXXX -> +91XXXXXXXXXX
    if cleaned.startswith('91') and len(cleaned) == 12:
        return '+' + cleaned
    
    return cleaned

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        bot.reply_to(message, "👑 *Admin Active!*\nSend: `+919999999999 msg`", parse_mode="Markdown")
    else:
        bot.reply_to(message, "✅ *SMS Bot Active!*\nSend: `+919999999999 msg`", parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    bot.reply_to(message, "👑 *Admin Panel*\n\nBot is running!", parse_mode="Markdown")

@bot.message_handler(func=lambda msg: True)
def send_sms(message):
    try:
        text = message.text.strip()
        parts = text.split(" ", 1)
        
        if len(parts) < 2:
            bot.reply_to(message, "❌ Use: `+919999999999 Your message`", parse_mode="Markdown")
            return
        
        raw_number = parts[0].strip()
        msg = parts[1].strip()
        
        # ===== DEBUG: Check karo number kya aa raha hai =====
        print(f"Raw number: '{raw_number}'")
        print(f"Cleaned: '{clean_number(raw_number)}'")
        
        if not is_valid_number(raw_number):
            bot.reply_to(message, f"❌ Invalid number! Use: `+919999999999` or `9876543210`\nYou sent: `{raw_number}`", parse_mode="Markdown")
            return
        
        number = format_number(raw_number)
        
        # Send SMS
        url = "https://customer-sms-bomber.noob73613.workers.dev/dual"
        response = requests.post(url, data={"number": number, "msg": msg}, timeout=10)
        
        if response.status_code == 200:
            bot.reply_to(message, f"✅ Sent to {number}\n📝 {msg}")
        else:
            bot.reply_to(message, f"❌ Failed! Status: {response.status_code}")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:100]}")

if __name__ == "__main__":
    print("🤖 Bot Started!")
    bot.polling(non_stop=True)
