"""
Smart Ease Subscription Client - Python SDK
Used by Smart Ease Photobook Designer and Ease Select to communicate with
the local Smart Ease Subscription & Revenue Manager (port 8100).

Includes automatic offline fallback so client applications never stall
if the Subscription Manager is closed or unreachable.
"""

import os
import urllib.request
import urllib.error
import json
from typing import Dict, Any, Optional

CLOUD_MANAGER_URL = "https://smart-ease-photobook-designer.onrender.com"
LOCAL_MANAGER_URL = "http://127.0.0.1:8100"
DEFAULT_MANAGER_URL = os.environ.get("SMART_EASE_MANAGER_URL") or CLOUD_MANAGER_URL

class SmartEaseSubscriptionClient:
    def __init__(self, base_url: str = DEFAULT_MANAGER_URL, product_name: str = "photobook"):
        self.base_url = base_url.rstrip("/")
        self.product_name = product_name

    def is_manager_online(self, timeout: float = 2.5) -> bool:
        # Check configured URL (Cloud by default)
        try:
            req = urllib.request.Request(f"{self.base_url}/health", headers={"User-Agent": "SmartEaseClient"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass

        # Fallback to local if primary was cloud and failed
        if self.base_url != LOCAL_MANAGER_URL:
            try:
                req = urllib.request.Request(f"{LOCAL_MANAGER_URL}/health", headers={"User-Agent": "SmartEaseClient"})
                with urllib.request.urlopen(req, timeout=0.8) as resp:
                    if resp.status == 200:
                        self.base_url = LOCAL_MANAGER_URL
                        return True
            except Exception:
                pass
        return False

    def verify_license(self, account_id: str, email: Optional[str] = None, timeout: float = 3.5) -> Dict[str, Any]:
        """
        Queries the Subscription Manager to verify whether this account
        has an active, valid entitlement for this product.
        """
        payload = {
            "account_id": account_id,
            "email": email,
            "product": self.product_name
        }
        
        # Try primary URL
        urls_to_try = [self.base_url]
        if self.base_url != LOCAL_MANAGER_URL:
            urls_to_try.append(LOCAL_MANAGER_URL)

        last_error = None
        for target_url in urls_to_try:
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{target_url}/api/v1/client/verify-license",
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.base_url = target_url
                        return data
            except Exception as e:
                last_error = e

        return {
            "has_access": False,
            "offline": True,
            "status": "Offline / Unreachable",
            "message": f"Could not reach Subscription Manager: {str(last_error)}"
        }

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
