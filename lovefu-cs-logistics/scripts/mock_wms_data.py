"""
WMS 暢流 Mock 資料 — 不打外部 API，本地測試用

欄位結構完全對齊 WMS 暢流 API 文件（2026-04 版）：
  - 訂單：/api_v1/order/order_query.php 回應 data.rows[]
  - 貨態：/api_v1/order/order_logistics.php 回應 data.rows[]
  - 庫存：/api_v1/inventory/stock_query.php 回應 data.rows[]
  - 門市：/api_v1/pos/store.php 回應 data.rows[]
  - 物流代碼：/api_v1/order/logistics_code.php 回應 data.rows[]
"""
from datetime import datetime, timedelta

NOW = datetime.now()


MOCK_ORDERS = {
    "L20260415001": {
        "source": "Shopline",
        "source_id": "lovefu321930",
        "source_key": "shopline",
        "order_date": (NOW - timedelta(days=9)).strftime("%Y/%m/%d %H:%M:%S"),
        "finish_time": (NOW - timedelta(days=2)).strftime("%Y/%m/%d %H:%M:%S"),
        "order_no": "L20260415001",
        "status_code": "F",  # F=出貨完成
        "status_name": "出貨完成",
        "collection": False,
        "total_price": 28800,
        "freight_price": 0,
        "pay_name": "信用卡",
        # AES 加密欄位（mock 模式直接給已遮罩值，無需真解密）
        "receiver_name": "王*明",
        "receiver_phone": "****5678",
        "logistics_code": "post",
        "freight_name": "中華郵政",
        "receiver_info": {
            "address": "台北市大安區忠孝東路***",
            "zip": "106",
            "city": "台北市",
            "area": "大安區",
            "street": "忠孝東路***",
        },
        "send_num": "8901234567890",
        "AllPayLogisticsID": "ECPAY1234567",
        "buyer_msg": "",
        "remark": "",
        "notice": "",
        "products": [
            {
                "sku": "MAT-HILL-Q",
                "item_no": "MAT-HILL-Q",
                "type": "warehouse",
                "name": "山丘床墊 標準雙人",
                "spec": "Queen 5x6.2尺",
                "price": 28800,
                "qty": 1,
                "shipp_qty": 1,
            },
        ],
        "warehouse": {"id": 1, "name": "總倉", "type": "實體倉"},
    },
    "L20260413007": {
        "source": "Shopline",
        "source_id": "lovefu321930",
        "source_key": "shopline",
        "order_date": (NOW - timedelta(days=11)).strftime("%Y/%m/%d %H:%M:%S"),
        "finish_time": None,
        "order_no": "L20260413007",
        "status_code": "W",  # W=待出貨
        "status_name": "待出貨",
        "collection": False,
        "total_price": 5960,
        "freight_price": 0,
        "pay_name": "信用卡",
        "receiver_name": "陳*",
        "receiver_phone": "****1234",
        "logistics_code": "kerry",
        "freight_name": "嘉里大榮",
        "receiver_info": {
            "address": "新北市板橋區文化路***",
            "zip": "220",
            "city": "新北市",
            "area": "板橋區",
            "street": "文化路***",
        },
        "send_num": "",
        "buyer_msg": "希望下午配送",
        "remark": "",
        "notice": "",
        "products": [
            {
                "sku": "PIL-MOON-3",
                "item_no": "PIL-MOON-3",
                "type": "warehouse",
                "name": "月眠枕 3.0",
                "spec": "標準款",
                "price": 2980,
                "qty": 2,
                "shipp_qty": 0,
            },
        ],
        "warehouse": {"id": 1, "name": "總倉", "type": "實體倉"},
    },
}


