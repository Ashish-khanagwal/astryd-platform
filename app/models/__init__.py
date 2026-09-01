"""MongoDB document models for the Astryd persistence layer."""

from dataclasses import asdict, dataclass, field
from typing import Any


class DocumentModel:
    def to_document(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class OperatingHours(DocumentModel):
    day: str | None = None
    open_time: str | None = None
    close_time: str | None = None
    slot_duration_mins: int | None = None
    max_per_slot: int | None = None
    is_closed: bool | None = None


@dataclass
class BlockedDate(DocumentModel):
    date: str | None = None
    reason: str | None = None


@dataclass
class EmbedConfig(DocumentModel):
    widget_token: str | None = None


@dataclass
class Business(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    name: str | None = None
    type: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    timezone: str | None = None
    description: str | None = None
    cuisine_type: str | None = None
    price_range: str | None = None
    rating: float | None = None
    operating_hours: list[OperatingHours] = field(default_factory=list)
    blocked_dates: list[BlockedDate] = field(default_factory=list)
    amenities: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    embed_config: EmbedConfig | None = None


@dataclass
class Table(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    name: str | None = None
    capacity: int | None = None
    seating_type: str | None = None
    is_active: bool | None = None
    floor_position: Any = None


@dataclass
class ReservationGuest(DocumentModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    special_requests: str | None = None
    newsletter_opt_in: bool | None = None


@dataclass
class ReservationBooking(DocumentModel):
    date: str | None = None
    time_slot: str | None = None
    time_display: str | None = None
    party_size: int | None = None
    seating_preference: str | None = None
    table_id: Any = None


@dataclass
class Reservation(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    confirmation_code: str | None = None
    guest: ReservationGuest | dict[str, Any] | None = None
    booking: ReservationBooking | dict[str, Any] | None = None
    status: str | None = None
    internal_notes: str | None = None
    created_at: Any = None
    reminder_sent: bool = False


@dataclass
class MenuSection(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    name: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


@dataclass
class MenuItem(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    section_id: str | None = None
    name: str | None = None
    description: str | None = None
    price: float | None = None
    prep_time_mins: int | None = None
    dietary_tags: list[str] = field(default_factory=list)
    image_url: str | None = None
    is_available: bool | None = None
    rating: float | None = None


@dataclass
class OrderCustomer(DocumentModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None


@dataclass
class OrderItem(DocumentModel):
    item_id: str | None = None
    name: str | None = None
    quantity: int | None = None
    price: float | None = None
    customizations: Any = None


@dataclass
class Order(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    order_number: str | None = None
    customer: OrderCustomer | dict[str, Any] | None = None
    items: list[OrderItem | dict[str, Any]] = field(default_factory=list)
    service_type: str | None = None
    subtotal: float | None = None
    tax: float | None = None
    delivery_fee: float | None = None
    total: float | None = None
    payment_status: str | None = None
    payment_intent_id: str | None = None
    status: str | None = None
    estimated_prep_time: int | None = None
    created_at: Any = None


@dataclass
class Staff(DocumentModel):
    _id: Any = None
    business_id: str | None = None
    name: str | None = None
    email: str | None = None
    password_hash: str | None = None
    role: str | None = None
    is_active: bool | None = None
