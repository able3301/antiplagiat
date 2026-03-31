import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
    ReactionTypeEmoji,
)
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "bridge.db"

BOT_TOKEN = "8692288654:AAGQgjLeZyVveHjt0ysyjtnYwquSyK1L8uY"
TARGET_GROUP_ID_RAW = "-1003312509381"
ADMIN_USER_IDS_RAW = ""

CLAIM_REACTION = "👍"
DONE_REACTION = "👍"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. .env faylga BOT_TOKEN kiriting.")

if not TARGET_GROUP_ID_RAW:
    raise RuntimeError("TARGET_GROUP_ID topilmadi. .env faylga TARGET_GROUP_ID kiriting.")

try:
    TARGET_GROUP_ID = int(TARGET_GROUP_ID_RAW)
except ValueError as exc:
    raise RuntimeError("TARGET_GROUP_ID noto'g'ri. U son bo'lishi kerak.") from exc


def parse_admin_ids(raw_value: str) -> set[int]:
    admin_ids: set[int] = set()
    if not raw_value.strip():
        return admin_ids

    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            admin_ids.add(int(item))
        except ValueError as exc:
            raise RuntimeError(
                "ADMIN_USER_IDS noto'g'ri. Masalan: 123456789,987654321"
            ) from exc
    return admin_ids


ADMIN_USER_IDS = parse_admin_ids(ADMIN_USER_IDS_RAW)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-support-bot")

router = Router(name="main")


