"""Small Finix API adapter. Raw card data never enters this module."""

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.payments.exceptions import PaymentConfigurationError, PaymentProviderError


class FinixClient:
    def __init__(self, config, opener=None):
        self.base_url = config.get("FINIX_API_URL", "").rstrip("/")
        self.username = config.get("FINIX_API_USERNAME", "")
        self.password = config.get("FINIX_API_PASSWORD", "")
        self.merchant_id = config.get("FINIX_MERCHANT_ID", "")
        self.opener = opener or urlopen
        if not all((self.base_url, self.username, self.password, self.merchant_id)):
            raise PaymentConfigurationError("Finix sandbox credentials are not configured")

    def create_identity(self, tags):
        return self._post("/identities", {"entity": {}, "tags": tags})

    def create_payment_instrument(self, identity_id, token):
        return self._post(
            "/payment_instruments",
            {"identity": identity_id, "token": token, "type": "TOKEN"},
        )

    def create_transfer(self, amount_cents, source_id, idempotency_id, tags, fraud_session_id=None):
        payload = {
            "amount": amount_cents,
            "currency": "USD",
            "merchant": self.merchant_id,
            "source": source_id,
            "idempotency_id": idempotency_id,
            "tags": tags,
        }
        if fraud_session_id:
            payload["fraud_session_id"] = fraud_session_id
        return self._post("/transfers", payload, uncertain_on_network=True)

    def get_transfer(self, transfer_id):
        return self._request("GET", f"/transfers/{transfer_id}")

    def _post(self, path, payload, uncertain_on_network=False):
        return self._request("POST", path, payload, uncertain_on_network)

    def _request(self, method, path, payload=None, uncertain_on_network=False):
        try:
            credentials = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            request = Request(
                f"{self.base_url}{path}",
                data=data,
                method=method,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Finix-Version": "2022-02-01",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with self.opener(request, timeout=20) as response:
                raw_body = response.read()
                return json.loads(raw_body) if raw_body else {}
        except HTTPError as exc:
            raw_body = exc.read()
            try:
                body = json.loads(raw_body) if raw_body else {}
            except (TypeError, ValueError):
                body = {}
            message = (
                body.get("message") or body.get("error")
                if isinstance(body, dict)
                else None
            ) or "Finix rejected the request"
            raise PaymentProviderError(str(message), status_code=502, details=body) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PaymentProviderError(
                "Finix is temporarily unreachable",
                uncertain=uncertain_on_network,
            ) from exc
