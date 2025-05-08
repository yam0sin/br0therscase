import asyncio
import random
import requests
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

API_TOKEN = '7140261010:AAG4Yw-ZX-vmSVZiYtdoK8hMsM_YkUDAK4c'
BASE_URL = 'https://script.google.com/macros/s/AKfycbw05PInvb1sppOcBFroZmzaKqb6Njv-H34LSsKCHJsPUEoogDQ8AfCxIMI80VICSWQT0A/exec'

SHEET_USER_URL = BASE_URL + '?type=users'
SHEET_SKINS_URL = BASE_URL + '?type=skins'
SHEET_HISTORY_URL = BASE_URL + '?type=history'

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

user_sessions = {}
waiting_for_login = set()

def load_users_from_sheet():
    try:
        data = requests.get(SHEET_USER_URL).json()
        return [{
            'username': row['Пользователь'],
            'password': row['Пароль'],
            'balance': int(row['Баланс'])
        } for row in data if 'Пользователь' in row]
    except Exception as e:
        print("[ERROR] Ошибка загрузки:", e)
        return []

def update_user_balance(username, new_balance):
    try:
        requests.post(SHEET_USER_URL, json={
            "action": "update_balance",
            "username": username,
            "new_balance": new_balance
        })
    except Exception as e:
        print("[WARN] Не удалось обновить баланс:", e)

def send_to_google_sheet(user, dropped_skin):
    try:
        requests.post(SHEET_HISTORY_URL, json={
            "username": user['username'],
            "skin": dropped_skin['Скин'],
            "rarity": dropped_skin['Редкость'],
            "quality": dropped_skin.get('Качество', ''),
            "image_url": dropped_skin.get('Фотография', '')
        })
    except Exception as e:
        print("[WARN] Не удалось записать дроп:", e)

@dp.message(F.text == "/start")
async def start(message: Message):
    await message.answer("\U0001F44B Добро пожаловать в br0therscase bot!\nНапиши <b>/login</b> чтобы войти.")

@dp.message(F.text == "/login")
async def login(message: Message):
    waiting_for_login.add(message.from_user.id)
    await message.answer("Введи логин и пароль через пробел:\nПример: <code>ivan password123</code>")

@dp.message()
async def process_login(message: Message):
    user_id = message.from_user.id
    if message.text.startswith("/"):
        return
    if user_id not in waiting_for_login:
        await message.answer("Неизвестная команда. Напиши /start или /login.")
        return

    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Неверный формат. Введи логин и пароль через пробел.")
        return

    login, password = parts
    users = load_users_from_sheet()
    user = next((u for u in users if u['username'] == login and u['password'] == password), None)

    if user:
        user_sessions[user_id] = user['username']
        waiting_for_login.discard(user_id)
        await message.answer(f"✅ Успешный вход как <b>{login}</b>\n💰 Баланс: <b>{user['balance']}</b> очков")
    else:
        await message.answer("❌ Неверный логин или пароль")

@dp.message(F.text == "/open_case")
async def open_case(message: Message):
    tg_id = message.from_user.id
    username = user_sessions.get(tg_id)

    if not username:
        await message.answer("❌ Сначала авторизуйся командой /login")
        return

    users = load_users_from_sheet()
    user = next((u for u in users if u['username'] == username), None)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return

    if user['balance'] < 10 and username != 'admin':
        await message.answer("❌ Недостаточно баланса. Нужно 10 очков.")
        return

    try:
        skins_data = requests.get(SHEET_SKINS_URL).json()
    except Exception as e:
        await message.answer("⚠️ Ошибка загрузки скинов.")
        return

    rarity_weights = {
        'Ширпотреб': 76,
        'Промышленное': 18,
        'Армейское': 4,
        'Запрещённое': 0.98,
        'Засекреченное': 0.21,
        'Тайное': 0.05
    }
    rarity_buckets = {r: [] for r in rarity_weights}
    for row in skins_data:
        name = row.get('Скин', '').strip()
        rarity = row.get('Редкость', '').strip()
        image = row.get('Фотография', '').strip()
        if name and rarity and image and rarity in rarity_buckets:
            rarity_buckets[rarity].append(row)

    filtered_weights = {r: w for r, w in rarity_weights.items() if rarity_buckets[r]}
    if not filtered_weights:
        await message.answer("❌ Нет доступных скинов.")
        return

    chosen_rarity = random.choices(list(filtered_weights.keys()), weights=filtered_weights.values())[0]
    dropped_skin = random.choice(rarity_buckets[chosen_rarity])

    if username != 'admin':
        new_balance = user['balance'] - 10
        update_user_balance(username, new_balance)
    else:
        new_balance = user['balance']

    send_to_google_sheet(user, dropped_skin)

    name = dropped_skin['Скин']
    quality = dropped_skin.get('Качество', 'Неизвестно')
    rarity = dropped_skin['Редкость']
    price = dropped_skin.get('Цена', '0')
    img = dropped_skin['Фотография']

    text = (
        f"🎉 Выпал скин: <b>{name}</b>\n"
        f"💎 Редкость: <b>{rarity}</b>\n"
        f"⚙️ Качество: <b>{quality}</b>\n"
        f"💰 Цена: <b>{price} очков</b>\n"
        f"💼 Баланс: <b>{new_balance} очков</b>"
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="💵 Продать", callback_data=f"sell|{name}|{quality}")
    kb.button(text="📦 Вывести", callback_data=f"withdraw|{name}|{quality}")
    kb.adjust(2)

    await message.answer_photo(photo=img, caption=text, reply_markup=kb.as_markup())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())