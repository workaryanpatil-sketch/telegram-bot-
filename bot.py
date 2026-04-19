import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
import requests
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

ADMIN_ID = 1376681483

FACE_OFF_QUEUE = []
ACTIVE_MATCHES = {}

QUESTION_TIME = 30
FACE_OFF_FINISH_WAIT = 60  # seconds

CLASS_SUBJECTS = {
    "1st": ["Anatomy", "Physiology", "Biochemistry"],
    "2nd": ["Pathology", "Microbiology", "Pharmacology"],
    "3rd": ["PSM", "FMT"],
    "final": ["Medicine", "Surgery", "Obgy", "Pediatrics"]
}

QUESTIONS_PER_MATCH = 5
QUESTIONS_PER_QUIZ = 5

SUPABASE_URL = "https://veyesmsdlyaooepvkjmr.supabase.co"
QUESTIONS_TABLE_URL = f"{SUPABASE_URL}/rest/v1/questions"
USERS_TABLE_URL = f"{SUPABASE_URL}/rest/v1/users"
USER_QUESTIONS_URL = f"{SUPABASE_URL}/rest/v1/user_questions"

HEADERS = {
    "apikey": "sb_publishable_98PpqkF49oh36BAQYIFB1A_hA0vl0-7",
    "Authorization": "Bearer sb_publishable_98PpqkF49oh36BAQYIFB1A_hA0vl0-7"
}

MOTIVATIONAL_MESSAGE = (
    "Every PYQ you solve = one step closer to your target rank. Don't stop the momentum now 💪\n\n"
    "👉 Press /start to solve more PYQs and keep the streak 🔥 alive.\n\n"
    "/feedback if want to report a bug or problem with bot.\n\n"
    "/share please share the bot to more med students to keep this bot alive.\n\n"
) 

INSTAGRAM_MESSAGE = (
    "🌟 Want more? We've got you covered!\n\n"
    "📲 Follow *@pyrexiamed* on Instagram for:\n"
    "• 📝 Daily PYQs & Notes\n"
    "• 🧠 Quizzes with Prizes 🎁\n"
    "• 🔥 Exclusive Study Material\n\n"
    "👉 https://www.instagram.com/pyrexiamed\n\n"
    "Join the community & level up your prep! 🚀"
)

# ================= USER STORAGE =================
def save_user(user):
    headers = {**HEADERS, "Prefer": "resolution=merge-duplicates"}
    try:
        requests.post(
            USERS_TABLE_URL,
            headers=headers,
            json={
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name
            },
            timeout=10
        )
    except:
        pass


# ================= QUESTION TRACKING =================
def mark_question_done(user_id, question_id, mode):
    try:
        requests.post(
            USER_QUESTIONS_URL,
            headers={**HEADERS, "Prefer": "resolution=ignore-duplicates"},
            json={
                "user_id": user_id,
                "question_id": question_id,
                "mode": mode
            },
            timeout=10
        )
    except:
        pass


def get_done_question_ids(user_id):
    r = requests.get(
        f"{USER_QUESTIONS_URL}?user_id=eq.{user_id}&select=question_id",
        headers=HEADERS,
        timeout=20
    )
    r.raise_for_status()
    return {row["question_id"] for row in r.json()}


