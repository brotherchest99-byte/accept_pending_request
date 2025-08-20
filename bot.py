# bot.py
# Main file for the Telegram Join Request Manager Bot

import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import datetime

# Import the keep_alive function
from keep_alive import keep_alive

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BotCommandScopeChat,
                           BotCommandScopeDefault, ChatJoinRequest,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message, ChatMemberUpdated)
from aiogram.utils.markdown import hbold, hitalic, hlink

# --- Configuration ---
from config import BOT_TOKEN, BOT_OWNER_ID, MAIN_CHANNEL_USERNAME, LOG_CHAT_ID

# --- Constants ---
DB_NAME = "bot_data.db"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- Bot and Dispatcher Initialization ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- Database Setup ---
def init_db():
    """Initializes the SQLite database and creates tables if they don't exist."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS managed_channels (
                    channel_id INTEGER PRIMARY KEY,
                    title TEXT,
                    added_by INTEGER NOT NULL,
                    add_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            logger.info("Database initialized successfully.")

# --- Database Helper Functions ---
def add_user_to_db(user_id: int, first_name: str, username: str | None):
    """Adds or updates a user in the database."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "INSERT OR REPLACE INTO users (user_id, first_name, username) VALUES (?, ?, ?)",
                    (user_id, first_name, username),
                )
                conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error while adding user {user_id}: {e}")

def add_managed_channel(channel_id: int, title: str, added_by: int):
    """Adds or updates a managed channel in the database."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "INSERT OR REPLACE INTO managed_channels (channel_id, title, added_by) VALUES (?, ?, ?)",
                    (channel_id, title, added_by),
                )
                conn.commit()
                logger.info(f"Channel {title} ({channel_id}) added/updated by user {added_by}.")
    except sqlite3.Error as e:
        logger.error(f"Database error while adding channel {channel_id}: {e}")

def remove_managed_channel(channel_id: int):
    """Removes a managed channel from the database."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("DELETE FROM managed_channels WHERE channel_id = ?", (channel_id,))
                conn.commit()
                logger.info(f"Channel {channel_id} removed from management.")
    except sqlite3.Error as e:
        logger.error(f"Database error while removing channel {channel_id}: {e}")

def get_channel_manager_id(channel_id: int) -> int | None:
    """Gets the ID of the user who added the bot to a specific channel."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT added_by FROM managed_channels WHERE channel_id = ?", (channel_id,))
                result = cursor.fetchone()
                return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Database error while fetching manager for channel {channel_id}: {e}")
        return None

def get_all_users() -> list[int]:
    """Retrieves all user IDs from the database."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT user_id FROM users")
                return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Database error while fetching all users: {e}")
        return []

def get_stats() -> tuple[int, int]:
    """Gets statistics (total users, total channels) from the database."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM managed_channels")
                channel_count = cursor.fetchone()[0]
                return user_count, channel_count
    except sqlite3.Error as e:
        logger.error(f"Database error while fetching stats: {e}")
        return 0, 0

def clear_database():
    """Deletes all data from the users and managed_channels tables."""
    try:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM managed_channels")
                conn.commit()
                logger.info("Database has been cleared by the owner.")
                return True
    except sqlite3.Error as e:
        logger.error(f"Database error while clearing tables: {e}")
        return False

# --- States for FSM ---
class BroadcastStates(StatesGroup):
    get_message = State()
    confirm_broadcast = State()

class OwnerMenuStates(StatesGroup):
    confirm_db_clear = State()

# --- Keyboards ---
def get_main_channel_join_kb():
    """Keyboard to prompt user to join the main channel."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Join Main Channel", url=f"https://t.me/{MAIN_CHANNEL_USERNAME}")],
        [InlineKeyboardButton(text="✅ I have joined!", callback_data="check_join_status")]
    ])

def get_owner_menu_kb():
    """Owner-only menu keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 Broadcast", callback_data="owner_broadcast"),
            InlineKeyboardButton(text="📊 Stats", callback_data="owner_stats")
        ],
        [
            InlineKeyboardButton(text="🗑 Clear Database", callback_data="owner_clear_db"),
            InlineKeyboardButton(text="⚙️ Channel Control", callback_data="owner_channels")
        ],
    ])
    
def get_add_bot_menu_kb():
    """Keyboard shown after a user joins the main channel."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Bot to Channel", callback_data="add_to_channel")],
        [InlineKeyboardButton(text="➕ Add Bot to Group", callback_data="add_to_group")],
    ])

