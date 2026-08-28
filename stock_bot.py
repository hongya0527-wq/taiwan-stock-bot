import os
from datetime import datetime
import requests

# === 你的全新機器人與聊天設定 ===
TELEGRAM_BOT_TOKEN = "8924824456:AAGI1KwdUqihOktG-OIR08CVmIdtc-krX-4"
TELEGRAM_CHAT_ID = "6273931436"

# 核心熱門概念股對應字典
CONCEPT_DICT = {
    "2330": ["半導體", "AI概念"],
    "2454": ["IC設計", "AI概念"],
    "2317": ["鴻海集團", "代工"],
    "2308": ["台達電", "重電綠能"],
    "3231": ["緯創", "AI伺服器"],
    "2382": ["廣達", "AI伺服器"],
    "3017": ["奇鋐", "散熱"],
    "2421": ["建準", "散熱"],
    "1519": ["華城", "重電綠能"],
    "1503": ["士電", "重電綠能"],
    "2603": ["長榮", "航運"],
    "2609": ["陽明", "航運"],
    "2881": ["富邦金", "金融"],
    "2882": ["國泰金", "金融"],
    "2891": ["中信金", "金融"],
}


def send_telegram_message(text):
    """傳送 Telegram 訊息"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")


def is_market_open():
    """簡單檢查今天是否為週末（週六=5, 週日=6）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return True


def job():
    """每日 12:45 執行主邏輯"""
    if not is_market_open():
        print("今日休市，不執行推播。")
        return

    market_info = "大盤：盤中即時監控中"

    # 策略一：出量陽包線（示範數據）
    strategy_1_results = [
        {
            "code": "2330",
            "name": "台積電",
            "price": 1050,
            "change": 4.5,
            "vol": "6.5萬張",
            "mult": "2.3倍",
        }
    ]

    # 策略二：跌破10日線站回（示範數據）
    strategy_2_results = [
        {
            "code": "2317",
            "name": "鴻海",
            "price": 210,
            "change": 3.9,
            "vol": "8.9萬張",
            "mult": "2.5倍",
        }
    ]

    # === 發送策略一訊息（獨立推播） ===
    msg_1 = f"🤖 **【策略一：出量陽包線】(12:45)**\n{market_info}\n\n"
    if strategy_1_results:
        msg_1 += f"符合條件 (共 {len(strategy_1_results)} 檔)：\n"
        for item in strategy_1_results:
            tags = CONCEPT_DICT.get(item["code"], ["一般類股"])
            tag_str = ", ".join(tags)
            link = f"https://tw.stock.yahoo.com/quote/{item['code']}.TW"
            msg_1 += f"• [{item['code']} {item['name']}]({link}) | [{tag_str}] | {item['price']} (+{item['change']}%) | 量 {item['vol']} ({item['mult']})\n"
    else:
        msg_1 += "🧊 今日無符合標的\n「收手吧阿祖，外面全都是空頭」 📉"
    send_telegram_message(msg_1)

    # === 發送策略二訊息（獨立推播） ===
    msg_2 = f"🤖 **【策略二：跌破10日線站回】(12:45)**\n{market_info}\n\n"
    if strategy_2_results:
        msg_2 += f"符合條件 (共 {len(strategy_2_results)} 檔)：\n"
        for item in strategy_2_results:
            tags = CONCEPT_DICT.get(item["code"], ["一般類股"])
            tag_str = ", ".join(tags)
            link = f"https://tw.stock.yahoo.com/quote/{item['code']}.TW"
            msg_2 += f"• [{item['code']} {item['name']}]({link}) | [{tag_str}] | {item['price']} (+{item['change']}%) | 量 {item['vol']} ({item['mult']})\n"
    else:
        msg_2 += "🧊 今日無符合標的\n「收手吧阿祖，外面全都是空頭」 📉"
    send_telegram_message(msg_2)


if __name__ == "__main__":
    job()
