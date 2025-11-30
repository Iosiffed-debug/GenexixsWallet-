from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio
import sqlite3
import aiohttp
from tronpy import Tron
from tronpy.keys import PrivateKey
from web3 import Web3
import hashlib
from datetime import datetime

# === НАСТРОЙКИ ===
BOT_TOKEN = '8547527507:AAFgh61OMpYX05sMT_39V3JgS07TfMEsLLg'  # ← ТВОЙ ТОКЕН
ADMIN_ID = 1352689663  # ← ТВОЙ ID
BOT_USERNAME = "GenexixsWallet"  # ← ТВОЙ @username БЕЗ @
COMMISSION_RATE = 0.07  # 7% — тебе
REFERRAL_RATE = 0.02   # 2% — рефереру

# === ПРИВАТНЫЕ КЛЮЧИ (ВСТАВЬ СВОИ!) ===
PRIVATE_KEYS = {
    'TRC20': 'd5f0dc5d92d004423460cd830d595a36792e7e5c4f3ddebae0916ac580da4f75',
    'ERC20': '5c7342305cccd5ece50ff1dca00773b8a4a96a38d43ef20270fa0164a10c6d4c',
    'Polygon': '5c7342305cccd5ece50ff1dca00773b8a4a96a38d43ef20270fa0164a10c6d4c'
}

# === WEB3 ===
w3_erc = Web3(Web3.HTTPProvider('https://eth-mainnet.g.alchemy.com/v2/demo'))
w3_poly = Web3(Web3.HTTPProvider('https://polygon-rpc.com'))

ERC20_ADDRESS = w3_erc.eth.account.from_key(PRIVATE_KEYS['ERC20']).address
POLYGON_ADDRESS = w3_poly.eth.account.from_key(PRIVATE_KEYS['Polygon']).address

USDT_ERC20 = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDT_POLYGON = "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"

# === TRON ===
TRON = Tron(network='mainnet')
TRC20_ADDRESS = PrivateKey(bytes.fromhex(PRIVATE_KEYS['TRC20'])).public_key.to_base58check_address()