# --- Helper Functions ---
async def is_user_in_main_channel(user_id: int) -> bool:
    """Checks if a user is a member of the main channel."""
    try:
        member = await bot.get_chat_member(f"@{MAIN_CHANNEL_USERNAME}", user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Error checking channel membership for {user_id}: {e}")
        return False

async def log_to_owner(message_text: str):
    """Sends a log message to the owner/log chat."""
    if LOG_CHAT_ID:
        try:
            await bot.send_message(LOG_CHAT_ID, f"🚨 BOT LOG\n\n{message_text}")
        except Exception as e:
            logger.error(f"Failed to send log to LOG_CHAT_ID: {e}")

# --- Bot Status Change Handler ---
@dp.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated):
    """Handles the bot being added to or removed from a channel."""
    # Check if the update is for a channel
    if update.chat.type not in ["channel"]:
        return

    new_member_status = update.new_chat_member.status
    
    # Bot is promoted to administrator
    if new_member_status == ChatMemberStatus.ADMINISTRATOR:
        # The user who promoted the bot is in `update.from_user`
        adder_id = update.from_user.id
        add_managed_channel(update.chat.id, update.chat.title, adder_id)
        try:
            await bot.send_message(adder_id, f"✅ Bot successfully added as admin to the channel '{hbold(update.chat.title)}'. It will now manage join requests for this channel.")
        except Exception as e:
            logger.error(f"Could not notify user {adder_id} about successful channel add: {e}")

    # Bot is removed or demoted
    elif new_member_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_managed_channel(update.chat.id)

# --- Command Handlers ---
@dp.message(CommandStart())
async def handle_start(message: Message, state: FSMContext):
    """Handles the /start command."""
    await state.clear()
    user = message.from_user
    add_user_to_db(user.id, user.first_name, user.username)

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    if not await is_user_in_main_channel(user.id):
        text = (
            f"👋 Welcome, {hbold(user.first_name)}!\n\n"
            "To unlock the full functionality of this bot, please join our main channel first. "
            "This helps us build a community and keep you updated.\n\n"
            "Once you've joined, click the button below."
        )
        await message.answer(text, reply_markup=get_main_channel_join_kb())
        return

    text = (
        f"🎉 Welcome back, {hbold(user.first_name)}!\n\n"
        "You're all set! You can now add this bot to your channels or groups to manage join requests.\n\n"
        "Select an option below to get started:"
    )
    await message.answer(text, reply_markup=get_add_bot_menu_kb())


@dp.message(Command("owner"))
async def handle_owner_menu(message: Message):
    """Displays the owner menu."""
    if str(message.from_user.id) != str(BOT_OWNER_ID):
        return
    await message.answer("🔑 Welcome, Owner! Accessing the control panel.", reply_markup=get_owner_menu_kb())


# --- Join Request Handler ---
@dp.chat_join_request()
async def handle_join_request(request: ChatJoinRequest):
    """Handles incoming join requests for any chat the bot is an admin in."""
    user = request.from_user
    chat = request.chat
    
    logger.info(f"Received join request from {user.full_name} (@{user.username}) for chat {chat.title} ({chat.id})")

    # 1. Notify the user that their request is pending
    try:
        await bot.send_message(
            user.id,
            f"⏳ Your join request for the channel '{hbold(chat.title)}' is pending admin approval. We’ll notify you soon."
        )
    except Exception as e:
        logger.error(f"Failed to send pending message to user {user.id}: {e}")
        await log_to_owner(f"Could not DM user {user.id} ({user.full_name}). They may have blocked the bot.")

    # 2. Notify the channel manager and the bot owner
    manager_id = get_channel_manager_id(chat.id)
    if not manager_id:
        logger.warning(f"Could not find a manager for channel {chat.id} in the database.")
        await log_to_owner(f"⚠️ No manager found for channel {chat.title} ({chat.id}). Cannot process join request for {user.full_name}.")
        return

    admin_notification_text = (
        f"🔔 New Join Request for {hbold(chat.title)}\n\n"
        f"👤 User: {hbold(user.full_name)}\n"
        f"🔗 Profile: {hlink(f'@{user.username}' if user.username else 'Link', f'tg://user?id={user.id}')}"
    )
    
    approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{chat.id}_{user.id}"),
            InlineKeyboardButton(text="❌ Decline", callback_data=f"decline_{chat.id}_{user.id}")
        ]
    ])
    
    # Send notification to the channel manager
    try:
        await bot.send_message(manager_id, admin_notification_text, reply_markup=approval_keyboard)
    except Exception as e:
        logger.error(f"Failed to send join request notification to manager {manager_id}: {e}")

    # Also send to bot owner if the owner is not the manager
    if str(manager_id) != str(BOT_OWNER_ID):
        try:
            await bot.send_message(BOT_OWNER_ID, admin_notification_text, reply_markup=approval_keyboard)
        except Exception as e:
            logger.error(f"Failed to send join request notification to owner: {e}")


