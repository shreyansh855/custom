import os
import telebot
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN set karo!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "✅ Bot Active!\nSend: `+919999999999 message`", parse_mode="Markdown")

@bot.message_handler(func=lambda msg: True)
def send(message):
    try:
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Use: `+919999999999 message`", parse_mode="Markdown")
            return
        
        number = parts[0].strip()
        msg = parts[1].strip()
        
        url = "https://customer-sms-bomber.noob73613.workers.dev/dual"
        r = requests.post(url, data={"number": number, "msg": msg}, timeout=10)
        
        if r.status_code == 200:
            bot.reply_to(message, f"✅ Sent to {number}")
        else:
            bot.reply_to(message, f"❌ Failed! Status: {r.status_code}")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:50]}")

print("🤖 Bot Started!")
bot.polling()
