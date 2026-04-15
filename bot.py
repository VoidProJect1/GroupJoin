"""
Advanced Multi-Session Telegram Bot
Uses python-telegram-bot + Telethon
All InlineKeyboard → ReplyKeyboard | All bugs fixed
Pydroid3 compatible (asyncio.run)
"""

import os, json, asyncio, logging, hashlib, random, re
from datetime import datetime
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ChatAction, ParseMode

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    FloodWaitError, UserAlreadyParticipantError,
    InviteHashInvalidError, ChannelPrivateError
)

# ── aiohttp (optional – only needed for Join Folder) ──────────────
try:
    import aiohttp as _aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

# ──────────────────────────── CONFIG ──────────────────────────────
BOT_TOKEN       = "8134839444:AAF5oQEQsPYH0gp9iMCSWsfP0RoY6I5ZUqc"
DATA_FILE       = "data.json"
_USER_PASS_HASH = hashlib.sha256("Void#123".encode()).hexdigest()
_MASTER_HASH    = hashlib.sha256("VoidProject#000".encode()).hexdigest()

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ────────────────────── CONVERSATION STATES ───────────────────────
(
    WAIT_PASSWORD, WAIT_NEW_PASS, WAIT_CONFIRM_PASS,
    WAIT_API_ID, WAIT_API_HASH, WAIT_PHONE, WAIT_OTP, WAIT_2FA,
    WAIT_GROUP_LINK, WAIT_FOLDER_LINK
) = range(10)

# ──────────────────────────── DATA ────────────────────────────────
def default_data() -> dict:
    return {
        "admins":   [],
        "sessions": {},
        "password": _USER_PASS_HASH,
        "stats":    {"joins": 0, "errors": 0}
    }

def load_data() -> dict:
    base = default_data()
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                raw = json.load(f)
            for k, v in base.items():
                if k not in raw:
                    raw[k] = v
            raw.setdefault("stats", {}).setdefault("joins", 0)
            raw.setdefault("stats", {}).setdefault("errors", 0)
            if not isinstance(raw["admins"], list):
                raw["admins"] = []
            if not isinstance(raw["sessions"], dict):
                raw["sessions"] = {}
            return raw
        except Exception:
            pass
    return base

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def is_admin(uid: int, data: dict) -> bool:
    return uid in data.get("admins", [])

def is_master(pwd: str) -> bool:
    return hashlib.sha256(pwd.encode()).hexdigest() == _MASTER_HASH

def check_password(pwd: str, data: dict) -> bool:
    stored = data.get("password", _USER_PASS_HASH)
    return hashlib.sha256(pwd.encode()).hexdigest() == stored

