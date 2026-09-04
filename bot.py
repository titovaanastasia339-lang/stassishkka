import os
import logging
import sqlite3
import signal
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from contextlib import contextmanager
from typing import Optional, Dict, List, Tuple

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ------------------------------------------------------------------
# НАСТРОЙКИ
# ------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Please set the TELEGRAM_BOT_TOKEN environment variable")

# Пароль для организаторов
ORGANIZER_PASSWORD = "260826"

# Пароли команд (индивидуальный пароль для каждой команды)
TEAM_PASSWORDS = {
    "team_1": "pass1",
    "team_2": "pass2",
    "team_3": "pass3",
    "team_4": "pass4",
    "team_5": "pass5",
    "team_6": "pass6",
    "team_7": "pass7",
    "team_8": "pass8",
    "team_9": "pass9",
    "team_10": "pass10",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# БАЗА ДАННЫХ (SQLite)
# ------------------------------------------------------------------

DB_PATH = "dozor_bot.db"

@contextmanager
def get_db():
    """Контекстный менеджер для работы с БД"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()

def init_db():
    """Инициализация таблиц БД"""
    with get_db() as conn:
        # Таблица пользователей (кураторов команд)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                team_id TEXT,
                is_organizer INTEGER DEFAULT 0
            )
        ''')

        # Таблица состояния команд
        conn.execute('''
            CREATE TABLE IF NOT EXISTS team_state (
                team_id TEXT PRIMARY KEY,
                current_location_index INTEGER DEFAULT 0,
                current_phase TEXT DEFAULT 'riddle',
                codes_found INTEGER DEFAULT 0,
                codes_entered TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица статистики
        conn.execute('''
            CREATE TABLE IF NOT EXISTS team_stats (
                team_id TEXT PRIMARY KEY,
                riddles_solved INTEGER DEFAULT 0,
                codes_entered INTEGER DEFAULT 0,
                wrong_attempts INTEGER DEFAULT 0,
                total_time_seconds INTEGER DEFAULT 0,
                start_time TIMESTAMP,
                finish_time TIMESTAMP,
                completed INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица занятых локаций
        conn.execute('''
            CREATE TABLE IF NOT EXISTS location_locks (
                location_id INTEGER PRIMARY KEY,
                locked_by_team TEXT,
                locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

def migrate_db():
    """Миграция базы данных для старых версий"""
    with get_db() as conn:
        # Проверяем, есть ли колонка codes_found в team_state
        cursor = conn.execute("PRAGMA table_info(team_state)")
        columns = [row["name"] for row in cursor.fetchall()]
        
        if "codes_found" not in columns:
            logger.info("Миграция: добавляем колонку codes_found в team_state")
            conn.execute("ALTER TABLE team_state ADD COLUMN codes_found INTEGER DEFAULT 0")
        
        if "codes_entered" not in columns:
            logger.info("Миграция: добавляем колонку codes_entered в team_state")
            conn.execute("ALTER TABLE team_state ADD COLUMN codes_entered TEXT DEFAULT ''")
        
        # Проверяем, есть ли таблица location_locks
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='location_locks'")
        if not cursor.fetchone():
            logger.info("Миграция: создаем таблицу location_locks")
            conn.execute('''
                CREATE TABLE IF NOT EXISTS location_locks (
                    location_id INTEGER PRIMARY KEY,
                    locked_by_team TEXT,
                    locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

# ------------------------------------------------------------------
# ДАННЫЕ: 22 локации с загадками и кодами
# ------------------------------------------------------------------

# Все 22 локации с загадками и двумя цифровыми кодами
LOCATIONS = [
    {
        "id": 1,
        "name": "Мост",
        "riddle": "Два мира делю я одним лишь шагом,\nС одной стороны — суета, \nс другой — благо. \nКто ступит на меня — выбирает путь, \nНазад не вернуться, не обмануть. \nЯ не судья, но я проверяю, \nСлабых качаю, сильных спасаю. \nГде это? \nГде река течёт, \nА над ней — изгиб, как взлёт?",
        "codes": ["5821", "9047"],
        "photo": None,
    },
    {
        "id": 2,
        "name": "Забор",
        "riddle": "Вы нашли место.Но место — не точка. \nУ него есть граница. Ищите то, что стоит между ним и остальным миром",
        "codes": ["3164", "7502"],
        "photo": "https://disk.yandex.ru/i/igus4e3V05sI8g",
    },
    {
        "id": 3,
        "name": "Детская площадка",
        "riddle": "Место детских игр и гам, \nОбойди его по краям. \nВдоль забора обойди, то, что ищешь, там найди. \nЧто это за место?",
        "codes": ["8396", "4150"],
        "photo": None,
    },
    {
        "id": 4,
        "name": "52.248805,104.137154",
        "riddle": "Решите примеры и по координатам вычислите место. \nВ ответе укажите верные координаты места через запятую и отправляйтесь туда.\n 1.	(18 ÷ 3) − 1 = ___ \n 2.	7 × 2 − 12 = ___ \n 3.	(20 − 8) ÷ 6 = ___ \n 4.	15 ÷ 3 + 2 − 3 = ___ \n 5.	6 × 2 − 4 = ___ \n 6.	(24 ÷ 3) + 4 − 4 = ___ \n 7.	16 ÷ 4 − 4 = ___ \n 8.	18 ÷ 6 + 4 − 2 = ___ \n 9.	6 − 3 × 2 + 1 = ___ \n 10.	15 ÷ 3 − 5 = ___ \n 11.	(18 − 6) ÷ 3 = ___ \n 12.	10 − 18 ÷ 2 = ___ \n 13.	12 ÷ 2 − 3 = ___ \n 14.	3 × 4 − 5 = ___ \n 15.	9 − 16 ÷ 2 = ___ \n 16.	20 ÷ 4 + 2 − 2 = ___ \n 17.	16 ÷ 2 − 4 = ___",
        "codes": ["2738", "6914"],
        "photo": "https://disk.yandex.ru/i/4T0PRvQxI-wUtw", # ЗАМЕНИТЕ НА РЕАЛЬНУЮ ССЫЛКУ
    },
    {
        "id": 5,
        "name": "Корт",
        "riddle": "Загадка: Найдите место, где все четыре слова могут одновременно быть одним целым.\n 18-16-9-29-4-18-29-26 \n 17-6-18-10-16-5 \n 2-21-13-13-10-20 \n 26-1-11-2-1",
        "codes": ["5073", "1846"],
        "photo": None,
    },
    {
        "id": 6,
        "name": "Сцена",
        "riddle": "Играем в морской бой! \nА1-В3-C5-D2-E4",
        "codes": ["9425", "3601"],
        "photo": "https://disk.yandex.ru/i/u37k2_mpsu0vvQ",  # ЗАМЕНИТЕ НА РЕАЛЬНУЮ ССЫЛКУ
    },
    {
        "id": 7,
        "name": "Турник",
        "riddle": "Перед вами текст, в котором смысл может оказаться ловушкой. \nВам нужно смотреть не на то, что она означает,\nа на то, как начинается этот путь.\nТихо вечер опустился на окраины города,\nУлицы стали длиннее в сумерках, чем были днём.\nРедкий прохожий спешил домой, не замечая витрин,\nНад крышами медленно собирались облака.\nИз открытого окна доносилась музыка из старого фильма,\nКаждый звук растворялся в прохладном воздухе.",
        "codes": ["7180", "2593"],
        "photo": None,
    },
    {
        "id": 8,
        "name": "Вывеска РОСПРОФЖЕЛ",
        "riddle": "Иногда, чтобы понять идею, нужно всего лишь взглянуть на неё наоборот.\nЭДЭЪНФЯ\nОРНПОРКШЪУ\nИнструкция: Позиция букв в правильном порядке, равна позициям букв в обратном",
        "codes": ["4652", "8371"],
        "photo": "https://disk.yandex.ru/i/98Qltfxn0PGUxg",  # ЗАМЕНИТЕ НА РЕАЛЬНУЮ ССЫЛКУ
    },
    {
        "id": 9,
        "name": "За главным корпусом",
        "riddle": "У дома есть лицо и тыл,\nФасад — парадный, гордый вид.\nА сзади — тишина и быль,\nГде ветер скомканный лежит.\nНайди то место за спиной\nУ главного, что как герой.\n",
        "codes": ["1947", "6280"],
        "photo": None,
    },
    {
        "id": 10,
        "name": "Лавка-качели",
        "riddle": "Сообщение передавали из уст в уста, но каждый человек немного изменял его. Полученное сообщение состоит из двух слов.\nИтоговое сообщение: АВКАЛ_ ЛИКАЧЕ",
        "codes": ["3065", "8714"],
        "photo": None,
    },
    {
        "id": 11,
        "name": "Беседка у главного корпуса",
        "riddle": "У главного дома есть правый сосед,\nОн крыт, но не прячет от солнца и бед.\nВ нём можно укрыться от дождика,\nНо это не дом, а лишь лёгкий навес,\nОн справа от входа, шагах в десяти,\nНа доски гляди — там подсказку ищи.",
        "codes": ["5239", "7801"],
        "photo": None,
    },
    {
        "id": 12,
        "name": "Стеклянная беседка",
        "riddle": "Не аквариум, но из стекла,\nВ нём скамейка и стол — дела.\nСидишь внутри — как на ладони,\nВсе вокруг, а ты — не в загоне.\nЧто это?",
        "codes": ["6492", "1537"],
        "photo": None,
    },
    {
        "id": 13,
        "name": "Горка низ",
        "riddle": "По нечетным дням я делал два шага вперёд, а по четным – один назад. Как хорошо, что эти дни позади.\nЕ Н Т Й В\nП З Й ",
        "codes": ["8706", "3945"],
        "photo": None,
    },
    {
        "id": 14,
        "name": "Горка верх",
        "riddle": "Подскажите, сколько времени, но помните, что в каждом матче минуты решают всё!",
        "codes": ["1524", "6908"],
        "photo": "https://disk.yandex.ru/i/Y3CtmwhFf8le3Q",  # ЗАМЕНИТЕ НА РЕАЛЬНУЮ ССЫЛКУ
    },
    {
        "id": 15,
        "name": "Беседка на Мосту",
        "riddle": "Над водой — деревянный гребень,\nНа гребне — домик, как навершье.\nОн не жилой, но в нём сидят,\nОн не стена, но преграждает взгляд.\nГде это?",
        "codes": ["4371", "2865"],
        "photo": None,
    },
    {
        "id": 16,
        "name": "Беседка 1",
        "riddle": "Спустишься с горы крутой —\nВидишь домик небольшой.\nУ реки стоит, в песке,\nВся из досок, налегке.\nТа, что первая у воды,\nОтгадай, куда идти?",
        "codes": ["7183", "9450"],
        "photo": None,
    },
    {
        "id": 17,
        "name": "Средняя беседка",
        "riddle": "Стоит в ряду, но не с краю,\nМежду соседками — как в раю.\nИз дерева, с крышей простой,/nСмотрит на речку с мечтой./nНе первая и не последняя —/nУгадай, какая именно?",
        "codes": ["2640", "5973"],
        "photo": None,
    },
    {
        "id": 18,
        "name": "Беседка в конце",
        "riddle": "Последняя в том ряду стоит,\nДальше всех от склона — глядит.\nТихо там, трава кругом,\nРечка слышится с трудом.\nКрайняя, в самом конце —\nПодходи, сиди в тепле.",
        "codes": ["8015", "3762"],
        "photo": None,
    },
    {
        "id": 19,
        "name": "Пирс",
        "riddle": "Он не пересекает воду. Он входит в неё",
        "codes": ["5498", "1207"],
        "photo": "https://disk.yandex.ru/i/A0cZSdXIUSecoQ",  # ЗАМЕНИТЕ НА РЕАЛЬНУЮ ССЫЛКУ
    },
    {
        "id": 20,
        "name": "Снаружи корпуса 3",
        "riddle": "Есть дом, на нём три входа,\nНо тебе не в них дорога.\nОбойди его, и ты поймешь—\nПодсказку там себе найдешь.",
        "codes": ["9352", "4681"],
        "photo": None,
    },
    {
        "id": 21,
        "name": "Беседка у корпуса 3",
        "riddle": "Есть дом под номером «три»,\nВ нём окон много, дверей — смотри.\nНо та, что крайняя, в конце ряда,\nСтоит особняком — не для входа, а для взгляда.\nРядом с ней — навес из досок,\nПод ним — скамья, а не порог.\nГде это? ",
        "codes": ["3764", "8190"],
        "photo": None,
    },
    {
        "id": 22,
        "name": "Вокруг корпуса 2",
        "riddle": "Жилой корпус — окна в ряд,\nК реке поближе, он у вод.\nЗа ним тропинка к трём беседкам,/nГде тихо речка всё поёт.\nНе заходи, а обойди —\nСнаружи то, что ждёт, найди.",
        "codes": ["2059", "7436"],
        "photo": None,
    },]

# Порядок локаций для каждой команды (индексы в массиве LOCATIONS)
TEAM_ROUTES = {
    "team_1": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
    "team_2": [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 0, 1],
    "team_3": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 0, 1, 2, 3],
    "team_4": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 0, 1, 2, 3, 4, 5],
    "team_5": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 0, 1, 2, 3, 4, 5, 6, 7],
    "team_6": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    "team_7": [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "team_8": [14, 15, 16, 17, 18, 19, 20, 21, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
    "team_9": [16, 17, 18, 19, 20, 21, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    "team_10": [18, 19, 20, 21, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
}

TEAM_NAMES = {
    "team_1": "Команда 1",
    "team_2": "Команда 2",
    "team_3": "Команда 3",
    "team_4": "Команда 4",
    "team_5": "Команда 5",
    "team_6": "Команда 6",
    "team_7": "Команда 7",
    "team_8": "Команда 8",
    "team_9": "Команда 9",
    "team_10": "Команда 10",
}

# ------------------------------------------------------------------
# СОСТОЯНИЯ ДЛЯ CONVERSATION HANDLER
# ------------------------------------------------------------------

(
    SELECTING_TEAM,
    ENTERING_TEAM_PASSWORD,
    ENTERING_ORGANIZER_PASSWORD,
) = range(3)

# ------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ------------------------------------------------------------------

def normalize(text: str) -> str:
    """Приводит текст к единому виду для сравнения"""
    return text.strip().lower().replace("ё", "е")

def get_team_state(team_id: str) -> Optional[dict]:
    """Получает состояние команды из БД"""
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT current_location_index, current_phase, codes_found, codes_entered FROM team_state WHERE team_id = ?",
            (team_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "team_id": team_id,
                "index": row["current_location_index"],
                "phase": row["current_phase"],
                "codes_found": row["codes_found"] if "codes_found" in row.keys() else 0,
                "codes_entered": row["codes_entered"] if "codes_entered" in row.keys() else ""
            }
    return None

def save_team_state(team_id: str, index: int, phase: str, codes_found: int = 0, codes_entered: str = ""):
    """Сохраняет состояние команды в БД"""
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO team_state (team_id, current_location_index, current_phase, codes_found, codes_entered, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (team_id, index, phase, codes_found, codes_entered))

def get_current_location(team_id: str) -> Tuple[Optional[dict], Optional[int]]:
    """Возвращает текущую локацию и её порядковый номер для команды"""
    state = get_team_state(team_id)
    if not state:
        return None, None
    
    route = TEAM_ROUTES[team_id]
    current_index = state["index"]
    
    if current_index >= len(route):
        return None, None
    
    location_index = route[current_index]
    return LOCATIONS[location_index], current_index

def is_location_locked(location_id: int, team_id: str) -> bool:
    """Проверяет, занята ли локация другой командой"""
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT locked_by_team FROM location_locks WHERE location_id = ?",
            (location_id,)
        )
        row = cursor.fetchone()
        
        if row and row["locked_by_team"] != team_id:
            # Локация занята другой командой
            return True
        elif row and row["locked_by_team"] == team_id:
            # Локация занята этой же командой (нормально)
            return False
        else:
            # Локация свободна
            return False

def lock_location(location_id: int, team_id: str):
    """Блокирует локацию для команды"""
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO location_locks (location_id, locked_by_team, locked_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (location_id, team_id))

def unlock_location(location_id: int):
    """Разблокирует локацию"""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM location_locks WHERE location_id = ?",
            (location_id,)
        )

def update_team_stats(team_id: str, wrong: bool = False, codes_found: int = 0):
    """Обновляет статистику команды"""
    with get_db() as conn:
        if wrong:
            conn.execute('''
                UPDATE team_stats 
                SET wrong_attempts = wrong_attempts + 1
                WHERE team_id = ?
            ''', (team_id,))
        else:
            conn.execute('''
                UPDATE team_stats 
                SET riddles_solved = riddles_solved + 1,
                    codes_entered = codes_entered + ?
                WHERE team_id = ?
            ''', (codes_found, team_id))

def reset_game():
    """Сбрасывает всю игру к начальному состоянию"""
    with get_db() as conn:
        # Очищаем состояние команд
        conn.execute("DELETE FROM team_state")
        conn.execute("DELETE FROM team_stats")
        conn.execute("DELETE FROM users WHERE is_organizer = 0")
        conn.execute("DELETE FROM location_locks")
        
        # Инициализируем начальное состояние для всех команд
        for team_id in TEAM_NAMES.keys():
            conn.execute('''
                INSERT INTO team_state (team_id, current_location_index, current_phase, codes_found, codes_entered)
                VALUES (?, 0, 'riddle', 0, '')
            ''', (team_id,))
            
            conn.execute('''
                INSERT INTO team_stats (team_id, start_time)
                VALUES (?, CURRENT_TIMESTAMP)
            ''', (team_id,))
    
    logger.info("Игра сброшена к начальному состоянию")

def is_organizer(chat_id: int) -> bool:
    """Проверяет, является ли пользователь организатором"""
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT is_organizer FROM users WHERE chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        return row and row["is_organizer"] == 1

def get_user_team(chat_id: int) -> Optional[str]:
    """Получает команду пользователя"""
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT team_id FROM users WHERE chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        return row["team_id"] if row else None

def build_team_selection_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора команды"""
    keyboard = []
    row = []
    for i in range(1, 11):
        team_id = f"team_{i}"
        row.append(InlineKeyboardButton(
            TEAM_NAMES[team_id],
            callback_data=f"select_{team_id}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔑 Вход для организаторов", callback_data="organizer_login")])
    
    return InlineKeyboardMarkup(keyboard)

