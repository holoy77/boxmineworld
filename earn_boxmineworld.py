import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BoxMineWorld-AFK")

# ==================== 环境变量与配置 ====================
SESSION_KEY = os.getenv("BOXMINEWORLD_SESSION_KEY", "").strip()
RUN_MINUTES = int(os.getenv("RUN_MINUTES", "340"))
STATE_FILE = "boxmineworld_state.json"
TARGET_URL = "https://afk.boxmineworld.com"

# 清理 Session Key（兼容包含键名或前后引号的情况）
if "_SECURE_BOX_AUTH_SESSION_=" in SESSION_KEY:
    SESSION_KEY = SESSION_KEY.split("_SECURE_BOX_AUTH_SESSION_=")[-1].split(";")[0].strip()

# ==================== 状态管理 ====================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_run": None, "daily_count": 0, "coins_earned": 0, "status": "init"}

def save_state(state: dict):
    try:
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存状态文件失败: {e}")

# ==================== 主挂机逻辑 ====================
def run_browser_afk():
    if not SESSION_KEY:
        logger.error("❌ 缺少 BOXMINEWORLD_SESSION_KEY 环境变量！")
        sys.exit(1)

    state = load_state()
    start_time = time.time()
    max_seconds = RUN_MINUTES * 60
    current_coins = state.get("coins_earned", 0)

    logger.info(f"🚀 启动 Playwright 无头浏览器挂机模式 | 预设最长运行: {RUN_MINUTES} 分钟")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--mute-audio"
            ]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )

        # 注入登录 Cookie
        context.add_cookies([{
            "name": "_SECURE_BOX_AUTH_SESSION_",
            "value": SESSION_KEY,
            "domain": ".boxmineworld.com",
            "path": "/"
        }])

        page = context.new_page()

        # 监听 WebSocket 消息（仅处理错误与金币结算）
        def handle_ws_frame(frame):
            nonlocal current_coins
            try:
                msg = json.loads(frame.payload)
                
                # 错误处理：仅在失败/异常时输出
                if msg.get("error") == "{device:true}":
                    logger.error("❌ 心跳异常: 账号正在其他设备挂机中，连接冲突！")
                
                # 金币数据监听（捕获 /earn 路径下的数据推送）
                data = msg.get("data", {})
                if isinstance(data, dict):
                    earned = data.get("earned")
                    max_earn = data.get("max_earn", 8)
                    cooldown = data.get("cooldown", False)

                    if earned is not None:
                        if earned != current_coins:
                            current_coins = earned
                            state["coins_earned"] = earned
                            save_state(state)
                            logger.info(f"💰 【金币增加】当前累计获得: {earned} / {max_earn} 个金币")

                        # 达到额度或进入冷却自动退出
                        if cooldown or earned >= max_earn:
                            logger.info(f"🏁 已达到每日挂机上限或进入冷却 (已得: {earned} 个)，任务完成，结束运行。")
                            state["status"] = "completed"
                            save_state(state)
                            browser.close()
                            sys.exit(0)

            except Exception:
                pass

        def on_web_socket(ws):
            # 仅在 WS 异常关闭时报警
            ws.on("framereceived", handle_ws_frame)
            ws.on("close", lambda: logger.warning("⚠️ WebSocket 心跳连接断开，页面将自动重连..."))
            ws.on("socketerror", lambda err: logger.error(f"❌ WebSocket 连接失败: {err}"))

        page.on("websocket", on_web_socket)

        # 访问挂机页面
        try:
            logger.info("🌐 正在载入 afk.boxmineworld.com ...")
            page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        except Exception as e:
            logger.error(f"❌ 页面加载超时或失败: {e}")

        # 挂机轮询循环
        while time.time() - start_time < max_seconds:
            time.sleep(30)
            # 保持状态更新
            save_state(state)

        logger.info("⏱️ 本轮挂机设定时间已满，正常结束。")
        browser.close()

if __name__ == "__main__":
    run_browser_afk()
