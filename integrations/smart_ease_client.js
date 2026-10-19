/**
 * Smart Ease Subscription Client - JavaScript SDK
 * Can be included in Photobook Designer frontend or Ease Select frontend.
 */

class SmartEaseSubscriptionClient {
  constructor(baseUrl = 'https://smart-ease-photobook-designer.onrender.com', productName = 'photobook') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.productName = productName;
  }

  async checkStatus() {
    try {
      const res = await fetch(`${this.baseUrl}/health`);
      return res.ok;
    } catch (e) {
      return false;
    }
  }

  async verifyLicense(accountId) {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/client/verify-license`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId, product: this.productName })
      });
      return await res.json();
    } catch (e) {
      return { has_access: false, offline: true, error: e.message };
    }
  }

  async registerUser(fullName, accountId, email = '') {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: fullName,
          smart_ease_account_id: accountId,
          email: email,
          product_owned: this.productName === 'photobook' ? 'Smart Ease Photobook Designer' : 'Ease Select',
          source_app: this.productName.toUpperCase()
        })
      });
      return await res.json();
    } catch (e) {
      return { status: 'error', error: e.message };
    }
  }
}

// Export for module or global use
if (typeof module !== 'undefined' && module.exports) {
  module.exports = SmartEaseSubscriptionClient;
} else if (typeof window !== 'undefined') {
  window.SmartEaseSubscriptionClient = SmartEaseSubscriptionClient;
}