def build_organizer_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для организаторов"""
    keyboard = [
        [InlineKeyboardButton("📊 Прогресс команд", callback_data="show_progress")],
        [InlineKeyboardButton("🔄 Сбросить игру", callback_data="reset_game")],
        [InlineKeyboardButton("🔌 Перезапустить бота", callback_data="restart_bot")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_check_location_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для проверки доступности локации"""
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить доступность", callback_data="check_location")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ------------------------------------------------------------------
# ОБРАБОТЧИКИ КОМАНД
# ------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало работы с ботом"""
    chat_id = update.effective_chat.id
    username = update.effective_user.username or str(chat_id)
    
    # Проверяем, зарегистрирован ли пользователь
    with get_db() as conn:
        cursor = conn.execute("SELECT team_id, is_organizer FROM users WHERE chat_id = ?", (chat_id,))
        user = cursor.fetchone()
        
        if user and user["team_id"]:
            # Пользователь уже зарегистрирован как команда
            team_id = user["team_id"]
            state = get_team_state(team_id)
            
            if state and state["index"] >= len(TEAM_ROUTES[team_id]):
                await update.message.reply_text(
                    f"🏁 Команда «{TEAM_NAMES[team_id]}» завершила маршрут!"
                )
            else:
                await send_current_task(chat_id, team_id, context)
            return ConversationHandler.END
        elif user and user["is_organizer"]:
            # Организатор
            await show_organizer_menu(chat_id, context)
            return ConversationHandler.END
    
    # Новый пользователь
    await update.message.reply_text(
        """
    Приветствуем вас на мероприятии «Шаг в Zавтра»

    Правила интерактивной игры "Дозор ППОС"
    Каждой команде необходимо пройти 22 локации. Для этого необходимо выполнить все задания. В них зашифровано конкретное место на базе отдыха. Ваша задача — понять, что это за место. Как только поняли, где это, — бегом туда. На этом месте спрятано 2 цифровых кода (также они могут быть написаны на объектах). Каждый код представляет собой четырехзначное число. Найдите оба кода! Введите найденные цифры в ответ боту. Если оба кода верные — бот выдаст вам следующую подсказку.

    Важно: локация блокируется для других команд, пока команда ищет коды. Другие команды не смогут получить загадку для этой локации, пока она занята.

    Выберите вашу команду:
    """,
        reply_markup=build_team_selection_keyboard()
    )
    
    return SELECTING_TEAM

