import uvicorn
from app import create_app
from dotenv import load_dotenv
import os
import logging

# Load environment variables from .env file
load_dotenv()

# Configure logging to show INFO level and above
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Set specific loggers to INFO level
logging.getLogger("applicationca").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = create_app()

if __name__ == "__main__":
    # ws_ping_* are an explicit second layer of WebSocket keepalive; the
    # app-level heartbeat in the Electron bridge is the primary mechanism.
    uvicorn.run(
        "run:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
