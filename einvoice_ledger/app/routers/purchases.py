from fastapi import APIRouter


def build_router(*, list_handler, detail_handler, update_handler, reset_handler, months_handler) -> APIRouter:
    router = APIRouter(tags=["purchases"])
    router.add_api_route("/api/purchases", list_handler, methods=["GET"])
    router.add_api_route("/api/purchase-months", months_handler, methods=["GET"])
    router.add_api_route("/api/purchases/{line_id}", detail_handler, methods=["GET"])
    router.add_api_route("/api/purchases/{line_id}", update_handler, methods=["POST"])
    router.add_api_route("/api/purchases/{line_id}/reset", reset_handler, methods=["POST"])
    return router
