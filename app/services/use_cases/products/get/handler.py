from app.domain.common.handlers import RequestHandler
from app.domain.errors.product import ProductNotFoundError
from app.infrastructure.ports.uow import UnitOfWorkPort

from .request import GetProductRequest
from .response import GetProductResponse


class GetProductHandler(
    RequestHandler[GetProductRequest, GetProductResponse]
):
    def __init__(self, uow: UnitOfWorkPort) -> None:
        self._uow = uow

    async def handle(self, request: GetProductRequest) -> GetProductResponse:
        async with self._uow as uow:
            product_repo = uow.products

            product = await product_repo.get_by_guid(request.guid)

            if product is None:
                raise ProductNotFoundError(
                    guid=request.guid,
                )

        return GetProductResponse(
            guid=product.guid,
            name=product.name,
            slug=product.slug,
            price_cents=product.price_cents,
            description=product.description,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )
