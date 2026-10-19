"""
Smart Ease Subscription Manager - Main Server
FastAPI + Uvicorn server running on port 8100
Serves the desktop administrative interface and client sync API.
"""

import os
import sys
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

# Ensure current dir is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database import init_db
from api_routes import router as api_router

# Initialize database
init_db()

app = FastAPI(
    title="Smart Ease Subscription & Revenue Manager",
    description="Administrative system for Smart Ease Photobook Designer & Ease Select",
    version="1.0.0"
)

# Enable CORS for local client apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API
app.include_router(api_router)

# Mount static frontend
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "Smart Ease Subscription Manager", "version": "1.0.0"}

def start():
    port = 8100
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    print(f"============================================================")
    print(f"  Smart Ease Subscription & Revenue Manager Server Active    ")
    print(f"  URL: http://127.0.0.1:{port}/                             ")
    print(f"============================================================")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    start()