# ================= FETCH QUESTIONS =================
def fetch_questions(class_name, subject=None, user_id=None):
    params = {"class": f"eq.{class_name}", "select": "*"}
    if subject:
        params["subject"] = f"eq.{subject}"

    r = requests.get(QUESTIONS_TABLE_URL, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()

    questions = r.json()
    random.shuffle(questions)

    if user_id:
        done_ids = get_done_question_ids(user_id)
        questions = [q for q in questions if q["id"] not in done_ids]

    return questions


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    context.user_data.clear()

    keyboard = [[
        InlineKeyboardButton("🧠 Self Practice", callback_data="mode_self"),
        InlineKeyboardButton("⚔️ Face-Off", callback_data="mode_faceoff")
    ]]
    await update.message.reply_text("Choose mode:", reply_markup=InlineKeyboardMarkup(keyboard))


# ================= SUBJECT HANDLER =================
async def subject_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("mode_"):
        context.user_data.clear()
        context.user_data["mode"] = data.split("_")[1]

        keyboard = [
            [InlineKeyboardButton("1st Year", callback_data="class_1st"),
             InlineKeyboardButton("2nd Year", callback_data="class_2nd")],
            [InlineKeyboardButton("3rd Year", callback_data="class_3rd"),
             InlineKeyboardButton("Final Year", callback_data="class_final")]
        ]
        await query.edit_message_text("Select class:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("class_"):
        context.user_data["class"] = data.split("_")[1]

        if context.user_data["mode"] == "self":
            subs = CLASS_SUBJECTS[context.user_data["class"]]
            keyboard = [[InlineKeyboardButton(s.title(), callback_data=f"sub_{s}")] for s in subs]
            await query.edit_message_text("Choose subject:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await add_to_faceoff_queue(query, context)
        return

    if data.startswith("sub_"):
        context.user_data["subject"] = data.split("_")[1]
        await start_self_quiz(query.message.chat_id, context)


# ================= SELF QUIZ =================
async def start_self_quiz(chat_id, context):
    user_id = context._user_id
    questions = fetch_questions(
        context.user_data["class"],
        context.user_data.get("subject"),
        user_id=user_id
    )

    if not questions:
        await context.bot.send_message(chat_id, "🎉 You've completed all questions for this subject!")
        context.user_data.clear()
        return

    context.user_data.update({
        "questions": questions[:QUESTIONS_PER_QUIZ],
        "current_q": 0,
        "score": 0,
        "answered": False,
        "timer_task": None,
        "timer_msg_id": None
    })

    await send_self_question(chat_id, context)


async def self_question_timer(chat_id, context):
    """30-second countdown timer for self-mode questions"""
    timer_msg = await context.bot.send_message(chat_id, "⏱️ Time left: 30s")
    context.user_data["timer_msg_id"] = timer_msg.message_id
    
    for remaining in range(29, -1, -1):
        await asyncio.sleep(1)
        
        # Check if question was already answered
        if context.user_data.get("answered"):
            try:
                await context.bot.delete_message(chat_id, timer_msg.message_id)
            except:
                pass
            return
        
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=timer_msg.message_id,
                text=f"⏱️ Time left: {remaining}s"
            )
        except:
            pass
    
    # Time's up - auto-skip if not answered
    if not context.user_data.get("answered"):
        context.user_data["answered"] = True
        try:
            await context.bot.delete_message(chat_id, timer_msg.message_id)
        except:
            pass
        
        await context.bot.send_message(chat_id, "⏰ Time's up!")
        await asyncio.sleep(1)
        
        context.user_data["current_q"] += 1
        await send_self_question(chat_id, context)


async def send_self_question(chat_id, context):
    idx = context.user_data["current_q"]
    questions = context.user_data["questions"]

    if idx >= len(questions):
        await context.bot.send_message(
            chat_id,
            f"🎉 Quiz Finished!\nScore: {context.user_data['score']} / {len(questions)}\n\n"
            f"{MOTIVATIONAL_MESSAGE}"
        )
        await context.bot.send_message(chat_id, INSTAGRAM_MESSAGE, parse_mode="Markdown")
        context.user_data.clear()
        return

    q = questions[idx]

    context.user_data.update({
        "correct": q["correct_answer"],
        "question_id": q["id"],
        "answered": False
    })

    text = (
        f"🧠 Q{idx+1}/{len(questions)} • {q.get('year', 'PYQ')}\n\n"
        f"{q['question']}\n\n"
        f"A. {q['option_a']}\nB. {q['option_b']}\n"
        f"C. {q['option_c']}\nD. {q['option_d']}"
    )

    keyboard = [[InlineKeyboardButton(o, callback_data=f"self_{o}")]
                for o in ["A", "B", "C", "D"]]

    await context.bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Start timer for this question
    context.user_data["timer_task"] = asyncio.create_task(self_question_timer(chat_id, context))


async def self_answer_handler(update, context):
    query = update.callback_query
    await query.answer()

    if context.user_data.get("answered"):
        return

    user_id = query.from_user.id
    qid = context.user_data["question_id"]
    mark_question_done(user_id, qid, "self")

    selected = query.data.split("_")[1]
    correct = context.user_data["correct"]
    context.user_data["answered"] = True

    # Delete timer message
    if context.user_data.get("timer_msg_id"):
        try:
            await context.bot.delete_message(
                query.message.chat_id,
                context.user_data["timer_msg_id"]
            )
        except:
            pass

    if selected == correct:
        context.user_data["score"] += 1
        fb = "✅ Correct!"
    else:
        fb = f"❌ Wrong\nCorrect: {correct}"

    await query.edit_message_text(query.message.text + "\n\n" + fb)
    await asyncio.sleep(1)

    context.user_data["current_q"] += 1
    await send_self_question(query.message.chat_id, context)


# ================= FACE-OFF =================
async def add_to_faceoff_queue(query, context):
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    user_class = context.user_data["class"]

    for waiting in FACE_OFF_QUEUE:
        if waiting["class"] == user_class and waiting["user_id"] != user_id:
            FACE_OFF_QUEUE.remove(waiting)
            match_id = f"{waiting['user_id']}_{user_id}"

            questions = fetch_questions(user_class)[:QUESTIONS_PER_MATCH]

            ACTIVE_MATCHES[match_id] = {
                "questions": questions,
                "finished_count": 0,
                "finish_timer_started": False,
                "finish_task": None,
                "ended": False,
                "countdown_msgs": {},
                "players": {
                    waiting["user_id"]: {
                        "chat_id": waiting["chat_id"],
                        "score": 0,
                        "current_q": 0,
                        "finished": False
                    },
                    user_id: {
                        "chat_id": chat_id,
                        "score": 0,
                        "current_q": 0,
                        "finished": False
                    }
                }
            }

            for pid, pdata in ACTIVE_MATCHES[match_id]["players"].items():
                asyncio.create_task(faceoff_countdown(match_id, pid, pdata["chat_id"], context))
            return

    FACE_OFF_QUEUE.append({"user_id": user_id, "chat_id": chat_id, "class": user_class})
    await query.edit_message_text("⏳ Waiting for opponent...")


async def faceoff_countdown(match_id, user_id, chat_id, context):
    msg = await context.bot.send_message(chat_id, "⚔️ Face-off starting...")
    for t in ["3️⃣", "2️⃣", "1️⃣", "🔥 GO!"]:
        await asyncio.sleep(1)
        await msg.edit_text(f"⏳ {t}")
    await send_faceoff_question(match_id, user_id, chat_id, context)


async def send_faceoff_question(match_id, user_id, chat_id, context):
    match = ACTIVE_MATCHES.get(match_id)
    if not match or match["ended"]:
        return

    player = match["players"][user_id]
    idx = player["current_q"]

    if idx >= QUESTIONS_PER_MATCH:
        if not player["finished"]:
            player["finished"] = True
            match["finished_count"] += 1

            if match["finished_count"] == 1:
                match["finish_task"] = asyncio.create_task(faceoff_finish_timer(match_id, context))

            if match["finished_count"] == 2:
                await end_faceoff(match_id, context)
        return

    q = match["questions"][idx]

    text = (
        f"⚔️ Face-Off Q{idx+1}/{QUESTIONS_PER_MATCH} • {q.get('year','PYQ')}\n\n"
        f"{q['question']}\n\n"
        f"A. {q['option_a']}\nB. {q['option_b']}\n"
        f"C. {q['option_c']}\nD. {q['option_d']}"
    )

    keyboard = [[InlineKeyboardButton(o, callback_data=f"fo_{match_id}_{o}")]
                for o in ["A", "B", "C", "D"]]

    await context.bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def faceoff_finish_timer(match_id, context):
    match = ACTIVE_MATCHES.get(match_id)
    if not match:
        return

    for uid, pdata in match["players"].items():
        msg = await context.bot.send_message(
            pdata["chat_id"],
            "⏳ Opponent finished!\nTime left: 60s"
        )
        match["countdown_msgs"][uid] = msg.message_id

    for remaining in range(FACE_OFF_FINISH_WAIT - 1, -1, -1):
        await asyncio.sleep(1)

        if match_id not in ACTIVE_MATCHES or match.get("ended"):
            return

        for uid, pdata in match["players"].items():
            msg_id = match["countdown_msgs"].get(uid)
            if msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=pdata["chat_id"],
                        message_id=msg_id,
                        text=f"⏳ Opponent finished!\nTime left: {remaining}s"
                    )
                except:
                    pass

    await end_faceoff(match_id, context)


async def faceoff_answer_handler(update, context):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    match_id = "_".join(parts[1:-1])
    selected = parts[-1]

    match = ACTIVE_MATCHES.get(match_id)
    if not match or match["ended"]:
        return

    user_id = query.from_user.id
    player = match["players"].get(user_id)
    if not player or player["finished"]:
        return

    q = match["questions"][player["current_q"]]
    mark_question_done(user_id, q["id"], "faceoff")

    if selected == q["correct_answer"]:
        player["score"] += 1
        fb = "✅ Correct!"
    else:
        fb = f"❌ Wrong\nCorrect: {q['correct_answer']}"

    await query.edit_message_text(query.message.text + "\n\n" + fb)
    await asyncio.sleep(1)

    player["current_q"] += 1
    await send_faceoff_question(match_id, user_id, player["chat_id"], context)


async def end_faceoff(match_id, context):
    match = ACTIVE_MATCHES.get(match_id)
    if not match or match.get("ended"):
        return

    match["ended"] = True
    ACTIVE_MATCHES.pop(match_id, None)

    for uid, msg_id in match.get("countdown_msgs", {}).items():
        try:
            await context.bot.delete_message(
                chat_id=match["players"][uid]["chat_id"],
                message_id=msg_id
            )
        except:
            pass

    (u1, p1), (u2, p2) = match["players"].items()

    if p1["score"] > p2["score"]:
        m1, m2 = "🏆 You Win!", "❌ You Lose"
    elif p2["score"] > p1["score"]:
        m1, m2 = "❌ You Lose", "🏆 You Win!"
    else:
        m1 = m2 = "🤝 Draw!"

    await context.bot.send_message(
        p1["chat_id"],
        f"{m1}\nScore: {p1['score']} – {p2['score']}\n\n{MOTIVATIONAL_MESSAGE}"
    )
    await context.bot.send_message(p1["chat_id"], INSTAGRAM_MESSAGE, parse_mode="Markdown")
    
    await context.bot.send_message(
        p2["chat_id"],
        f"{m2}\nScore: {p2['score']} – {p1['score']}\n\n{MOTIVATIONAL_MESSAGE}"
    )
    await context.bot.send_message(p2["chat_id"], INSTAGRAM_MESSAGE, parse_mode="Markdown")

# ================= SHARE =================
async def share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Send notification to admin when /share command is used
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📤 /share Command Used!\n\n"
                 f"👤 User: {user.first_name} (@{user.username or 'No username'})\n"
                 f"🆔 User ID: {user.id}"
        )
    except:
        pass
    
    share_text = (
        "🏥 *MedRoyale Bot* 🏥\n\n"
        "📚 The ultimate NEET PYQ practice bot!\n\n"
        "✨ *Features:*\n"
        "• 🧠 Self Practice Mode - Quiz yourself\n"
        "• ⚔️ Face-Off Mode - Challenge friends\n"
        "• 📖 All years (1st to Final)\n"
        "• 🎯 Subject-wise practice\n"
        "• ⏱️ Timed questions\n"
        "• 📊 Track your progress\n\n"
        "🚀 Start practicing now: @Medroyalebot\n\n"
        "💪 Master NEET PYQs one question at a time!"
    )
    
    # Share button with inline query (opens share window)
    keyboard = [[
        InlineKeyboardButton("🤖 Try the Bot", url="https://t.me/Medroyalebot"),
        InlineKeyboardButton("📤 Share", switch_inline_query=share_text)
    ]]
    
    await update.message.reply_text(
        share_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= BROADCAST =================
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a message to broadcast.\n\n"
            "Usage: /broadcast Your message here"
        )
        return
    
    broadcast_message = " ".join(context.args)
    
    # Generate unique broadcast ID
    import time
    broadcast_id = f"bc_{int(time.time())}"
    
    try:
        response = requests.get(
            f"{USERS_TABLE_URL}?select=user_id",
            headers=HEADERS,
            timeout=30
        )
        response.raise_for_status()
        users = response.json()
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to fetch users: {str(e)}")
        return
    
    if not users:
        await update.message.reply_text("❌ No users found in database.")
        return
    
    success_count = 0
    fail_count = 0
    
    status_msg = await update.message.reply_text(
        f"📢 Broadcasting to {len(users)} users...\n"
        f"Progress: 0/{len(users)}"
    )
    
    # Add acknowledgment button
    keyboard = [[InlineKeyboardButton("✅ Got it!", callback_data=f"ack_{broadcast_id}")]]
    
    for idx, user in enumerate(users):
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=f"📢 *Broadcast Message*\n\n{broadcast_message}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            success_count += 1
        except Exception:
            fail_count += 1
        
        if (idx + 1) % 10 == 0 or (idx + 1) == len(users):
            try:
                await status_msg.edit_text(
                    f"📢 Broadcasting to {len(users)} users...\n"
                    f"Progress: {idx + 1}/{len(users)}\n"
                    f"✅ Sent: {success_count}\n"
                    f"❌ Failed: {fail_count}"
                )
            except:
                pass
    
    await status_msg.edit_text(
        f"✅ Broadcast Complete!\n\n"
        f"Total Users: {len(users)}\n"
        f"✅ Successfully Sent: {success_count}\n"
        f"❌ Failed: {fail_count}\n\n"
        f"📊 Tracking ID: `{broadcast_id}`\n"
        f"Use /broadcast_stats {broadcast_id} to see who acknowledged"
    )


# Handler for acknowledgment button
async def broadcast_ack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ Thanks for acknowledging!")
    
    user = query.from_user
    broadcast_id = query.data.split("_", 1)[1]
    
    # Notify admin
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"✅ Broadcast Acknowledged!\n\n"
                 f"📋 ID: {broadcast_id}\n"
                 f"👤 User: {user.first_name} (@{user.username or 'No username'})\n"
                 f"🆔 User ID: {user.id}"
        )
    except:
        pass
    
    # Optional: Remove the button after clicking
    await query.edit_message_reply_markup(reply_markup=None)
    
async def share_tracking_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    # Send notification to admin
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📤 Share Button Clicked!\n\n"
                 f"👤 User: {user.first_name} (@{user.username or 'No username'})\n"
                 f"🆔 User ID: {user.id}"
        )
    except:
        pass

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("share", share))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(broadcast_ack_handler, pattern="^ack_"))
    app.add_handler(CallbackQueryHandler(faceoff_answer_handler, pattern="^fo_"))
    app.add_handler(CallbackQueryHandler(self_answer_handler, pattern="^self_"))
    app.add_handler(CallbackQueryHandler(subject_handler))

    print("🤖 Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
