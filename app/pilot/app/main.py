from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.redis import close_redis
from app.utils.logging import setup_logging, get_logger
from app.utils.exceptions import PantryPilotError, NotAuthenticatedError, TokenExpiredError
from app.api import auth, onboard, webhooks, settings_router, basket, orders

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("pantrypilot_started", env=settings.app_env)
    yield
    # Shutdown
    await close_redis()
    await engine.dispose()
    logger.info("pantrypilot_stopped")


app = FastAPI(
    title="PantryPilot API",
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.jwt_secret,
    max_age=86400,          # 24 hours
    https_only=not settings.debug,
    same_site="lax",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://pantrypilot.in",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────
app.include_router(auth.router,            prefix="/v1")
app.include_router(onboard.router,         prefix="/v1")
app.include_router(webhooks.router,        prefix="/v1")
app.include_router(settings_router.router, prefix="/v1")
app.include_router(basket.router,          prefix="/v1")
app.include_router(orders.router,          prefix="/v1")

# ── Domain error handler ──────────────────────
@app.exception_handler(PantryPilotError)
async def domain_exception_handler(request: Request, exc: PantryPilotError):
    status = 401 if isinstance(exc, (TokenExpiredError, NotAuthenticatedError)) else 400
    logger.warning("domain_error", path=request.url.path, code=exc.code, message=exc.message)
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            }
        }
    )

# ── Global error handler ──────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
                "retryable": True
            }
        }
    )


# ── Health check ──────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
