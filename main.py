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
    error_body = exc.read().decode("utf-8", errors="replace")
    raise HTTPException(
        status_code=502,
        detail={
            "ozon_path": path,
            "ozon_http_status": exc.code,
            "ozon_error": error_body[:500],
        },
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
        "filter": {"visibility": "ALL"},
        "last_id": last_id,
        "limit": limit,
    }

    product_result = ozon_post("/v3/product/list", payload)
    result_data = product_result.get("result", product_result)
    items = result_data.get("items", [])

    if not items:
        return product_result

    product_ids = [
        int(item["product_id"])
        for item in items
        if item.get("product_id")
    ]

    skus = [
        int(item["sku"])
        for item in items
        if item.get("sku")
    ]

    info_result = ozon_post(
        "/v3/product/info/list",
        {"product_id": product_ids},
    )
    info_data = info_result.get("result", info_result)
    info_items = info_data.get("items", [])

    info_map = {
        str(item.get("id", item.get("product_id"))): item
        for item in info_items
    }

    # 读取 FBO/FBS 分类库存；此接口若没有返回仓库明细，不把缺失值冒充为 0。
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

    stock_map = {
        str(item.get("product_id")): item
        for item in stock_items
    }

    # 单独读取 FBS 卖家仓库库存。
    fbs_result = ozon_post(
        "/v2/product/info/stocks-by-warehouse/fbs",
        {"sku": skus},
    )
    fbs_data = fbs_result.get("result", fbs_result)

    if isinstance(fbs_data, list):
        fbs_items = fbs_data
    else:
        fbs_items = fbs_data.get("items", [])

    fbs_map = {}
    for fbs_item in fbs_items:
        sku_key = str(fbs_item.get("sku", ""))
        if sku_key:
            fbs_map.setdefault(sku_key, []).append(fbs_item)

    for item in items:
        product_id = str(item.get("product_id", ""))
        sku_key = str(item.get("sku", ""))

        detail = info_map.get(product_id, {})
        stock = stock_map.get(product_id, {})
        v3_stocks = stock.get("stocks", [])

        fbo_present = 0
        fbo_reserved = 0
        fbo_found = False
        warehouse_stocks = []

        for stock_item in v3_stocks:
            stock_type = str(stock_item.get("type", "")).lower()
            present_value = stock_item.get(
                "present",
                stock_item.get("stock"),
            )
            reserved_value = stock_item.get("reserved")

            if stock_type == "fbo":
                fbo_found = True
                fbo_present += int(present_value or 0)
                fbo_reserved += int(reserved_value or 0)

        fbs_present = 0
        fbs_reserved = 0
        fbs_found = False

        for fbs_item in fbs_map.get(sku_key, []):
            # 有些响应直接给仓库字段，有些把仓库记录放在 stocks 数组中。
            nested_stocks = fbs_item.get("stocks")
            rows = nested_stocks if isinstance(nested_stocks, list) else [fbs_item]

            for row in rows:
                present_value = row.get(
                    "present",
                    row.get("stock"),
                )
                reserved_value = row.get("reserved")

                if present_value is None and reserved_value is None:
                    continue

                fbs_found = True
                present = int(present_value or 0)
                reserved = int(reserved_value or 0)

                fbs_present += present
                fbs_reserved += reserved

                warehouse_stocks.append(
                    {
                        "warehouse_id": row.get("warehouse_id"),
                        "warehouse_name": row.get("warehouse_name"),
                        "type": "FBS",
                        "present": present,
                        "reserved": reserved,
                    }
                )

        item["name"] = detail.get("name")
        item["barcode"] = detail.get("barcode")
        item["category_id"] = detail.get("category_id")
        item["primary_image"] = detail.get("primary_image")

        item["fbo_stock_present"] = fbo_present if fbo_found else None
        item["fbo_stock_reserved"] = fbo_reserved if fbo_found else None
        item["fbs_stock_present"] = fbs_present if fbs_found else None
        item["fbs_stock_reserved"] = fbs_reserved if fbs_found else None
        item["warehouse_stocks"] = warehouse_stocks

        # 只有 FBO、FBS 两边都实际返回数据，才给出总数。
        if fbo_found and fbs_found:
            item["stock_present"] = fbo_present + fbs_present
            item["stock_reserved"] = fbo_reserved + fbs_reserved
        else:
            item["stock_present"] = None
            item["stock_reserved"] = None

    result_data["items"] = items
    product_result["result"] = result_data
    return product_result
    result_data["items"] = items
    product_result["result"] = result_data

    return product_result
