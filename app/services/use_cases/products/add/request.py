import msgspec


class AddProductRequest(msgspec.Struct, frozen=True, gc=False):
    name: str
    slug: str
    price_cents: int
    description: str | None = None
