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

    def _get_target_urls(self) -> list:
        # Prioritize Cloud Manager URL (Render server), then fallback to local
        order = [self.base_url]
        for u in [CLOUD_MANAGER_URL, LOCAL_MANAGER_URL]:
            if u not in order:
                order.append(u)
        return order

    def is_manager_online(self, timeout: float = 3.0) -> bool:
        """Checks if the Render Cloud Manager or local server is reachable."""
        for target_url in self._get_target_urls():
            try:
                req = urllib.request.Request(f"{target_url}/health", headers={"User-Agent": "SmartEaseClient"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        self.base_url = target_url
                        return True
            except Exception:
                pass
        return False

    def verify_license(self, account_id: str, email: Optional[str] = None, mode: str = "online", timeout: float = 5.0) -> Dict[str, Any]:
        """
        Queries the Render Cloud Subscription Server to verify whether this account
        has an active, valid entitlement for this product.
        Falls back to local Subscription Manager if cloud is temporarily offline.
        """
        import socket
        payload = {
            "account_id": account_id,
            "email": email,
            "product": self.product_name,
            "mode": mode,
            "device_hostname": socket.gethostname()
        }
        
        urls_to_try = self._get_target_urls()
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
            "message": f"Could not reach Subscription Server: {str(last_error)}"
        }

    def send_heartbeat(self, account_id: str, mode: str = "online", timeout: float = 3.0) -> Dict[str, Any]:
        """
        Sends an active heartbeat to the Render Cloud Subscription Server so the admin can see real-time
        who is Online vs who uses Offline Mode.
        """
        import socket
        payload = {
            "account_id": account_id,
            "product": self.product_name,
            "mode": mode,
            "device_hostname": socket.gethostname(),
            "app_version": "v2.4"
        }
        urls_to_try = self._get_target_urls()

        for target_url in urls_to_try:
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{target_url}/api/v1/client/heartbeat",
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception:
                continue
        return {"status": "offline_mode", "recorded": False}

    def register_user(self, full_name: str, account_id: str, email: str = "", notes: str = "", timeout: float = 4.0) -> Dict[str, Any]:
        """
        Registers a new or existing user into the Subscription Server (Render Cloud primary).
        """
        payload = {
            "full_name": full_name,
            "smart_ease_account_id": account_id,
            "email": email,
            "product_owned": "Smart Ease Photobook Designer" if self.product_name == "photobook" else "Ease Select",
            "source_app": self.product_name.capitalize(),
            "notes": notes
        }
        for target_url in self._get_target_urls():
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{target_url}/api/v1/users",
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                continue
        return {"status": "error", "message": "Failed to connect to Subscription Server"}

    def record_subscription_purchase(self, user_id: str, package_id: str, amount: float, provider: str, reference: str, timeout: float = 5.0) -> Dict[str, Any]:
        """
        Transmits verified payment and creates/activates customer subscription on Render Cloud.
        """
        payload = {
            "user_id": user_id,
            "package_id": package_id,
            "amount": amount,
            "payment_provider": provider,
            "transaction_reference": reference
        }
        for target_url in self._get_target_urls():
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{target_url}/api/v1/subscriptions",
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "SmartEaseClient"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                continue
        return {"status": "error", "message": "Failed to record subscription purchase"}
