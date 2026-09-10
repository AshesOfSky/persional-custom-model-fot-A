from fastapi import FastAPI
from .errors import install_error_handlers
from .routes import market, analysis, fundamentals, instruments, watchlist, system

def create_app(**services):
    app = FastAPI(title="Market Strategy Terminal", version="1.0.0")
    for key, value in services.items():
        setattr(app.state, key, value)
    install_error_handlers(app)
    for module in (market, analysis, fundamentals, instruments, watchlist, system):
        app.include_router(module.router)
    return app
