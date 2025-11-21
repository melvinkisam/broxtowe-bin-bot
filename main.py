from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, ConversationHandler
from broxtowe_scraper import scrape_bin_collection, load_users, save_user
from datetime import datetime, time
from dotenv import load_dotenv
import os
import re
import pytz

# ---------- Settings ----------
load_dotenv()

BOT_API = os.getenv("BOT_API")
BOT_USERNAME = os.getenv('BOT_USERNAME')

START_URL = os.getenv("START_URL")
DATA_FILE = os.getenv("DATA_FILE")

ASK_POSTCODE, ASK_ADDRESS = range(2)
# ------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Hello! I am your Broxtowe Bin Reminder Bot. Use /schedule to get your bin collection schedule. For more commands, use /help.')


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"I can help you keep track of your bin collection schedule in Broxtowe.\n\n"
        f"Commands:\n"
        f"/start - Start the bot\n"
        f"/help - Show this help message\n"
        f"/setaddress - Set or update your address and postcode\n"
        f"/schedule - Get your upcoming bin collection schedule\n"
        f"/currentaddress - View your currently saved address and postcode\n"
        f"/onreminder - Turn on reminders for bin collection\n"
        f"/offreminder - Turn off reminders\n"
        f"/statusreminder - Check the status of your reminders\n"
    )


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Give me a moment to fetch your bin collection schedule...')

    user_id = str(update.message.from_user.id)
    users = load_users(filepath=DATA_FILE)

    if user_id not in users:
        await update.message.reply_text("You haven't set your address yet! Please use /setaddress first.")
        return

    POSTCODE = users[user_id]["POSTCODE"]
    ADDRESS_TO_MATCH = users[user_id]["ADDRESS"]

    out, exact_address = scrape_bin_collection(START_URL, postcode=POSTCODE, address_to_match=ADDRESS_TO_MATCH, headless=True, debug=True)
    bins = out.get("bins", [])

    msg_lines = []
    for b in bins:
        next_date_str = b["Next Collection"]

        next_date = datetime.strptime(next_date_str, "%A, %d %B %Y").date()  # Example format: "Friday, 28 November 2025"
        today = datetime.today().date()
        days_left = (next_date - today).days

        if days_left == 0:
            left_text = "(today!)"
        elif days_left == 1:
            left_text = "(1 day left)"
        elif days_left > 1:
            left_text = f"({days_left} days left)"
        else:
            left_text = "(already collected)"

        msg_lines.append(
            f"🗑️ *{b['Bin Type']}*\n"
            f"- Collection Day: {b['Collection Day']}\n"
            f"- Last: {b['Last Collection']}\n"
            f"- Next: {next_date_str} *{left_text}*\n"
        )

    formatted_message = "\n".join(msg_lines)

    # print(formatted_message)

    await update.message.reply_text(formatted_message, parse_mode='Markdown')


# ---------- Set address ----------
async def set_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter your postcode:")
    return ASK_POSTCODE


async def receive_postcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["postcode"] = update.message.text.strip()
    await update.message.reply_text("Got it! Now please enter your address:")
    return ASK_ADDRESS


