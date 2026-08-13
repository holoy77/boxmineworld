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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Origin": "https://afk.boxmineworld.com",
    "Referer": "https://afk.boxmineworld.com/",
}

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

# ==================== AFK 心跳逻辑 ====================
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

                # 1. 处理多设备冲突警告
                if msg.get("error") == "{device:true}":
                    logger.warning("⚠️ 检测到账号已在其他设备（如网页端）挂机中！请先关闭网页端挂机。")
                    return

# 2. 响应服务端的活跃度检查 (activity_check)
                if msg_type == "activity_check":
                    check_id = msg.get("checkId")
                    logger.info(f"📩 收到服务端心跳校验 checkId: {check_id}")
                    
                    # ⚠️ 注意：这里 checkId 的 I 必须大写！
                    response_payload = {
                        "type": "activity_check_response",
                        "checkId": check_id,
                        "subscriptionId": self.subscription_id
                    }
                    ws.send(json.dumps(response_payload))
                    logger.info(f"📤 已成功应答心跳校验！")

                # 3. 统计金币/收益变动
                if "coins" in msg or "balance" in msg or "earned" in msg:
                    coins = msg.get("coins") or msg.get("balance") or msg.get("earned") or 0
                    self.state["coins_earned"] = coins
                    logger.info(f"💰 当前收益金币: {coins}")

            except Exception:
                logger.info(f"📩 收到消息: {message}")

        def on_error(ws, error):
            logger.error(f"⚠️ WebSocket 错误: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"🔒 WebSocket 连接关闭: {close_status_code} - {close_msg}")

        def on_open(ws):
            logger.info("🟢 WebSocket 已连接，发送挂机订阅请求...")
            
            # 生成全新的订阅 ID 并发送 subscribe 报文
            self.subscription_id = str(uuid.uuid4())
            subscribe_payload = {
                "type": "subscribe",
                "path": "/earn",
                "subscriptionId": self.subscription_id
            }
            ws.send(json.dumps(subscribe_payload))
            logger.info(f"📤 已发送订阅: {subscribe_payload}")

        token_param = SESSION_KEY or DISCORD_TOKEN
        ws_url = f"{BASE_WS_URL}?token={token_param}&session_key={token_param}"

        while time.time() - self.start_time < self.max_seconds:
            elapsed = time.time() - self.start_time
            remaining_min = int((self.max_seconds - elapsed) / 60)
            logger.info(f"⌛ 连接挂机服务器中... | 剩余 {remaining_min} 分钟")

            ws = websocket.WebSocketApp(
                ws_url,
                header=HEADERS,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            # 保持连接
            ws.run_forever()

            # 断线保存并准备重连
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
