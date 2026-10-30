"""Business rules shared by Product.clean() and ProductSerializer.validate().

Keeping them in one place guarantees the admin, the API and the tests
apply exactly the same validation.
"""

from .choices import AccessType, ProductType


def product_rule_errors(
    *,
    product_type,
    access_type,
    price,
    discount_price,
    stock,
    weight,
):
    """Return a {field: message} dict; empty dict means the data is valid."""
    errors = {}
    is_physical = product_type == ProductType.PHYSICAL

    # A physical item can't be delivered by subscription
    if is_physical and access_type == AccessType.SUBSCRIPTION:
        errors["access_type"] = (
            "A physical product can't be sold by subscription."
        )

    # Price depends on how the product is obtained
    if access_type == AccessType.PURCHASE and price is None:
        errors["price"] = "Price is required for products sold by purchase."
    elif access_type == AccessType.SUBSCRIPTION and price is not None:
        errors["price"] = (
            "Subscription products have no price; it belongs to the plan."
        )
    elif access_type == AccessType.FREE and price not in (None, 0):
        errors["price"] = "Free products must not have a price."

    # Discount only makes sense with a price, and must be lower
    if discount_price is not None:
        if price is None:
            errors["discount_price"] = "A discount requires a regular price."
        elif discount_price >= price:
            errors["discount_price"] = (
                "The discount price must be less than the regular price."
            )

    # Stock and weight only apply to physical items
    if not is_physical:
        if stock:
            errors["stock"] = "Stock only applies to physical products."
        if weight is not None:
            errors["weight"] = "Weight only applies to physical products."

    return errors
