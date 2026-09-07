import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query

OZON_API_BASE = "https://api-seller.ozon.ru"

app = FastAPI(
    title="Ozon ChatGPT API",
    description="ChatGPT Ozon Seller API integration service",
    version="1.0.0",
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


@app.get("/ozon/config-status", tags=["Ozon"])
def config_status():
    return {
        "client_id_configured": bool(os.getenv("OZON_CLIENT_ID")),
        "api_key_configured": bool(os.getenv("OZON_API_KEY")),
    }


@app.get("/ozon/seller-info", tags=["Ozon"])
def seller_info():
    return ozon_post("/v1/seller/info", {})


@app.get("/ozon/products", tags=["Ozon"])
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

    return ozon_post("/v3/product/list", payload)