# =========================
# DATABASE
# =========================
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_links (
                group_chat_id INTEGER NOT NULL,
                group_message_id INTEGER NOT NULL,
                ticket_message_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                user_message_id INTEGER,
                username TEXT,
                full_name TEXT,
                question_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_chat_id, group_message_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_claims (
                group_chat_id INTEGER NOT NULL,
                ticket_message_id INTEGER NOT NULL,
                admin_user_id INTEGER NOT NULL,
                admin_full_name TEXT NOT NULL,
                claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_chat_id, ticket_message_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_open_tickets (
                user_chat_id INTEGER PRIMARY KEY,
                group_chat_id INTEGER NOT NULL,
                ticket_message_id INTEGER NOT NULL,
                question_type TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_text_cache (
                group_chat_id INTEGER NOT NULL,
                ticket_message_id INTEGER NOT NULL,
                rendered_text TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_chat_id, ticket_message_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_states (
                user_chat_id INTEGER PRIMARY KEY,
                selected_question TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_responses (
                group_chat_id INTEGER NOT NULL,
                ticket_message_id INTEGER NOT NULL,
                admin_user_id INTEGER NOT NULL,
                admin_full_name TEXT NOT NULL,
                responded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_chat_id, ticket_message_id)
            )
            """
        )

        conn.commit()


# =========================
# USER STATE
# =========================
def set_user_selected_question(user_chat_id: int, question: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO user_states (user_chat_id, selected_question, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_chat_id) DO UPDATE SET
                selected_question = excluded.selected_question,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_chat_id, question),
        )
        conn.commit()


def get_user_selected_question(user_chat_id: int) -> Optional[str]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT selected_question
            FROM user_states
            WHERE user_chat_id = ?
            """,
            (user_chat_id,),
        ).fetchone()
        return row[0] if row else None


def clear_user_selected_question(user_chat_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM user_states WHERE user_chat_id = ?",
            (user_chat_id,),
        )
        conn.commit()


# =========================
# TICKETS
# =========================
def save_link(
    group_chat_id: int,
    group_message_id: int,
    ticket_message_id: int,
    user_chat_id: int,
    user_message_id: Optional[int],
    username: Optional[str],
    full_name: str,
    question_type: Optional[str],
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO message_links (
                group_chat_id,
                group_message_id,
                ticket_message_id,
                user_chat_id,
                user_message_id,
                username,
                full_name,
                question_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_chat_id,
                group_message_id,
                ticket_message_id,
                user_chat_id,
                user_message_id,
                username,
                full_name,
                question_type,
            ),
        )
        conn.commit()


def get_user_by_group_message(group_chat_id: int, group_message_id: int) -> Optional[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT ticket_message_id, user_chat_id, user_message_id, username, full_name, question_type
            FROM message_links
            WHERE group_chat_id = ? AND group_message_id = ?
            """,
            (group_chat_id, group_message_id),
        ).fetchone()
        return dict(row) if row else None


def get_ticket_messages(group_chat_id: int, ticket_message_id: int) -> list[int]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT group_message_id
            FROM message_links
            WHERE group_chat_id = ? AND ticket_message_id = ?
            ORDER BY group_message_id ASC
            """,
            (group_chat_id, ticket_message_id),
        ).fetchall()
        return [row[0] for row in rows]


def get_ticket_claim(group_chat_id: int, ticket_message_id: int) -> Optional[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT admin_user_id, admin_full_name, claimed_at
            FROM ticket_claims
            WHERE group_chat_id = ? AND ticket_message_id = ?
            """,
            (group_chat_id, ticket_message_id),
        ).fetchone()
        return dict(row) if row else None


def claim_ticket(
    group_chat_id: int,
    ticket_message_id: int,
    admin_user_id: int,
    admin_full_name: str,
) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO ticket_claims (
                group_chat_id,
                ticket_message_id,
                admin_user_id,
                admin_full_name
            ) VALUES (?, ?, ?, ?)
            """,
            (group_chat_id, ticket_message_id, admin_user_id, admin_full_name),
        )
        conn.commit()
        return cursor.rowcount > 0


def save_ticket_response(
    group_chat_id: int,
    ticket_message_id: int,
    admin_user_id: int,
    admin_full_name: str,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO ticket_responses (
                group_chat_id,
                ticket_message_id,
                admin_user_id,
                admin_full_name,
                responded_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (group_chat_id, ticket_message_id, admin_user_id, admin_full_name),
        )
        conn.commit()


def get_open_ticket_for_user(user_chat_id: int) -> Optional[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT user_chat_id, group_chat_id, ticket_message_id, question_type, status, created_at, updated_at
            FROM user_open_tickets
            WHERE user_chat_id = ? AND status = 'open'
            """,
            (user_chat_id,),
        ).fetchone()
        return dict(row) if row else None


def open_or_update_user_ticket(
    user_chat_id: int,
    group_chat_id: int,
    ticket_message_id: int,
    question_type: Optional[str],
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO user_open_tickets (
                user_chat_id, group_chat_id, ticket_message_id, question_type, status, updated_at
            )
            VALUES (?, ?, ?, ?, 'open', CURRENT_TIMESTAMP)
            ON CONFLICT(user_chat_id) DO UPDATE SET
                group_chat_id = excluded.group_chat_id,
                ticket_message_id = excluded.ticket_message_id,
                question_type = excluded.question_type,
                status = 'open',
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_chat_id, group_chat_id, ticket_message_id, question_type),
        )
        conn.commit()


def close_user_ticket(user_chat_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE user_open_tickets
            SET status = 'closed',
                updated_at = CURRENT_TIMESTAMP
            WHERE user_chat_id = ? AND status = 'open'
            """,
            (user_chat_id,),
        )
        conn.commit()


def save_ticket_text(group_chat_id: int, ticket_message_id: int, rendered_text: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO ticket_text_cache (
                group_chat_id, ticket_message_id, rendered_text, updated_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(group_chat_id, ticket_message_id) DO UPDATE SET
                rendered_text = excluded.rendered_text,
                updated_at = CURRENT_TIMESTAMP
            """,
            (group_chat_id, ticket_message_id, rendered_text),
        )
        conn.commit()


def get_ticket_text(group_chat_id: int, ticket_message_id: int) -> Optional[str]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT rendered_text
            FROM ticket_text_cache
            WHERE group_chat_id = ? AND ticket_message_id = ?
            """,
            (group_chat_id, ticket_message_id),
        ).fetchone()
        return row[0] if row else None


def append_text_to_ticket(existing_text: str, message: Message) -> str:
    added_text = (message.text or "").strip()
    if not added_text:
        return existing_text

    msg_time = message.date.strftime("%d.%m.%Y %H:%M") if message.date else "vaqt noma'lum"

    return (
        existing_text
        + "\n\n"
        + f"📨 <b>Qo'shimcha xabar ({msg_time})</b>\n"
        + f"{added_text}"
    )


def get_open_tickets(limit: int = 20) -> list[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                uot.user_chat_id,
                uot.ticket_message_id,
                uot.question_type,
                uot.updated_at,
                ml.full_name,
                ml.username
            FROM user_open_tickets uot
            LEFT JOIN message_links ml
              ON ml.group_chat_id = uot.group_chat_id
             AND ml.group_message_id = uot.ticket_message_id
            WHERE uot.status = 'open'
            ORDER BY uot.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_stats() -> dict:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM user_open_tickets WHERE status = 'open'"
        ).fetchone()[0]

        closed_count = conn.execute(
            "SELECT COUNT(*) FROM user_open_tickets WHERE status = 'closed'"
        ).fetchone()[0]

        total_count = open_count + closed_count

        today_count = conn.execute(
            """
            SELECT COUNT(*) FROM user_open_tickets
            WHERE DATE(created_at, 'localtime') = DATE('now', 'localtime')
            """
        ).fetchone()[0]

        top_sections = conn.execute(
            """
            SELECT question_type, COUNT(*) as cnt
            FROM user_open_tickets
            WHERE question_type IS NOT NULL
              AND TRIM(question_type) != ''
            GROUP BY question_type
            ORDER BY cnt DESC
            LIMIT 3
            """
        ).fetchall()

        answered_count = conn.execute(
            "SELECT COUNT(*) FROM ticket_responses"
        ).fetchone()[0]

        admin_today_rows = conn.execute(
            """
            SELECT admin_full_name, COUNT(*) as cnt
            FROM ticket_responses
            WHERE DATE(responded_at, 'localtime') = DATE('now', 'localtime')
            GROUP BY admin_user_id, admin_full_name
            ORDER BY cnt DESC, admin_full_name ASC
            """
        ).fetchall()

        return {
            "open": open_count,
            "closed": closed_count,
            "total": total_count,
            "today": today_count,
            "answered": answered_count,
            "top_sections": [(row[0], row[1]) for row in top_sections],
            "admin_today": [(row[0], row[1]) for row in admin_today_rows],
        }


# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def is_allowed_user_content(message: Message) -> bool:
    return bool(message.text or message.voice or message.document)


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="1️⃣ Tasdiqlash tugmasi bosilmayapti",
                callback_data="question:Metodik ish tasdiqlash muammosi",
            )],
            [InlineKeyboardButton(
                text="2️⃣ Antiplagiat: login yoki parol xato",
                callback_data="question:Antiplagiat login/parol muammosi",
            )],
            [InlineKeyboardButton(
                text="4️⃣ Parolni tiklashda kod kelmayapti",
                callback_data="question:Parolni tiklashda kod kelmayapti",
            )],
            [InlineKeyboardButton(
                text="5️⃣ Sertifikatda boshqa ism chiqdi",
                callback_data="question:Sertifikatda noto'g'ri ism",
            )],
            [InlineKeyboardButton(
                text="7️⃣ Natija hisoblanmoqda deb turibdi",
                callback_data="question:Natija uzoq hisoblanmoqda",
            )],
            [InlineKeyboardButton(
                text="🆕 Boshqa turdagi so'rov",
                callback_data="question:Boshqa turdagi so'rov",
            )],
        ]
    )


def question_menu_text() -> str:
    return (
        "Assalomu alaykum! 👋\n\n"
        "Muammoingizga mos bo‘limni tanlang:\n\n"
        "1️⃣ Tasdiqlash tugmasi bosilmayapti\n"
        "2️⃣ Antiplagiat: login yoki parol xato\n"
        "4️⃣ Parolni tiklashda kod kelmayapti\n"
        "5️⃣ Sertifikatda boshqa ism chiqdi\n"
        "7️⃣ Natija hisoblanmoqda deb turibdi\n"
        "🆕 Boshqa turdagi so'rov\n\n"
        "Quyidagi tugmalardan birini bosing 👇"
    )


def build_sender_card(message: Message, question_type: Optional[str]) -> str:
    user = message.from_user
    full_name = user.full_name if user else "Noma'lum foydalanuvchi"
    user_id = user.id if user else 0
    username = f"@{user.username}" if user and user.username else "yo'q"
    q_type = question_type or "Tanlanmagan"

    return (
        "📩 <b>Yangi murojaat</b>\n"
        f"🗂 <b>Bo'lim:</b> {q_type}\n"
        f"👤 <b>F.I.Sh.:</b> {full_name}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n\n"
        "Quyidagi xabarga <b>reply</b> qilib javob bering."
    )


def build_full_text_message(message: Message, question_type: Optional[str]) -> str:
    user = message.from_user
    full_name = user.full_name if user else "Noma'lum foydalanuvchi"
    user_id = user.id if user else 0
    username = f"@{user.username}" if user and user.username else "yo'q"
    user_text = (message.text or "").strip()
    q_type = question_type or "Tanlanmagan"

    return (
        "📩 <b>Yangi murojaat</b>\n"
        f"🗂 <b>Bo'lim:</b> {q_type}\n"
        f"👤 <b>F.I.Sh.:</b> {full_name}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n\n"
        f"💬 <b>Xabar:</b>\n{user_text}\n\n"
        "Quyidagi xabarga <b>reply</b> qilib javob bering."
    )


def build_group_message_link(group_chat_id: int, message_id: int) -> str:
    chat_id_str = str(group_chat_id)

    if not chat_id_str.startswith("-100"):
        raise ValueError(
            "Ticket link faqat supergroup uchun ishlaydi. TARGET_GROUP_ID -100 bilan boshlanishi kerak."
        )

    internal_chat_id = chat_id_str[4:]
    return f"https://t.me/c/{internal_chat_id}/{message_id}"


async def safe_set_reaction(bot: Bot, chat_id: int, message_id: int, emoji: str) -> None:
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
            is_big=False,
        )
    except Exception as exc:
        logger.warning(
            "Reaction qo'yib bo'lmadi | chat_id=%s | message_id=%s | error=%s",
            chat_id,
            message_id,
            exc,
        )


