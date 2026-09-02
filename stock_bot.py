import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import twstock
import yfinance as yf


# =========================================================
# Telegram 設定
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# =========================================================
# 台灣時區
# =========================================================

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


# =========================================================
# 核心策略設定
# =========================================================

# 正式訊號：當下成交量 >= 5,000 張
MIN_VOLUME_LOTS = 5000

# 調整後的今日相對量能比例（因 12:45 已過大半交易時間，採用動態時間權重基準）
MIN_TODAY_VOLUME_RATIO = 0.50

# 目標掃描時間
TARGET_HOUR = 12
TARGET_MINUTE = 45

# 最晚允許推播時間
DEADLINE_HOUR = 13
DEADLINE_MINUTE = 10


# =========================================================
# MIS API 設定
# =========================================================

TWSE_MIS_URL = (
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
)

TWSE_HOME_URL = (
    "https://mis.twse.com.tw/stock/index.jsp"
)

# 每批 80 檔
REALTIME_BATCH_SIZE = 80

# 每批間隔
REALTIME_BATCH_DELAY = 1.0


# =========================================================
# Yahoo 設定
# =========================================================

YAHOO_BATCH_SIZE = 50
YAHOO_BATCH_DELAY = 1.2

HISTORY_PERIOD = "3mo"
HISTORY_INTERVAL = "1d"

CACHE_PREFIX = "stock_history_"


# =========================================================
# Retry Session
# =========================================================

def create_retry_session(
    retries=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
):

    session = requests.Session()

    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry
    )

    session.mount(
        "https://",
        adapter,
    )

    session.mount(
        "http://",
        adapter,
    )

    return session


# =========================================================
# Telegram
# =========================================================

