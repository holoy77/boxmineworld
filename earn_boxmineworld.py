import os
import sys
import time
import json
import uuid
import logging
from datetime import datetime, timezone
import websocket

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BoxMineWorld-AFK")

# ==================== 配置与环境变量 ====================
DISCORD_TOKEN = os.getenv("BOXMINEWORLD_DISCORD_TOKEN", "")
SESSION_KEY = os.getenv("BOXMINEWORLD_SESSION_KEY", "")
RUN_MINUTES = int(os.getenv("RUN_MINUTES", "340"))

STATE_FILE = "boxmineworld_state.json"
BASE_WS_URL = "wss://afkapi.boxmineworld.com/socket"

# 组装请求头：将 SESSION_KEY 正确拼装入 Cookie 中
headers_list = [
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Origin: https://afk.boxmineworld.com",
    "Referer: https://afk.boxmineworld.com/"
]

if SESSION_KEY:
    # 判断如果环境变量里只是纯 Token，就自动补全 Cookie 键名；如果是完整 Cookie 则直接使用
    if "_SECURE_BOX_AUTH_SESSION_" in SESSION_KEY:
        headers_list.append(f"Cookie: {SESSION_KEY}")
    else:
        headers_list.append(f"Cookie: _SECURE_BOX_AUTH_SESSION_={SESSION_KEY}")
elif DISCORD_TOKEN:
    headers_list.append(f"Authorization: Bearer {DISCORD_TOKEN}")

# ==================== 状态管理 ====================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_run": None, "daily_count": 0, "total_minutes": 0, "coins_earned": 0}

def save_state(state: dict):
    try:
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存状态文件失败: {e}")

# ==================== AFK 核心逻辑 ====================
class AFKWorker:
    def __init__(self, state: dict, run_minutes: int):
        self.state = state
        self.max_seconds = run_minutes * 60
        self.start_time = time.time()
        self.subscription_id = str(uuid.uuid4())

    def run_websocket(self):
        def on_message(ws, message):
            try:
                msg = json.loads(message)
                msg_type = msg.get("type")

                # 多设备挂机排他检测
                if msg.get("error") == "{device:true}":
                    logger.warning("⚠️ 警告：账号已在其他设备（如浏览器）挂机！请关闭网页挂机。")
                    return

                # 响应心跳校验
                if msg_type == "activity_check":
                    check_id = msg.get("checkId")
                    logger.info(f"📩 收到心跳校验 checkId: {check_id}")
                    
                    response_payload = {
                        "type": "activity_check_response",
                        "checkId": check_id,
                        "subscriptionId": self.subscription_id
                    }
                    ws.send(json.dumps(response_payload))
                    logger.info(f"📤 已成功应答心跳！")

                # 监听所有的金币与状态消息（抓取任何可能包含收益的字段）
                for key in ["coins", "balance", "earned", "session_coins", "amount"]:
                    if key in msg:
                        coins = msg[key]
                        self.state["coins_earned"] = coins
                        logger.info(f"🎉 成功获取金币变化通知！当前收益: {coins}")
                        break
                else:
                    if msg_type not in ["activity_check"]:
                        logger.info(f"📩 收到服务端下发数据: {msg}")

            except Exception:
                logger.info(f"📩 收到原始消息: {message}")

        def on_error(ws, error):
            logger.error(f"⚠️ WebSocket 错误: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"🔒 WebSocket 连接关闭: {close_status_code} - {close_msg}")

        def on_open(ws):
            logger.info("🟢 已成功带凭证连接至 WebSocket！发送挂机订阅...")
            
            self.subscription_id = str(uuid.uuid4())
            subscribe_payload = {
                "type": "subscribe",
                "path": "/earn",
                "subscriptionId": self.subscription_id
            }
            ws.send(json.dumps(subscribe_payload))
            logger.info(f"📤 已发送挂机订阅包")

        ws_url = BASE_WS_URL

        while time.time() - self.start_time < self.max_seconds:
            elapsed = time.time() - self.start_time
            remaining_min = int((self.max_seconds - elapsed) / 60)
            logger.info(f"⌛ 运行挂机任务中... | 本轮剩余 {remaining_min} 分钟")

            ws = websocket.WebSocketApp(
                ws_url,
                header=headers_list,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            ws.run_forever()

            self.state["total_minutes"] += 1
            save_state(self.state)

            if time.time() - self.start_time < self.max_seconds:
                logger.info("🔄 10 秒后重新连接...")
                time.sleep(10)

    def start(self):
        logger.info(f"🚀 开始运行 AFK 挂机脚本，设定时长: {RUN_MINUTES} 分钟")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("last_date") != today_str:
            self.state["last_date"] = today_str
            self.state["daily_count"] = self.state.get("daily_count", 0) + 1

        try:
            self.run_websocket()
        except KeyboardInterrupt:
            pass
        finally:
            save_state(self.state)

if __name__ == "__main__":
    if not DISCORD_TOKEN and not SESSION_KEY:
        logger.error("❌ 未配置 BOXMINEWORLD_SESSION_KEY！")
        sys.exit(1)
    state = load_state()
    worker = AFKWorker(state=state, run_minutes=RUN_MINUTES)
    worker.start()
