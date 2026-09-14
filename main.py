import json
import os
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OZON_API_BASE = "https://api-seller.ozon.ru"
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Ozon ChatGPT API",
    description="ChatGPT Ozon Seller API integration service",
    version="1.0.0",
    servers=[
        {
            "url": "https://ozon-chatgpt-x0xb.onrender.com"
        }
    ],
)


def require_action_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    expected_token = os.getenv("CHATGPT_ACTIONS_TOKEN")

    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="ChatGPT Actions token is not configured on the server.",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(credentials.credentials, expected_token):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def ozon_post(path: str, payload: dict[str, Any]) -> Any:
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key = os.getenv("OZON_API_KEY")

    if not client_id or not api_key:
        raise HTTPException(
            status_code=500,
            detail="Ozon credentials are not configured on the server.",
        )

    request = Request(
        url=f"{OZON_API_BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    except HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ozon API returned HTTP {exc.code}.",
        ) from exc

    except (URLError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not connect to Ozon API.",
        ) from exc

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail="Ozon returned an invalid JSON response.",
        ) from exc


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Ozon ChatGPT API is running",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get(
    "/ozon/config-status",
    tags=["Ozon"],
    dependencies=[Depends(require_action_token)],
)
def config_status():
    return {
        "client_id_configured": bool(os.getenv("OZON_CLIENT_ID")),
        "api_key_configured": bool(os.getenv("OZON_API_KEY")),
    }


@app.get(
    "/ozon/seller-info",
    tags=["Ozon"],
    dependencies=[Depends(require_action_token)],
)
def seller_info():
    return ozon_post("/v1/seller/info", {})


@app.get(
    "/ozon/products",
    tags=["Ozon"],
    dependencies=[Depends(require_action_token)],
)
def products(
    limit: int = Query(default=20, ge=1, le=100),
    last_id: str = Query(default=""),
):
    payload = {
        "filter": {
            "visibility": "ALL",
        },
        "last_id": last_id,
        "limit": limit,
    }

    product_result = ozon_post("/v3/product/list", payload)

    result_data = product_result.get("result", product_result)
    items = result_data.get("items", [])

    if not items:
        return product_result

    product_ids = [
        str(item["product_id"])
        for item in items
        if item.get("product_id")
    ]

    # 获取商品详细信息
    info_result = ozon_post(
        "/v3/product/info/list",
        {
            "product_id": product_ids,
        },
    )

    info_data = info_result.get("result", info_result)
    info_items = info_data.get("items", [])

    info_map = {
        str(item.get("id", item.get("product_id"))): item
        for item in info_items
    }

    # 获取库存信息
    try:
        stock_result = ozon_post(
            "/v3/product/info/stocks",
            {
                "filter": {
                    "product_id": product_ids,
                    "visibility": "ALL",
                },
                "cursor": "",
                "limit": len(product_ids),
            },
        )

        stock_data = stock_result.get("result", stock_result)
        stock_items = stock_data.get("items", [])
    except HTTPException:
        stock_items = []

    stock_map = {
        str(item.get("product_id")): item
        for item in stock_items
    }

    # 合并商品详情和库存
    for item in items:
        product_id = str(item.get("product_id"))

        detail = info_map.get(product_id, {})
        stock = stock_map.get(product_id, {})

        item["name"] = detail.get("name")
        item["barcode"] = detail.get("barcode")
        item["category_id"] = detail.get("category_id")
        item["primary_image"] = detail.get("primary_image")

        stocks = stock.get("stocks", [])

fbo_present = 0
fbo_reserved = 0
fbs_present = 0
fbs_reserved = 0
warehouse_stocks = []

for stock_item in stocks:
    stock_type = str(stock_item.get("type", "")).lower()

    present = int(stock_item.get("present", 0) or 0)
    reserved = int(stock_item.get("reserved", 0) or 0)

    warehouse_stocks.append(
        {
            "warehouse_id": stock_item.get("warehouse_id"),
            "warehouse_name": stock_item.get("warehouse_name"),
            "type": stock_item.get("type"),
            "present": present,
            "reserved": reserved,
        }
    )

    if stock_type == "fbo":
        fbo_present += present
        fbo_reserved += reserved

    elif stock_type in ("fbs", "rfbs"):
        fbs_present += present
        fbs_reserved += reserved

item["fbo_stock_present"] = fbo_present
item["fbo_stock_reserved"] = fbo_reserved
item["fbs_stock_present"] = fbs_present
item["fbs_stock_reserved"] = fbs_reserved

item["stock_present"] = fbo_present + fbs_present
item["stock_reserved"] = fbo_reserved + fbs_reserved
item["warehouse_stocks"] = warehouse_stocks
    result_data["items"] = items
    product_result["result"] = result_data

    return product_result