def send_telegram_message(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("Telegram Secrets 不存在")

        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    if len(text) > 4000:

        text = (
            text[:3900]
            + "\n\n...（訊息過長，已截斷）"
        )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    session = create_retry_session(
        retries=4,
        backoff_factor=1,
    )

    try:

        response = session.post(
            url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        print("Telegram 推播成功")

    except Exception as e:

        print(f"Telegram 發送失敗：{e}")


# =========================================================
# 台灣現在時間
# =========================================================

def taipei_now():

    return datetime.now(TAIPEI_TZ)


# =========================================================
# 是否超過 13:10
# =========================================================

def is_past_deadline():

    now = taipei_now()

    deadline = now.replace(
        hour=DEADLINE_HOUR,
        minute=DEADLINE_MINUTE,
        second=0,
        microsecond=0,
    )

    return now >= deadline


# =========================================================
# 等待 12:45
# =========================================================

def wait_until_target_time():

    now = taipei_now()

    target = now.replace(
        hour=TARGET_HOUR,
        minute=TARGET_MINUTE,
        second=0,
        microsecond=0,
    )

    if now >= target:

        print(
            "目前已經超過 12:45，"
            "直接開始盤中掃描。"
        )

        return

    wait_seconds = (
        target - now
    ).total_seconds()

    print(
        f"目前時間："
        f"{now.strftime('%H:%M:%S')}"
    )

    print(
        f"等待至 12:45，"
        f"約 {int(wait_seconds)} 秒..."
    )

    while True:

        now = taipei_now()

        remaining = (
            target - now
        ).total_seconds()

        if remaining <= 0:
            break

        time.sleep(
            min(remaining, 30)
        )

    print(
        "已到 12:45，開始掃描。"
    )


# =========================================================
# 台股交易日
# =========================================================

def is_market_open():

    now = taipei_now()

    if now.weekday() >= 5:

        return False

    try:

        holidays = twstock.twse.holidays(
            year=now.year
        )

        today_str = now.strftime(
            "%Y-%m-%d"
        )

        for item in holidays:

            try:

                holiday_date = item[0].strftime(
                    "%Y-%m-%d"
                )

                if holiday_date == today_str:

                    return False

            except Exception:

                continue

    except Exception as e:

        print(
            f"休市日資料取得失敗：{e}"
        )

    return True


# =========================================================
# 股票清單
# =========================================================

def get_stock_list():

    stocks = twstock.codes

    ticker_map = {}

    for code, info in stocks.items():

        if len(code) != 4:
            continue

        if info.type in ["股票", "上市"]:

            ticker_map[code] = (
                f"{code}.TW"
            )

        elif info.type == "上櫃":

            ticker_map[code] = (
                f"{code}.TWO"
            )

    return stocks, ticker_map


# =========================================================
# Safe Float
# =========================================================

def safe_float(value):

    if value in [
        None,
        "",
        "-",
        "--",
    ]:

        return None

    try:

        number = float(value)

        if number <= 0:
            return None

        return number

    except Exception:

        return None


# =========================================================
# TWSE / TPEx 即時行情
# =========================================================

def download_realtime_market_data(
    ticker_map,
):

    print(
        "開始取得 TWSE / TPEx "
        "盤中即時行情..."
    )

    realtime_data = {}

    items = []

    for code, symbol in ticker_map.items():

        if symbol.endswith(".TW"):

            items.append(
                f"tse_{code}.tw"
            )

        elif symbol.endswith(".TWO"):

            items.append(
                f"otc_{code}.tw"
            )

    total_batches = (
        (
            len(items)
            + REALTIME_BATCH_SIZE
            - 1
        )
        // REALTIME_BATCH_SIZE
    )

    print(
        f"即時行情共 {len(items)} 檔，"
        f"分 {total_batches} 批取得。"
    )

    session = create_retry_session(
        retries=3,
        backoff_factor=1,
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/122.0.0.0 "
            "Safari/537.36"
        ),
        "Referer": TWSE_HOME_URL,
        "Accept": (
            "application/json, "
            "text/javascript, */*; q=0.01"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
    }

    try:

        session.get(
            TWSE_HOME_URL,
            headers=headers,
            timeout=10,
        )

    except Exception as e:

        print(
            f"MIS Session 建立失敗：{e}"
        )

    for start in range(
        0,
        len(items),
        REALTIME_BATCH_SIZE,
    ):

        batch = items[
            start:
            start + REALTIME_BATCH_SIZE
        ]

        batch_number = (
            start
            // REALTIME_BATCH_SIZE
            + 1
        )

        ex_ch = "|".join(batch)

        params = {
            "ex_ch": ex_ch,
            "json": "1",
            "delay": "0",
            "_": str(
                int(
                    time.time() * 1000
                )
            ),
        }

        success = False

        for attempt in range(1, 4):

            try:

                response = session.get(
                    TWSE_MIS_URL,
                    params=params,
                    headers=headers,
                    timeout=25,
                )

                response.raise_for_status()

                data = response.json()

                msg_array = data.get(
                    "msgArray",
                    [],
                )

                valid_count = 0

                for item in msg_array:

                    try:

                        code = item.get("c")

                        if not code:
                            continue

                        price = safe_float(
                            item.get("z")
                        )

                        volume = safe_float(
                            item.get("v")
                        )

                        open_price = safe_float(
                            item.get("o")
                        )

                        if (
                            price is None
                            or volume is None
                        ):
                            continue

                        realtime_data[code] = {
                            "price": price,
                            "volume": volume,
                            "open": open_price,
                            "time": item.get("t"),
                        }

                        valid_count += 1

                    except Exception:
                        continue

                print(
                    f"即時第 "
                    f"{batch_number}/{total_batches} "
                    f"批成功："
                    f"{valid_count} 檔"
                )

                success = True

                break

            except Exception as e:

                print(
                    f"即時第 "
                    f"{batch_number} 批 "
                    f"第 {attempt} 次失敗："
                    f"{e}"
                )

                if attempt < 3:

                    time.sleep(
                        attempt * 2
                    )

        if not success:

            print(
                f"⚠️ 即時第 "
                f"{batch_number} 批最終失敗"
            )

        time.sleep(
            REALTIME_BATCH_DELAY
        )

    print(
        f"TWSE / TPEx 即時資料完成："
        f"{len(realtime_data)} 檔"
    )

    return realtime_data


# =========================================================
# 找出 >= 5,000 張候選股
# =========================================================

def filter_volume_candidates(
    realtime_data,
):

    candidates = {}

    for code, data in realtime_data.items():

        try:

            volume = float(
                data["volume"]
            )

            price = float(
                data["price"]
            )

            if (
                volume >= MIN_VOLUME_LOTS
                and price > 0
            ):

                candidates[code] = data

        except Exception:

            continue

    print(
        f"成交量 >= "
        f"{MIN_VOLUME_LOTS:,} 張："
        f"{len(candidates)} 檔"
    )

    return candidates


# =========================================================
# Yahoo MultiIndex 相容
# =========================================================

def extract_stock_data(
    df_all,
    symbol,
):

    if (
        df_all is None
        or df_all.empty
    ):

        return None

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    if not isinstance(
        df_all.columns,
        pd.MultiIndex,
    ):

        if all(
            column in df_all.columns
            for column in required
        ):

            return df_all.copy()

        return None

    for level in range(
        df_all.columns.nlevels
    ):

        try:

            level_values = (
                df_all.columns
                .get_level_values(level)
            )

            if symbol not in level_values:
                continue

            df = df_all.xs(
                symbol,
                axis=1,
                level=level,
            ).copy()

            if isinstance(
                df.columns,
                pd.MultiIndex,
            ):

                new_columns = []

                for col in df.columns:

                    found = None

                    if isinstance(
                        col,
                        tuple,
                    ):

                        for value in col:

                            if value in required:

                                found = value

                                break

                    elif col in required:

                        found = col

                    new_columns.append(
                        found
                        if found is not None
                        else str(col)
                    )

                df.columns = new_columns

            if all(
                column in df.columns
                for column in required
            ):

                return df

        except Exception:

            continue

    return None


# =========================================================
# 清理歷史資料
# =========================================================

def prepare_historical_data(
    df,
):

    if (
        df is None
        or df.empty
    ):

        return None

    df = df.copy()

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    if not all(
        column in df.columns
        for column in required
    ):

        return None

    try:

        today = taipei_now().date()

        dates = pd.to_datetime(
            df.index
        ).date

        df = df[
            dates < today
        ]

    except Exception:

        pass

    df = df.dropna(
        subset=required
    )

    if len(df) < 15:

        return None

    return df


# =========================================================
# Cache
# =========================================================

def get_cache_file():

    today_str = (
        taipei_now()
        .strftime("%Y-%m-%d")
    )

    return (
        f"{CACHE_PREFIX}"
        f"{today_str}.pkl"
    )


# =========================================================
# 清理舊快取
# =========================================================

def cleanup_old_cache():

    today = taipei_now().date()

    try:

        for filename in os.listdir("."):

            if not filename.startswith(
                CACHE_PREFIX
            ):
                continue

            if not filename.endswith(
                ".pkl"
            ):
                continue

            try:

                date_text = filename[
                    len(CACHE_PREFIX):-4
                ]

                file_date = datetime.strptime(
                    date_text,
                    "%Y-%m-%d",
                ).date()

                if (
                    today - file_date
                ).days > 3:

                    os.remove(
                        filename
                    )

                    print(
                        f"刪除舊快取："
                        f"{filename}"
                    )

            except Exception:

                continue

    except Exception as e:

        print(
            f"清理快取失敗：{e}"
        )


# =========================================================
# Yahoo 歷史資料 (加入完整度檢查與防污染驗證)
# =========================================================

def download_candidate_history(
    candidate_tickers,
):

    if not candidate_tickers:

        print(
            "沒有候選股票，"
            "不下載 Yahoo 歷史資料。"
        )

        return None

    cleanup_old_cache()

    cache_file = get_cache_file()

    if os.path.exists(cache_file):

        print(
            "發現今日 Yahoo 歷史快取，"
            "嘗試讀取與驗證..."
        )

        try:

            cached = pd.read_pickle(
                cache_file
            )

            if (
                cached is not None
                and not cached.empty
            ):
                valid_cached_count = 0
                for symbol in candidate_tickers:
                    df_test = extract_stock_data(cached, symbol)
                    df_test = prepare_historical_data(df_test)
                    if df_test is not None:
                        valid_cached_count += 1
                
                completeness = valid_cached_count / len(candidate_tickers) if candidate_tickers else 0
                if completeness >= 0.60:
                    print("今日歷史快取驗證有效，直接使用。")
                    return cached
                else:
                    print("⚠️ 快取資料完整度不足，將重新下載覆蓋。")
                    os.remove(cache_file)

        except Exception as e:

            print(
                f"快取損毀，重新下載："
                f"{e}"
            )

            try:
                os.remove(cache_file)
            except Exception:
                pass

    print(
        f"開始下載候選股歷史日 K："
        f"{len(candidate_tickers)} 檔"
    )

    all_batches = []

    total = len(candidate_tickers)

    for start in range(
        0,
        total,
        YAHOO_BATCH_SIZE,
    ):

        batch = candidate_tickers[
            start:
            start + YAHOO_BATCH_SIZE
        ]

        print(
            f"Yahoo 歷史："
            f"{start + 1}-"
            f"{min(start + len(batch), total)}"
            f"/{total}"
        )

        success = False

        for attempt in range(1, 4):

            try:

                df_batch = yf.download(
                    batch,
                    period=HISTORY_PERIOD,
                    interval=HISTORY_INTERVAL,
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=True,
                )

                if (
                    df_batch is not None
                    and not df_batch.empty
                ):

                    all_batches.append(
                        df_batch
                    )

                    success = True

                    break

            except Exception as e:

                print(
                    f"Yahoo 批次失敗 "
                    f"(第 {attempt} 次)："
                    f"{e}"
                )

                if attempt < 3:

                    time.sleep(
                        attempt * 2
                    )

        if not success:

            print(
                "⚠️ 此批 Yahoo 歷史資料"
                "最終下載失敗"
            )

        time.sleep(
            YAHOO_BATCH_DELAY
        )

    if not all_batches:

        print(
            "Yahoo 完全沒有取得歷史資料"
        )

        return None

    try:

        df_all = pd.concat(
            all_batches,
            axis=1,
        )

    except Exception as e:

        print(
            f"Yahoo 歷史資料合併失敗："
            f"{e}"
        )

        return None

    valid_symbols = 0

    for symbol in candidate_tickers:

        df = extract_stock_data(
            df_all,
            symbol,
        )

        df = prepare_historical_data(
            df
        )

        if df is not None:

            valid_symbols += 1

    expected = len(
        candidate_tickers
    )

    completeness = (
        valid_symbols / expected
        if expected > 0
        else 0
    )

    print(
        f"Yahoo 歷史資料完整度："
        f"{valid_symbols}/{expected} "
        f"({completeness:.1%})"
    )

    if (
        expected > 0
        and completeness < 0.60
    ):

        print(
            "⚠️ Yahoo 歷史資料完整度過低，"
            "不使用本次結果。"
        )

        return None

    temp_file = cache_file + ".tmp"

    try:

        df_all.to_pickle(
            temp_file
        )

        os.replace(
            temp_file,
            cache_file,
        )

        print(
            f"今日 Yahoo 歷史資料"
            f"已建立快取："
            f"{cache_file}"
        )

    except Exception as e:

        print(
            f"寫入歷史快取失敗："
            f"{e}"
        )

        try:

            if os.path.exists(
                temp_file
            ):

                os.remove(
                    temp_file
                )

        except Exception:
            pass

    return df_all


# =========================================================
# 主掃描
# =========================================================

def scan_taiwan_stocks():

    strategy_1_results = []

    start_time = time.time()

    try:

        print(
            "================================"
        )

        print(
            "開始 12:45 台股即時掃描"
        )

        print(
            "================================"
        )

        stocks, ticker_map = (
            get_stock_list()
        )

        print(
            f"市場股票數量："
            f"{len(ticker_map)} 檔"
        )

        realtime_data = (
            download_realtime_market_data(
                ticker_map
            )
        )

        if not realtime_data:

            error_msg = "❌ 掃描失敗：沒有取得任何即時行情（可能是 TWSE MIS API 異常或被阻擋）"
            print(error_msg)
            send_telegram_message(f"🚨 **【系統警報】**\n{error_msg}")
            return strategy_1_results

        realtime_completeness = (
            len(realtime_data)
            / len(ticker_map)
            if ticker_map
            else 0
        )

        print(
            f"即時資料完整度："
            f"{len(realtime_data)}/"
            f"{len(ticker_map)} "
            f"({realtime_completeness:.1%})"
        )

        if realtime_completeness < 0.60:

            error_msg = f"❌ 掃描失敗：即時資料完整度過低 ({realtime_completeness:.1%})"
            print(error_msg)
            send_telegram_message(f"🚨 **【系統警報】**\n{error_msg}")
            return strategy_1_results

        candidates = (
            filter_volume_candidates(
                realtime_data
            )
        )

        if not candidates:

            print(
                "今日目前沒有 "
                ">= 5,000 張候選股。"
            )

            return strategy_1_results

        candidate_tickers = []

        for code in candidates:

            symbol = ticker_map.get(code)

            if symbol:

                candidate_tickers.append(
                    symbol
                )

        df_all = (
            download_candidate_history(
                candidate_tickers
            )
        )

        if (
            df_all is None
            or df_all.empty
        ):

            error_msg = "❌ 掃描失敗：候選股歷史資料不足，無法進行策略運算。"
            print(error_msg)
            send_telegram_message(f"🚨 **【系統警報】**\n{error_msg}")
            return strategy_1_results

        print(
            f"開始計算 "
            f"{len(candidates)} 檔候選股..."
        )

        for code, realtime in candidates.items():

            try:

                symbol = ticker_map.get(code)

                if not symbol:
                    continue

                current_price = float(
                    realtime["price"]
                )

                current_volume = float(
                    realtime["volume"]
                )

                current_open = realtime.get(
                    "open"
                )

                if (
                    current_price <= 0
                    or current_volume < MIN_VOLUME_LOTS
                ):

                    continue

                df = extract_stock_data(
                    df_all,
                    symbol,
                )

                df = prepare_historical_data(
                    df
                )

                if df is None:
                    continue

                if len(df) < 15:
                    continue

                yesterday = df.iloc[-1]

                close_yesterday = float(
                    yesterday["Close"]
                )

                open_yesterday = float(
                    yesterday["Open"]
                )

                high_yesterday = float(
                    yesterday["High"]
                )

                low_yesterday = float(
                    yesterday["Low"]
                )

                yesterday_volume = float(
                    yesterday["Volume"]
                )

                if (
                    close_yesterday <= 0
                    or open_yesterday <= 0
                    or yesterday_volume <= 0
                ):

                    continue

                vol_yesterday_lots = (
                    yesterday_volume / 1000
                )

                if vol_yesterday_lots <= 0:
                    continue

                time_weight = 270.0 / 225.0
                estimated_full_day_volume = current_volume * time_weight
                volume_ratio = estimated_full_day_volume / vol_yesterday_lots

                if (
                    volume_ratio
                    < MIN_TODAY_VOLUME_RATIO
                ):

                    continue

                change_pct = (
                    (
                        current_price
                        - close_yesterday
                    )
                    / close_yesterday
                ) * 100

                # -----------------------------------------
                # 策略核心：陽包陰 + 量能增加
                # -----------------------------------------

                # 1. 昨天必須是黑 K（收盤 < 開盤）
                yesterday_is_bearish = close_yesterday < open_yesterday
                if not yesterday_is_bearish:
                    continue

                # 2. 今天盤中必須是紅 K（現價 > 開盤）
                current_is_bullish = current_open is not None and current_price > current_open
                if not current_is_bullish:
                    continue

                # 3. 陽包陰核心條件：今天開盤 <= 昨天收盤，且今天現價 >= 昨天開盤
                is_bullish_engulfing = (
                    current_open is not None
                    and current_open <= close_yesterday
                    and current_price >= open_yesterday
                )

                if not is_bullish_engulfing:
                    continue

                signal_type = "🔥 陽包陰"

                stock_info = stocks.get(code)

                stock_name = (
                    stock_info.name
                    if stock_info
                    else code
                )

                stock_group = (
                    stock_info.group
                    if (
                        stock_info
                        and stock_info.group
                    )
                    else "其他產業"
                )

                suffix = (
                    ".TW"
                    if symbol.endswith(".TW")
                    else ".TWO"
                )

                yesterday_body_pct = (
                    (
                        open_yesterday
                        - close_yesterday
                    )
                    / open_yesterday
                ) * 100

                strategy_1_results.append(
                    {
                        "code": code,
                        "name": stock_name,
                        "group": stock_group,
                        "price": round(
                            current_price,
                            2,
                        ),
                        "change": round(
                            change_pct,
                            2,
                        ),
                        "vol": (
                            f"{int(current_volume):,}"
                            "張"
                        ),
                        "mult": (
                            f"{volume_ratio:.1f}"
                            "倍昨日量"
                        ),
                        "signal": signal_type,
                        "suffix": suffix,
                        "yesterday_open": round(
                            open_yesterday,
                            2,
                        ),
                        "yesterday_close": round(
                            close_yesterday,
                            2,
                        ),
                        "body": round(
                            yesterday_body_pct,
                            2,
                        ),
                    }
                )

            except Exception as e:

                print(
                    f"股票 {code} "
                    f"策略計算失敗："
                    f"{e}"
                )

                continue

        strategy_1_results.sort(
            key=lambda x: (
                x["change"],
                x["mult"],
            ),
            reverse=True,
        )

        elapsed = (
            time.time()
            - start_time
        )

        print(
            "================================"
        )

        print(
            f"掃描完成，耗時 "
            f"{elapsed:.1f} 秒"
        )

        print(
            f"符合條件："
            f"{len(strategy_1_results)} 檔"
        )

        print(
            "================================"
        )

    except Exception as e:

        err_msg = f"全台股掃描發生未預期錯誤：{e}"
        print(err_msg)
        send_telegram_message(f"🚨 **【系統異常警報】**\n{err_msg}")

    return strategy_1_results


# =========================================================
# Telegram 訊息
# =========================================================

def build_message(
    title,
    results,
):

    now = taipei_now()

    now_text = now.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    message = (
        f"🤖 **【{title}】(12:45 盤中)**\n"
        f"即時掃描時間：{now_text}\n"
        f"條件：成交量 ≥ 5,000 張 (含時間權重估算)\n\n"
    )

    if results:

        message += (
            f"符合條件 "
            f"(共 {len(results)} 檔)：\n"
        )

        for item in results:

            link = (
                "https://tw.stock.yahoo.com/quote/"
                f"{item['code']}"
                f"{item['suffix']}"
            )

            message += (
                f"• [{item['code']} "
                f"{item['name']}]"
                f"({link}) | "
                f"{item['signal']} | "
                f"{item['price']} "
                f"({item['change']:+.2f}%) | "
                f"量 {item['vol']} "
                f"({item['mult']})\n"
            )

        message += (
            "\n⚠️ TWSE / TPEx 12:45 盤中訊號"
            "\n⚠️ 尚未收盤"
        )

    else:

        message += (
            "🧊 今日無符合標的\n"
            "「收手吧阿祖，外面全都是空頭」 📉"
        )

    return message


# =========================================================
# 主程式
# =========================================================

def job():

    now = taipei_now()

    print(
        "================================"
    )

    print(
        "台股 12:45 即時掃描器"
    )

    print(
        f"目前台灣時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "================================"
    )

    if not is_market_open():

        print(
            "今日休市，不執行。"
        )

        return

    wait_until_target_time()

    if is_past_deadline():

        now = taipei_now()

        print(
            "❌ 已超過 13:10，"
            "不執行盤中 12:45 訊號。"
        )

        print(
            f"目前時間："
            f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return

    s1_results = scan_taiwan_stocks()

    send_telegram_message(
        build_message(
            "陽包陰量能增加掃描",
            s1_results,
        )
    )

    now = taipei_now()

    print(
        "================================"
    )

    print(
        f"推播完成時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "================================"
    )


# =========================================================
# 執行
# =========================================================

if __name__ == "__main__":

    job()
