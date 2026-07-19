from fastapi import APIRouter


def build_router(
    *, list_handler, comparisons_handler, prices_handler, aliases_handler,
    alias_name_handler, category_handler, rules_list_handler, rules_create_handler,
    rules_delete_handler,
) -> APIRouter:
    router = APIRouter(tags=["products"])
    router.add_api_route("/api/products", list_handler, methods=["GET"])
    router.add_api_route("/api/product-comparisons", comparisons_handler, methods=["GET"])
    router.add_api_route("/api/products/{product_id}/prices", prices_handler, methods=["GET"])
    router.add_api_route("/api/products/{product_id}/aliases", aliases_handler, methods=["POST"])
    router.add_api_route("/api/products/{product_id}/alias-name", alias_name_handler, methods=["POST"])
    router.add_api_route("/api/products/{product_id}/category", category_handler, methods=["POST"])
    router.add_api_route("/api/rules", rules_list_handler, methods=["GET"])
    router.add_api_route("/api/rules", rules_create_handler, methods=["POST"])
    router.add_api_route("/api/rules/{rule_id}", rules_delete_handler, methods=["DELETE"])
    return router
