"""
Smart Ease Subscription Client - Python SDK
Used by Smart Ease Photobook Designer and Ease Select to communicate with
the local Smart Ease Subscription & Revenue Manager (port 8100).

Includes automatic offline fallback so client applications never stall
if the Subscription Manager is closed or unreachable.
"""

import urllib.request
import urllib.error
import json
from typing import Dict, Any, Optional

DEFAULT_MANAGER_URL = "http://127.0.0.1:8100"

class SmartEaseSubscriptionClient:
    def __init__(self, base_url: str = DEFAULT_MANAGER_URL, product_name: str = "photobook"):
        self.base_url = base_url.rstrip("/")
        self.product_name = product_name

    def is_manager_online(self, timeout: float = 0.5) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/health", headers={"User-Agent": "SmartEaseClient"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def verify_license(self, account_id: str, email: Optional[str] = None, timeout: float = 1.0) -> Dict[str, Any]:
        """
        Queries the Subscription Manager to verify whether this account
        has an active, valid entitlement for this product.
        """
        payload = {
            "account_id": account_id,
            "email": email,
            "product": self.product_name
        }
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/v1/client/verify-license",
                data=req_data,
                headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            # Graceful offline fallback
            return {
                "has_access": False,
                "offline": True,
                "status": "Offline / Unreachable",
                "message": f"Could not reach Subscription Manager: {str(e)}"
            }

        return {"has_access": False, "status": "Unknown"}

    def register_user(self, full_name: str, account_id: str, email: str = "", notes: str = "", timeout: float = 1.0) -> Dict[str, Any]:
        """
        Registers a new or existing user into the Subscription Manager.
        """
        payload = {
            "full_name": full_name,
            "smart_ease_account_id": account_id,
            "email": email,
            "product_owned": "Smart Ease Photobook Designer" if self.product_name == "photobook" else "Ease Select",
            "source_app": self.product_name.capitalize(),
            "notes": notes
        }
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/v1/users",
                data=req_data,
                headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def record_subscription_purchase(self, user_id: str, package_id: str, amount: float, provider: str, reference: str, timeout: float = 1.5) -> Dict[str, Any]:
        """
        Transmits verified payment and creates/activates customer subscription.
        """
        payload = {
            "user_id": user_id,
            "package_id": package_id,
            "amount": amount,
            "payment_provider": provider,
            "transaction_reference": reference
        }
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/v1/subscriptions",
                data=req_data,
                headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"status": "error", "message": str(e)}
