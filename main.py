import firebase_admin
from firebase_admin import credentials, db
import requests
import json
import hmac
import hashlib
import base64
import time
import os
import re
from datetime import datetime, timedelta
from colorama import init, Fore, Style
from dotenv import load_dotenv
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import logging

class ItemkuMonitor:
    def __init__(self):
        init()
        load_dotenv()
        
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
        firebase_creds_json = os.getenv('FIREBASE_CREDENTIALS')
        if firebase_creds_json:
            cred = credentials.Certificate(json.loads(firebase_creds_json))
        else:
            cred = credentials.Certificate("itemku.json")
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://itemku-proj-default-rtdb.firebaseio.com'
        })
        self.ref = db.reference('/Products')
        self.orders_ref = db.reference('/Orders')
        
        self.api_key = os.getenv('API_KEY')
        self.api_secret = os.getenv('API_SECRET')
        self.base_url = "https://tokoku-gateway.itemku.com/api"
        
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.manual_process = {}
        
        if self.bot_token and self.chat_id:
            self.bot = telebot.TeleBot(self.bot_token)
            self.setup_telegram_handlers()
        else:
            self.bot = None

    def setup_telegram_handlers(self):
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call):
            if call.data.startswith('manual_process_'):
                order_id = call.data.replace('manual_process_', '')
                self.start_manual_process(order_id)
            elif call.data.startswith('check_stock_'):
                product_id = call.data.replace('check_stock_', '')
                self.check_and_send_stock_status(product_id)
            elif call.data.startswith('delivery_type_'):
                _, order_id, type_num = call.data.split('_')
                self.handle_delivery_type(order_id, int(type_num))

        @self.bot.message_handler(func=lambda message: True)
        def handle_message(message):
            if message.reply_to_message and 'manual_process' in message.reply_to_message.text:
                self.handle_manual_input(message)

    def handle_delivery_type(self, order_id, type_num):
        order_info = self.get_order_info(order_id)
        if not order_info:
            self.send_telegram_message("**❌ Order not found**")
            return

        self.manual_process[order_id] = {'type': type_num, 'inputs': []}
        
        if type_num == 1:
            msg = "**📝 Enter delivery info in format: value**"
            self.send_telegram_message(msg, force_reply=True)
        elif type_num == 2:
            msg = "**📝 Enter multiple values separated by comma**"
            self.send_telegram_message(msg, force_reply=True)
        elif type_num == 3:
            fields = order_info.get('delivery_info_field', [])
            if not fields:
                self.send_telegram_message("**❌ No fields defined for this order type**")
                return
                
            field = fields[len(self.manual_process[order_id]['inputs'])]
            msg = f"**📝 Enter {field['field_name']}:**"
            self.send_telegram_message(msg, force_reply=True)

    def handle_manual_input(self, message):
        order_id = next((k for k, v in self.manual_process.items() if v), None)
        if not order_id:
            return

        process_info = self.manual_process[order_id]
        input_value = message.text.strip()
        
        if process_info['type'] == 1:
            if self.validate_input(input_value, order_id):
                self.process_order(order_id, "DELIVER", [input_value])
                del self.manual_process[order_id]
        
        elif process_info['type'] == 2:
            values = [v.strip() for v in input_value.split(',')]
            if all(self.validate_input(v, order_id) for v in values):
                self.process_order(order_id, "DELIVER", values)
                del self.manual_process[order_id]
        
        elif process_info['type'] == 3:
            order_info = self.get_order_info(order_id)
            fields = order_info.get('delivery_info_field', [])
            current_field = fields[len(process_info['inputs'])]
            
            if self.validate_input(input_value, order_id, current_field.get('validation_pattern')):
                process_info['inputs'].append(input_value)
                
                if len(process_info['inputs']) == len(fields):
                    delivery_info = dict(zip(
                        [f['field_name'] for f in fields],
                        process_info['inputs']
                    ))
                    self.process_order(order_id, "DELIVER", [delivery_info])
                    del self.manual_process[order_id]
                else:
                    next_field = fields[len(process_info['inputs'])]
                    msg = f"**📝 Enter {next_field['field_name']}:**"
                    self.send_telegram_message(msg, force_reply=True)

    def validate_input(self, value, order_id, pattern=None):
        if not value:
            self.send_telegram_message("**❌ Input cannot be empty**")
            return False
            
        if pattern:
            if not re.match(pattern, value):
                self.send_telegram_message("**❌ Input format invalid**")
                return False
        
        return True

    def get_order_info(self, order_id):
        response = requests.post(
            f"{self.base_url}/order/detail",
            headers=self.get_headers({"order_id": order_id}),
            json={"order_id": order_id}
        )
        
        if response.ok:
            return response.json().get('data')
        return None

    def start_manual_process(self, order_id):
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("**Type 1**", callback_data=f"delivery_type_{order_id}_1"),
            InlineKeyboardButton("**Type 2**", callback_data=f"delivery_type_{order_id}_2"),
            InlineKeyboardButton("**Type 3**", callback_data=f"delivery_type_{order_id}_3")
        )
        
        msg = (
            "**🔧 Manual Processing**\n"
            "Select delivery info type:\n"
            "1. Single value\n"
            "2. Multiple values\n"
            "3. Form fields"
        )
        
        self.send_telegram_message(msg, markup)

    def send_telegram_message(self, message, markup=None, force_reply=False):
        try:
            if not self.bot:
                return
                
            reply_markup = ForceReply() if force_reply else markup
            self.bot.send_message(
                self.chat_id,
                message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Failed to send Telegram message: {str(e)}")

    def process_pending_orders(self, orders):
        for order in orders:
            if order.get('status') == 'REQUIRE_PROCESS':
                order_id = order.get('order_id')
                if not order_id:
                    continue
                    
                product_id = order.get('product_id')
                if not product_id:
                    continue
                    
                quantity = order.get('quantity', 1)
                
                order_message = (
                    f"**🔔 New Order**\n"
                    f"Order ID: `{order_id}`\n"
                    f"Product: {order.get('product_name', 'Unknown')}\n"
                    f"Quantity: {quantity}\n"
                    f"Price: Rp {int(order.get('price', 0)):,}"
                )
                self.send_telegram_message(order_message)
                
                account, error = self.get_available_account(product_id, quantity)
                if error:
                    markup = InlineKeyboardMarkup()
                    markup.row(
                        InlineKeyboardButton(
                            "**🔧 Process Manually**",
                            callback_data=f"manual_process_{order_id}"
                        )
                    )
                    
                    error_message = (
                        f"**❌ Stock Not Available**\n"
                        f"Order ID: `{order_id}`\n"
                        f"Error: {error}"
                    )
                    self.send_telegram_message(error_message, markup)
                    continue
                
                if account:
                    email = account.get('email', '')
                    delivery_info = [email]
                    if not any(keyword in email.lower() for keyword in ['token', 'invite']):
                        delivery_info.append(account.get('password', ''))
                    
                    success, message = self.process_order(order_id, "DELIVER", delivery_info)
                    if success:
                        success_message = (
                            f"**✅ Order Delivered**\n"
                            f"Order ID: `{order_id}`\n"
                            f"Info: `{' | '.join(delivery_info)}`"
                        )
                        self.send_telegram_message(success_message)
                    else:
                        error_message = (
                            f"**❌ Delivery Failed**\n"
                            f"Order ID: `{order_id}`\n"
                            f"Error: {message}"
                        )
                        self.send_telegram_message(error_message)

    def process_order(self, order_id, action, delivery_info=None):
        try:
            payload = {
                "order_id": order_id,
                "action": action
            }
            
            if action == "DELIVER" and delivery_info:
                payload["delivery_info"] = delivery_info

            response = requests.post(
                f"{self.base_url}/order/action",
                headers=self.get_headers(payload),
                json=payload
            )
            
            data = response.json()
            if data.get('success'):
                self.orders_ref.child(str(order_id)).update({
                    'status': action,
                    'processed_at': int(time.time() * 1000),
                    'delivery_info': delivery_info
                })
                return True, "Success"
            
            return False, data.get('message', 'Failed')
            
        except Exception as e:
            return False, str(e)
        
    def get_available_account(self, product_id, order_quantity=1):
        try:
            product_data = self.ref.child(str(product_id)).get()
            if not product_data:
                return None, "Product not found"
            
            accounts = product_data.get('accounts', [])
            available_accounts = []
            current_time = int(time.time() * 1000)
            
            for idx, acc in enumerate(accounts):
                if not isinstance(acc, dict):
                    continue
                    
                current_users = acc.get('currentUser', 0)
                max_users = acc.get('maxUser', 0)
                remaining = max_users - current_users
                expired_at = acc.get('expired_at', 0)
                
                if expired_at >= current_time and remaining >= order_quantity:
                    available_accounts.append((idx, acc, remaining))
            
            if not available_accounts:
                return None, "No available accounts"
            
            available_accounts.sort(key=lambda x: x[2])
            idx, account, _ = available_accounts[0]
            
            new_current_user = account.get('currentUser', 0) + order_quantity
            self.ref.child(str(product_id)).child('accounts').child(str(idx)).update({
                'currentUser': new_current_user,
                'lastUsed': int(time.time() * 1000)
            })
            
            return account, None
        except Exception as e:
            self.logger.error(f"Error getting available account: {str(e)}")
            return None, f"Error: {str(e)}"
        
    def get_recent_orders(self):
        try:
            payload = {
                "date_start": (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d'),
                "limit": 20
            }
            
            response = requests.post(
                f"{self.base_url}/order/list",
                headers=self.get_headers(payload),
                json=payload
            )
            
            data = response.json()
            if not data.get('success'):
                self.logger.warning(f"Failed to get orders: {data.get('message', 'Unknown error')}")
                return []
                
            return data.get('data', [])
        except Exception as e:
            self.logger.error(f"Error getting recent orders: {str(e)}")
            return []
        
    def get_headers(self, payload):
        nonce = str(int(time.time()))
        return {
            'X-Api-Key': self.api_key,
            'Authorization': f'Bearer {self.generate_token(payload)}',
            'Content-Type': 'application/json',
            'Nonce': nonce
        }

    def generate_token(self, payload):
        nonce = str(int(time.time()))
        header = {"X-Api-Key": self.api_key, "Nonce": nonce, "alg": "HS256"}
        header_encoded = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b'=').decode()
        payload_encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
        unsigned_token = f"{header_encoded}.{payload_encoded}"
        signature = hmac.new(self.api_secret.encode(), unsigned_token.encode(), hashlib.sha256).digest()
        return f"{unsigned_token}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def display_orders(self, orders):
        print(f"\n{Fore.CYAN}Recent Orders:{Style.RESET_ALL}")
        print(f"{'Order ID':<15} {'Product':<30} {'Price':<12} {'Status':<15} {'Date':<20}")
        print("-" * 92)
        
        for order in orders:
            try:
                order_id = order.get('order_id', 'N/A')
                game_name = order.get('game_name', '')
                product_name = order.get('product_name', 'Unknown Product')
                full_product_name = f"{game_name} {product_name}".strip()[:28]
                
                status = order.get('status', 'UNKNOWN')
                status_color = (Fore.GREEN if status == 'DELIVERED' else 
                              Fore.YELLOW if status == 'REQUIRE_PROCESS' else 
                              Fore.WHITE)
                
                price = int(order.get('price', 0))
                created_at = datetime.fromtimestamp(
                    order.get('created_at', int(time.time() * 1000)) / 1000
                ).strftime('%Y-%m-%d %H:%M:%S')
                
                print(f"{status_color}"
                      f"{order_id:<15} "
                      f"{full_product_name:<30} "
                      f"Rp {price:,} "
                      f"{status:<15} "
                      f"{created_at}{Style.RESET_ALL}")
            except Exception as e:
                self.logger.error(f"Error displaying order: {str(e)}")
                continue

    def monitor(self):
        print(f"{Fore.CYAN}Starting Monitor...{Style.RESET_ALL}")
        
        try:
            while True:
                try:
                    orders = self.get_recent_orders()
                    os.system('cls' if os.name == 'nt' else 'clear')
                    self.display_orders(orders)
                    self.process_pending_orders(orders)
                    time.sleep(10)
                except Exception as e:
                    self.logger.error(f"Error in monitor loop: {str(e)}")
                    time.sleep(10)  # Continue monitoring even if there's an error
                
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Monitor stopped{Style.RESET_ALL}")

if __name__ == "__main__":
    monitor = ItemkuMonitor()
    monitor.monitor()