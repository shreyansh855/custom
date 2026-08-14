import telebot
import requests
import sqlite3
import time
import re
import json
import threading
import random
from datetime import datetime, timedelta
from collections import defaultdict
import logging

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8651849879:AAFcqcIqUWz34RUsee2_EP_TVnoFQkKHgMo"
ADMIN_IDS = [7423951207]  # Multiple admins ke liye
SMS_API_URL = "https://customer-sms-bomber.noob73613.workers.dev/dual"
MAX_MESSAGES_PER_DAY = 20
MAX_MESSAGE_LENGTH = 160
MIN_MESSAGE_LENGTH = 1
COOLDOWN_SECONDS = 5

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('sms_bot.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_tables()
    
    def init_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_use TIMESTAMP,
                total_sent INTEGER DEFAULT 0,
                is_banned BOOLEAN DEFAULT 0,
                is_admin BOOLEAN DEFAULT 0,
                daily_limit INTEGER DEFAULT 20
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                number TEXT,
                message TEXT,
                timestamp TIMESTAMP,
                status TEXT,
                response_code INTEGER,
                response_text TEXT
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT UNIQUE,
                reason TEXT,
                added_by INTEGER,
                added_at TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                content TEXT,
                created_at TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                number TEXT,
                message TEXT,
                scheduled_time TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        ''')
        
        self.conn.commit()
    
    def add_user(self, user_id, username, first_name, last_name):
        self.cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, first_use)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, datetime.now()))
        self.conn.commit()
    
    def get_user(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()
    
    def update_user_stats(self, user_id):
        self.cursor.execute('UPDATE users SET total_sent = total_sent + 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def log_message(self, user_id, number, message, status, response_code=0, response_text=''):
        self.cursor.execute('''
            INSERT INTO messages (user_id, number, message, timestamp, status, response_code, response_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, number, message[:500], datetime.now(), status, response_code, response_text[:500]))
        self.conn.commit()
    
    def get_today_count(self, user_id):
        today = datetime.now().strftime('%Y-%m-%d')
        self.cursor.execute('''
            SELECT COUNT(*) FROM messages 
            WHERE user_id = ? AND date(timestamp) = ?
        ''', (user_id, today))
        return self.cursor.fetchone()[0]
    
    def get_user_stats(self, user_id):
        self.cursor.execute('SELECT total_sent FROM users WHERE user_id = ?', (user_id,))
        total = self.cursor.fetchone()
        return total[0] if total else 0
    
    def is_banned(self, user_id):
        self.cursor.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        return result and result[0] == 1
    
    def ban_user(self, user_id):
        self.cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def unban_user(self, user_id):
        self.cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def add_to_blacklist(self, number, reason, added_by):
        try:
            self.cursor.execute('INSERT INTO blacklist (number, reason, added_by, added_at) VALUES (?, ?, ?, ?)',
                               (number, reason, added_by, datetime.now()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def is_blacklisted(self, number):
        self.cursor.execute('SELECT * FROM blacklist WHERE number = ?', (number,))
        return self.cursor.fetchone() is not None
    
    def save_template(self, user_id, name, content):
        self.cursor.execute('INSERT INTO templates (user_id, name, content, created_at) VALUES (?, ?, ?, ?)',
                           (user_id, name, content, datetime.now()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_templates(self, user_id):
        self.cursor.execute('SELECT id, name, content FROM templates WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        return self.cursor.fetchall()
    
    def delete_template(self, template_id, user_id):
        self.cursor.execute('DELETE FROM templates WHERE id = ? AND user_id = ?', (template_id, user_id))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def close(self):
        self.conn.close()

# ==================== INIT DATABASE ====================
db = Database()

# ==================== RATE LIMITER ====================
class RateLimiter:
    def __init__(self):
        self.cooldowns = defaultdict(dict)
        self.daily_counts = defaultdict(int)
    
    def can_send(self, user_id, number):
        # Check cooldown
        if user_id in self.cooldowns and number in self.cooldowns[user_id]:
            last_time = self.cooldowns[user_id][number]
            if (datetime.now() - last_time).seconds < COOLDOWN_SECONDS:
                return False, f"Please wait {COOLDOWN_SECONDS - (datetime.now() - last_time).seconds}s"
        
        # Check daily limit
        today_count = db.get_today_count(user_id)
        user = db.get_user(user_id)
        limit = user[8] if user else MAX_MESSAGES_PER_DAY
        
        if today_count >= limit:
            return False, f"Daily limit reached! ({limit}/day)"
        
        return True, "OK"
    
    def record_send(self, user_id, number):
        if user_id not in self.cooldowns:
            self.cooldowns[user_id] = {}
        self.cooldowns[user_id][number] = datetime.now()

rate_limiter = RateLimiter()

# ==================== BOT INIT ====================
bot = telebot.TeleBot(BOT_TOKEN)

# ==================== HELPER FUNCTIONS ====================
def is_valid_number(number):
    # Support multiple formats
    patterns = [
        r'^\+?\d{10,15}$',  # +91 9876543210
        r'^0\d{9,14}$',     # 09876543210
        r'^\d{10,15}$'      # 9876543210
    ]
    for pattern in patterns:
        if re.match(pattern, number):
            return True
    return False

def format_number(number):
    # Remove spaces, hyphens, parentheses
    number = re.sub(r'[\s\-\(\)]', '', number)
    
    # Add +91 if missing (India)
    if number.startswith('0'):
        number = '+91' + number[1:]
    elif len(number) == 10 and number.isdigit():
        number = '+91' + number
    elif not number.startswith('+'):
        number = '+' + number
    
    return number

def send_sms(number, message, retries=3):
    for attempt in range(retries):
        try:
            response = requests.post(
                SMS_API_URL,
                data={"number": number, "msg": message},
                timeout=10
            )
            if response.status_code == 200:
                return True, response.status_code, "Success"
            elif response.status_code == 429:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            else:
                return False, response.status_code, response.text[:100]
        except requests.exceptions.Timeout:
            time.sleep(2 ** attempt)
            continue
        except Exception as e:
            return False, 500, str(e)[:100]
    
    return False, 500, "Max retries exceeded"

# ==================== COMMANDS ====================

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    db.add_user(user_id, message.from_user.username or '', message.from_user.first_name or '', message.from_user.last_name or '')
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned from using this bot.")
        return
    
    welcome = f"""
👋 *Welcome to Advanced SMS Bot!*

🎯 *Features:*
• 📱 Send SMS to any number
• 📊 Real-time usage stats
• 📝 Save message templates
• 📅 Schedule messages
• 🔒 Rate limiting
• 📜 Full history

📝 *Basic Usage:*
`+919999999999 Your message here`

📋 *Commands:*
/start - Show this
/help - Full help
/stats - Your stats
/history - Last 10 messages
/templates - Manage templates
/schedule - Schedule messages
/balance - Your remaining today
/clear - Clear history
/about - About bot

👤 *Your ID:* `{user_id}`
📆 *Joined:* {datetime.now().strftime('%d %b %Y %I:%M %p')}

⚠️ *Limits:* {MAX_MESSAGES_PER_DAY} messages/day
    """
    bot.reply_to(message, welcome, parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """
📖 *Advanced SMS Bot - Complete Guide*

📤 *SEND SMS:*
Just type:
`+919999999999 Your message`

✅ *Valid Formats:*
• +919876543210
• 9876543210
• 09876543210

📏 *MESSAGE LIMITS:*
• Min: 1 character
• Max: 160 characters

📊 *STATS COMMANDS:*
/stats - Your total usage
/balance - Today's remaining
/history - Last 10 messages

📝 *TEMPLATES:*
/save name message - Save template
/templates - List templates
/delete id - Delete template
/use name - Use template

📅 *SCHEDULE:*
/schedule number message time
Example: /schedule +919999999999 Hello 18:30

🛡️ *SAFETY:*
• 20 messages/day limit
• 5 second cooldown
• Blacklist system
• Banned users blocked

📞 *Need help?* Contact: @YourSupport
    """
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def stats(message):
    user_id = message.from_user.id
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    total = db.get_user_stats(user_id)
    today = db.get_today_count(user_id)
    user = db.get_user(user_id)
    limit = user[8] if user else MAX_MESSAGES_PER_DAY
    
    stats_text = f"""
📊 *Your Statistics*

📨 *Total SMS Sent:* {total}
📆 *Today:* {today}/{limit}
⏳ *Remaining Today:* {limit - today}
📅 *First Use:* {user[4][:16] if user else 'N/A'}
🕐 *Last Message:* {datetime.now().strftime('%I:%M %p')}

📊 *Status:* {'🟢 Active' if not db.is_banned(user_id) else '🔴 Banned'}
    """
    bot.reply_to(message, stats_text, parse_mode="Markdown")

@bot.message_handler(commands=['balance'])
def balance(message):
    user_id = message.from_user.id
    today = db.get_today_count(user_id)
    user = db.get_user(user_id)
    limit = user[8] if user else MAX_MESSAGES_PER_DAY
    
    remaining = limit - today
    bar = '█' * min(remaining, 10) + '░' * max(0, 10 - min(remaining, 10))
    
    balance_text = f"""
💰 *Daily Balance*

📊 `{bar}`
└── {today}/{limit} used

✅ *Remaining:* {remaining} messages

⏳ *Resets at:* Midnight (12:00 AM)

📈 *Suggestion:* {'🔴 Low!' if remaining < 5 else '🟢 Good!'}
    """
    bot.reply_to(message, balance_text, parse_mode="Markdown")

@bot.message_handler(commands=['history'])
def history(message):
    user_id = message.from_user.id
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    db.cursor.execute('''
        SELECT number, message, timestamp, status FROM messages 
        WHERE user_id = ? ORDER BY id DESC LIMIT 10
    ''', (user_id,))
    results = db.cursor.fetchall()
    
    if not results:
        bot.reply_to(message, "📭 No message history found.")
        return
    
    history_text = "📜 *Last 10 Messages*\n\n"
    for i, (number, msg, ts, status) in enumerate(results, 1):
        status_emoji = "✅" if status == "Success" else "❌"
        msg_short = msg[:30] + "..." if len(msg) > 30 else msg
        history_text += f"{i}. {status_emoji} `{number}`\n   📝 {msg_short}\n   🕐 {ts[:16]}\n\n"
    
    bot.reply_to(message, history_text, parse_mode="Markdown")

@bot.message_handler(commands=['clear'])
def clear(message):
    user_id = message.from_user.id
    
    db.cursor.execute('DELETE FROM messages WHERE user_id = ?', (user_id,))
    db.cursor.execute('UPDATE users SET total_sent = 0 WHERE user_id = ?', (user_id,))
    db.conn.commit()
    
    bot.reply_to(message, "🗑️ Your history has been cleared!")

@bot.message_handler(commands=['templates'])
def templates(message):
    user_id = message.from_user.id
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    templates_list = db.get_templates(user_id)
    
    if not templates_list:
        bot.reply_to(message, "📝 No templates saved.\n\nSave with: `/save name Your message`", parse_mode="Markdown")
        return
    
    text = "📝 *Your Templates*\n\n"
    for template_id, name, content in templates_list:
        content_short = content[:40] + "..." if len(content) > 40 else content
        text += f"🆔 `{template_id}` - *{name}*\n   📝 {content_short}\n\n"
    
    text += "\nUse: `/use name`\nDelete: `/delete id`"
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['save'])
def save_template(message):
    user_id = message.from_user.id
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    try:
        parts = message.text.split(" ", 2)
        if len(parts) < 3:
            bot.reply_to(message, "❌ Use: `/save name Your message`", parse_mode="Markdown")
            return
        
        name = parts[1].strip()
        content = parts[2].strip()
        
        if len(content) > 500:
            bot.reply_to(message, "❌ Template too long! Max 500 characters")
            return
        
        template_id = db.save_template(user_id, name, content)
        bot.reply_to(message, f"✅ Template saved!\n🆔 ID: `{template_id}`\n📝 Name: *{name}*", parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['use'])
def use_template(message):
    user_id = message.from_user.id
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    try:
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Use: `/use name`", parse_mode="Markdown")
            return
        
        name = parts[1].strip()
        
        db.cursor.execute('SELECT content FROM templates WHERE user_id = ? AND name = ?', (user_id, name))
        result = db.cursor.fetchone()
        
        if not result:
            bot.reply_to(message, f"❌ Template '*{name}*' not found!", parse_mode="Markdown")
            return
        
        content = result[0]
        bot.reply_to(message, f"📝 *{name}*\n\n`{content}`\n\n📌 Send with: `+919999999999 {content}`", parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['delete'])
def delete_template(message):
    user_id = message.from_user.id
    
    try:
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Use: `/delete id`", parse_mode="Markdown")
            return
        
        template_id = int(parts[1].strip())
        
        if db.delete_template(template_id, user_id):
            bot.reply_to(message, f"✅ Template `{template_id}` deleted!", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Template not found or you don't own it.")
            
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID! Use: `/delete 123`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['schedule'])
def schedule(message):
    user_id = message.from_user.id
    
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    try:
        parts = message.text.split(" ", 3)
        if len(parts) < 4:
            bot.reply_to(message, "❌ Use: `/schedule +919999999999 Message 18:30`", parse_mode="Markdown")
            return
        
        number = parts[1].strip()
        msg = parts[2].strip()
        time_str = parts[3].strip()
        
        # Validate number
        if not is_valid_number(number):
            bot.reply_to(message, "❌ Invalid number! Use: `+919999999999`", parse_mode="Markdown")
            return
        
        number = format_number(number)
        
        # Parse time
        try:
            scheduled_time = datetime.strptime(time_str, '%H:%M')
            now = datetime.now()
            scheduled_datetime = datetime.combine(now.date(), scheduled_time.time())
            
            # If time is in past, schedule for tomorrow
            if scheduled_datetime < now:
                scheduled_datetime += timedelta(days=1)
        except ValueError:
            bot.reply_to(message, "❌ Invalid time! Use: `18:30` (24-hour format)")
            return
        
        db.cursor.execute('''
            INSERT INTO scheduled (user_id, number, message, scheduled_time)
            VALUES (?, ?, ?, ?)
        ''', (user_id, number, msg, scheduled_datetime))
        db.conn.commit()
        
        wait_time = (scheduled_datetime - datetime.now()).seconds // 60
        bot.reply_to(message, f"✅ Scheduled!\n📱 To: `{number}`\n📝 Msg: `{msg}`\n⏰ At: {scheduled_datetime.strftime('%I:%M %p')}\n⏳ In: {wait_time} minutes", parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['about'])
def about(message):
    about_text = """
🤖 *Advanced SMS Bot v3.0*

📱 *Features:*
• SMS sending
• Templates
• Scheduling
• Statistics
• History
• Rate limiting
• Blacklist system

🔧 *Tech:*
• Python + pyTelegramBotAPI
• SQLite database
• Multi-threading
• Rate limiting

👨‍💻 *Developer:* Secured Site Dns
📅 *Version:* 3.0
🔄 *Uptime:* 24/7

⚠️ *Disclaimer:*
For educational purposes only.
Users responsible for their actions.

📊 *Status:* 🟢 Online
    """
    bot.reply_to(message, about_text, parse_mode="Markdown")

# ==================== ADMIN COMMANDS ====================

def is_admin(user_id):
    return user_id in ADMIN_IDS

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    db.cursor.execute('SELECT COUNT(*) FROM users')
    total_users = db.cursor.fetchone()[0]
    db.cursor.execute('SELECT COUNT(*) FROM messages')
    total_messages = db.cursor.fetchone()[0]
    db.cursor.execute('SELECT SUM(total_sent) FROM users')
    total_sent = db.cursor.fetchone()[0] or 0
    db.cursor.execute('SELECT COUNT(*) FROM blacklist')
    blacklist_count = db.cursor.fetchone()[0]
    
    admin_text = f"""
👑 *Admin Panel*

📊 *Statistics:*
• Total Users: {total_users}
• Total Messages: {total_messages}
• Total SMS Sent: {total_sent}
• Blacklisted: {blacklist_count}

🛠️ *Commands:*
/broadcast - Send to all
/ban id - Ban user
/unban id - Unban user
/blacklist number reason - Blacklist
/stats_all - Full stats
/shutdown - Stop bot

📋 *Recent Activity:*
{get_recent_activity()}
    """
    bot.reply_to(message, admin_text, parse_mode="Markdown")

def get_recent_activity():
    db.cursor.execute('''
        SELECT username, number, timestamp FROM messages 
        ORDER BY id DESC LIMIT 5
    ''')
    results = db.cursor.fetchall()
    
    if not results:
        return "No recent activity"
    
    text = ""
    for username, number, ts in results:
        text += f"• @{username} → `{number}` at {ts[:16]}\n"
    return text

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    bot.reply_to(message, "📢 Send broadcast message:")
    bot.register_next_step_handler(message, process_broadcast)

def process_broadcast(message):
    broadcast_text = message.text
    
    db.cursor.execute('SELECT user_id FROM users WHERE is_banned = 0')
    users = db.cursor.fetchall()
    
    sent = 0
    for user in users:
        try:
            bot.send_message(user[0], f"📢 *Broadcast*\n\n{broadcast_text}", parse_mode="Markdown")
            sent += 1
            time.sleep(0.1)
        except:
            pass
    
    bot.reply_to(message, f"✅ Broadcast sent to {sent} users!")

@bot.message_handler(commands=['ban'])
def ban(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    try:
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Use: `/ban user_id`", parse_mode="Markdown")
            return
        
        target_id = int(parts[1].strip())
        db.ban_user(target_id)
        bot.reply_to(message, f"✅ User `{target_id}` banned!", parse_mode="Markdown")
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['unban'])
def unban(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    try:
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Use: `/unban user_id`", parse_mode="Markdown")
            return
        
        target_id = int(parts[1].strip())
        db.unban_user(target_id)
        bot.reply_to(message, f"✅ User `{target_id}` unbanned!", parse_mode="Markdown")
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['blacklist'])
def blacklist(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    try:
        parts = message.text.split(" ", 2)
        if len(parts) < 3:
            bot.reply_to(message, "❌ Use: `/blacklist number reason`", parse_mode="Markdown")
            return
        
        number = parts[1].strip()
        reason = parts[2].strip()
        
        if db.add_to_blacklist(number, reason, user_id):
            bot.reply_to(message, f"✅ `{number}` blacklisted!\nReason: {reason}", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"⚠️ `{number}` already in blacklist!", parse_mode="Markdown")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['stats_all'])
def stats_all(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    db.cursor.execute('''
        SELECT 
            COUNT(*) as total_users,
            SUM(total_sent) as total_sent,
            AVG(total_sent) as avg_sent
        FROM users
    ''')
    result = db.cursor.fetchone()
    
    stats_text = f"""
📊 *Full Statistics*

👥 *Total Users:* {result[0]}
📨 *Total SMS:* {result[1]}
📊 *Average SMS/User:* {result[2]:.1f}

📈 *Top Users:*
{get_top_users()}
    """
    bot.reply_to(message, stats_text, parse_mode="Markdown")

def get_top_users():
    db.cursor.execute('''
        SELECT username, total_sent FROM users 
        WHERE total_sent > 0 
        ORDER BY total_sent DESC LIMIT 5
    ''')
    results = db.cursor.fetchall()
    
    if not results:
        return "No active users"
    
    text = ""
    for i, (username, total) in enumerate(results, 1):
        text += f"{i}. @{username}: {total} messages\n"
    return text

@bot.message_handler(commands=['shutdown'])
def shutdown(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    bot.reply_to(message, "🔄 Bot is shutting down...")
    logger.info("Bot shutting down by admin command")
    db.close()
    exit(0)

# ==================== MAIN MESSAGE HANDLER ====================

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Check if user is banned
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned from using this bot.")
        return
    
    # Add user to database
    db.add_user(user_id, message.from_user.username or '', message.from_user.first_name or '', message.from_user.last_name or '')
    
    # Parse input
    try:
        parts = text.split(" ", 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Format: `+919999999999 Your message`", parse_mode="Markdown")
            return
        
        number = parts[0].strip()
        msg = parts[1].strip()
        
        # Validate number
        if not is_valid_number(number):
            bot.reply_to(message, "❌ Invalid number! Use: `+919999999999` or `9876543210`", parse_mode="Markdown")
            return
        
        # Format number
        number = format_number(number)
        
        # Check blacklist
        if db.is_blacklisted(number):
            bot.reply_to(message, f"🚫 Number `{number}` is blacklisted!", parse_mode="Markdown")
            return
        
        # Validate message length
        if len(msg) < MIN_MESSAGE_LENGTH:
            bot.reply_to(message, f"❌ Message too short! Min {MIN_MESSAGE_LENGTH} character")
            return
        
        if len(msg) > MAX_MESSAGE_LENGTH:
            bot.reply_to(message, f"❌ Message too long! {len(msg)}/{MAX_MESSAGE_LENGTH} characters")
            return
        
        # Check rate limits
        can_send, msg_limit = rate_limiter.can_send(user_id, number)
        if not can_send:
            bot.reply_to(message, f"⏳ {msg_limit}")
            return
        
        # Send SMS
        status, response_code, response_text = send_sms(number, msg)
        
        if status:
            db.update_user_stats(user_id)
            rate_limiter.record_send(user_id, number)
            db.log_message(user_id, number, msg, "Success", response_code, response_text)
            
            today_count = db.get_today_count(user_id)
            user = db.get_user(user_id)
            limit = user[8] if user else MAX_MESSAGES_PER_DAY
            
            success_msg = f"""
✅ *Message Sent!*

📱 To: `{number}`
📝 Msg: `{msg}`
📊 {len(msg)}/{MAX_MESSAGE_LENGTH} chars
📈 Today: {today_count}/{limit}
⏳ Remaining: {limit - today_count}
            """
            bot.reply_to(message, success_msg, parse_mode="Markdown")
        else:
            db.log_message(user_id, number, msg, "Failed", response_code, response_text)
            
            error_msg = f"""
❌ *Failed!*

📱 To: `{number}`
📝 Msg: `{msg}`
🔴 Status: {response_code}
💬 {response_text[:100]}
            """
            bot.reply_to(message, error_msg, parse_mode="Markdown")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:200]}")
        logger.error(f"Error in handle_message: {str(e)}")

# ==================== SCHEDULER THREAD ====================

def scheduler():
    while True:
        try:
            now = datetime.now()
            db.cursor.execute('''
                SELECT id, user_id, number, message FROM scheduled 
                WHERE status = 'pending' AND scheduled_time <= ?
            ''', (now,))
            scheduled_msgs = db.cursor.fetchall()
            
            for msg_id, user_id, number, message in scheduled_msgs:
                # Send scheduled message
                status, response_code, response_text = send_sms(number, message)
                
                if status:
                    db.update_user_stats(user_id)
                    db.log_message(user_id, number, message, "Scheduled", response_code, response_text)
                
                # Update status
                db.cursor.execute('UPDATE scheduled SET status = ? WHERE id = ?', 
                                 ('sent' if status else 'failed', msg_id))
                db.conn.commit()
                
                time.sleep(1)
            
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            logger.error(f"Scheduler error: {str(e)}")
            time.sleep(60)

# ==================== START THREADS ====================

def start_scheduler():
    thread = threading.Thread(target=scheduler, daemon=True)
    thread.start()
    logger.info("Scheduler started")

# ==================== START BOT ====================

if __name__ == "__main__":
    start_scheduler()
    
    logger.info("🤖 Advanced SMS Bot Started!")
    logger.info(f"📊 Database: sms_bot.db")
    logger.info(f"👑 Admins: {ADMIN_IDS}")
    logger.info(f"📈 Daily Limit: {MAX_MESSAGES_PER_DAY}")
    logger.info("Press Ctrl+C to stop")
    
    while True:
        try:
            bot.polling(non_stop=True, interval=0)
        except Exception as e:
            logger.error(f"Polling error: {str(e)}")
            time.sleep(5)