async def mark_ticket(bot: Bot, chat_id: int, ticket_message_id: int, emoji: str) -> None:
    for message_id in get_ticket_messages(chat_id, ticket_message_id):
        await safe_set_reaction(bot, chat_id, message_id, emoji)


# =========================
# AVTOMATIK JAVOBLAR
# =========================
AUTO_ANSWERS: dict[str, str] = {
    "Metodik ish tasdiqlash muammosi": (
        "📋 <b>Tasdiqlash tugmasi bosilmayotgan bo‘lsa:</b>\n\n"
        "1️⃣ Sahifani pastga aylantiring va <b>Ommaviy oferta shartlari</b> bo‘limini toping\n"
        "2️⃣ Eng pastdagi <b>\"Ommaviy oferta shartlariga roziman\"</b> tugmasini bosing\n"
        "3️⃣ Tugma ko‘rinmasa, sahifani kichraytiring (<b>Ctrl + sichqoncha g‘ildiragi</b>)\n\n"
        "💡 Shundan so‘ng tasdiqlash tugmasi faollashadi."
    ),
    "Antiplagiat login/parol muammosi": (
        "🔐 <b>Login yoki parol xato bo‘lsa:</b>\n\n"
        "<b>Parolni unutgan bo‘lsangiz:</b>\n"
        "1️⃣ <b>\"Parolni unutdingizmi?\"</b> tugmasini bosing\n"
        "2️⃣ Telegram bot orqali a’zo bo‘lish tugmasini bosing\n"
        "3️⃣ Botga <b>/start</b> yuboring va telefon raqamingizni ulashing\n"
        "4️⃣ Saytda raqamingizni kiriting va <b>Telegramga kod yuborish</b> tugmasini bosing\n"
        "5️⃣ Telegramga kelgan 6 xonali kodni saytga kiriting va yangi parol o‘rnating\n\n"
        "<b>Loginni unutgan bo‘lsangiz:</b>\n"
        "💡 Saytda ro‘yxatdan o‘tishda kiritgan <b>username</b> sizning loginingiz hisoblanadi.\n"
        "Eslay olmasangiz, texnik mutaxassisga murojaat qiling."
    ),
    "Parolni tiklashda kod kelmayapti": (
        "📱 <b>Telegram orqali kod kelmayotgan bo‘lsa:</b>\n\n"
        "1️⃣ Telegramda botga <b>/start</b> yozing\n"
        "2️⃣ Telefon raqamingizni qayta ulashing\n"
        "3️⃣ Saytga qaytib, raqamingizni kiriting va <b>Telegramga kod yuborish</b> tugmasini bosing\n\n"
        "💡 Agar avval botga a’zo bo‘lgan bo‘lsangiz, saytda o‘sha raqamni kiritib to‘g‘ridan-to‘g‘ri kod so‘rang."
    ),
    "Sertifikatda noto'g'ri ism": (
        "📜 <b>Sertifikatda boshqa ism chiqsa:</b>\n\n"
        "Sertifikat har doim <b>ro‘yxatdan o‘tgan foydalanuvchi nomiga</b> chiqariladi.\n\n"
        "Agar boshqa o‘qituvchining ishi sizning accountingiz orqali yuborilgan bo‘lsa, ismni o‘zgartirib bo‘lmaydi.\n\n"
        "💡 Yechim: o‘sha o‘qituvchi <b>o‘z nomidan yangi account ochib</b>, metodik ishini o‘zi yuborishi kerak."
    ),
    "Natija uzoq hisoblanmoqda": (
        "⏳ <b>Natija uzoq vaqt hisoblanmoqda bo‘lsa:</b>\n\n"
        "Tizimda foydalanuvchilar soni ko‘p bo‘lganda navbat hosil bo‘lishi mumkin.\n\n"
        "✅ Xavotir olmang — balansingiz yechilmaydi va natijangiz albatta chiqadi.\n\n"
        "💡 Biroz kutib, sahifani yangilang. Muammo davom etsa, quyida savol yuboring."
    ),
}


