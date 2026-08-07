from enum import StrEnum


class Retailer(StrEnum):
    TARGET = "target"
    WALMART = "walmart"


class Fulfillment(StrEnum):
    SHIPPING = "shipping"
    PICKUP = "pickup"
    ANY = "any"