# --- Callback Query Handlers for Join Requests ---
@dp.callback_query(F.data.startswith("approve_"))
async def handle_approve_request(callback_query: types.CallbackQuery):
    """Handles the 'Approve' button click."""
    _, chat_id_str, user_id_str = callback_query.data.split("_")
    chat_id, user_id = int(chat_id_str), int(user_id_str)

    manager_id = get_channel_manager_id(chat_id)
    
    # Check if the user is either the bot owner or the channel manager
    allowed_ids = {str(BOT_OWNER_ID)}
    if manager_id:
        allowed_ids.add(str(manager_id))

    if str(callback_query.from_user.id) not in allowed_ids:
        await callback_query.answer("⚠️ This action is restricted to the channel manager or bot owner.", show_alert=True)
        return

    await callback_query.answer("Processing approval...")
    
    try:
        await bot.approve_chat_join_request(chat_id, user_id)
        
        chat = await bot.get_chat(chat_id)
        user = await bot.get_chat(user_id)

        await bot.send_message(user_id, f"🎉 Approved! Welcome to {hbold(chat.title)}!")

        await callback_query.message.edit_text(
            f"✅ Request from {hbold(user.full_name)} for {hbold(chat.title)} was approved by {callback_query.from_user.full_name}."
        )
        logger.info(f"Approved join request for user {user_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"Error approving join request: {e}")
        await callback_query.message.edit_text(f"❌ Error approving request: {e}")
        await log_to_owner(f"Failed to approve user. Error: {e}")


@dp.callback_query(F.data.startswith("decline_"))
async def handle_decline_request(callback_query: types.CallbackQuery):
    """Handles the 'Decline' button click."""
    _, chat_id_str, user_id_str = callback_query.data.split("_")
    chat_id, user_id = int(chat_id_str), int(user_id_str)

    manager_id = get_channel_manager_id(chat_id)

    # Check if the user is either the bot owner or the channel manager
    allowed_ids = {str(BOT_OWNER_ID)}
    if manager_id:
        allowed_ids.add(str(manager_id))

    if str(callback_query.from_user.id) not in allowed_ids:
        await callback_query.answer("⚠️ This action is restricted to the channel manager or bot owner.", show_alert=True)
        return

    await callback_query.answer("Processing decline...")

    try:
        await bot.decline_chat_join_request(chat_id, user_id)
        
        chat = await bot.get_chat(chat_id)
        user = await bot.get_chat(user_id)

        await bot.send_message(user_id, f"❌ Your join request for {hbold(chat.title)} was declined.")
        
        await callback_query.message.edit_text(
            f"❌ Request from {hbold(user.full_name)} for {hbold(chat.title)} was declined by {callback_query.from_user.full_name}."
        )
        logger.info(f"Declined join request for user {user_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"Error declining join request: {e}")
        await callback_query.message.edit_text(f"❌ Error declining request: {e}")
        await log_to_owner(f"Failed to decline user. Error: {e}")


# --- Callback Query Handlers for Menus ---
@dp.callback_query(F.data == "check_join_status")
async def handle_check_join_status(callback_query: types.CallbackQuery):
    """Handles the 'I have joined' button click to re-check membership."""
    user = callback_query.from_user
    await callback_query.answer("Checking your membership status...")

    if await is_user_in_main_channel(user.id):
        text = (
            f"🎉 Great, you're in! Welcome, {hbold(user.first_name)}!\n\n"
            "You can now add this bot to your channels or groups to manage join requests.\n\n"
            "Select an option below to get started:"
        )
        await callback_query.message.edit_text(text, reply_markup=get_add_bot_menu_kb())
    else:
        await callback_query.answer(
            "⚠️ You don't seem to be a member of the main channel yet. Please join and then click the button again.",
            show_alert=True
        )

@dp.callback_query(F.data == "back_to_add_menu")
async def handle_back_to_add_menu(callback_query: types.CallbackQuery):
    """Handles the 'Back' button, returning to the add bot menu."""
    user = callback_query.from_user
    text = (
        f"🎉 Welcome back, {hbold(user.first_name)}!\n\n"
        "You're all set! You can now add this bot to your channels or groups to manage join requests.\n\n"
        "Select an option below to get started:"
    )
    await callback_query.message.edit_text(text, reply_markup=get_add_bot_menu_kb())
    await callback_query.answer()


@dp.callback_query(F.data == "add_to_channel")
async def handle_add_to_channel(callback_query: types.CallbackQuery):
    """Provides instructions for adding the bot to a channel."""
    bot_username = (await bot.get_me()).username
    text = (
        "⚙️ To add this bot to your channel:\n\n"
        "1. Open your channel settings.\n"
        "2. Go to `Administrators` -> `Add Admin`.\n"
        f"3. Search for `@{bot_username}` and select it.\n"
        "4. Grant the `Invite Users via Link` permission.\n"
        "5. That's it! The bot will now manage join requests."
    )
    deep_link_url = f"https://t.me/{bot_username}?startchannel&admin=invite_users"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add to Channel Now", url=deep_link_url)],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_add_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=keyboard)
    await callback_query.answer()

@dp.callback_query(F.data == "add_to_group")
async def handle_add_to_group(callback_query: types.CallbackQuery):
    """Provides instructions for adding the bot to a group as an admin."""
    bot_username = (await bot.get_me()).username
    text = (
        "⚙️ To add this bot to your group as an admin:\n\n"
        "1. Open your group settings.\n"
        "2. Go to `Administrators` -> `Add Admin`.\n"
        f"3. Search for `@{bot_username}` and select it.\n"
        "4. Grant the `Invite Users via Link` permission. This is required to manage join requests.\n"
        "5. Click the button below for a direct link to start the process."
    )
    # This deep link will prompt the user to select a group and grant the specified admin rights.
    deep_link_url = f"https://t.me/{bot_username}?startgroup=true&admin=invite_users"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add to Group as Admin", url=deep_link_url)],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_add_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=keyboard)
    await callback_query.answer()
    
# --- Owner Menu Handlers ---
@dp.callback_query(F.data == "owner_stats")
async def owner_stats(callback_query: types.CallbackQuery):
    """Displays bot statistics to the owner."""
    if str(callback_query.from_user.id) != str(BOT_OWNER_ID):
        await callback_query.answer("Access denied.", show_alert=True)
        return
        
    await callback_query.answer("Fetching stats...")
    user_count, channel_count = get_stats()
    
    text = (
        "📊 Bot Statistics\n\n"
        f"👥 Total Users: {hbold(user_count)}\n"
        f"📢 Managed Channels: {hbold(channel_count)}"
    )
    await callback_query.message.edit_text(text, reply_markup=get_owner_menu_kb())

@dp.callback_query(F.data == "owner_clear_db")
async def owner_clear_db_confirm(callback_query: types.CallbackQuery, state: FSMContext):
    """Asks for confirmation before clearing the database."""
    if str(callback_query.from_user.id) != str(BOT_OWNER_ID):
        return
        
    await state.set_state(OwnerMenuStates.confirm_db_clear)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❗️ YES, DELETE ALL DATA", callback_data="confirm_db_delete"),
            InlineKeyboardButton(text="CANCEL", callback_data="cancel_db_delete")
        ]
    ])
    await callback_query.message.edit_text(
        "⚠️ Are you absolutely sure you want to clear the database? This will delete all user and channel data. This action is irreversible.",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query(F.data == "cancel_db_delete", OwnerMenuStates.confirm_db_clear)
async def cancel_db_clear(callback_query: types.CallbackQuery, state: FSMContext):
    """Cancels the database clearing process."""
    await state.clear()
    await callback_query.message.edit_text("Database clearing operation cancelled.", reply_markup=get_owner_menu_kb())
    await callback_query.answer()

@dp.callback_query(F.data == "confirm_db_delete", OwnerMenuStates.confirm_db_clear)
async def process_db_clear(callback_query: types.CallbackQuery, state: FSMContext):
    """Proceeds with clearing the database."""
    await state.clear()
    if clear_database():
        await callback_query.message.edit_text("🗑 Database cleared successfully.", reply_markup=get_owner_menu_kb())
    else:
        await callback_query.message.edit_text("❌ Failed to clear the database. Check logs.", reply_markup=get_owner_menu_kb())
    await callback_query.answer()

# --- Broadcast Feature ---
@dp.callback_query(F.data == "owner_broadcast")
async def start_broadcast(callback_query: types.CallbackQuery, state: FSMContext):
    """Starts the broadcast process."""
    if str(callback_query.from_user.id) != str(BOT_OWNER_ID):
        return
    await state.set_state(BroadcastStates.get_message)
    await callback_query.message.edit_text("Please send the message you want to broadcast. You can use HTML formatting. Send /cancel to abort.")
    await callback_query.answer()

@dp.message(Command("cancel"), BroadcastStates.get_message)
async def cancel_broadcast(message: Message, state: FSMContext):
    """Cancels the broadcast at the message input stage."""
    await state.clear()
    await message.answer("Broadcast cancelled.", reply_markup=get_owner_menu_kb())

@dp.message(BroadcastStates.get_message)
async def get_broadcast_message(message: Message, state: FSMContext):
    """Receives the broadcast message and asks for confirmation."""
    await state.update_data(broadcast_message=message.html_text)
    await state.set_state(BroadcastStates.confirm_broadcast)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 SEND BROADCAST", callback_data="send_broadcast"),
            InlineKeyboardButton(text="❌ CANCEL", callback_data="cancel_broadcast_confirm")
        ]
    ])
    await message.answer("Here is a preview of your broadcast message. Are you sure you want to send it to all users?", reply_markup=keyboard)
    await bot.send_message(message.chat.id, message.html_text) # Send preview