# ──────────────────────────── UI HELPERS ──────────────────────────
def main_keyboard(master: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("➕ Add Session"),     KeyboardButton("📋 My Sessions")],
        [KeyboardButton("👥 Join Group"),       KeyboardButton("📁 Join Folder")],
        [KeyboardButton("📊 Statistics"),       KeyboardButton("🔑 Change Password")],
    ]
    if master:
        rows.append([KeyboardButton("⚙️ Master Panel [DEV]")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🏠 Back to Menu")]],
        resize_keyboard=True
    )

def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

def progress_bar(pct: int, width: int = 16) -> str:
    pct    = max(0, min(100, pct))
    filled = int(width * pct / 100)
    return f"[{'█'*filled}{'░'*(width-filled)}] {pct}%"

async def typing(ctx: ContextTypes.DEFAULT_TYPE, cid: int, secs: float = 0.9):
    try:
        await ctx.bot.send_chat_action(cid, ChatAction.TYPING)
        await asyncio.sleep(secs)
    except Exception:
        pass

# ──────────────────────── TELETHON HELPERS ────────────────────────
_clients: dict = {}

def get_client(session_str: str, api_id: int, api_hash: str) -> TelegramClient:
    key = hashlib.md5(session_str.encode()).hexdigest()[:16]
    if key not in _clients:
        _clients[key] = TelegramClient(StringSession(session_str), api_id, api_hash)
    return _clients[key]

async def ensure_connected(client: TelegramClient):
    if not client.is_connected():
        await client.connect()

async def join_single(client: TelegramClient, link: str) -> tuple:
    try:
        await ensure_connected(client)
        link = link.strip().rstrip("/")
        if re.search(r"t\.me/\+", link) or "joinchat/" in link:
            hash_part = re.split(r"joinchat/|\+", link)[-1]
            await client(ImportChatInviteRequest(hash_part))
        else:
            username = link.split("/")[-1].lstrip("@")
            await client(JoinChannelRequest(username))
        return True, "✅ Joined"
    except UserAlreadyParticipantError:
        return True, "⚠️ Already member"
    except FloodWaitError as e:
        return False, f"⏳ Flood {e.seconds}s"
    except InviteHashInvalidError:
        return False, "❌ Invalid link"
    except ChannelPrivateError:
        return False, "❌ Private channel"
    except Exception as ex:
        return False, f"❌ {type(ex).__name__}: {str(ex)[:50]}"

async def get_folder_links(client: TelegramClient, folder_link: str) -> list:
    # FIX: graceful error if aiohttp is not installed
    if not AIOHTTP_OK:
        log.warning("aiohttp not installed. Run: pip install aiohttp")
        return []
    links = []
    try:
        slug = folder_link.strip().rstrip("/").split("/")[-1]
        async with _aiohttp.ClientSession() as s:
            async with s.get(
                f"https://t.me/addlist/{slug}",
                headers={"User-Agent": "Mozilla/5.0"}
            ) as resp:
                html = await resp.text()
        found = re.findall(r'href="(https://t\.me/[^"]+)"', html)
        links = list({u for u in found if "/addlist/" not in u})
    except Exception as ex:
        log.warning(f"Folder parse error: {ex}")
    return links

# ────────────────────────── /start ────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    data = load_data()
    await typing(ctx, uid, 0.7)

    if is_admin(uid, data):
        master_flag = ctx.user_data.get("master", False)
        await update.message.reply_text(
            f"🤖 <b>VoidBot – Multi Session Manager</b>\n\n"
            f"👋 Welcome back, <b>{update.effective_user.first_name}</b>!\n"
            f"🕐 <i>{datetime.now().strftime('%d %b %Y  %H:%M')}</i>\n\n"
            "Choose an option:",
            reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🔒 <b>VoidBot</b> – Restricted Access\n\nEnter your password to continue:",
        reply_markup=remove_kb(),
        parse_mode=ParseMode.HTML
    )
    return WAIT_PASSWORD

# ──────────────────────── PASSWORD ENTRY ──────────────────────────
async def handle_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    pwd  = update.message.text.strip()
    data = load_data()

    try:
        await update.message.delete()
    except Exception:
        pass

    # ── Master check ──────────────────────────────────────────────
    if is_master(pwd):
        ctx.user_data["master"] = True
        admins = data["admins"]
        if uid not in admins:
            admins.append(uid)
            data["admins"] = admins
            save_data(data)
        await typing(ctx, uid, 0.5)
        await update.message.reply_text(
            "⚙️ <b>Master Access Granted</b>\n\nWelcome, Developer.",
            reply_markup=main_keyboard(True),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # ── Normal password ───────────────────────────────────────────
    if check_password(pwd, data):
        admins = data["admins"]
        if uid not in admins:
            admins.append(uid)
            data["admins"] = admins
            save_data(data)
        await typing(ctx, uid, 0.5)
        await update.message.reply_text(
            f"✅ <b>Access Granted</b>\n\nHello, <b>{update.effective_user.first_name}</b>! 👋",
            reply_markup=main_keyboard(False),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await update.message.reply_text("❌ Incorrect password. Try again:")
    return WAIT_PASSWORD

# ───────────── ENTRY POINTS FOR MENU BUTTONS (THE KEY FIX) ───────
# These are registered as ConversationHandler entry_points so that
# pressing keyboard buttons AFTER the conversation ended correctly
# re-enters the conversation and activates the right state.

async def _require_admin(update: Update, data: dict) -> bool:
    """Returns True if user is admin, else sends error and returns False."""
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text(
            "🔒 Please use /start and authenticate first.",
            reply_markup=remove_kb()
        )
        return False
    return True

async def entry_add_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point: ➕ Add Session button."""
    data = load_data()
    if not await _require_admin(update, data):
        return ConversationHandler.END
    ctx.user_data["add_sess"] = {}
    await update.message.reply_text(
        "➕ <b>Add New Session</b>\n\n"
        "<b>Step 1 / 4</b> – Enter your <b>API ID</b>\n"
        "<i>Get it from: my.telegram.org → App API</i>",
        reply_markup=remove_kb(),
        parse_mode=ParseMode.HTML
    )
    return WAIT_API_ID

async def entry_join_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point: 👥 Join Group button."""
    data = load_data()
    if not await _require_admin(update, data):
        return ConversationHandler.END
    if not data.get("sessions"):
        await update.message.reply_text("⚠️ No sessions. Add one first.", reply_markup=back_kb())
        return ConversationHandler.END
    await update.message.reply_text(
        "👥 <b>Join Group / Channel</b>\n\nSend the link:\n\n"
        "<i>Public:</i>  <code>https://t.me/username</code>\n"
        "<i>Private:</i> <code>https://t.me/+inviteHash</code>",
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML
    )
    return WAIT_GROUP_LINK

async def entry_join_folder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point: 📁 Join Folder button."""
    data = load_data()
    if not await _require_admin(update, data):
        return ConversationHandler.END
    if not data.get("sessions"):
        await update.message.reply_text("⚠️ No sessions. Add one first.", reply_markup=back_kb())
        return ConversationHandler.END
    if not AIOHTTP_OK:
        await update.message.reply_text(
            "⚠️ <b>aiohttp not installed.</b>\n\nRun: <code>pip install aiohttp</code> and restart.",
            reply_markup=back_kb(), parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "📁 <b>Join Folder</b>\n\nSend the folder link:\n"
        "<i>Example: https://t.me/addlist/xxxxxxxxx</i>\n\n"
        "All groups inside will be joined by all sessions.",
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML
    )
    return WAIT_FOLDER_LINK

async def entry_change_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point: 🔑 Change Password button."""
    data = load_data()
    if not await _require_admin(update, data):
        return ConversationHandler.END
    await update.message.reply_text(
        "🔑 Enter your <b>new password</b>:",
        reply_markup=remove_kb(),
        parse_mode=ParseMode.HTML
    )
    return WAIT_NEW_PASS

# ──────────────────── MAIN MENU MESSAGE HANDLER ───────────────────
# Handles non-flow buttons (Stats, My Sessions, Master Panel, etc.)
async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    data = load_data()
    text = update.message.text.strip()

    if not is_admin(uid, data):
        await update.message.reply_text(
            "🔒 Please use /start and authenticate first.",
            reply_markup=remove_kb()
        )
        return

    # ── Back to menu ──────────────────────────────────────────────
    if text == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option:",
            reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return

    # ── Stats ─────────────────────────────────────────────────────
    if text == "📊 Statistics":
        s         = data.get("stats", {"joins": 0, "errors": 0})
        total_ops = s.get("joins", 0) + s.get("errors", 0)
        rate      = f"{int(s['joins']/total_ops*100)}%" if total_ops else "N/A"
        await update.message.reply_text(
            "📊 <b>Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Sessions:        <b>{len(data.get('sessions', {}))}</b>\n"
            f"✅ Joins:           <b>{s.get('joins', 0)}</b>\n"
            f"❌ Errors:          <b>{s.get('errors', 0)}</b>\n"
            f"📈 Success rate:    <b>{rate}</b>\n"
            f"👥 Admins:          <b>{len(data.get('admins', []))}</b>\n"
            f"🕐 At: {datetime.now().strftime('%H:%M:%S')}",
            reply_markup=back_kb(), parse_mode=ParseMode.HTML
        )
        return

    # ── List sessions ─────────────────────────────────────────────
    if text == "📋 My Sessions":
        sessions = data.get("sessions", {})
        if not sessions:
            await update.message.reply_text(
                "📋 <b>No sessions yet.</b>\n\nUse ➕ Add Session to get started.",
                reply_markup=back_kb(), parse_mode=ParseMode.HTML
            )
            return
        lines = [f"📋 <b>Sessions ({len(sessions)})</b>\n━━━━━━━━━━━━━━━━━"]
        for i, (sid, info) in enumerate(sessions.items(), 1):
            lines.append(
                f"{i}. 📱 <code>{info.get('phone','?')}</code>  "
                f"👤 {info.get('name','?')}  📅 {info.get('added','?')}"
            )
        remove_rows = [
            [KeyboardButton(f"🗑 Remove {v.get('phone','?')}")]
            for k, v in sessions.items()
        ]
        remove_rows.append([KeyboardButton("🏠 Back to Menu")])
        ctx.user_data["session_map"] = {
            f"🗑 Remove {v.get('phone','?')}": k
            for k, v in sessions.items()
        }
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=ReplyKeyboardMarkup(remove_rows, resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return

    # ── Delete session via keyboard button ────────────────────────
    if text.startswith("🗑 Remove "):
        session_map = ctx.user_data.get("session_map", {})
        sid = session_map.get(text)
        if sid:
            sessions = data.get("sessions", {})
            phone    = sessions.get(sid, {}).get("phone", sid)
            sessions.pop(sid, None)
            data["sessions"] = sessions
            save_data(data)
            ctx.user_data.pop("session_map", None)
            await update.message.reply_text(
                f"🗑 Session <code>{phone}</code> removed.",
                reply_markup=back_kb(), parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "⚠️ Session not found.", reply_markup=back_kb()
            )
        return

    # ── Master panel ──────────────────────────────────────────────
    if text == "⚙️ Master Panel [DEV]":
        if not ctx.user_data.get("master"):
            await update.message.reply_text("⛔ Access denied.")
            return
        s = data.get("stats", {})
        await update.message.reply_text(
            "⚙️ <b>MASTER DEVELOPER PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Admins:    <b>{len(data.get('admins', []))}</b>\n"
            f"📱 Sessions:  <b>{len(data.get('sessions', {}))}</b>\n"
            f"✅ Joins:     <b>{s.get('joins', 0)}</b>\n"
            f"❌ Errors:    <b>{s.get('errors', 0)}</b>\n"
            f"🔑 Pass hash: <code>{data.get('password','')[:20]}…</code>\n\n"
            "<i>⚠️ This panel is invisible to regular admins.</i>",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📋 List Admin IDs")],
                 [KeyboardButton("🏠 Back to Menu")]],
                resize_keyboard=True
            ),
            parse_mode=ParseMode.HTML
        )
        return

    if text == "📋 List Admin IDs":
        if not ctx.user_data.get("master"):
            await update.message.reply_text("⛔ Access denied.")
            return
        admins = data.get("admins", [])
        lines  = ["👥 <b>Admin IDs</b>\n━━━━━━━━━━━━━"] + [f"• <code>{a}</code>" for a in admins]
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=back_kb(), parse_mode=ParseMode.HTML
        )
        return

# ────────────────────── ADD SESSION FLOW ──────────────────────────
async def got_api_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        ctx.user_data.pop("add_sess", None)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option:",
            reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    raw = update.message.text.strip()
    try:
        api_id = int(raw)
        if api_id <= 0:
            raise ValueError("must be positive")
    except ValueError:
        await update.message.reply_text(
            f"❌ <b>Invalid API ID</b>\n\n"
            f"You entered: <code>{raw}</code>\n"
            f"API ID must be a <b>positive number</b> (e.g. <code>12345678</code>).\n\n"
            f"Get it from: my.telegram.org → App API\n\nTry again:",
            parse_mode=ParseMode.HTML,
            reply_markup=remove_kb()
        )
        return WAIT_API_ID

    ctx.user_data.setdefault("add_sess", {})["api_id"] = api_id
    await typing(ctx, update.effective_chat.id, 0.4)
    await update.message.reply_text(
        f"✅ API ID saved: <code>{api_id}</code>\n\n"
        f"<b>Step 2 / 4</b> – Enter your <b>API Hash</b>:\n"
        f"<i>(32-character hex string from my.telegram.org)</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=remove_kb()
    )
    return WAIT_API_HASH

async def got_api_hash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        ctx.user_data.pop("add_sess", None)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>", reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    api_hash = update.message.text.strip()
    if len(api_hash) != 32 or not re.fullmatch(r"[a-fA-F0-9]+", api_hash):
        await update.message.reply_text(
            "❌ <b>Invalid API Hash</b>\n\n"
            "It must be a 32-character hex string.\n"
            "Get it from: my.telegram.org → App API\n\nTry again:",
            parse_mode=ParseMode.HTML,
            reply_markup=remove_kb()
        )
        return WAIT_API_HASH

    ctx.user_data.setdefault("add_sess", {})["api_hash"] = api_hash
    await typing(ctx, update.effective_chat.id, 0.4)
    await update.message.reply_text(
        "✅ API Hash saved.\n\n<b>Step 3 / 4</b> – Enter your <b>Phone Number</b>:\n"
        "<i>Include country code, e.g. +91xxxxxxxxxx</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=remove_kb()
    )
    return WAIT_PHONE

async def got_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        ctx.user_data.pop("add_sess", None)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>", reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    phone    = update.message.text.strip()
    sess     = ctx.user_data.setdefault("add_sess", {})

    if "api_id" not in sess or "api_hash" not in sess:
        await update.message.reply_text(
            "❌ Session data lost. Please use /start and begin again.",
            reply_markup=remove_kb()
        )
        return ConversationHandler.END

    sess["phone"] = phone
    api_id   = int(sess["api_id"])
    api_hash = sess["api_hash"]

    msg = await update.message.reply_text("📲 Sending OTP… please wait.")
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        sess["client"]          = client
        sess["phone_code_hash"] = result.phone_code_hash
        await msg.edit_text(
            "✅ OTP sent!\n\n<b>Step 4 / 4</b> – Enter the OTP:\n"
            "<i>Plain or spaced: <code>1 2 3 4 5</code></i>",
            parse_mode=ParseMode.HTML
        )
        return WAIT_OTP
    except FloodWaitError as e:
        await msg.edit_text(f"⏳ Flood wait – retry in {e.seconds}s.")
        return ConversationHandler.END
    except Exception as ex:
        await msg.edit_text(f"❌ Error sending OTP: {ex}")
        return ConversationHandler.END

async def got_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        ctx.user_data.pop("add_sess", None)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>", reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    otp       = update.message.text.strip().replace(" ", "")
    sess_data = ctx.user_data.get("add_sess", {})
    client    = sess_data.get("client")
    phone     = sess_data.get("phone")

    if not client or not phone:
        await update.message.reply_text("❌ Session expired. Use /start.")
        return ConversationHandler.END

    msg = await update.message.reply_text("🔄 Verifying OTP…")
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        await client.sign_in(phone, otp, phone_code_hash=sess_data["phone_code_hash"])
    except SessionPasswordNeededError:
        await msg.edit_text(
            "🔐 2FA is enabled. Enter your <b>Telegram password</b>:",
            parse_mode=ParseMode.HTML
        )
        return WAIT_2FA
    except PhoneCodeInvalidError:
        await msg.edit_text("❌ Invalid OTP. Use /start to retry.")
        return ConversationHandler.END
    except Exception as ex:
        await msg.edit_text(f"❌ Sign-in error: {ex}")
        return ConversationHandler.END

    return await _finalize_session(update, ctx, client, msg)

async def got_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pwd = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    client = ctx.user_data.get("add_sess", {}).get("client")
    if not client:
        await update.message.reply_text("❌ Session expired. Use /start.")
        return ConversationHandler.END
    msg = await update.message.reply_text("🔄 Verifying 2FA…")
    try:
        await client.sign_in(password=pwd)
    except Exception as ex:
        await msg.edit_text(f"❌ 2FA error: {ex}")
        return ConversationHandler.END
    return await _finalize_session(update, ctx, client, msg)

async def _finalize_session(update, ctx, client, msg):
    me          = await client.get_me()
    session_str = client.session.save()
    sess_data   = ctx.user_data.get("add_sess", {})
    data        = load_data()
    sid         = hashlib.md5(session_str.encode()).hexdigest()[:12]

    data["sessions"][sid] = {
        "session":  session_str,
        "api_id":   sess_data["api_id"],
        "api_hash": sess_data["api_hash"],
        "phone":    sess_data["phone"],
        "name":     f"{me.first_name or ''} {me.last_name or ''}".strip(),
        "added":    datetime.now().strftime("%d/%m/%Y")
    }
    save_data(data)
    ctx.user_data.pop("add_sess", None)

    master_flag = ctx.user_data.get("master", False)
    await msg.edit_text(
        f"✅ <b>Session Added!</b>\n\n"
        f"👤 Name:  {data['sessions'][sid]['name']}\n"
        f"📱 Phone: <code>{sess_data['phone']}</code>\n"
        f"🆔 ID:    <code>{sid}</code>",
        parse_mode=ParseMode.HTML
    )
    await update.effective_message.reply_text(
        "🏠 Returning to main menu…",
        reply_markup=main_keyboard(master_flag)
    )
    return ConversationHandler.END

# ───────────────────────── JOIN GROUP ─────────────────────────────
async def got_group_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>", reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    link     = text
    data     = load_data()
    sessions = data.get("sessions", {})

    if not sessions:
        await update.message.reply_text("⚠️ No sessions. Add one first.", reply_markup=back_kb())
        return ConversationHandler.END

    total = len(sessions)
    msg   = await update.message.reply_text(
        f"⚡ <b>Join Operation Started</b>\n\n"
        f"🔗 <code>{link}</code>\n"
        f"👥 Sessions: <b>{total}</b>\n\n"
        f"{progress_bar(0)}\n<i>Connecting…</i>",
        parse_mode=ParseMode.HTML
    )

    ok = fail = 0
    results = []

    for i, (sid, info) in enumerate(sessions.items(), 1):
        pct = max(1, int((i - 0.5) / total * 100))
        try:
            await msg.edit_text(
                f"⚡ <b>Joining…</b>\n\n"
                f"🔗 <code>{link}</code>\n"
                f"📱 <b>{info.get('phone','?')}</b>  ({i}/{total})\n\n"
                f"{progress_bar(pct)}\n✅ {ok}  •  ❌ {fail}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

        client = get_client(info["session"], int(info["api_id"]), info["api_hash"])
        success, note = await join_single(client, link)
        if success:
            ok += 1
            data["stats"]["joins"] = data["stats"].get("joins", 0) + 1
        else:
            fail += 1
            data["stats"]["errors"] = data["stats"].get("errors", 0) + 1
        results.append(f"{'✅' if success else '❌'} <code>{info.get('phone','?')}</code> – {note}")
        await asyncio.sleep(random.uniform(1.2, 2.5))

    save_data(data)
    await msg.edit_text(
        f"🏁 <b>Done!</b>\n\n"
        f"{progress_bar(100)}\n\n"
        f"✅ Joined: <b>{ok}</b>  |  ❌ Failed: <b>{fail}</b>\n\n"
        + "\n".join(results[-20:]),
        parse_mode=ParseMode.HTML
    )
    master_flag = ctx.user_data.get("master", False)
    await update.message.reply_text("🏠 Back to menu:", reply_markup=main_keyboard(master_flag))
    return ConversationHandler.END

# ───────────────────────── JOIN FOLDER ────────────────────────────
async def got_folder_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>", reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    folder_link = text
    data        = load_data()
    sessions    = data.get("sessions", {})

    if not sessions:
        await update.message.reply_text("⚠️ No sessions. Add one first.", reply_markup=back_kb())
        return ConversationHandler.END

    msg = await update.message.reply_text(
        f"📁 <b>Fetching folder…</b>\n\n<code>{folder_link}</code>\n\n{progress_bar(5)}\n<i>Extracting links…</i>",
        parse_mode=ParseMode.HTML
    )

    first = next(iter(sessions.values()))
    fc    = get_client(first["session"], int(first["api_id"]), first["api_hash"])
    links = await get_folder_links(fc, folder_link)

    if not links:
        await msg.edit_text(
            "❌ <b>No links found.</b>\n\nCheck that it's a valid public <code>t.me/addlist/…</code> link.",
            parse_mode=ParseMode.HTML
        )
        master_flag = ctx.user_data.get("master", False)
        await update.message.reply_text("🏠 Back to menu:", reply_markup=main_keyboard(master_flag))
        return ConversationHandler.END

    await msg.edit_text(
        f"📁 Found <b>{len(links)}</b> group(s)\n"
        f"👥 Sessions: <b>{len(sessions)}</b>\n\n{progress_bar(10)}\n<i>Starting…</i>",
        parse_mode=ParseMode.HTML
    )

    total_ops = len(links) * len(sessions)
    done = ok = fail = 0

    for li, link in enumerate(links, 1):
        for si, (sid, info) in enumerate(sessions.items(), 1):
            done += 1
            pct  = max(10, int(done / total_ops * 100))
            try:
                await msg.edit_text(
                    f"📁 <b>Folder Join Progress</b>\n\n"
                    f"Group <b>{li}/{len(links)}</b>: <code>{link[-40:]}</code>\n"
                    f"Session <b>{si}/{len(sessions)}</b>: <code>{info.get('phone','?')}</code>\n\n"
                    f"{progress_bar(pct)}\n✅ {ok}  •  ❌ {fail}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            client = get_client(info["session"], int(info["api_id"]), info["api_hash"])
            s, _   = await join_single(client, link)
            if s:
                ok += 1
                data["stats"]["joins"] = data["stats"].get("joins", 0) + 1
            else:
                fail += 1
                data["stats"]["errors"] = data["stats"].get("errors", 0) + 1
            await asyncio.sleep(random.uniform(1.5, 3.0))

    save_data(data)
    await msg.edit_text(
        f"🏁 <b>Folder Join Complete!</b>\n\n{progress_bar(100)}\n\n"
        f"📁 Groups: <b>{len(links)}</b>  👥 Sessions: <b>{len(sessions)}</b>\n"
        f"✅ Joined: <b>{ok}</b>  ❌ Failed: <b>{fail}</b>",
        parse_mode=ParseMode.HTML
    )
    master_flag = ctx.user_data.get("master", False)
    await update.message.reply_text("🏠 Back to menu:", reply_markup=main_keyboard(master_flag))
    return ConversationHandler.END

# ─────────────────────── CHANGE PASSWORD ──────────────────────────
async def got_new_pass(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "🏠 Back to Menu":
        master_flag = ctx.user_data.get("master", False)
        await update.message.reply_text(
            "🏠 <b>Main Menu</b>", reply_markup=main_keyboard(master_flag),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    try:
        await update.message.delete()
    except Exception:
        pass
    ctx.user_data["new_pass"] = update.message.text.strip()
    await update.message.reply_text(
        "🔑 <b>Confirm</b> your new password:", parse_mode=ParseMode.HTML,
        reply_markup=remove_kb()
    )
    return WAIT_CONFIRM_PASS

async def got_confirm_pass(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    new_p   = ctx.user_data.get("new_pass", "")
    confirm = update.message.text.strip()
    master_flag = ctx.user_data.get("master", False)
    if new_p != confirm:
        await update.message.reply_text(
            "❌ Passwords don't match. Try /start.",
            reply_markup=main_keyboard(master_flag)
        )
        return ConversationHandler.END
    data = load_data()
    data["password"] = hashlib.sha256(new_p.encode()).hexdigest()
    save_data(data)
    ctx.user_data.pop("new_pass", None)
    await update.message.reply_text(
        "✅ <b>Password changed!</b>",
        reply_markup=main_keyboard(master_flag),
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ──────────────────────── CANCEL / ERRORS ─────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    master_flag = ctx.user_data.get("master", False)
    await update.message.reply_text(
        "❌ Cancelled. Use /start.",
        reply_markup=main_keyboard(master_flag) if ctx.user_data.get("master") is not None else remove_kb()
    )
    return ConversationHandler.END

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"Unhandled exception: {ctx.error}", exc_info=ctx.error)

# ──────────────────────────── MAIN ────────────────────────────────
# FIX: Pydroid3-compatible async main using asyncio.run()
async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    # Menu button filter (for the global non-flow handler)
    menu_filter = filters.Regex(
        r"^(📋 My Sessions|📊 Statistics|⚙️ Master Panel \[DEV\]|"
        r"🏠 Back to Menu|📋 List Admin IDs|🗑 Remove .+)$"
    )

    # ── CONVERSATION HANDLER ──────────────────────────────────────
    # FIX: Menu buttons that start a flow are now proper entry_points.
    #      Previously they were in a global handler whose return values
    #      (WAIT_API_ID etc.) were silently ignored — the conversation
    #      never entered those states.
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            # ↓ These are the KEY FIX — each flow button is an entry point
            MessageHandler(filters.Regex(r"^➕ Add Session$")     & ~filters.COMMAND, entry_add_session),
            MessageHandler(filters.Regex(r"^👥 Join Group$")      & ~filters.COMMAND, entry_join_group),
            MessageHandler(filters.Regex(r"^📁 Join Folder$")     & ~filters.COMMAND, entry_join_folder),
            MessageHandler(filters.Regex(r"^🔑 Change Password$") & ~filters.COMMAND, entry_change_password),
        ],
        states={
            WAIT_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)
            ],
            WAIT_API_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_id)
            ],
            WAIT_API_HASH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_hash)
            ],
            WAIT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_phone)
            ],
            WAIT_OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_otp)
            ],
            WAIT_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_2fa)
            ],
            WAIT_GROUP_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_group_link)
            ],
            WAIT_FOLDER_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_folder_link)
            ],
            WAIT_NEW_PASS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_new_pass)
            ],
            WAIT_CONFIRM_PASS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_confirm_pass)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(conv)
    # Global handler for non-flow menu buttons (Stats, My Sessions, Master Panel…)
    app.add_handler(MessageHandler(menu_filter & ~filters.COMMAND, menu_handler))

    print("🤖 VoidBot starting…")

    # FIX: Use explicit async startup for Pydroid3 compatibility.
    #      app.run_polling() calls asyncio.run() internally which can
    #      conflict with Pydroid3's environment. Using initialize/start
    #      directly inside an already-running asyncio.run() loop is safe.
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    print("🤖 VoidBot is running! Press Ctrl+C to stop.")

    # Keep alive until interrupted
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("\n🛑 Stopping bot…")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped.")
