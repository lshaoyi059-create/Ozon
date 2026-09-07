from fastapi import FastAPI

app = FastAPI(
    title="Ozon ChatGPT API",
    description="ChatGPT Ozon Seller API integration service",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Ozon ChatGPT API is running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
