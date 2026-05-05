import os
import threading
import time
import urllib.request
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from core.db import init_db

# Initialize SocketIO globally
socketio = SocketIO(cors_allowed_origins="*", async_mode='eventlet')

def _keep_alive(url: str):
    """Pings the server's health endpoint every 10 minutes to prevent Render sleep."""
    time.sleep(60)  # Wait 1 min after startup before first ping
    while True:
        try:
            urllib.request.urlopen(f"{url}/web/api/health", timeout=10)
            print(f"[KeepAlive] Ping sent to {url}")
        except Exception as e:
            print(f"[KeepAlive] Ping failed: {e}")
        time.sleep(600)  # 10 minutes

def create_app():
    app = Flask(__name__)
    
    # Fetch Secret Key from environment
    secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not secret_key:
        # In production (Render), refuse to start without a secret key
        if os.environ.get("RENDER_EXTERNAL_URL"):
            raise EnvironmentError("CRITICAL: FLASK_SECRET_KEY env var is not set. Set it in Render Dashboard > Environment.")
        # In local dev, use a warning
        secret_key = "dev_only_insecure_key_do_not_use_in_prod"
        print("WARNING: FLASK_SECRET_KEY not set. Using local dev key only.")
    
    app.secret_key = secret_key
    
    # Configure Session Cookie for Iframe Compatibility (Hugging Face)
    app.config.update(
        SESSION_COOKIE_SAMESITE='None',
        SESSION_COOKIE_SECURE=True,
        PERMANENT_SESSION_LIFETIME=86400 # 24 hours
    )
    
    # Enable CORS with credentials support
    CORS(app, supports_credentials=True)

    # Initialize Database
    init_db()

    # Register Blueprints
    from routes.bot_api import bot_api_bp
    from routes.web_api import web_api_bp
    from routes.views import views_bp
    from routes.auth_api import auth_api_bp
    
    app.register_blueprint(views_bp)
    app.register_blueprint(bot_api_bp)
    app.register_blueprint(web_api_bp)
    app.register_blueprint(auth_api_bp, url_prefix='/api/auth')

    # Initialize SocketIO with app
    socketio.init_app(app)
    
    # Import socket handlers to register them
    import core.socket_handlers

    # Start keep-alive thread (only in production on Render)
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        t = threading.Thread(target=_keep_alive, args=(render_url,), daemon=True)
        t.start()
        print(f"[KeepAlive] Self-ping started → {render_url}")

    return app

app = create_app()

if __name__ == "__main__":
    # Hugging Face Spaces usually uses port 7860
    port = int(os.environ.get("PORT", 7860))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