# === БАЗА ===
conn = sqlite3.connect('wallet.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    trc20_address TEXT,
    erc20_address TEXT,
    polygon_address TEXT,
    balance_trc20 REAL DEFAULT 0,
    balance_erc20 REAL DEFAULT 0,
    balance_polygon REAL DEFAULT 0,
    ref_code TEXT,
    referrer_id INTEGER,
    banned INTEGER DEFAULT 0
)
''')
cursor.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, amount REAL, timestamp TEXT)')
conn.commit()

# === FSM ===
class AdminState(StatesGroup):
    waiting_broadcast = State()
    waiting_ban = State()

class WithdrawState(StatesGroup):
    waiting_network = State()
    waiting_address = State()
    waiting_amount = State()

# === ИНИЦИАЛИЗАЦИЯ ===
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN, parse_mode='HTML')
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# === ЦЕНЫ ===
async def get_prices():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,solana,zcash&vs_currencies=usd') as resp:
                data = await resp.json()
        return {
            'BTC': f"${data['bitcoin']['usd']:,}",
            'SOL': f"${data['solana']['usd']:.2f}",
            'ZEC': f"${data['zcash']['usd']:.2f}"
        }
    except:
        return {'BTC': 'N/A', 'SOL': 'N/A', 'ZEC': 'N/A'}

# === ЛОГИ ===
def log_action(user_id, action, amount=0):
    cursor.execute("INSERT INTO logs (user_id, action, amount, timestamp) VALUES (?, ?, ?, ?)",
                  (user_id, action, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()

# === СКАНИРОВАНИЕ ===
async def scan_trc20():
    while True:
        try:
            cursor.execute("SELECT user_id, trc20_address FROM users WHERE trc20_address IS NOT NULL AND banned = 0")
            for uid, addr in cursor.fetchall():
                bal = TRON.get_account_balance(addr) / 1_000_000
                cursor.execute("SELECT balance_trc20 FROM users WHERE user_id = ?", (uid,))
                cur = cursor.fetchone()[0]
                if bal > cur:
                    diff = bal - cur
                    cursor.execute("UPDATE users SET balance_trc20 = ? WHERE user_id = ?", (bal, uid))
                    conn.commit()
                    await bot.send_message(uid, f"Пополнение TRC20: +{diff:.6f} USDT")
                    await bot.send_message(ADMIN_ID, f"Пополнение от {uid}: +{diff:.6f} USDT (TRC20)")
                    log_action(uid, "deposit_trc20", diff)
        except: pass
        await asyncio.sleep(30)

async def scan_erc20():
    while True:
        try:
            contract = w3_erc.eth.contract(address=USDT_ERC20, abi=[{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}])
            cursor.execute("SELECT user_id, erc20_address FROM users WHERE erc20_address IS NOT NULL AND banned = 0")
            for uid, addr in cursor.fetchall():
                bal = contract.functions.balanceOf(addr).call() / 1_000_000
                cursor.execute("SELECT balance_erc20 FROM users WHERE user_id = ?", (uid,))
                cur = cursor.fetchone()[0]
                if bal > cur:
                    diff = bal - cur
                    cursor.execute("UPDATE users SET balance_erc20 = ? WHERE user_id = ?", (bal, uid))
                    conn.commit()
                    await bot.send_message(uid, f"Пополнение ERC20: +{diff:.6f} USDT")
                    await bot.send_message(ADMIN_ID, f"Пополнение от {uid}: +{diff:.6f} USDT (ERC20)")
                    log_action(uid, "deposit_erc20", diff)
        except: pass
        await asyncio.sleep(60)

async def scan_polygon():
    while True:
        try:
            contract = w3_poly.eth.contract(address=USDT_POLYGON, abi=[{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}])
            cursor.execute("SELECT user_id, polygon_address FROM users WHERE polygon_address IS NOT NULL AND banned = 0")
            for uid, addr in cursor.fetchall():
                bal = contract.functions.balanceOf(addr).call() / 1_000_000
                cursor.execute("SELECT balance_polygon FROM users WHERE user_id = ?", (uid,))
                cur = cursor.fetchone()[0]
                if bal > cur:
                    diff = bal - cur
                    cursor.execute("UPDATE users SET balance_polygon = ? WHERE user_id = ?", (bal, uid))
                    conn.commit()
                    await bot.send_message(uid, f"Пополнение Polygon: +{diff:.6f} USDT")
                    await bot.send_message(ADMIN_ID, f"Пополнение от {uid}: +{diff:.6f} USDT (Polygon)")
                    log_action(uid, "deposit_polygon", diff)
        except: pass
        await asyncio.sleep(60)

# === СТАРТ ===
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    uid = message.from_user.id
    args = message.text.split()
    referrer_id = None

    if len(args) > 1:
        ref_code = args[1]
        cursor.execute("SELECT user_id FROM users WHERE ref_code = ?", (ref_code,))
        row = cursor.fetchone()
        if row:
            referrer_id = row[0]
            await bot.send_message(referrer_id, f"Новый реферал: @{message.from_user.username or uid}")

    cursor.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
    row = cursor.fetchone()

    if not row:
        trc_priv = PrivateKey.random()
        trc_addr = trc_priv.public_key.to_base58check_address()
        eth_acct = w3_erc.eth.account.create()
        eth_addr = eth_acct.address
        poly_addr = eth_addr
        ref_code = hashlib.md5(str(uid).encode()).hexdigest()[:8]
        cursor.execute("INSERT INTO users (user_id, trc20_address, erc20_address, polygon_address, ref_code, referrer_id) VALUES (?, ?, ?, ?, ?, ?)",
                      (uid, trc_addr, eth_addr, poly_addr, ref_code, referrer_id))
        conn.commit()
    else:
        _, trc_addr, eth_addr, poly_addr, _, _, _, _, _ = row

    bal_trc = cursor.execute("SELECT balance_trc20 FROM users WHERE user_id = ?", (uid,)).fetchone()[0]
    bal_erc = cursor.execute("SELECT balance_erc20 FROM users WHERE user_id = ?", (uid,)).fetchone()[0]
    bal_poly = cursor.execute("SELECT balance_polygon FROM users WHERE user_id = ?", (uid,)).fetchone()[0]
    prices = await get_prices()
    ref_link = f"https://t.me/{BOT_USERNAME}?start={ref_code}"

    await message.answer(
        f"<b>USDT Кошелёк</b>\n\n"
        f"<b>TRC20:</b> <code>{trc_addr}</code>\nБаланс: <b>{bal_trc:.6f}</b>\n\n"
        f"<b>ERC20:</b> <code>{eth_addr}</code>\nБаланс: <b>{bal_erc:.6f}</b>\n\n"
        f"<b>Polygon:</b> <code>{poly_addr}</code>\nБаланс: <b>{bal_poly:.6f}</b>\n\n"
        f"<b>Рефералка (2%):</b>\n{ref_link}\n\n"
        f"<b>Цены:</b>\n₿ BTC: {prices['BTC']}\n◉ SOL: {
