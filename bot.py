"""
Telegram Bot for PDF Form Filling
Receives PDF files, asks which profile to use, fills the form using AI,
and returns the filled PDF.
"""

import os
import sys
import json
import logging
import tempfile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from pdf_filler import fill_pdf, PROFILES

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Bot token from environment variable
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Allowed users - only these Telegram user IDs can use the bot
# Set via environment variable ALLOWED_USERS as comma-separated IDs
# If empty, bot is open to everyone
ALLOWED_USERS_STR = os.environ.get("ALLOWED_USERS", "256157841")
ALLOWED_USERS = set()
if ALLOWED_USERS_STR:
    ALLOWED_USERS = {int(uid.strip()) for uid in ALLOWED_USERS_STR.split(",") if uid.strip()}

# Optional: Map Telegram user IDs to profiles for auto-detection
# Format: {"user_id": "profile_name"}
# Set via environment variable USER_PROFILE_MAP as JSON string
USER_PROFILE_MAP = {}
try:
    map_str = os.environ.get("USER_PROFILE_MAP", "{}")
    USER_PROFILE_MAP = json.loads(map_str)
except (json.JSONDecodeError, TypeError):
    pass


async def check_access(update: Update) -> bool:
    """Check if user is allowed to use the bot."""
    if not ALLOWED_USERS:
        return True
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ אין לך גישה לבוט זה.")
        logger.warning(f"Unauthorized access attempt by user {user_id}")
        return False
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not await check_access(update):
        return
    welcome_text = (
        "שלום! 👋\n\n"
        "אני בוט למילוי טפסי PDF אוטומטי.\n\n"
        "📄 שלח/י לי קובץ PDF ואני אמלא אותו עם הפרטים האישיים.\n\n"
        "הפרופילים הזמינים:\n"
        "• אינה ברזק\n"
        "• אריק ברזק\n\n"
        "פשוט שלח/י קובץ PDF להתחלה!"
    )
    await update.message.reply_text(welcome_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not await check_access(update):
        return
    help_text = (
        "📋 איך להשתמש בבוט:\n\n"
        "1. שלח/י קובץ PDF (טופס עם שדות ריקים)\n"
        "2. בחר/י את הפרופיל למילוי (אינה או אריק)\n"
        "3. המתן/י - הבוט ימלא את הטופס אוטומטית\n"
        "4. קבל/י את הקובץ הממולא בחזרה\n\n"
        "⚠️ שים/י לב:\n"
        "• הבוט לא ממלא פרטי כרטיס אשראי/תשלום\n"
        "• הבוט עובד עם טפסים בעברית\n"
        "• זמן העיבוד: 30-60 שניות בממוצע"
    )
    await update.message.reply_text(help_text)


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle received PDF files."""
    if not await check_access(update):
        return
    document = update.message.document
    
    # Verify it's a PDF
    if document.mime_type != "application/pdf" and not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ אנא שלח/י קובץ PDF בלבד.")
        return
    
    # Store file info in user context for later use
    context.user_data["pdf_file_id"] = document.file_id
    context.user_data["pdf_file_name"] = document.file_name
    
    # Check if we can auto-detect the profile
    user_id = str(update.effective_user.id)
    if user_id in USER_PROFILE_MAP:
        profile_name = USER_PROFILE_MAP[user_id]
        if profile_name in PROFILES:
            await update.message.reply_text(
                f"🔄 זיהיתי אותך! ממלא עבור: {profile_name}\n"
                f"⏳ מעבד את הטופס... אנא המתן/י."
            )
            await process_pdf(update, context, profile_name)
            return
    
    # Ask which profile to use
    keyboard = [
        [
            InlineKeyboardButton("אינה", callback_data="profile_אינה"),
            InlineKeyboardButton("אריק", callback_data="profile_אריק"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "למלא בשביל מי? אינה או אריק?",
        reply_markup=reply_markup,
    )


async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle profile selection callback."""
    query = update.callback_query
    await query.answer()
    
    # Extract profile name from callback data
    profile_name = query.data.replace("profile_", "")
    
    if profile_name not in PROFILES:
        await query.edit_message_text("❌ פרופיל לא נמצא.")
        return
    
    if "pdf_file_id" not in context.user_data:
        await query.edit_message_text("❌ לא נמצא קובץ PDF. אנא שלח/י קובץ מחדש.")
        return
    
    await query.edit_message_text(
        f"✅ ממלא עבור: {profile_name}\n"
        f"⏳ מעבד את הטופס... אנא המתן/י (30-60 שניות)."
    )
    
    await process_pdf(update, context, profile_name)


async def process_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE, profile_name: str) -> None:
    """Download, process, and return the filled PDF."""
    # Determine the chat to reply to
    if update.callback_query:
        chat = update.callback_query.message.chat
    else:
        chat = update.message.chat
    
    try:
        # Download the PDF file
        file_id = context.user_data["pdf_file_id"]
        file_name = context.user_data.get("pdf_file_name", "form.pdf")
        
        file = await context.bot.get_file(file_id)
        
        # Create temp directory for processing
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = os.path.join(tmp_dir, file_name)
            await file.download_to_drive(input_path)
            
            logger.info(f"Processing PDF: {file_name} for profile: {profile_name}")
            
            # Fill the PDF
            output_path = fill_pdf(input_path, profile_name)
            
            if output_path and os.path.exists(output_path):
                # Send the filled PDF back
                output_filename = f"filled_{profile_name}_{file_name}"
                with open(output_path, "rb") as f:
                    await chat.send_document(
                        document=f,
                        filename=output_filename,
                        caption=f"✅ הטופס מולא בהצלחה עבור {profile_name}!",
                    )
                logger.info(f"Successfully sent filled PDF: {output_filename}")
            else:
                await chat.send_message(
                    "❌ שגיאה במילוי הטופס. אנא נסה/י שוב."
                )
    
    except Exception as e:
        logger.error(f"Error processing PDF: {e}", exc_info=True)
        await chat.send_message(
            f"❌ שגיאה בעיבוד הקובץ: {str(e)}\n"
            "אנא נסה/י שוב או פנה/י לתמיכה."
        )
    
    finally:
        # Clean up user data
        context.user_data.pop("pdf_file_id", None)
        context.user_data.pop("pdf_file_name", None)


async def handle_non_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle non-PDF messages."""
    if not await check_access(update):
        return
    await update.message.reply_text(
        "📄 אנא שלח/י קובץ PDF למילוי.\n"
        "לעזרה, שלח/י /help"
    )


def main() -> None:
    """Start the bot."""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error(
            "Bot token not configured! Set TELEGRAM_BOT_TOKEN environment variable."
        )
        sys.exit(1)
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Handle PDF documents
    application.add_handler(
        MessageHandler(filters.Document.PDF, handle_pdf)
    )
    
    # Handle profile selection
    application.add_handler(
        CallbackQueryHandler(profile_callback, pattern=r"^profile_")
    )
    
    # Handle other documents (non-PDF)
    application.add_handler(
        MessageHandler(filters.Document.ALL & ~filters.Document.PDF, handle_non_pdf)
    )
    
    # Handle text messages
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_non_pdf)
    )
    
    # Start the bot
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
