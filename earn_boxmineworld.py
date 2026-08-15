import os
import re
import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

AUTH_STATE_PATH = "boxmineworld_state.json"
TARGET_URL = "https://afk.boxmineworld.com"
TARGET_COINS = 8          # 达到 8 个金币后自动退出
CHECK_INTERVAL = 30       # 检查页面状态间隔（秒）
MAX_TIMEOUT_MINUTES = 120 # 保底最长运行时间（分钟），防止意外卡住

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [INFO] {msg}", flush=True)

def main():
    log(f"🚀 启动 Playwright 无头浏览器挂机模式 | 目标金币: {TARGET_COINS} 个")
    
    if not os.path.exists(AUTH_STATE_PATH):
        log(f"❌ 找不到登录凭证文件: {AUTH_STATE_PATH}，请确保已提交有效的 state 文件！")
        sys.exit(1)

    start_time = time.time()

    with sync_playwright() as p:
        # 启动 Chromium 浏览器
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        # 使用持久化 Session 状态创建上下文
        context = browser.new_context(
            storage_state=AUTH_STATE_PATH,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()

        try:
            log(f"🌐 正在载入 {TARGET_URL} ...")
            page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            time.sleep(5)  # 等待动态 WebSocket/脚本建立连接

            # 验证是否处于未登录状态
            if "/auth/sign-in" in page.url or "Sign in" in page.content():
                log("⚠️ 检测到当前未登录或 Session 已失效，请更新 boxmineworld_state.json")
                sys.exit(1)

            log("✅ 页面加载成功，已进入后台挂机与金币监控状态...")

            while True:
                # 检查是否超过保底超时时间
                elapsed_minutes = (time.time() - start_time) / 60
                if elapsed_minutes >= MAX_TIMEOUT_MINUTES:
                    log(f"⏰ 已达到最大安全挂机时长 ({MAX_TIMEOUT_MINUTES} 分钟)，准备退出。")
                    break

                try:
                    content = page.content()

                    # 1. 提取 Session 获得金币数（匹配类似 "+8" 或 "Session Coins Earned" 区域）
                    coins_match = re.search(r'\+(\d+)', content)
                    current_coins = int(coins_match.group(1)) if coins_match else 0

                    # 2. 检测是否触发冷却（如：Next coins available in ...）
                    is_on_cooldown = "Next coins available in" in content or "available in" in content

                    log(f"💰 挂机进度: +{current_coins}/{TARGET_COINS} 金币 | 已运行时长: {int(elapsed_minutes)} 分钟")

                    # 达成 8 个金币
                    if current_coins >= TARGET_COINS:
                        log(f"🎉 本次已成功获取 {current_coins} 个金币，达到目标上限，正在退出...")
                        break

                    # 触发每日冷却限制
                    if is_on_cooldown and current_coins > 0:
                        log("⏳ 页面显示已进入每日冷却状态（达到当日上限），正在退出...")
                        break

                except Exception as e:
                    log(f"⚠️ 状态解析异常: {e}")

                time.sleep(CHECK_INTERVAL)

        finally:
            # 退出前更新并保存 storage_state
            try:
                context.storage_state(path=AUTH_STATE_PATH)
                log(f"💾 已更新并保存登录状态至 {AUTH_STATE_PATH}")
            except Exception as e:
                log(f"⚠️ 保存 Session 失败: {e}")

            browser.close()
            log("👋 浏览器已安全关闭，任务正常结束。")

if __name__ == "__main__":
    main()