async def show_organizer_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню организатора"""
    await context.bot.send_message(
        chat_id=chat_id,
        text="🔐 *Меню организатора*\n\n"
             "Выберите действие:",
        reply_markup=build_organizer_keyboard(),
        parse_mode="Markdown"
    )

async def team_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора команды"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "organizer_login":
        await query.edit_message_text(
            "🔑 Введите пароль организатора:"
        )
        return ENTERING_ORGANIZER_PASSWORD
    
    team_id = query.data.replace("select_", "")
    context.user_data["selected_team"] = team_id
    
    await query.edit_message_text(
        f"🔐 Введите пароль для команды «{TEAM_NAMES[team_id]}»:"
    )
    
    return ENTERING_TEAM_PASSWORD

async def team_password_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка пароля команды"""
    chat_id = update.effective_chat.id
    username = update.effective_user.username or str(chat_id)
    password = update.message.text.strip()
    team_id = context.user_data.get("selected_team")
    
    if team_id in TEAM_PASSWORDS and TEAM_PASSWORDS[team_id] == password:
        # Сохраняем пользователя
        with get_db() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO users (chat_id, username, team_id, is_organizer)
                VALUES (?, ?, ?, 0)
            ''', (chat_id, username, team_id))
            
            # Инициализируем состояние команды, если нужно
            conn.execute('''
                INSERT OR IGNORE INTO team_state (team_id, current_location_index, current_phase, codes_found, codes_entered)
                VALUES (?, 0, 'riddle', 0, '')
            ''', (team_id,))
            
            # Инициализируем статистику
            conn.execute('''
                INSERT OR IGNORE INTO team_stats (team_id, start_time)
                VALUES (?, CURRENT_TIMESTAMP)
            ''', (team_id,))
        
        await update.message.reply_text(
            f"✅ Вы успешно вошли как куратор команды «{TEAM_NAMES[team_id]}»!\n\n"
            f"Маршрут состоит из {len(TEAM_ROUTES[team_id])} локаций.\n"
            f"На каждой локации нужно найти 2 кода."
        )
        
        await send_current_task(chat_id, team_id, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "❌ Неверный пароль! Попробуйте ещё раз или /cancel для отмены."
        )
        return ENTERING_TEAM_PASSWORD

async def organizer_password_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка пароля организатора"""
    chat_id = update.effective_chat.id
    username = update.effective_user.username or str(chat_id)
    password = update.message.text.strip()
    
    if password == ORGANIZER_PASSWORD:
        with get_db() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO users (chat_id, username, team_id, is_organizer)
                VALUES (?, ?, NULL, 1)
            ''', (chat_id, username))
        
        await update.message.reply_text(
            "✅ Вы вошли как организатор!"
        )
        await show_organizer_menu(chat_id, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "❌ Неверный пароль! Попробуйте ещё раз или /cancel для отмены."
        )
        return ENTERING_ORGANIZER_PASSWORD

