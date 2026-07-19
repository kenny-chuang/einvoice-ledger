from fastapi import APIRouter


def build_router(*, list_handler, allocate_handler, reset_handler) -> APIRouter:
    router = APIRouter(prefix="/api/discounts", tags=["discounts"])
    router.add_api_route("", list_handler, methods=["GET"])
    router.add_api_route("/{discount_line_id}/allocate", allocate_handler, methods=["POST"])
    router.add_api_route("/{discount_line_id}/reset", reset_handler, methods=["POST"])
    return router
