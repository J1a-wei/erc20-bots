import os
import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from web3 import Web3
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
import logging
from datetime import datetime
import asyncio

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 配置变量
BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
ETHEREUM_RPC_URL = os.getenv('ETHEREUM_RPC_URL')

# 全局变量
application = None

# 检查必要的环境变量
if not BOT_TOKEN:
    logger.error("错误：请设置 TG_BOT_TOKEN 环境变量")
    sys.exit(1)

# 初始化 Web3
try:
    w3 = Web3(Web3.HTTPProvider(ETHEREUM_RPC_URL))
    if not w3.is_connected():
        logger.error("无法连接到以太坊节点")
        sys.exit(1)
    logger.info("成功连接到以太坊节点")
except Exception as e:
    logger.error(f"初始化 Web3 失败: {str(e)}")
    sys.exit(1)

# 数据库配置
DB_DIR = os.getenv('DB_DIR', 'data')
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, 'tokens.db')

# ERC20代币ABI
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]

def format_token_amount(amount_wei: str, decimals: int):
    """格式化代币数量，考虑精度并保留两位小数"""
    amount = float(amount_wei) / (10 ** decimals)
    return "{:,.2f}".format(amount)

def get_token_info(contract_address):
    """获取代币信息"""
    # 确保使用 checksum 地址
    contract_address = w3.to_checksum_address(contract_address)
    contract = w3.eth.contract(address=contract_address, abi=ERC20_ABI)
    try:
        name = contract.functions.name().call()
        symbol = contract.functions.symbol().call()
        decimals = contract.functions.decimals().call()
        total_supply = str(contract.functions.totalSupply().call())
        return name, symbol, decimals, total_supply
    except Exception as e:
        logger.error(f"获取代币信息失败: {str(e)}")
        raise

def format_supply(supply):
    """格式化供应量，添加千位分隔符"""
    return "{:,}".format(int(supply))

def get_chat_ids():
    """获取所有需要通知的聊天ID"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT chat_id FROM chat_subscriptions')
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"获取聊天ID时发生错误: {str(e)}")
        return []
    finally:
        conn.close()

def init_db():
    """初始化数据库"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 创建代币表（添加name字段）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                contract_address TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decimals INTEGER NOT NULL DEFAULT 18
            )
        ''')
        
        # 创建供应量历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS supply_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address TEXT,
                total_supply TEXT,
                circulating_supply TEXT,
                timestamp TEXT,
                FOREIGN KEY (contract_address) REFERENCES tokens(contract_address)
            )
        ''')

        # 创建锁仓地址表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS locked_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address TEXT,
                address TEXT NOT NULL,
                description TEXT,
                FOREIGN KEY (contract_address) REFERENCES tokens(contract_address)
            )
        ''')

        # 创建聊天订阅表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_subscriptions (
                chat_id INTEGER,
                contract_address TEXT,
                PRIMARY KEY (chat_id, contract_address),
                FOREIGN KEY (contract_address) REFERENCES tokens(contract_address)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("数据库初始化成功")
    except Exception as e:
        logger.error(f"数据库初始化失败: {str(e)}")
        raise

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    await update.message.reply_text(
        "欢迎使用ERC20代币监控机器人！\n\n"
        "可用命令：\n"
        "/add <contract_address> - 添加代币监控\n"
        "/list - 查看监控的代币列表\n"
        "/lock <contract_address> <lock_address> <description> - 添加锁仓地址\n"
        "/delete <id> - 删除指定ID的代币监控"
    )

async def add_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/add命令，添加新的代币监控"""
    if len(context.args) != 1:
        await update.message.reply_text("格式错误！请使用: /add <contract_address>")
        return

    contract_address = context.args[0]
    chat_id = update.effective_chat.id

    if not w3.is_address(contract_address):
        await update.message.reply_text("无效的合约地址！")
        return

    try:
        # 转换为 checksum 地址
        contract_address = w3.to_checksum_address(contract_address)
        
        # 获取代币信息
        name, symbol, decimals, total_supply = get_token_info(contract_address)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 添加代币
        cursor.execute('''
            INSERT INTO tokens (contract_address, name, symbol, decimals) 
            VALUES (?, ?, ?, ?)
        ''', (contract_address, name, symbol, decimals))
        
        # 添加聊天订阅
        cursor.execute('''
            INSERT OR IGNORE INTO chat_subscriptions (chat_id, contract_address)
            VALUES (?, ?)
        ''', (chat_id, contract_address))
        
        # 记录初始供应量
        cursor.execute('''
            INSERT INTO supply_history (contract_address, total_supply, circulating_supply, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (contract_address, total_supply, total_supply, datetime.now().isoformat()))
        
        conn.commit()
        
        # 发送成功消息和当前供应量
        formatted_supply = format_token_amount(total_supply, decimals)
        await update.message.reply_text(
            f"成功添加代币监控：\n"
            f"名称: {name}\n"
            f"符号: {symbol}\n"
            f"合约: {contract_address}\n"
            f"精度: {decimals}\n"
            f"当前总供应量: {formatted_supply} {symbol}"
        )
        
    except sqlite3.IntegrityError:
        await update.message.reply_text("该代币已在监控列表中！")
    except Exception as e:
        logger.error(f"添加代币时发生错误: {str(e)}")
        await update.message.reply_text("添加代币时发生错误，请稍后重试")
    finally:
        conn.close()

