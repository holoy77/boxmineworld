import os
import sys
import time
import json
import threading
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Origin": "https://afk.boxmineworld.com",
    "Referer": "https://afk.boxmineworld.com/",
}

if SESSION_KEY:
    HEADERS["Authorization"] = f"Bearer {SESSION_KEY}"
elif DISCORD_TOKEN:
    HEADERS["Authorization"] = f"Bearer {DISCORD_TOKEN}"

# ==================== 状态管理 ====================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                logger.info(f"已加载本地状态: {state}")
                return state
        except Exception as e:
            logger.warning(f"读取状态文件失败，将使用默认状态: {e}")
    
    return {
        "last_run": None,
        "daily_count": 0,
        "total_minutes": 0,
        "coins_earned": 0
    }

def save_state(state: dict):
    try:
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        logger.info(f"已更新状态文件 {STATE_FILE}")
    except Exception as e:
        logger.error(f"保存状态文件失败: {e}")

# ==================== AFK 心跳逻辑 ====================
class AFKWorker:
    def __init__(self, state: dict, run_minutes: int):
        self.state = state
        self.max_seconds = run_minutes * 60
        self.start_time = time.time()
        self.stop_heartbeat = threading.Event()

    def send_heartbeat_loop(self, ws):
        """后台子线程：每 15 秒发送一次应用层心跳"""
        logger.info("⏱️ 启动业务心跳定时器（每 15 秒发送一次）...")
        
        # 尝试几种常见的心跳载荷
        payloads = [
            json.dumps({"type": "ping"}),
            json.dumps({"action": "ping"}),
            json.dumps({"event": "ping"}),
            "ping",
            "2"  # Socket.IO 格式的 ping
        ]
        
        index = 0
        while not self.stop_heartbeat.is_set():
            try:
                # 轮询或者固定发送 ping 字符串
                # BoxMineWorld 通常接受文本 "ping" 或 JSON
                ping_str = "ping"
                ws.send(ping_str)
                logger.debug(f"📤 已发送心跳包: {ping_str}")
            except Exception as e:
                logger.warning(f"发送心跳失败: {e}")
                break
            
            # 每 15 秒发送一次，防止 80s 无响应被踢
            time.sleep(15)

    def run_websocket(self):
        def on_message(ws, message):
            try:
                msg = json.loads(message)
                logger.info(f"📩 收到服务器消息: {msg}")
                if "coins" in msg or "balance" in msg:
                    coins = msg.get("coins") or msg.get("balance") or 0
                    self.state["coins_earned"] = coins
                    logger.info(f"💰 当前金币数量: {coins}")
            except Exception:
                logger.info(f"📩 收到原始消息: {message}")

        def on_error(ws, error):
            logger.error(f"⚠️ WebSocket 错误: {error}")

        def on_close(ws, close_status_code, close_msg):
            self.stop_heartbeat.set()
            logger.info(f"🔒 WebSocket 连接关闭: {close_status_code} - {close_msg}")

        def on_open(ws):
            logger.info("🟢 WebSocket 连接成功，开始保活...")
            self.stop_heartbeat.clear()
            
            # 开启独立线程维持心跳
            t = threading.Thread(target=self.send_heartbeat_loop, args=(ws,), daemon=True)
            t.start()

        token_param = SESSION_KEY or DISCORD_TOKEN
        ws_url = f"{BASE_WS_URL}?session_key={token_param}&token={token_param}"

        while time.time() - self.start_time < self.max_seconds:
            elapsed = time.time() - self.start_time
            remaining_min = int((self.max_seconds - elapsed) / 60)
            logger.info(f"⌛ 尝试连接 AFK 服务器 | 剩余 {remaining_min} 分钟...")

            ws = websocket.WebSocketApp(
                ws_url,
                header=HEADERS,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            # 运行连接
            ws.run_forever()

            # 连接挂掉后，更新状态
            self.state["total_minutes"] += 1
            save_state(self.state)

            if time.time() - self.start_time < self.max_seconds:
                logger.info("🔄 连接中断，5 秒后尝试重新连接...")
                time.sleep(5)

    def start(self):
        logger.info(f"🚀 开始运行 AFK 挂机脚本，设定运行时长: {RUN_MINUTES} 分钟")
        
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("last_date") != today_str:
            self.state["last_date"] = today_str
            self.state["daily_count"] = self.state.get("daily_count", 0) + 1

        try:
            self.run_websocket()
        except KeyboardInterrupt:
            logger.info("🛑 收到中断信号，准备退出...")
        finally:
            save_state(self.state)
            logger.info("✅ 本轮挂机任务完成。")

# ==================== 入口函数 ====================
if __name__ == "__main__":
    if not DISCORD_TOKEN and not SESSION_KEY:
        logger.error("❌ 错误: 未配置 BOXMINEWORLD_DISCORD_TOKEN 或 BOXMINEWORLD_SESSION_KEY 环境变量！")
        sys.exit(1)

    state = load_state()
    worker = AFKWorker(state=state, run_minutes=RUN_MINUTES)
    worker.start()
