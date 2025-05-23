import os
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import aiohttp
from typing import Dict, List
from dotenv import load_dotenv
from telegram import BotCommandScopeDefault

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

def format_large_number(num: float) -> str:
    """Format large numbers with K, M, B suffixes."""
    if num >= 1_000_000_000:  # Billion
        return f"{num/1_000_000_000:.1f}B"
    elif num >= 1_000_000:  # Million
        return f"{num/1_000_000:.1f}M"
    elif num >= 1_000:  # Thousand
        return f"{num/1_000:.1f}K"
    else:
        return f"{num:.1f}"

def format_small_number(num: float) -> str:
    """Format small numbers with appropriate precision."""
    if num < 0.000001:
        return f"{num:.9f}"
    elif num < 0.001:
        return f"{num:.6f}"
    else:
        return f"{num:.4f}"

def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = str(text).replace(char, f'\\{char}')
    return text

class PriceTracker:
    def __init__(self):
        self.active_trackers: Dict[int, List[tuple]] = {}  # chat_id -> [(network, contract_address), ...]
        self.tasks: Dict[int, List[asyncio.Task]] = {}  # chat_id -> [task1, task2, ...]
        self.initial_prices: Dict[int, Dict[str, float]] = {}  # chat_id -> {contract_address: initial_price}
        self.initial_fdv: Dict[int, Dict[str, float]] = {}  # chat_id -> {contract_address: initial_fdv}
        self.alert_intervals: Dict[int, Dict[str, int]] = {}  # chat_id -> {contract_address: interval_minutes}

    async def start_tracking(self, chat_id: int, network: str, contract_address: str, interval: int, context: ContextTypes.DEFAULT_TYPE):
        # Initialize user's tracking data if not exists
        if chat_id not in self.active_trackers:
            self.active_trackers[chat_id] = []
            self.tasks[chat_id] = []
            self.initial_prices[chat_id] = {}
            self.initial_fdv[chat_id] = {}
            self.alert_intervals[chat_id] = {}

        # Check for duplicate token
        if any(addr == contract_address for _, addr in self.active_trackers[chat_id]):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Already tracking {contract_address}. Use /stop to stop tracking first."
            )
            return

        # Validate interval
        if interval not in [1, 5, 15, 30, 60]:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Invalid interval. Please use one of: 1, 5, 15, 30, 60 minutes."
            )
            return

        # Add new token to tracking
        self.active_trackers[chat_id].append((network, contract_address))
        self.alert_intervals[chat_id][contract_address] = interval
        task = asyncio.create_task(
            self._track_price(chat_id, network, contract_address, context)
        )
        self.tasks[chat_id].append(task)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Started tracking {contract_address} on {network} with {interval} minute interval."
        )

    async def stop_tracking(self, chat_id: int, contract_address: str = None):
        if chat_id not in self.active_trackers:
            return False

        if contract_address:
            # Stop tracking specific token
            token_index = next((i for i, (_, addr) in enumerate(self.active_trackers[chat_id]) 
                              if addr == contract_address), None)
            if token_index is not None:
                self.tasks[chat_id][token_index].cancel()
                del self.tasks[chat_id][token_index]
                del self.active_trackers[chat_id][token_index]
                if contract_address in self.initial_prices[chat_id]:
                    del self.initial_prices[chat_id][contract_address]
                if contract_address in self.initial_fdv[chat_id]:
                    del self.initial_fdv[chat_id][contract_address]
                if contract_address in self.alert_intervals[chat_id]:
                    del self.alert_intervals[chat_id][contract_address]
                
                # Clean up if no more tokens
                if not self.active_trackers[chat_id]:
                    del self.active_trackers[chat_id]
                    del self.tasks[chat_id]
                    del self.initial_prices[chat_id]
                    del self.initial_fdv[chat_id]
                    del self.alert_intervals[chat_id]
                return True
        else:
            # Stop tracking all tokens
            for task in self.tasks[chat_id]:
                task.cancel()
            del self.tasks[chat_id]
            del self.active_trackers[chat_id]
            del self.initial_prices[chat_id]
            del self.initial_fdv[chat_id]
            del self.alert_intervals[chat_id]
            return True
        return False

    async def _track_price(self, chat_id: int, network: str, contract_address: str, context: ContextTypes.DEFAULT_TYPE):
        is_first_message = True
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://pro-api.coingecko.com/api/v3/onchain/networks/{network}/tokens/{contract_address}?include=top_pools"
                    headers = {"x-cg-pro-api-key": COINGECKO_API_KEY}
                    
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            # Get token data
                            token_data = data.get("data", {})
                            token_attributes = token_data.get("attributes", {})
                            price = token_attributes.get("price_usd")
                            token_name = token_attributes.get("name")
                            fdv = token_attributes.get("fdv_usd")
                            
                            # Get first pool data from included array
                            included = data.get("included", [])
                            if included:
                                pool_data = included[0]
                                pool_attributes = pool_data.get("attributes", {})
                                price_percentage_5m = pool_attributes.get("price_change_percentage", {}).get("m5")
                                vol_5m = pool_attributes.get("volume_usd", {}).get("m5")
                            else:
                                price_percentage_5m = None
                                vol_5m = None
                            
                            if price is not None:
                                try:
                                    # Convert price to float and format it
                                    price_float = float(price)
                                    fdv_float = float(fdv) if fdv is not None else 0.0
                                    vol_5m_float = float(vol_5m) if vol_5m is not None else 0.0
                                    price_percentage = float(price_percentage_5m) if price_percentage_5m is not None else 0.0
                                    
                                    # Store initial price and FDV on first message
                                    if is_first_message:
                                        self.initial_prices[chat_id][contract_address] = price_float
                                        self.initial_fdv[chat_id][contract_address] = fdv_float
                                        is_first_message = False
                                    
                                    # Format numbers
                                    price_str = escape_markdown(format_small_number(price_float))
                                    fdv_formatted = format_large_number(fdv_float)
                                    fdv_str = escape_markdown(f"${fdv_formatted}")
                                    vol_5m_formatted = format_large_number(vol_5m_float)
                                    vol_5m_str = escape_markdown(f"${vol_5m_formatted}")
                                    
                                    # Format percentage with sign
                                    percentage_str = f"{price_percentage:+.2f}%" if price_percentage != 0 else "0%"
                                    percentage_str = escape_markdown(percentage_str)
                                    
                                    # Format initial price and FDV
                                    initial_price = self.initial_prices[chat_id].get(contract_address, price_float)
                                    initial_fdv = self.initial_fdv[chat_id].get(contract_address, fdv_float)
                                    initial_price_str = escape_markdown(format_small_number(initial_price))
                                    initial_fdv_formatted = format_large_number(initial_fdv)
                                    initial_fdv_str = escape_markdown(f"${initial_fdv_formatted}")
                                    
                                    network_str = escape_markdown(network)
                                    token_name_str = escape_markdown(token_name)
                                    contract_str = escape_markdown(contract_address)
                                    time_str = escape_markdown(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                                    
                                    message = (
                                        f"*{token_name_str}*\n"
                                        f"💵 *Price: ${price_str} {percentage_str}*\n"
                                        f"🚀 *FDV: {fdv_str}*\n"
                                        f"🌊 *Vol 5m: {vol_5m_str}*\n\n"
                                        f"⏰ *Track Start Price: ${initial_price_str}*\n"
                                        f"*FDV: {initial_fdv_str}*\n\n"
                                        f"✉️ *Contract:* `{contract_str}`\n\n"
                                        f"📈 [View Charts](https://www.geckoterminal.com/{network}/pools/{contract_address})\n"
                                    )
                                    await context.bot.send_message(
                                        chat_id=chat_id, 
                                        text=message,
                                        parse_mode='MarkdownV2',
                                        disable_web_page_preview=True
                                    )
                                except (ValueError, TypeError) as e:
                                    await context.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"Error formatting data for {contract_address}: {str(e)}"
                                    )
                            else:
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"Could not fetch price for {contract_address}"
                                )
                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"Error fetching price for {contract_address}"
                            )
            except Exception as e:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Error tracking {contract_address}: {str(e)}"
                )
            
            # Use token-specific interval
            await asyncio.sleep(self.alert_intervals[chat_id][contract_address] * 60)  # Convert minutes to seconds

