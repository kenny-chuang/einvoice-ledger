from fastapi import APIRouter


def build_router(*, preview_handler, interact_handler, login_handler) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    router.add_api_route("/login-preview", preview_handler, methods=["GET"])
    router.add_api_route("/login-interact", interact_handler, methods=["POST"])
    router.add_api_route("/login", login_handler, methods=["POST"])
    return router