@dp.callback_query(F.data == "cancel_broadcast_confirm", BroadcastStates.confirm_broadcast)
async def cancel_broadcast_confirm(callback_query: types.CallbackQuery, state: FSMContext):
    """Cancels the broadcast at the confirmation stage."""
    await state.clear()
    await callback_query.message.edit_text("Broadcast cancelled.", reply_markup=get_owner_menu_kb())
    await callback_query.answer()

@dp.callback_query(F.data == "send_broadcast", BroadcastStates.confirm_broadcast)
async def process_broadcast(callback_query: types.CallbackQuery, state: FSMContext):
    """Sends the broadcast message to all users."""
    data = await state.get_data()
    message_text = data.get("broadcast_message")
    await state.clear()

    await callback_query.message.edit_text("📢 Starting broadcast...")
    
    users = get_all_users()
    if not users:
        await callback_query.message.edit_text("No users in the database to broadcast to.", reply_markup=get_owner_menu_kb())
        return

    success_count = 0
    fail_count = 0
    
    for user_id in users:
        try:
            await bot.send_message(user_id, message_text)
            success_count += 1
            await asyncio.sleep(0.1) # Avoid hitting API limits
        except Exception as e:
            fail_count += 1
            logger.warning(f"Failed to send broadcast to {user_id}: {e}")

    result_text = (
        f"📢 Broadcast finished.\n\n"
        f"✅ Sent successfully to {success_count} users.\n"
        f"❌ Failed to send to {fail_count} users (likely blocked the bot)."
    )
    await callback_query.message.edit_text(result_text, reply_markup=get_owner_menu_kb())
    await log_to_owner(result_text)

# --- Bot Startup and Shutdown ---
async def set_bot_commands():
    """Sets the bot commands that appear in the Telegram menu."""
    # Commands for all users
    default_commands = [
        BotCommand(command="start", description="🚀 Start or restart the bot"),
    ]
    await bot.set_my_commands(default_commands, BotCommandScopeDefault())

    # Commands for the bot owner only
    owner_commands = [
        BotCommand(command="start", description="🚀 Start or restart the bot"),
        BotCommand(command="owner", description="🔑 Access owner control panel"),
    ]
    # The BOT_OWNER_ID from config is a string, but the scope requires an integer
    await bot.set_my_commands(owner_commands, BotCommandScopeChat(chat_id=int(BOT_OWNER_ID)))

async def main():
    """Main function to start the bot."""
    init_db()
    await set_bot_commands()
    logger.info("Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        # Start the web server to keep the bot alive
        keep_alive()
        # Start the bot
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