# 貨態 — 欄位對齊 WMS API：timelines[].{time, text}
MOCK_CARGO_TIMELINES = {
    "L20260415001": {
        "order_no": "L20260415001",
        "send_num": "8901234567890",
        "timelines": [
            {"time": (NOW - timedelta(days=2)).strftime("%Y/%m/%d %H:%M:%S"), "text": "已建立物流單"},
            {"time": (NOW - timedelta(days=1, hours=8)).strftime("%Y/%m/%d %H:%M:%S"), "text": "已交付物流商-中華郵政"},
            {"time": (NOW - timedelta(hours=18)).strftime("%Y/%m/%d %H:%M:%S"), "text": "幹線運送中-台北轉運中心"},
            {"time": (NOW - timedelta(hours=2)).strftime("%Y/%m/%d %H:%M:%S"), "text": "派送中-大安郵局"},
        ],
    },
    "L20260413007": {
        "order_no": "L20260413007",
        "send_num": "",
        "timelines": [],  # 待出貨，尚無貨態
    },
}


# 庫存 — 欄位對齊 WMS API：sku, item_no, name, spec, stock, safe_stock, occupied_stock, spaces[]
MOCK_INVENTORY = {
    "MAT-HILL-Q": {
        "sku": "MAT-HILL-Q",
        "item_no": "MAT-HILL-Q",
        "name": "山丘床墊 標準雙人",
        "spec": "Queen 5x6.2尺",
        "stock": 23,
        "safe_stock": 5,
        "occupied_stock": 3,
        "spaces": [
            {"name": "A-01-01", "stock": 23, "occupied_stock": 3, "wh_id": "1", "warehouse": {"id": 1, "name": "總倉", "type": "實體倉"}},
        ],
    },
    "MAT-HILL-K": {
        "sku": "MAT-HILL-K",
        "item_no": "MAT-HILL-K",
        "name": "山丘床墊 加大雙人",
        "spec": "King 6x6.2尺",
        "stock": 8,
        "safe_stock": 3,
        "occupied_stock": 1,
        "spaces": [
            {"name": "A-01-02", "stock": 8, "occupied_stock": 1, "wh_id": "1", "warehouse": {"id": 1, "name": "總倉", "type": "實體倉"}},
        ],
    },
    "MAT-ICE-Q": {
        "sku": "MAT-ICE-Q",
        "item_no": "MAT-ICE-Q",
        "name": "冰島床墊 標準雙人",
        "spec": "Queen 5x6.2尺",
        "stock": 0,
        "safe_stock": 5,
        "occupied_stock": 0,
        "spaces": [],
    },
    "PIL-MOON-3": {
        "sku": "PIL-MOON-3",
        "item_no": "PIL-MOON-3",
        "name": "月眠枕 3.0",
        "spec": "標準款",
        "stock": 156,
        "safe_stock": 20,
        "occupied_stock": 8,
        "spaces": [
            {"name": "B-02-01", "stock": 156, "occupied_stock": 8, "wh_id": "1", "warehouse": {"id": 1, "name": "總倉", "type": "實體倉"}},
        ],
    },
}


MOCK_STORES = [
    {"store_id": "ST001", "name": "大島樂眠 信義體驗店", "address": "台北市信義區松壽路 9 號 2F", "phone": "02-2722-xxxx", "open": "11:00-21:00"},
    {"store_id": "ST002", "name": "大島樂眠 板橋體驗店", "address": "新北市板橋區縣民大道二段 7 號", "phone": "02-2956-xxxx", "open": "11:00-21:00"},
    {"store_id": "ST003", "name": "大島樂眠 台中七期店", "address": "台中市西屯區市政路 386 號", "phone": "04-2255-xxxx", "open": "11:00-21:00"},
]


MOCK_LOGISTICS_CODES = [
    {"code": "post", "name": "中華郵政"},
    {"code": "kerry", "name": "嘉里大榮"},
    {"code": "cat", "name": "黑貓宅急便"},
    {"code": "shopee", "name": "蝦皮店到店"},
    {"code": "none", "name": "不需列印（門市自取/自行出貨）"},
]
