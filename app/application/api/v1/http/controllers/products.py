"""Product HTTP controllers."""
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status

from app.application.api.v1.http.schemas.products import CreateProductRequest, ProductResponse
from app.domain.errors.product import ProductNotFoundError
from app.infrastructure.ports.uow import UnitOfWorkPort
from app.services.use_cases.products.add.handler import AddProductHandler
from app.services.use_cases.products.add.request import AddProductRequest
from app.services.use_cases.products.get.handler import GetProductHandler
from app.services.use_cases.products.get.request import GetProductRequest

router = APIRouter()


# ============================================================================
# Endpoints
# ============================================================================

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ProductResponse,
)
@inject
async def create_product(
    payload: CreateProductRequest,
    uow: FromDishka[UnitOfWorkPort],
) -> ProductResponse:
    """
    Create a new product.

    Args:
        payload: Product data
        uow: Unit of Work (injected by Dishka)

    Returns:
        Created product with generated GUID and timestamps
    """
    # Map API request to use case request
    use_case_request = AddProductRequest(
        name=payload.name,
        slug=payload.slug,
        price_cents=payload.price_cents,
        description=payload.description,
    )

    # Execute use case
    handler = AddProductHandler(uow)
    use_case_response = await handler.handle(use_case_request)

    # Map use case response to API response
    return ProductResponse(
        guid=use_case_response.guid,
        name=use_case_response.name,
        slug=use_case_response.slug,
        price_cents=use_case_response.price_cents,
        description=use_case_response.description,
        created_at=use_case_response.created_at.isoformat(),
        updated_at=use_case_response.updated_at.isoformat(),
    )


@router.get(
    "/{product_guid}",
    status_code=status.HTTP_200_OK,
    response_model=ProductResponse,
)
@inject
async def get_product(
    product_guid: uuid.UUID,
    uow: FromDishka[UnitOfWorkPort],
) -> ProductResponse:
    """
    Get product by GUID.

    Args:
        product_guid: Product unique identifier
        uow: Unit of Work (injected by Dishka)

    Returns:
        Product data

    Raises:
        HTTPException: 404 if product not found
    """
    # Create use case request
    use_case_request = GetProductRequest(guid=product_guid)

    # Execute use case
    handler = GetProductHandler(uow)

    try:
        use_case_response = await handler.handle(use_case_request)
    except ProductNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product not found: {product_guid}",
        ) from e

    # Map use case response to API response
    return ProductResponse(
        guid=use_case_response.guid,
        name=use_case_response.name,
        slug=use_case_response.slug,
        price_cents=use_case_response.price_cents,
        description=use_case_response.description,
        created_at=use_case_response.created_at.isoformat(),
        updated_at=use_case_response.updated_at.isoformat(),
    )