async def organizer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок организатора"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    if not is_organizer(chat_id):
        await query.edit_message_text("❌ У вас нет прав организатора.")
        return
    
    if query.data == "show_progress":
        await show_progress_message(chat_id, context)
    elif query.data == "reset_game":
        await confirm_reset_game(chat_id, context)
    elif query.data == "restart_bot":
        await restart_bot(chat_id, context)

async def check_location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка проверки доступности локации"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    team_id = get_user_team(chat_id)
    
    if not team_id:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь — наберите /start")
        return
    
    location, current_index = get_current_location(team_id)
    if not location:
        await query.edit_message_text("🏁 Вы уже завершили маршрут!")
        return
    
    # Проверяем, не занята ли локация
    if is_location_locked(location["id"], team_id):
        # Локация все еще занята
        await query.edit_message_text(
            f"⏳ Локация «{location['name']}» всё ещё занята другой командой.\n"
            f"Пожалуйста, подождите ещё немного.",
            reply_markup=build_check_location_keyboard()
        )
    else:
        # Локация освободилась
        await query.edit_message_text(
            f"✅ Локация «{location['name']}» освободилась!\n"
            f"Получаю задание..."
        )
        await send_current_task(chat_id, team_id, context)

async def show_progress_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Показывает прогресс команд организатору"""
    with get_db() as conn:
        cursor = conn.execute('''
            SELECT ts.team_id, ts.current_location_index, ts.current_phase, ts.codes_found,
                   tst.riddles_solved, tst.codes_entered, tst.wrong_attempts,
                   tst.finish_time, tst.completed
            FROM team_state ts
            LEFT JOIN team_stats tst ON ts.team_id = tst.team_id
            ORDER BY ts.team_id
        ''')
        rows = cursor.fetchall()
        
        # Получаем информацию о занятых локациях
        locks = conn.execute('''
            SELECT location_id, locked_by_team FROM location_locks
        ''').fetchall()
    
    if not rows:
        await context.bot.send_message(chat_id, "Нет данных о командах.")
        return
    
    message = "📊 *Прогресс команд:*\n\n"
    
    for row in rows:
        team_id = row["team_id"]
        team_name = TEAM_NAMES.get(team_id, team_id)
        location_index = row["current_location_index"]
        phase = row["current_phase"]
        codes_found = row["codes_found"] if "codes_found" in row.keys() else 0
        total_locations = len(TEAM_ROUTES[team_id])
        
        if row["completed"]:
            status = f"✅ Завершили! ({location_index}/{total_locations})"
        else:
            if phase == "riddle":
                status = f"🧩 Отгадывают загадку ({location_index}/{total_locations})"
            elif phase == "code":
                status = f"🔍 Ищут коды ({codes_found}/2) ({location_index}/{total_locations})"
            else:
                status = f"📍 {location_index}/{total_locations}"
        
        message += f"*{team_name}*: {status}\n"
        
        if row["wrong_attempts"] > 0:
            message += f"  Ошибок: {row['wrong_attempts']}\n"
        
        if row["finish_time"]:
            message += f"  Финиш: {row['finish_time']}\n"
    
    # Добавляем информацию о занятых локациях
    if locks:
        message += "\n🔒 *Занятые локации:*\n"
        for lock in locks:
            location = LOCATIONS[lock["location_id"] - 1]
            team = TEAM_NAMES.get(lock["locked_by_team"], lock["locked_by_team"])
            message += f"• {location['name']} — {team}\n"
    
    await context.bot.send_message(
        chat_id,
        message,
        parse_mode="Markdown",
        reply_markup=build_organizer_keyboard()
    )

async def confirm_reset_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение сброса игры"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, сбросить", callback_data="confirm_reset"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
        ]
    ]
    
    await context.bot.send_message(
        chat_id,
        "⚠️ *ВНИМАНИЕ!*\n\n"
        "Вы уверены, что хотите сбросить игру?\n"
        "Это действие удалит весь прогресс всех команд и очистит статистику.\n\n"
        "Это действие нельзя отменить!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def confirm_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка подтверждения сброса"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    if not is_organizer(chat_id):
        await query.edit_message_text("❌ У вас нет прав организатора.")
        return
    
    if query.data == "confirm_reset":
        reset_game()
        await query.edit_message_text(
            "✅ Игра успешно сброшена!\n"
            "Все команды начнут с начала."
        )
        await show_organizer_menu(chat_id, context)
    elif query.data == "cancel_reset":
        await query.edit_message_text("❌ Сброс отменён.")
        await show_organizer_menu(chat_id, context)

async def restart_bot(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Перезапуск бота"""
    await context.bot.send_message(
        chat_id,
        "🔄 Бот будет перезапущен...\n"
        "Подождите несколько секунд."
    )
    
    # Отправляем сигнал для перезапуска
    os._exit(0)  # Полный выход из программы