# Initialize price tracker
price_tracker = PriceTracker()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the Crypto Price Tracker Bot!\n"
        "Use /track <network> <contract_address> [interval] to start tracking a token\n"
        "Use /stop [contract_address] to stop tracking\n"
        "Example: /track solana 0x123... 5\n"
        "Interval options: 1, 5, 15, 30, 60 minutes"
    )

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Please provide network and address: /track <network> <contract_address> [interval]\n"
            "Example: /track solana 0x123... 5\n"
            "Interval options: 1, 5, 15, 30, 60 minutes"
        )
        return
    
    # Parse arguments
    try:
        network = context.args[0]
        address = context.args[1]
        interval = int(context.args[2]) if len(context.args) > 2 else 1  # Default to 1 minute if not specified
        
        chat_id = update.effective_chat.id
        await price_tracker.start_tracking(chat_id, network, address, interval, context)
        
    except ValueError:
        await update.message.reply_text(
            "Invalid interval. Please use one of: 1, 5, 15, 30, 60 minutes."
        )
    except Exception as e:
        await update.message.reply_text(
            "Invalid format. Please use: /track <network> <contract_address> [interval]\n"
            "Example: /track solana 0x123... 5"
        )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    contract_address = context.args[0] if context.args else None
    
    if await price_tracker.stop_tracking(chat_id, contract_address):
        if contract_address:
            await update.message.reply_text(f"Stopped tracking {contract_address}")
        else:
            await update.message.reply_text("Stopped tracking all tokens")
    else:
        await update.message.reply_text("Not tracking any token in this chat")

def main():
    # Create application with group commands enabled
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("track", track))
    application.add_handler(CommandHandler("stop", stop))

    # Enable group commands
    async def setup_commands():
        await application.bot.set_my_commands([
            ("start", "Start the bot"),
            ("track", "Track a token price"),
            ("stop", "Stop tracking")
        ], scope=BotCommandScopeDefault())

    # Run setup and start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 