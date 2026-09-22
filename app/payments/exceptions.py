class PaymentError(Exception):
    """Base exception for payment orchestration failures."""


class PaymentConfigurationError(PaymentError):
    """Finix credentials or merchant configuration are missing."""


class PaymentProviderError(PaymentError):
    """Finix rejected a request or could not be reached."""

    def __init__(self, message, *, status_code=502, details=None, uncertain=False):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}
        self.uncertain = uncertain


class PaymentAccessError(PaymentError):
    """A checkout secret is missing or invalid."""


class PaymentStateError(PaymentError):
    """A payment cannot perform the requested transition."""


class CheckoutValidationError(PaymentError):
    """Checkout input cannot be priced or fulfilled."""