def auto_answer_keyboard(question_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Muammo hal bo'ldi",
                    callback_data=f"resolved:{question_type}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Savol yuborish",
                    callback_data=f"ask:{question_type}",
                )
            ],
        ]
    )


# =========================
# USER FLOW
# =========================
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        question_menu_text(),
        reply_markup=start_keyboard(),
    )


@router.callback_query(F.data == "show_questions")
async def show_questions(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        question_menu_text(),
        reply_markup=start_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("question:"))
async def choose_question(callback: CallbackQuery) -> None:
    question_type = callback.data.split(":", 1)[1]
    user_chat_id = callback.from_user.id

    set_user_selected_question(user_chat_id, question_type)

    auto_text = AUTO_ANSWERS.get(question_type)

    if auto_text:
        await callback.message.edit_text(
            f"🗂 <b>{question_type}</b>\n\n"
            + auto_text
            + "\n\n"
            "─────────────────\n"
            "Ushbu javob muammoingizni hal qildimi?",
            parse_mode=ParseMode.HTML,
            reply_markup=auto_answer_keyboard(question_type),
        )
    else:
        await callback.message.edit_text(
            f"✅ Siz <b>{question_type}</b> ni tanladingiz.\n\n"
            "Endi savolingizni yozing yoki ovoz/fayl yuboring.",
            parse_mode=ParseMode.HTML,
        )
    await callback.answer("Savol turi tanlandi")