async def send_current_task(chat_id: int, team_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет текущее задание команде"""
    location, current_index = get_current_location(team_id)
    
    if not location:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🏁 Поздравляем! Команда «{TEAM_NAMES[team_id]}» завершила маршрут!\n"
                 f"Все {len(TEAM_ROUTES[team_id])} локаций пройдены!"
        )
        return
    
    state = get_team_state(team_id)
    
    if state["phase"] == "riddle":
        # Проверяем, не занята ли локация другой командой
        if is_location_locked(location["id"], team_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏳ Локация «{location['name']}» сейчас занята другой командой.\n"
                     f"Пожалуйста, подождите, пока она освободится.",
                reply_markup=build_check_location_keyboard()
            )
            return
        
        total_locations = len(TEAM_ROUTES[team_id])
        progress = f"📍 Локация {current_index + 1} из {total_locations}"
        message_text = f"{progress}\n\n🧩 Загадка:\n{location['riddle']}\n\nОтгадайте место и отправьте ответ."
        
        # Отправляем фото, если оно есть
        if location.get("photo"):
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=location["photo"],
                    caption=message_text
                )
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text
            )
    elif state["phase"] == "code":
        # Команда уже отгадала загадку и ищет коды
        codes_found = state.get("codes_found", 0)
        remaining = 2 - codes_found
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔍 Вы находитесь на локации «{location['name']}».\n"
                 f"Найдено кодов: {codes_found} из 2.\n"
                 f"Осталось найти: {remaining} код(а).\n\n"
                 f"Введите следующий код:"
        )

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответов пользователей"""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    # Проверяем, организатор ли это
    if is_organizer(chat_id):
        await show_organizer_menu(chat_id, context)
        return
    
    team_id = get_user_team(chat_id)
    if not team_id:
        await update.message.reply_text(
            "Сначала зарегистрируйтесь — наберите /start"
        )
        return
    
    location, current_index = get_current_location(team_id)
    if not location:
        await update.message.reply_text(
            f"🏁 Команда «{TEAM_NAMES[team_id]}» уже завершила маршрут!"
        )
        return
    
    state = get_team_state(team_id)
    
    # Проверяем фазу
    if state["phase"] == "riddle":
        # Проверяем, не занята ли локация другой командой
        if is_location_locked(location["id"], team_id):
            await update.message.reply_text(
                f"⏳ Локация «{location['name']}» сейчас занята другой командой.\n"
                f"Пожалуйста, подождите, пока она освободится.",
                reply_markup=build_check_location_keyboard()
            )
            return
        
        # Отгадываем загадку
        if normalize(text) == normalize(location["name"]):
            # Правильный ответ на загадку
            # Блокируем локацию для этой команды
            lock_location(location["id"], team_id)
            
            await update.message.reply_text(
                f"✅ Правильно! Это {location['name']}.\n\n"
                f"Теперь найдите 2 цифровых кода в этой локации.\n"
                f"Введите первый код:"
            )
            
            save_team_state(team_id, state["index"], "code", 0, "")
            update_team_stats(team_id, wrong=False, codes_found=0)
        else:
            # Неправильный ответ
            update_team_stats(team_id, wrong=True)
            await update.message.reply_text(
                "❌ Неверно! Подумайте ещё."
            )
    
    elif state["phase"] == "code":
        # Проверяем коды
        codes = location["codes"]
        codes_entered = state.get("codes_entered", "").split(",") if state.get("codes_entered") else []
        codes_entered = [code for code in codes_entered if code]  # Очищаем от пустых строк
        
        # Проверяем, не был ли этот код уже введен
        if text in codes and text not in codes_entered:
            # Правильный код
            codes_entered.append(text)
            codes_found = len(codes_entered)
            
            if codes_found == 2:
                # Оба кода найдены
                await update.message.reply_text(
                    f"🎉 Отлично! Оба кода верные!\n"
                    f"Локация «{location['name']}» пройдена."
                )
                
                # Разблокируем локацию
                unlock_location(location["id"])
                
                # Переходим к следующей локации
                new_index = state["index"] + 1
                if new_index >= len(TEAM_ROUTES[team_id]):
                    # Маршрут завершён
                    save_team_state(team_id, new_index, "completed", 0, "")
                    
                    with get_db() as conn:
                        conn.execute('''
                            UPDATE team_stats 
                            SET finish_time = CURRENT_TIMESTAMP, completed = 1
                            WHERE team_id = ?
                        ''', (team_id,))
                    
                    await update.message.reply_text(
                        f"🏁 ПОЗДРАВЛЯЕМ! Команда «{TEAM_NAMES[team_id]}» завершила весь маршрут!\n"
                        f"Все {len(TEAM_ROUTES[team_id])} локаций пройдены!"
                    )
                else:
                    save_team_state(team_id, new_index, "riddle", 0, "")
                    update_team_stats(team_id, wrong=False, codes_found=2)
                    await send_current_task(chat_id, team_id, context)
            else:
                # Один код найден, ждем второй
                await update.message.reply_text(
                    f"✅ Код верный! ({codes_found}/2)\n"
                    f"Введите второй код:"
                )
                save_team_state(team_id, state["index"], "code", codes_found, ",".join(codes_entered))
                update_team_stats(team_id, wrong=False, codes_found=1)
        else:
            # Неправильный код или уже введен
            if text in codes_entered:
                await update.message.reply_text(
                    "❌ Этот код уже был введен! Введите второй код."
                )
            else:
                update_team_stats(team_id, wrong=True)
                await update.message.reply_text(
                    "❌ Неверный код! Ищите внимательнее."
                )

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает прогресс всех команд (только для организаторов)"""
    chat_id = update.effective_chat.id
    
    if not is_organizer(chat_id):
        await update.message.reply_text(
            "❌ Эта команда доступна только организаторам.\n"
            "Введите пароль организатора через /start"
        )
        return
    
    await show_progress_message(chat_id, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена текущего действия"""
    await update.message.reply_text(
        "Действие отменено. Используйте /start для начала."
    )
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь"""
    help_text = """
🤖 *Помощь по боту «Дозор»*

*Для команд:*
1. Нажмите /start и выберите команду
2. Введите пароль команды
3. Получите загадку и отгадайте место
4. Найдите 2 кода в локации и введите их
5. Получите следующую загадку

*Важно:*
- Локация блокируется для других команд, пока вы ищете коды
- Если локация занята другой командой, вы не сможете получить загадку
- Нужно найти оба кода, чтобы пройти локацию
- Коды можно вводить в любом порядке

*Для организаторов:*
- /start - войти как организатор
- /progress - посмотреть прогресс всех команд
- Кнопка "Сбросить игру" - сброс всего прогресса
- Кнопка "Перезапустить бота" - перезапуск системы

*Команды:*
/start - начать или продолжить игру
/progress - прогресс команд (для организаторов)
/help - это сообщение

Удачи в игре! 🍀
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

# ------------------------------------------------------------------
# ОБРАБОТЧИК ОШИБОК
# ------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if isinstance(update, Update) and update.effective_chat:
            await update.effective_chat.send_message(
                "⚠️ Произошла ошибка. Пожалуйста, попробуйте ещё раз."
            )
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")

# ------------------------------------------------------------------
# ЗАПУСК БОТА
# ------------------------------------------------------------------

def main():
    """Главная функция запуска бота"""
    try:
        # Инициализируем базу данных
        init_db()
        # Выполняем миграцию если нужно
        migrate_db()
        logger.info("База данных инициализирована")
        
        # Создаем приложение
        builder = ApplicationBuilder().token(BOT_TOKEN)
        
        # Настройка времени ожидания
        builder.connect_timeout(30)  # Таймаут подключения (секунды)
        builder.read_timeout(30)     # Таймаут чтения (секунды)
        builder.write_timeout(30)    # Таймаут записи (секунды)
        
        # Создаем приложение
        app = builder.build()
        
        # Conversation handler для регистрации
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                SELECTING_TEAM: [
                    CallbackQueryHandler(team_selected)
                ],
                ENTERING_TEAM_PASSWORD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, team_password_entered)
                ],
                ENTERING_ORGANIZER_PASSWORD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, organizer_password_entered)
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
        
        # Добавляем обработчики
        app.add_handler(conv_handler)
        app.add_handler(CommandHandler("progress", progress))
        app.add_handler(CommandHandler("help", help_command))
        
        # Обработчики callback-запросов для организаторов
        app.add_handler(CallbackQueryHandler(organizer_callback, pattern="^(show_progress|reset_game|restart_bot)$"))
        app.add_handler(CallbackQueryHandler(confirm_reset_callback, pattern="^(confirm_reset|cancel_reset)$"))
        app.add_handler(CallbackQueryHandler(check_location_callback, pattern="^check_location$"))
        
        # Обработчик текстовых сообщений (ответы на загадки и коды)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
        
        # Обработчик ошибок
        app.add_error_handler(error_handler)
        
        logger.info("🚀 Бот «Дозор» запущен и готов к работе!")
        
        # Запускаем бота
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,  # Игнорировать старые обновления
        )
        
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()