async def list_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有监控的代币"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT t.rowid, t.name, t.symbol, t.contract_address, t.decimals,
                   h.total_supply, h.circulating_supply,
                   COUNT(l.id) as locked_addresses
            FROM tokens t
            LEFT JOIN supply_history h ON t.contract_address = h.contract_address
            LEFT JOIN locked_addresses l ON t.contract_address = l.contract_address
            WHERE h.id = (
                SELECT MAX(id) FROM supply_history 
                WHERE contract_address = t.contract_address
            )
            GROUP BY t.contract_address
        ''')
        
        tokens = cursor.fetchall()
        
        if not tokens:
            await update.message.reply_text("当前没有监控任何代币")
            return
            
        message = "监控中的代币列表：\n\n"
        for token in tokens:
            rowid, name, symbol, address, decimals, total_supply, circ_supply, lock_count = token
            total_fmt = format_token_amount(total_supply, decimals)
            circ_fmt = format_token_amount(circ_supply, decimals)
            
            message += (
                f"ID: {rowid}\n"
                f"名称: {name} ({symbol})\n"
                f"合约: {address}\n"
                f"总供应量: {total_fmt} {symbol}\n"
                f"流通量: {circ_fmt} {symbol}\n"
                f"锁仓地址数: {lock_count}\n\n"
            )
            
        message += "使用 /delete <id> 可以删除对应的代币监控"
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"获取代币列表时发生错误: {str(e)}")
        await update.message.reply_text("获取代币列表时发生错误，请稍后重试")
    finally:
        conn.close()

async def delete_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除代币监控"""
    if len(context.args) != 1:
        await update.message.reply_text("格式错误！请使用: /delete <id>")
        return

    try:
        token_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID必须是数字！")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 首先获取代币信息
        cursor.execute('''
            SELECT name, symbol, contract_address 
            FROM tokens 
            WHERE rowid = ?
        ''', (token_id,))
        
        result = cursor.fetchone()
        if not result:
            await update.message.reply_text(f"未找到ID为 {token_id} 的代币！")
            return
            
        name, symbol, contract_address = result
        
        # 开始删除相关数据
        cursor.execute('BEGIN TRANSACTION')
        try:
            # 删除锁仓地址
            cursor.execute('''
                DELETE FROM locked_addresses 
                WHERE contract_address = ?
            ''', (contract_address,))
            locked_count = cursor.rowcount
            
            # 删除供应量历史
            cursor.execute('''
                DELETE FROM supply_history 
                WHERE contract_address = ?
            ''', (contract_address,))
            history_count = cursor.rowcount
            
            # 删除聊天订阅
            cursor.execute('''
                DELETE FROM chat_subscriptions 
                WHERE contract_address = ?
            ''', (contract_address,))
            sub_count = cursor.rowcount
            
            # 删除代币信息
            cursor.execute('''
                DELETE FROM tokens 
                WHERE contract_address = ?
            ''', (contract_address,))
            
            cursor.execute('COMMIT')
            
            message = (
                f"✅ 成功删除代币 {name} ({symbol})：\n\n"
                f"• 删除了 {locked_count} 个锁仓地址\n"
                f"• 删除了 {history_count} 条供应量历史记录\n"
                f"• 删除了 {sub_count} 个聊天订阅"
            )
            
            await update.message.reply_text(message)
            
        except Exception as e:
            cursor.execute('ROLLBACK')
            raise e
        
    except Exception as e:
        logger.error(f"删除代币时发生错误: {str(e)}")
        await update.message.reply_text("删除代币时发生错误，请稍后重试")
    finally:
        conn.close()

async def add_lock_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加锁仓地址"""
    if len(context.args) < 3:
        await update.message.reply_text(
            "格式错误！请使用: /lock <contract_address> <lock_address> <description>"
        )
        return

    contract_address = context.args[0]
    lock_address = context.args[1]
    description = ' '.join(context.args[2:])

    if not w3.is_address(contract_address) or not w3.is_address(lock_address):
        await update.message.reply_text("无效的地址！")
        return

    try:
        # 转换为 checksum 地址
        contract_address = w3.to_checksum_address(contract_address)
        lock_address = w3.to_checksum_address(lock_address)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 检查代币是否存在
        cursor.execute('SELECT name, symbol, decimals FROM tokens WHERE contract_address = ?', (contract_address,))
        result = cursor.fetchone()
        if not result:
            await update.message.reply_text(f"代币 {contract_address} 不在监控列表中！")
            return
            
        name, symbol, decimals = result
        
        # 获取合约实例
        contract = w3.eth.contract(address=contract_address, abi=ERC20_ABI)
        
        # 获取锁仓地址余额
        try:
            locked_balance = contract.functions.balanceOf(lock_address).call()
            formatted_balance = format_token_amount(str(locked_balance), decimals)
        except Exception as e:
            logger.error(f"获取锁仓地址余额失败: {str(e)}")
            await update.message.reply_text("获取锁仓地址余额失败，请稍后重试")
            return

        # 添加锁仓地址
        cursor.execute('''
            INSERT INTO locked_addresses (contract_address, address, description)
            VALUES (?, ?, ?)
        ''', (contract_address, lock_address, description))
        
        # 获取最新总供应量
        total_supply = contract.functions.totalSupply().call()
        
        # 计算新的流通量
        new_circulating_supply = get_circulating_supply(contract, total_supply, contract_address)
        
        # 获取之前的流通量记录
        cursor.execute('''
            SELECT total_supply, circulating_supply FROM supply_history 
            WHERE contract_address = ? 
            ORDER BY timestamp DESC 
            LIMIT 1
        ''', (contract_address,))
        prev_record = cursor.fetchone()
        old_total_supply, old_circulating_supply = prev_record if prev_record else (str(total_supply), str(total_supply))
        
        # 记录新的供应量记录
        cursor.execute('''
            INSERT INTO supply_history (
                contract_address, total_supply, circulating_supply, timestamp
            ) VALUES (?, ?, ?, ?)
        ''', (
            contract_address,
            str(total_supply),
            str(new_circulating_supply),
            datetime.now().isoformat()
        ))
        
        conn.commit()
        
        # 计算锁仓比例
        lock_percentage = (locked_balance / total_supply) * 100
        
        # 计算总锁仓量
        cursor.execute('SELECT address FROM locked_addresses WHERE contract_address = ?', (contract_address,))
        all_locks = cursor.fetchall()
        total_locked = 0
        for (addr,) in all_locks:
            try:
                # 确保使用 checksum 地址
                addr = w3.to_checksum_address(addr)
                balance = contract.functions.balanceOf(addr).call()
                total_locked += balance
            except Exception as e:
                logger.error(f"获取地址 {addr} 余额时发生错误: {str(e)}")
        
        total_locked_fmt = format_token_amount(str(total_locked), decimals)
        total_lock_percentage = (total_locked / total_supply) * 100
        
        # 发送详细通知
        message = (
            f"✅ 成功添加锁仓地址到 {name} ({symbol})：\n\n"
            f"锁仓地址: {lock_address}\n"
            f"描述: {description}\n"
            f"锁仓数量: {formatted_balance} {symbol}\n"
            f"占总供应量: {lock_percentage:.2f}%\n\n"
            f"代币信息更新：\n"
            f"总供应量: {format_token_amount(str(total_supply), decimals)} {symbol}\n"
            f"总锁仓量: {total_locked_fmt} {symbol} ({total_lock_percentage:.2f}%)\n"
            f"当前流通量: {format_token_amount(str(new_circulating_supply), decimals)} {symbol}\n"
            f"锁仓地址数: {len(all_locks)}"
        )
        
        await update.message.reply_text(message)
        
        # 如果流通量发生变化，发送变化通知
        if str(new_circulating_supply) != old_circulating_supply:
            await notify_supply_change(
                name,
                symbol,
                old_total_supply,
                str(total_supply),
                old_circulating_supply,
                str(new_circulating_supply),
                decimals
            )
        
    except sqlite3.IntegrityError:
        await update.message.reply_text("该锁仓地址已存在！")
    except Exception as e:
        logger.error(f"添加锁仓地址时发生错误: {str(e)}")
        await update.message.reply_text("添加锁仓地址时发生错误，请稍后重试")
    finally:
        conn.close()

def get_circulating_supply(contract, total_supply: int, contract_address: str) -> int:
    """计算流通量"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取所有锁仓地址
        cursor.execute('SELECT address FROM locked_addresses WHERE contract_address = ?', (contract_address,))
        locked_addresses = cursor.fetchall()
        
        # 计算锁仓总量
        locked_amount = 0
        for (address,) in locked_addresses:
            try:
                # 确保使用 checksum 地址
                address = w3.to_checksum_address(address)
                balance = contract.functions.balanceOf(address).call()
                locked_amount += balance
            except Exception as e:
                logger.error(f"获取地址 {address} 余额时发生错误: {str(e)}")
        
        # 计算流通量
        circulating_supply = max(0, total_supply - locked_amount)
        return circulating_supply
        
    except Exception as e:
        logger.error(f"计算流通量时发生错误: {str(e)}")
        return total_supply  # 如果计算出错，返回总供应量
    finally:
        conn.close()

def update_supply():
    """更新所有代币的总供应量和流通量"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('SELECT contract_address, name, symbol, decimals FROM tokens')
        tokens = cursor.fetchall()
        
        for contract_address, name, symbol, decimals in tokens:
            try:
                # 获取最新供应量
                contract = w3.eth.contract(address=contract_address, abi=ERC20_ABI)
                new_total_supply = contract.functions.totalSupply().call()
                new_circulating_supply = get_circulating_supply(contract, new_total_supply, contract_address)
                
                # 获取上一次的记录
                cursor.execute('''
                    SELECT total_supply, circulating_supply FROM supply_history 
                    WHERE contract_address = ? 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                ''', (contract_address,))
                result = cursor.fetchone()
                old_total_supply = result[0] if result else "0"
                old_circulating_supply = result[1] if result else "0"
                
                # 如果供应量发生变化
                if (str(new_total_supply) != old_total_supply or 
                    str(new_circulating_supply) != old_circulating_supply):
                    # 记录新的供应量
                    cursor.execute('''
                        INSERT INTO supply_history (
                            contract_address, total_supply, circulating_supply, timestamp
                        ) VALUES (?, ?, ?, ?)
                    ''', (
                        contract_address, 
                        str(new_total_supply), 
                        str(new_circulating_supply), 
                        datetime.now().isoformat()
                    ))
                    conn.commit()
                    
                    logger.info(f"更新代币 {symbol} 供应量成功，供应量发生变化")
                    
                    # 异步发送通知
                    asyncio.run(notify_supply_change(
                        name,
                        symbol,
                        old_total_supply,
                        str(new_total_supply),
                        old_circulating_supply,
                        str(new_circulating_supply),
                        decimals
                    ))
                else:
                    logger.info(f"更新代币 {symbol} 供应量成功，供应量未变化")
                    
            except Exception as e:
                logger.error(f"更新代币 {symbol} 供应量失败: {str(e)}")
        
    except Exception as e:
        logger.error(f"更新供应量时发生错误: {str(e)}")
    finally:
        conn.close()

async def notify_supply_change(
    name: str,
    symbol: str,
    old_total: str,
    new_total: str,
    old_circ: str,
    new_circ: str,
    decimals: int
):
    """通知供应量变化"""
    if not application:
        return
        
    old_total_fmt = format_token_amount(old_total, decimals)
    new_total_fmt = format_token_amount(new_total, decimals)
    old_circ_fmt = format_token_amount(old_circ, decimals)
    new_circ_fmt = format_token_amount(new_circ, decimals)
    
    total_change = float(new_total) / (10 ** decimals) - float(old_total) / (10 ** decimals)
    circ_change = float(new_circ) / (10 ** decimals) - float(old_circ) / (10 ** decimals)
    
    message = (
        f"⚠️ {name} ({symbol}) 供应量发生变化：\n\n"
        f"总供应量: {old_total_fmt} → {new_total_fmt} {symbol}\n"
        f"变化量: {'{:+,.2f}'.format(total_change)} {symbol}\n\n"
        f"流通量: {old_circ_fmt} → {new_circ_fmt} {symbol}\n"
        f"变化量: {'{:+,.2f}'.format(circ_change)} {symbol}"
    )
    
    # 获取所有需要通知的聊天ID
    chat_ids = get_chat_ids()
    
    # 发送通知
    for chat_id in chat_ids:
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"发送通知到聊天 {chat_id} 时发生错误: {str(e)}")

def main():
    """主程序"""
    global application
    
    try:
        # 初始化数据库
        init_db()

        # 设置定时任务
        scheduler = BackgroundScheduler()
        scheduler.add_job(update_supply, 'interval', seconds=600)  # 10秒轮询一次
        scheduler.start()
        
        # 初始化机器人
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加命令处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("add", add_token))
        application.add_handler(CommandHandler("list", list_tokens))
        application.add_handler(CommandHandler("lock", add_lock_address))
        application.add_handler(CommandHandler("delete", delete_token))
        
        logger.info("机器人启动成功")
        
        # 启动机器人
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"启动失败: {str(e)}")
        if scheduler.running:
            scheduler.shutdown()
        raise
    finally:
        if scheduler.running:
            scheduler.shutdown()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {str(e)}")
        sys.exit(1) 