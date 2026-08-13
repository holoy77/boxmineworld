import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
import requests
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
BASE_HTTP_URL = "https://afkapi.boxmineworld.com"
BASE_WS_URL = "wss://afkapi.boxmineworld.com/socket"

# 请求头配置
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Origin": "https://afk.boxmineworld.com",
    "Referer": "https://afk.boxmineworld.com/",
    "Authorization": f"Bearer {SESSION_KEY}" if SESSION_KEY else f"Bearer {DISCORD_TOKEN}",
    "X-Session-Key": SESSION_KEY,
    "X-Discord-Token": DISCORD_TOKEN
}

# ==================== 状态管理 ====================
def load_state() -> dict:
    """读取本地状态文件"""
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
    """保存状态到本地 JSON 文件（供 Git 提交）"""
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
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def http_heartbeat(self) -> bool:
        """HTTP 心跳/挂机接口请求 fallback"""
        try:
            # 常见挂机心跳 Endpoint 尝试
            payload = {
                "session_key": SESSION_KEY,
                "timestamp": int(time.time())
            }
            res = self.session.post(f"{BASE_HTTP_URL}/api/afk/ping", json=payload, timeout=15)
            
            if res.status_code == 200:
                data = res.json()
                coins = data.get("coins", 0)
                logger.info(f"❤️ HTTP 心跳成功 | 当前收益: {coins} 金币")
                return True
            else:
                logger.warning(f"⚠️ HTTP 心跳返回状态码: {res.status_code} - {res.text}")
                return False
        except Exception as e:
            logger.error(f"❌ HTTP 心跳请求异常: {e}")
            return False

    def run_websocket(self):
        """WebSocket 模式挂机"""
        def on_message(ws, message):
            try:
                msg = json.loads(message)
                logger.info(f"📩 收到服务器消息: {msg}")
                # 累加挂机时长与金币
                if "coins" in msg:
                    self.state["coins_earned"] = msg.get("coins", self.state.get("coins_earned", 0))
            except Exception:
                logger.info(f"📩 收到原始消息: {message}")

        def on_error(ws, error):
            logger.error(f"⚠️ WebSocket 错误: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"🔒 WebSocket 连接关闭: {close_status_code} - {close_msg}")

        def on_open(ws):
            logger.info("🟢 WebSocket 连接成功，开始 AFK 挂机收益...")
            # 发送鉴权/初始化消息
            auth_payload = {
                "type": "auth",
                "session_key": SESSION_KEY,
                "token": DISCORD_TOKEN
            }
            ws.send(json.dumps(auth_payload))

        ws_url = f"{BASE_WS_URL}?key={SESSION_KEY or DISCORD_TOKEN}"
        ws = websocket.WebSocketApp(
            ws_url,
            header=HEADERS,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )

        # 挂机主循环
        elapsed = 0
        ping_interval = 60  # 每 60 秒一次心跳

        while elapsed < self.max_seconds:
            # 尝试通过 WebSocket 保活/心跳
            try:
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.warning(f"WebSocket 异常中断，重连中... Error: {e}")

            # 如果 WS 无法建立，使用 HTTP 保底心跳
            self.http_heartbeat()

            time.sleep(ping_interval)
            elapsed = time.time() - self.start_time
            self.state["total_minutes"] += 1
            
            # 定期打印进度
            remaining_min = int((self.max_seconds - elapsed) / 60)
            logger.info(f"⏱️ 已运行 {int(elapsed / 60)} 分钟 | 剩余 {remaining_min} 分钟")

            # 实时保存一次状态，防止中途被打断
            save_state(self.state)

    def start(self):
        logger.info(f"🚀 开始运行 AFK 挂机脚本，设定运行时长: {RUN_MINUTES} 分钟")
        
        # 检查重置日期
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