@router.callback_query(F.data.startswith("resolved:"))
async def ticket_resolved(callback: CallbackQuery) -> None:
    user_chat_id = callback.from_user.id
    clear_user_selected_question(user_chat_id)
    close_user_ticket(user_chat_id)

    await callback.message.edit_text(
        "✅ <b>Ajoyib!</b> Muammoingiz hal bo'lganidan xursandmiz.\n\n"
        "Boshqa savollaringiz bo'lsa, /start ni bosing.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Yopildi")


@router.callback_query(F.data.startswith("ask:"))
async def ask_question(callback: CallbackQuery) -> None:
    question_type = callback.data.split(":", 1)[1]
    user_chat_id = callback.from_user.id

    set_user_selected_question(user_chat_id, question_type)

    await callback.message.edit_text(
        f"📝 <b>{question_type}</b>\n\n"
        "Savolingizni yozing yoki ovozli xabar / fayl yuboring.\n"
        "Xabaringiz mutaxassislarimizga yuboriladi.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Savol yuborish rejimi")


@router.message(F.chat.type == ChatType.PRIVATE, Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer(
        question_menu_text(),
        reply_markup=start_keyboard(),
    )


@router.message(F.chat.type == ChatType.PRIVATE)
async def from_user_to_group(message: Message, bot: Bot) -> None:
    if not is_allowed_user_content(message):
        await message.answer(
            "❌ Faqat quyidagilar yuborish mumkin:\n"
            "• text xabar\n"
            "• ovozli xabar\n"
            "• fayl"
        )
        return

    try:
        full_name = message.from_user.full_name if message.from_user else "Noma'lum foydalanuvchi"
        username = message.from_user.username if message.from_user else None
        user_chat_id = message.chat.id

        selected_question = get_user_selected_question(user_chat_id)
        open_ticket = get_open_ticket_for_user(user_chat_id)

        if not selected_question and not open_ticket:
            await message.answer(
                "Avval savol turini tanlang 👇",
                reply_markup=start_keyboard(),
            )
            return

        if open_ticket:
            ticket_message_id = open_ticket["ticket_message_id"]
            question_type = open_ticket.get("question_type") or selected_question

            if message.text:
                current_text = get_ticket_text(TARGET_GROUP_ID, ticket_message_id)

                if not current_text:
                    current_text = build_sender_card(message, question_type)

                updated_text = append_text_to_ticket(current_text, message)

                await bot.edit_message_text(
                    chat_id=TARGET_GROUP_ID,
                    message_id=ticket_message_id,
                    text=updated_text,
                    parse_mode=ParseMode.HTML,
                )

                save_ticket_text(
                    group_chat_id=TARGET_GROUP_ID,
                    ticket_message_id=ticket_message_id,
                    rendered_text=updated_text,
                )

                open_or_update_user_ticket(
                    user_chat_id=user_chat_id,
                    group_chat_id=TARGET_GROUP_ID,
                    ticket_message_id=ticket_message_id,
                    question_type=question_type,
                )

                await message.answer("✅ Xabaringiz avvalgi murojaatga qo'shildi.")
                return

            if message.voice or message.document:
                group_msg = await bot.copy_message(
                    chat_id=TARGET_GROUP_ID,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_to_message_id=ticket_message_id,
                )

                save_link(
                    group_chat_id=TARGET_GROUP_ID,
                    group_message_id=group_msg.message_id,
                    ticket_message_id=ticket_message_id,
                    user_chat_id=user_chat_id,
                    user_message_id=message.message_id,
                    username=username,
                    full_name=full_name,
                    question_type=question_type,
                )

                open_or_update_user_ticket(
                    user_chat_id=user_chat_id,
                    group_chat_id=TARGET_GROUP_ID,
                    ticket_message_id=ticket_message_id,
                    question_type=question_type,
                )

                await message.answer("✅ Xabaringiz avvalgi murojaatga qo'shib yuborildi.")
                return

        question_type = selected_question

        if message.text:
            initial_text = build_full_text_message(message, question_type)

            group_msg = await bot.send_message(
                chat_id=TARGET_GROUP_ID,
                text=initial_text,
                parse_mode=ParseMode.HTML,
            )

            save_link(
                group_chat_id=TARGET_GROUP_ID,
                group_message_id=group_msg.message_id,
                ticket_message_id=group_msg.message_id,
                user_chat_id=user_chat_id,
                user_message_id=message.message_id,
                username=username,
                full_name=full_name,
                question_type=question_type,
            )

            save_ticket_text(
                group_chat_id=TARGET_GROUP_ID,
                ticket_message_id=group_msg.message_id,
                rendered_text=initial_text,
            )

            open_or_update_user_ticket(
                user_chat_id=user_chat_id,
                group_chat_id=TARGET_GROUP_ID,
                ticket_message_id=group_msg.message_id,
                question_type=question_type,
            )

        elif message.voice or message.document:
            initial_text = build_sender_card(message, question_type)

            sender_card = await bot.send_message(
                chat_id=TARGET_GROUP_ID,
                text=initial_text,
                parse_mode=ParseMode.HTML,
            )

            save_link(
                group_chat_id=TARGET_GROUP_ID,
                group_message_id=sender_card.message_id,
                ticket_message_id=sender_card.message_id,
                user_chat_id=user_chat_id,
                user_message_id=message.message_id,
                username=username,
                full_name=full_name,
                question_type=question_type,
            )

            save_ticket_text(
                group_chat_id=TARGET_GROUP_ID,
                ticket_message_id=sender_card.message_id,
                rendered_text=initial_text,
            )

            forwarded = await bot.copy_message(
                chat_id=TARGET_GROUP_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_to_message_id=sender_card.message_id,
            )

            save_link(
                group_chat_id=TARGET_GROUP_ID,
                group_message_id=forwarded.message_id,
                ticket_message_id=sender_card.message_id,
                user_chat_id=user_chat_id,
                user_message_id=message.message_id,
                username=username,
                full_name=full_name,
                question_type=question_type,
            )

            open_or_update_user_ticket(
                user_chat_id=user_chat_id,
                group_chat_id=TARGET_GROUP_ID,
                ticket_message_id=sender_card.message_id,
                question_type=question_type,
            )

        clear_user_selected_question(user_chat_id)

        await message.answer(
            "✅ Murojaatingiz qabul qilindi.\n"
            "Javob tayyor bo'lgach shu bot orqali sizga yuboriladi.",
            reply_markup=start_keyboard(),
        )

    except Exception as exc:
        logger.exception("Foydalanuvchi xabarini guruhga yuborishda xatolik: %s", exc)
        await message.answer(
            "❌ Xabarni guruhga yuborishda xatolik yuz berdi. "
            "Guruh ID va bot huquqlarini tekshiring."
        )


# =========================
# ADMIN FLOW
# =========================
async def _handle_tickets(message: Message) -> None:
    tickets = get_open_tickets(limit=20)

    if not tickets:
        await message.answer("✅ Hozircha ochiq murojaatlar yo'q.")
        return

    lines = ["📋 <b>Ochiq murojaatlar ro'yxati</b>\n"]
    keyboard_rows = []

    for idx, ticket in enumerate(tickets, start=1):
        full_name = ticket.get("full_name") or "Noma'lum foydalanuvchi"
        username = ticket.get("username")
        username_text = f"@{username}" if username else "yo'q"
        q_type = ticket.get("question_type") or "Tanlanmagan"
        ticket_id = ticket["ticket_message_id"]
        user_chat_id = ticket["user_chat_id"]

        lines.append(
            f"{idx}. <b>Ticket ID:</b> <code>{ticket_id}</code>\n"
            f"👤 {full_name}\n"
            f"🔗 {username_text}\n"
            f"🆔 User: <code>{user_chat_id}</code>\n"
            f"🗂 Bo'lim: {q_type}\n"
        )

        try:
            ticket_link = build_group_message_link(TARGET_GROUP_ID, ticket_id)
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📂 Ticket #{ticket_id} ni ochish",
                        url=ticket_link,
                    )
                ]
            )
        except Exception:
            pass

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows) if keyboard_rows else None

    extra_note = ""
    if not keyboard_rows:
        extra_note = (
            "\n⚠️ Ticket link yaratib bo'lmadi.\n"
            "Buning uchun guruh supergroup bo'lishi va ID -100 bilan boshlanishi kerak."
        )

    await message.answer(
        "\n".join(lines) + extra_note,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def _handle_stats(message: Message) -> None:
    stats = get_stats()

    admin_lines = ""
    for admin_name, cnt in stats["admin_today"]:
        admin_lines += f"{admin_name} -- <b>{cnt} ta</b>\n"

    if not admin_lines:
        admin_lines = "Ma'lumot yo'q\n"

    top_lines = ""
    for section, cnt in stats["top_sections"]:
        top_lines += f"{section} — <b>{cnt} ta</b>\n"

    if not top_lines:
        top_lines = "Ma'lumot yo'q\n"

    text = (
        "📊 <b>Statistika</b>\n\n"
        f"📂 Jami murojaatlar: <b>{stats['total']} ta</b>\n"
        f"🟢 Ochiq: <b>{stats['open']} ta</b>\n"
        f"✅ Yopiq: <b>{stats['closed']} ta</b>\n"
        f"📅 Bugun: <b>{stats['today']} ta</b>\n"
        f"👨‍💼 Javob berilgan: <b>{stats['answered']} ta</b>\n"
        f"{admin_lines}\n"
        f"🏆 <b>Eng ko'p murojaat:</b>\n"
        f"{top_lines}"
    )

    await message.answer(text, parse_mode="HTML")


@router.message(F.chat.id == TARGET_GROUP_ID, Command("stats"))
async def cmd_stats_in_group(message: Message) -> None:
    await _handle_stats(message)


@router.message(F.chat.type == ChatType.PRIVATE, Command("stats"))
async def cmd_stats_private(message: Message) -> None:
    await _handle_stats(message)


@router.message(F.chat.id == TARGET_GROUP_ID, Command("tickets"))
async def cmd_tickets_in_group(message: Message) -> None:
    await _handle_tickets(message)


@router.message(F.chat.type == ChatType.PRIVATE, Command("tickets"))
async def cmd_tickets_private(message: Message) -> None:
    await _handle_tickets(message)


@router.message(F.chat.id == TARGET_GROUP_ID, F.reply_to_message.as_("reply_to"))
async def from_group_to_user(message: Message, bot: Bot, reply_to: Message) -> None:
    link = get_user_by_group_message(message.chat.id, reply_to.message_id)
    if not link:
        await message.reply("❌ Bu reply qaysi foydalanuvchiga tegishli ekanini topa olmadim.")
        return

    admin = message.from_user
    if not admin:
        await message.reply("❌ Operator ma'lumotini aniqlab bo'lmadi.")
        return

    ticket_message_id = link["ticket_message_id"]
    existing_claim = get_ticket_claim(message.chat.id, ticket_message_id)

    if existing_claim is None:
        claim_ticket(
            group_chat_id=message.chat.id,
            ticket_message_id=ticket_message_id,
            admin_user_id=admin.id,
            admin_full_name=admin.full_name,
        )
        await mark_ticket(bot, message.chat.id, ticket_message_id, CLAIM_REACTION)
        existing_claim = get_ticket_claim(message.chat.id, ticket_message_id)

    if existing_claim and existing_claim["admin_user_id"] != admin.id:
        await message.reply(
            "⛔ Bu murojaat allaqachon boshqa admin tomonidan olindi. "
            f"Band qilgan admin: {existing_claim['admin_full_name']}"
        )
        return

    try:
        await bot.copy_message(
            chat_id=link["user_chat_id"],
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )

        save_ticket_response(
            group_chat_id=message.chat.id,
            ticket_message_id=ticket_message_id,
            admin_user_id=admin.id,
            admin_full_name=admin.full_name,
        )

        close_user_ticket(link["user_chat_id"])
        await mark_ticket(bot, message.chat.id, ticket_message_id, DONE_REACTION)

        await message.reply("✅ Javob foydalanuvchiga yuborildi va ticket yopildi.")
    except Exception as exc:
        logger.exception("Javobni foydalanuvchiga yuborishda xatolik: %s", exc)
        await message.reply(
            "❌ Javobni yuborib bo'lmadi. Foydalanuvchi botni bloklagan bo'lishi mumkin."
        )


@router.message(F.chat.id == TARGET_GROUP_ID)
async def ignore_non_replies(message: Message) -> None:
    return


# =========================
# MAIN
# =========================
async def main() -> None:
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.delete_webhook(drop_pending_updates=True)

    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    logger.info("Bot ishga tushdi: @%s", me.username)
    logger.info("Target group id: %s", TARGET_GROUP_ID)
    logger.info("Admin IDs count: %s", len(ADMIN_USER_IDS))

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
