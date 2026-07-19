from fastapi import APIRouter


def build_router(*, status_handler, dashboard_handler) -> APIRouter:
    router = APIRouter(tags=["dashboard"])
    router.add_api_route("/api/status", status_handler, methods=["GET"])
    router.add_api_route("/api/dashboard", dashboard_handler, methods=["GET"])
    return router
