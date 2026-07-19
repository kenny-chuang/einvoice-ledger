from fastapi import APIRouter


def build_router(*, list_handler, usage_handler, delete_handler) -> APIRouter:
    router = APIRouter(prefix="/api/categories", tags=["categories"])
    router.add_api_route("", list_handler, methods=["GET"])
    router.add_api_route("/usage", usage_handler, methods=["GET"])
    router.add_api_route("/delete", delete_handler, methods=["POST"])
    return router