async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Give me a moment to verify your address...")

    user_id = str(update.message.from_user.id)
    address = update.message.text.strip()
    postcode = context.user_data.get("postcode")

    out, exact_address = scrape_bin_collection(START_URL, postcode=postcode, address_to_match=address, headless=True, debug=True)
    bins = out.get("bins", [])

    if not bins:
        await update.message.reply_text("Sorry, I couldn't find your bin collection schedule. Please make sure your address is correct using /setaddress.")      
        return ConversationHandler.END

    match = re.match(r"^(.*),\s*([^,]+)$", exact_address)

    if match:
        address = match.group(1).strip()   # everything before the last comma
        postcode = match.group(2).strip()       # the last segment
    else:
        await update.message.reply_text("Sorry, I couldn't parse your exact address. Please make sure your address is correct using /setaddress.")      
        return ConversationHandler.END

    # Load existing data
    data = load_users(filepath=DATA_FILE)

    # Save new entry
    data[user_id] = {
        "POSTCODE": postcode,
        "ADDRESS": address
    }

    save_user(data, filepath=DATA_FILE)

    await update.message.reply_text(
        f"Your schedule info has been saved!\n\n"
        f"Postcode: {postcode}\nAddress: {address}"
        f"\n\nUse /schedule to get your bin collection schedule."
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")

    return ConversationHandler.END
# ------------------------------------


async def current_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    data = load_users(filepath=DATA_FILE)

    if user_id not in data:
        await update.message.reply_text("You have not registered your address yet. Use /setaddress to set your postcode and address.")
        return

    user_info = data[user_id]
    postcode = user_info.get("POSTCODE")
    address = user_info.get("ADDRESS")

    await update.message.reply_text(
        f"📍 *Your Current Address*\n"
        f"- Postcode: {postcode}\n"
        f"- Address: {address}",
        parse_mode="Markdown"
    )


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.chat_id

    users = load_users(filepath=DATA_FILE)

    if str(user_id) not in users:
        await context.bot.send_message(
            user_id,
            "Your schedule could not be found. Please use /setaddress first."
        )
        return

    POSTCODE = users[str(user_id)]["POSTCODE"]
    ADDRESS = users[str(user_id)]["ADDRESS"]

    out, exact_address = scrape_bin_collection(
        START_URL, postcode=POSTCODE, address_to_match=ADDRESS,
        headless=True, debug=False
    )
    bins = out.get("bins", [])

    if not bins:
        await context.bot.send_message(
            user_id,
            "Your schedule could not be found. Please use /setaddress first."
        )
        return

    # Check if any bin collection is tomorrow
    tomorrow_bins = []
    today = datetime.now(pytz.timezone("Europe/London")).date()

    for b in bins:
        next_date_str = b["Next Collection"]
        next_date = datetime.strptime(next_date_str, "%A, %d %B %Y").date()

        days_left = (next_date - today).days

        if days_left == 1:
            tomorrow_bins.append(b)

    if not tomorrow_bins:
        return # skip sending reminder

    msg_lines = ["🛎️ *Bin Collection Reminder*\nYour collection is *tomorrow*!\n"]

    for b in tomorrow_bins:
        msg_lines.append(
            f"🗑️ *{b['Bin Type']}*\n"
            f"- Collection Day: {b['Collection Day']}\n"
            f"- Next: {b['Next Collection']}\n"
        )

    message = "\n".join(msg_lines)

    await context.bot.send_message(
        chat_id=user_id,
        text=message,
        parse_mode="Markdown"
    )


async def on_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Remove any existing reminders
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    h, m = 18, 0
    context.job_queue.run_daily(
        send_reminder,
        time=time(hour=h, minute=m, tzinfo=pytz.timezone("Europe/London")),
        chat_id=user_id,
        name=str(user_id),
    )

    # Save settings to JSON
    save_reminder(user_id, h, m)

    await update.message.reply_text(
        f"Reminder scheduled is at *{h}:{m:02d}* UK time one day before collection (if any).",
        parse_mode="Markdown"
    )


async def off_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    jobs = context.job_queue.get_jobs_by_name(str(user_id))
    if not jobs:
        await update.message.reply_text("Your reminder is already off.")
        return

    for j in jobs:
        j.schedule_removal()

    # Disable in JSON
    remove_reminder(user_id)

    await update.message.reply_text("Your reminders have been disabled.")


async def status_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    # Get all jobs with this user's ID as the job name
    jobs = context.job_queue.get_jobs_by_name(user_id)

    if not jobs:
        await update.message.reply_text("You have no active reminders. Use /onreminder to enable reminders.")
        return

    msg_lines = ["📅 *Your Active Reminder Jobs:*", ""]

    for job in jobs:
        next_run = job.next_t
        if next_run:
            next_run = next_run.astimezone(pytz.timezone("Europe/London"))
            next_run_str = next_run.strftime("%A, %d %B %Y at %H:%M")
        else:
            next_run_str = "Unknown"

        msg_lines.append(
            f"• **Job Name:** `{job.name}`\n"
            f"  - Next Run: {next_run_str}\n"
            f"  - Callback: `{job.callback.__name__}`"
        )

    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")


def save_reminder(user_id: int, hour: int, minute: int, filepath=DATA_FILE):
    users = load_users(filepath)

    user_id = str(user_id)
    if user_id not in users:
        users[user_id] = {}

    users[user_id]["reminder"] = {
        "enabled": True,
        "hour": hour,
        "minute": minute
    }

    save_user(users, filepath)


def remove_reminder(user_id: int, filepath=DATA_FILE):
    users = load_users(filepath)

    user_id = str(user_id)
    if user_id in users and "reminder" in users[user_id]:
        users[user_id]["reminder"]["enabled"] = False
        save_user(users, filepath)


def handle_response(text: str):
    # For now, only return a placeholder response
    return f"text received: {text}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_type = update.message.chat.type
    user_message = update.message.text

    print(f"User {update.message.chat.id} in {message_type}: '{user_message}'")

    # Group chat handling: only respond if bot is mentioned
    if message_type == 'group' or message_type == 'supergroup':
        if BOT_USERNAME in user_message:
            user_message = user_message.replace(BOT_USERNAME, '').strip()
            response = handle_response(user_message)
        else:
            return
    # Private chat handling: respond to all messages
    else:
        response = handle_response(user_message)

    print(f"Bot response: '{response}'")

    await update.message.reply_text(response) 


async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Update {update} caused error {context.error}")


# In some command handler you can add:
async def test_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # schedule to run once in 5 seconds
    context.job_queue.run_once(send_reminder, when=5, chat_id=user_id, name=f"test-{user_id}")
    await update.message.reply_text("Scheduled a one-off test reminder in 5 seconds.")


def restore_jobs(app):
    users = load_users(DATA_FILE)

    for uid, data in users.items():
        reminder = data.get("reminder")  

        if not reminder or not reminder.get("enabled"):
            continue

        hour = reminder.get("hour")
        minute = reminder.get("minute")

        # Create the job
        app.job_queue.run_daily(
            send_reminder,
            time=time(hour=hour, minute=minute, tzinfo=pytz.timezone("Europe/London")),
            chat_id=int(uid),
            name=str(uid)
        )
        print(f"[restore] Restored reminder for user {uid} at {hour}:{minute:02d}")


if __name__ == '__main__':
    # Initialize bot application
    print("Starting bot...")
    app = Application.builder().token(BOT_API).build()

    restore_jobs(app)

    # Command and message handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help))
    app.add_handler(CommandHandler('schedule', schedule))
    app.add_handler(CommandHandler('currentaddress', current_address))
    app.add_handler(CommandHandler('onreminder', on_reminder))
    app.add_handler(CommandHandler('offreminder', off_reminder))
    app.add_handler(CommandHandler('statusreminder', status_reminder))
    # app.add_handler(CommandHandler('testrun', test_run))

    # Conversation handler for setting address
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('setaddress', set_address)],
        states={
            ASK_POSTCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_postcode)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(handle_error)

    # Start the bot
    print("Bot is running...")
    app.run_polling()