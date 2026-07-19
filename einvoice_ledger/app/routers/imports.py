from fastapi import APIRouter


def build_router(*, csv_handler) -> APIRouter:
    router = APIRouter(prefix="/api/imports", tags=["imports"])
    router.add_api_route("/csv", csv_handler, methods=["POST"])
    return router
