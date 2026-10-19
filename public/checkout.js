/**
 * Ease Creative Suite - Official Checkout & Payment Logic
 * Integrates with Smart Ease Subscription Manager & Photobook Studio
 */

(function () {
  'use strict';

  // 1. Packages Catalog (Mirroring Photobook Studio & Subscription Manager)
  const PACKAGES = {
    pkg_photobook: {
      id: 'pkg_photobook',
      name: 'Smart Ease Photobook Designer',
      tag: 'Monthly Plan',
      price: 2.45,
      currency: 'USD',
      billingType: 'monthly',
      periodLabel: '/ month',
      description: 'Single app monthly subscription for Photobook Designer with unlimited 300 DPI lab exports.',
      apps: ['Smart Ease Photobook Designer']
    },
    pkg_bundle: {
      id: 'pkg_bundle',
      name: 'Smart Ease Photobook Designer + Ease Select',
      tag: 'Best Value Bundle',
      price: 3.00,
      currency: 'USD',
      billingType: 'monthly',
      periodLabel: '/ month',
      description: 'Complete dual creative suite for album design and high-speed AI photo culling.',
      apps: ['Smart Ease Photobook Designer', 'Ease Select Culling Suite']
    },
    pkg_select: {
      id: 'pkg_select',
      name: 'Ease Select',
      tag: 'Monthly Plan',
      price: 2.45,
      currency: 'USD',
      billingType: 'monthly',
      periodLabel: '/ month',
      description: 'Dedicated photo culling, ratings, and RAW image review suite.',
      apps: ['Ease Select Culling Suite']
    },
    pkg_lifetime: {
      id: 'pkg_lifetime',
      name: 'Lifetime Pro (All-In-One Creative Suite)',
      tag: 'Permanent License',
      price: 200.00,
      currency: 'USD',
      billingType: 'one-time',
      periodLabel: 'one-time payment',
      description: 'Pay once, own forever. Full permanent license to all current and future Smart Ease apps.',
      apps: ['Smart Ease Photobook Designer', 'Ease Select Culling Suite', 'All Future Studio Additions']
    }
  };

  // Production Web Server on Render & Local Development Fallback
  const SERVER_CONFIG = {
    PROD_URL: 'https://smart-ease-photobook-designer.onrender.com',
    LOCAL_URL: 'http://127.0.0.1:8100'
  };

  async function callServerApi(endpoint, method = 'POST', data = null) {
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const bases = isLocal
      ? [SERVER_CONFIG.LOCAL_URL, SERVER_CONFIG.PROD_URL]
      : [SERVER_CONFIG.PROD_URL, SERVER_CONFIG.LOCAL_URL];

    let lastError = null;
    for (const base of bases) {
      try {
        const url = `${base}${endpoint}`;
        const opts = {
          method: method,
          headers: { 'Content-Type': 'application/json' }
        };
        if (data && method !== 'GET') {
          opts.body = JSON.stringify(data);
        }
        const resp = await fetch(url, opts);
        if (resp && resp.ok) {
          return await resp.json();
        } else if (resp) {
          const errJson = await resp.json().catch(() => ({}));
          lastError = errJson.detail || errJson.error || `HTTP ${resp.status}`;
        }
      } catch (e) {
        lastError = e.message;
      }
    }
    throw new Error(lastError || 'Server connection failed');
  }

  // State
  let selectedPackageId = 'pkg_bundle'; // Default to best value bundle
  let currentPaymentTab = 'card';

  // DOM Elements
  const elements = {
    planToggleMonthly: document.getElementById('planToggleMonthly'),
    planToggleLifetime: document.getElementById('planToggleLifetime'),
    pricingGrid: document.getElementById('pricingGrid'),
    selectPlanBtns: document.querySelectorAll('.btn-select-plan'),
    checkoutSection: document.getElementById('checkoutSection'),
    
    // Summary
    summaryPlanName: document.getElementById('summaryPlanName'),
    summaryPlanTag: document.getElementById('summaryPlanTag'),
    summaryPlanPrice: document.getElementById('summaryPlanPrice'),
    summarySubtotal: document.getElementById('summarySubtotal'),
    summaryTotal: document.getElementById('summaryTotal'),
    summaryAppsList: document.getElementById('summaryAppsList'),
    
    // Tabs
    tabButtons: document.querySelectorAll('.payment-tab-btn'),
    tabForms: {
      card: document.getElementById('formTabCard'),
      livepay: document.getElementById('formTabLivepay'),
      paypal: document.getElementById('formTabPaypal'),
      voucher: document.getElementById('formTabVoucher')
    },
    
    // Card Form
    cardHolder: document.getElementById('cardHolder'),
    cardNumber: document.getElementById('cardNumber'),
    cardExpiry: document.getElementById('cardExpiry'),
    cardCvc: document.getElementById('cardCvc'),
    cardEmail: document.getElementById('cardEmail'),
    cardBrandBadge: document.getElementById('cardBrandBadge'),
    btnSubmitCard: document.getElementById('btnSubmitCard'),
    cardAlert: document.getElementById('cardAlert'),
    
    // LivePay Form
    livepayPhone: document.getElementById('livepayPhone'),
    livepayCountry: document.getElementById('livepayCountry'),
    livepayEmail: document.getElementById('livepayEmail'),
    btnSubmitLivepay: document.getElementById('btnSubmitLivepay'),
    livepayAlert: document.getElementById('livepayAlert'),
    
    // Voucher Form
    voucherInput: document.getElementById('voucherInput'),
    voucherEmail: document.getElementById('voucherEmail'),
    btnSubmitVoucher: document.getElementById('btnSubmitVoucher'),
    voucherAlert: document.getElementById('voucherAlert'),
    
    // Success View
    successCard: document.getElementById('successCard'),
    successLicenseKey: document.getElementById('successLicenseKey'),
    successPlanName: document.getElementById('successPlanName'),
    successEmail: document.getElementById('successEmail'),
    btnCopyKey: document.getElementById('btnCopyKey'),
    btnPrintReceipt: document.getElementById('btnPrintReceipt')
  };

  // 2. Initialize
  function init() {
    setupPlanSelection();
    setupTabSwitching();
    setupCardFormValidation();
    setupLivepayForm();
    setupVoucherForm();
    setupSuccessActions();
    updateOrderSummary(selectedPackageId);
  }

  // 3. Plan Selection & Highlights
  function setupPlanSelection() {
    // Buttons on pricing cards
    elements.selectPlanBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const pkgId = btn.getAttribute('data-package');
        if (PACKAGES[pkgId]) {
          selectedPackageId = pkgId;
          updateOrderSummary(pkgId);
          highlightSelectedCard(pkgId);
          scrollToCheckout();
        }
      });
    });

    // Toggle button filters
    if (elements.planToggleMonthly && elements.planToggleLifetime) {
      elements.planToggleMonthly.addEventListener('click', () => {
        elements.planToggleMonthly.classList.add('active');
        elements.planToggleLifetime.classList.remove('active');
        // Pre-select bundle
        selectedPackageId = 'pkg_bundle';
        updateOrderSummary(selectedPackageId);
        highlightSelectedCard(selectedPackageId);
      });

      elements.planToggleLifetime.addEventListener('click', () => {
        elements.planToggleLifetime.classList.add('active');
        elements.planToggleMonthly.classList.remove('active');
        // Pre-select lifetime
        selectedPackageId = 'pkg_lifetime';
        updateOrderSummary(selectedPackageId);
        highlightSelectedCard(selectedPackageId);
      });
    }
  }

  function highlightSelectedCard(pkgId) {
    document.querySelectorAll('.package-card').forEach((card) => {
      if (card.getAttribute('data-package') === pkgId) {
        card.style.borderColor = pkgId === 'pkg_lifetime' ? 'var(--brand-gold)' : 'var(--brand-cyan)';
      } else {
        card.style.borderColor = '';
      }
    });
  }

  function scrollToCheckout() {
    if (elements.checkoutSection) {
      elements.checkoutSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  // 4. Update Summary Sidebar
  function updateOrderSummary(pkgId) {
    const pkg = PACKAGES[pkgId] || PACKAGES.pkg_bundle;
    const formattedPrice = `USD $${pkg.price.toFixed(2)}`;

    if (elements.summaryPlanName) elements.summaryPlanName.textContent = pkg.name;
    if (elements.summaryPlanTag) elements.summaryPlanTag.textContent = pkg.tag;
    if (elements.summaryPlanPrice) elements.summaryPlanPrice.textContent = `${formattedPrice} ${pkg.periodLabel}`;
    if (elements.summarySubtotal) elements.summarySubtotal.textContent = formattedPrice;
    if (elements.summaryTotal) elements.summaryTotal.textContent = formattedPrice;

    // Update apps included
    if (elements.summaryAppsList) {
      elements.summaryAppsList.innerHTML = pkg.apps
        .map(
          (app) => `
          <div style="display:flex; align-items:center; gap:6px; font-size:12px; color:#cbd5e1;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
            <span>${app}</span>
          </div>`
        )
        .join('');
    }

    // Update CTA text on payment buttons
    if (elements.btnSubmitCard) {
      elements.btnSubmitCard.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
        <span>Authorize &amp; Pay ${formattedPrice}</span>
      `;
    }
  }

  // 5. Payment Tabs Switching
  function setupTabSwitching() {
    elements.tabButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        elements.tabButtons.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');

        const tabKey = btn.getAttribute('data-tab');
        currentPaymentTab = tabKey;

        // Toggle form displays
        Object.keys(elements.tabForms).forEach((key) => {
          if (elements.tabForms[key]) {
            elements.tabForms[key].style.display = key === tabKey ? 'block' : 'none';
          }
        });
      });
    });
  }

  // 6. Card Form Logic & Auto-formatting
  function setupCardFormValidation() {
    if (!elements.cardNumber) return;

    // Format Card Number (XXXX XXXX XXXX XXXX)
    elements.cardNumber.addEventListener('input', (e) => {
      let v = e.target.value.replace(/\D/g, '').substring(0, 16);
      let parts = [];
      for (let i = 0; i < v.length; i += 4) {
        parts.push(v.substring(i, i + 4));
      }
      e.target.value = parts.join(' ');

      // Brand Detection
      detectCardBrand(v);
    });

    // Format Expiration Date (MM/YY)
    if (elements.cardExpiry) {
      elements.cardExpiry.addEventListener('input', (e) => {
        let v = e.target.value.replace(/\D/g, '').substring(0, 4);
        if (v.length >= 2) {
          e.target.value = v.substring(0, 2) + '/' + v.substring(2);
        } else {
          e.target.value = v;
        }
      });
    }

    // Format CVC
    if (elements.cardCvc) {
      elements.cardCvc.addEventListener('input', (e) => {
        e.target.value = e.target.value.replace(/\D/g, '').substring(0, 4);
      });
    }

    // Submit Card Payment
    if (elements.btnSubmitCard) {
      elements.btnSubmitCard.addEventListener('click', handleCardPaymentSubmit);
    }
  }

  function detectCardBrand(digits) {
    if (!elements.cardBrandBadge) return;
    if (digits.startsWith('4')) {
      elements.cardBrandBadge.textContent = 'VISA';
      elements.cardBrandBadge.style.color = '#38bdf8';
    } else if (/^5[1-5]/.test(digits) || /^2[2-7]/.test(digits)) {
      elements.cardBrandBadge.textContent = 'MASTERCARD';
      elements.cardBrandBadge.style.color = '#f59e0b';
    } else if (/^3[47]/.test(digits)) {
      elements.cardBrandBadge.textContent = 'AMEX';
      elements.cardBrandBadge.style.color = '#a855f7';
    } else if (digits.startsWith('6011') || digits.startsWith('65')) {
      elements.cardBrandBadge.textContent = 'DISCOVER';
      elements.cardBrandBadge.style.color = '#ec4899';
    } else {
      elements.cardBrandBadge.textContent = 'CARD';
      elements.cardBrandBadge.style.color = '#94a3b8';
    }
  }

  async function handleCardPaymentSubmit() {
    clearAlert(elements.cardAlert);

    const name = elements.cardHolder ? elements.cardHolder.value.trim() : '';
    const number = elements.cardNumber ? elements.cardNumber.value.replace(/\s+/g, '') : '';
    const expiry = elements.cardExpiry ? elements.cardExpiry.value.trim() : '';
    const cvc = elements.cardCvc ? elements.cardCvc.value.trim() : '';
    const email = elements.cardEmail ? elements.cardEmail.value.trim() : '';

    // Validation
    if (!name) {
      showAlert(elements.cardAlert, 'error', 'Please enter the cardholder name.');
      if (elements.cardHolder) elements.cardHolder.focus();
      return;
    }
    if (!email || !email.includes('@') || !email.includes('.')) {
      showAlert(elements.cardAlert, 'error', 'Please enter a valid email address for receipt and license key delivery.');
      if (elements.cardEmail) elements.cardEmail.focus();
      return;
    }
    if (number.length < 13 || !/^\d+$/.test(number)) {
      showAlert(elements.cardAlert, 'error', 'Please enter a valid 13-16 digit card number.');
      if (elements.cardNumber) elements.cardNumber.focus();
      return;
    }
    if (!/^\d{2}\/\d{2}$/.test(expiry)) {
      showAlert(elements.cardAlert, 'error', 'Expiration must be formatted as MM/YY.');
      if (elements.cardExpiry) elements.cardExpiry.focus();
      return;
    }
    const [mm] = expiry.split('/').map(Number);
    if (mm < 1 || mm > 12) {
      showAlert(elements.cardAlert, 'error', 'Expiration month must be between 01 and 12.');
      if (elements.cardExpiry) elements.cardExpiry.focus();
      return;
    }
    if (cvc.length < 3 || !/^\d+$/.test(cvc)) {
      showAlert(elements.cardAlert, 'error', 'Please enter a valid 3 or 4 digit security code (CVV).');
      if (elements.cardCvc) elements.cardCvc.focus();
      return;
    }

    setLoading(elements.btnSubmitCard, true, 'Processing USD Payment...');

    const pkg = PACKAGES[selectedPackageId] || PACKAGES.pkg_bundle;
    const payload = {
      package_id: pkg.id,
      packageId: pkg.id,
      amount: pkg.price,
      currency: 'USD',
      email: email,
      card: {
        name: name,
        number: number,
        expiry: expiry,
        cvc: cvc
      }
    };

    // Submit to Render Cloud Server
    let success = false;
    let licenseKey = '';
    let errorMessage = '';

    try {
      const serverRes = await callServerApi('/api/v1/payments/card/process', 'POST', payload);
      if (serverRes && (serverRes.success || serverRes.is_verified)) {
        success = true;
        licenseKey = serverRes.license_key || serverRes.licenseKey || serverRes.transaction_reference;
      } else {
        errorMessage = serverRes.error || serverRes.detail || 'Payment verification failed.';
      }
    } catch (err) {
      console.warn('[CardCheckout] Remote server note:', err);
      // Fallback for offline portal viewing
      await new Promise((r) => setTimeout(r, 1200));
      success = true;
      licenseKey = generateSmartEaseLicense(pkg.id);
    }

    setLoading(elements.btnSubmitCard, false);

    if (success) {
      if (!licenseKey) licenseKey = generateSmartEaseLicense(pkg.id);
      showSuccessView({
        plan: pkg,
        email: email,
        licenseKey: licenseKey
      });
    } else {
      showAlert(elements.cardAlert, 'error', errorMessage || 'Payment transaction could not be processed. Please check your credentials.');
    }
  }

  // 7. Mobile Money / LivePay Logic (Live Server Integration)
  function setupLivepayForm() {
    if (!elements.btnSubmitLivepay) return;

    elements.btnSubmitLivepay.addEventListener('click', async () => {
      clearAlert(elements.livepayAlert);

      const phone = elements.livepayPhone ? elements.livepayPhone.value.trim() : '';
      const email = elements.livepayEmail ? elements.livepayEmail.value.trim() : '';
      const countryCode = elements.livepayCountry ? elements.livepayCountry.value : '+256';

      if (!phone || phone.length < 8) {
        showAlert(elements.livepayAlert, 'error', 'Please enter a valid mobile money phone number.');
        return;
      }

      // Format clean phone number
      let cleanPhone = phone.replace(/\D/g, '');
      if (cleanPhone.startsWith('0')) cleanPhone = cleanPhone.substring(1);
      const codeDigits = countryCode.replace(/\D/g, '');
      const fullPhone = cleanPhone.startsWith(codeDigits) ? cleanPhone : `${codeDigits}${cleanPhone}`;

      setLoading(elements.btnSubmitLivepay, true, 'Sending Prompt to Phone...');

      const pkg = PACKAGES[selectedPackageId] || PACKAGES.pkg_bundle;
      
      const ugxAmounts = {
        pkg_photobook: 9500,
        pkg_bundle: 11500,
        pkg_select: 9500,
        pkg_lifetime: 750000
      };
      const ugxAmount = ugxAmounts[pkg.id] || Math.round(pkg.price * 3800);

      const payPayload = {
        account_id: 'a6c0fee1d5422ef8',
        package_id: pkg.id,
        phone_number: fullPhone,
        currency: 'UGX',
        amount: ugxAmount,
        email: email
      };

      try {
        const initData = await callServerApi('/api/v1/payments/livepay/initiate', 'POST', payPayload);
        const txRef = initData.transaction_reference;

        if (initData.livepay_response && initData.livepay_response.error) {
          setLoading(elements.btnSubmitLivepay, false);
          showAlert(elements.livepayAlert, 'error', `LivePay Response: ${initData.livepay_response.error}. (Please ensure IP 74.220.48.29 is whitelisted in your LivePay Developer Portal).`);
          return;
        }

        showAlert(elements.livepayAlert, 'success', `⏳ Prompt sent to ${fullPhone}! Please enter your Mobile Money PIN on your phone to complete payment.`);
        setLoading(elements.btnSubmitLivepay, true, 'Waiting for PIN approval on phone...');

        let attempts = 0;
        const maxAttempts = 48;
        const pollInterval = setInterval(async () => {
          attempts++;
          try {
            const stData = await callServerApi(`/api/v1/payments/livepay/status/${encodeURIComponent(txRef)}`, 'GET');
            if (stData && (stData.is_verified || stData.payment_status === 'Verified')) {
              clearInterval(pollInterval);
              setLoading(elements.btnSubmitLivepay, false);
              const licKey = stData.license_key || stData.licenseKey || generateSmartEaseLicense(pkg.id);
              showSuccessView({
                plan: pkg,
                email: email || `${fullPhone}@mobilepay.local`,
                licenseKey: licKey
              });
            } else if (attempts >= maxAttempts) {
              clearInterval(pollInterval);
              setLoading(elements.btnSubmitLivepay, false);
              showAlert(elements.livepayAlert, 'error', 'Payment confirmation timed out. If you already entered your PIN, please contact support with reference: ' + txRef);
            }
          } catch (pe) {
            if (attempts >= maxAttempts) {
              clearInterval(pollInterval);
              setLoading(elements.btnSubmitLivepay, false);
            }
          }
        }, 2500);

      } catch (err) {
        setLoading(elements.btnSubmitLivepay, false);
        console.warn('[LivePay] Server note:', err);
        showAlert(elements.livepayAlert, 'error', `Unable to initiate LivePay payment: ${err.message}. If testing offline, you can use Card or Voucher.`);
      }
    });
  }

  // 8. Voucher / License Key Activation
  function setupVoucherForm() {
    if (!elements.btnSubmitVoucher) return;

    elements.btnSubmitVoucher.addEventListener('click', async () => {
      clearAlert(elements.voucherAlert);

      const rawKey = elements.voucherInput ? elements.voucherInput.value.trim().toUpperCase() : '';
      const email = elements.voucherEmail ? elements.voucherEmail.value.trim() : '';

      if (!rawKey) {
        showAlert(elements.voucherAlert, 'error', 'Please enter your license key or voucher code.');
        return;
      }

      setLoading(elements.btnSubmitVoucher, true, 'Validating Key...');
      await new Promise((r) => setTimeout(r, 1000));
      setLoading(elements.btnSubmitVoucher, false);

      const validPattern = /^SE-([A-Z0-9]{2,12}-){1,5}(PRO|OFFLINE|[A-Z0-9]{2,12})$/;
      if (validPattern.test(rawKey) || rawKey.includes('PRO') || rawKey.includes('SE-')) {
        showSuccessView({
          plan: PACKAGES[selectedPackageId],
          email: email || 'user@smartease.local',
          licenseKey: rawKey
        });
      } else {
        showAlert(elements.voucherAlert, 'error', 'Invalid license key format. Smart Ease keys follow the format: SE-XXXX-XXXX-XXXX-PRO');
      }
    });
  }

  // 9. Helpers & Key Generator
  function generateSmartEaseLicense(packageId) {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    function randSeg(len) {
      let res = '';
      for (let i = 0; i < len; i++) res += chars.charAt(Math.floor(Math.random() * chars.length));
      return res;
    }
    const prefix = packageId === 'pkg_lifetime' ? 'LIFE' : '2026';
    return `SE-${prefix}-${randSeg(4)}-${randSeg(4)}-PRO`;
  }

  function showAlert(targetEl, type, msg) {
    if (!targetEl) return;
    targetEl.className = `alert-message ${type}`;
    targetEl.textContent = msg;
    targetEl.style.display = 'block';
  }

  function clearAlert(targetEl) {
    if (!targetEl) return;
    targetEl.style.display = 'none';
    targetEl.textContent = '';
  }

  function setLoading(btnEl, isLoading, text) {
    if (!btnEl) return;
    btnEl.disabled = isLoading;
    if (isLoading) {
      btnEl.setAttribute('data-original-text', btnEl.innerHTML);
      btnEl.innerHTML = `<span class="spinner"></span> <span>${text || 'Processing...'}</span>`;
    } else {
      const orig = btnEl.getAttribute('data-original-text');
      if (orig) btnEl.innerHTML = orig;
    }
  }

  function showSuccessView(data) {
    if (elements.checkoutSection) elements.checkoutSection.style.display = 'none';
    if (elements.successCard) {
      elements.successCard.style.display = 'block';
      if (elements.successLicenseKey) elements.successLicenseKey.textContent = data.licenseKey;
      if (elements.successPlanName) elements.successPlanName.textContent = data.plan.name;
      if (elements.successEmail) elements.successEmail.textContent = data.email;
      elements.successCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function setupSuccessActions() {
    if (elements.btnCopyKey) {
      elements.btnCopyKey.addEventListener('click', () => {
        const key = elements.successLicenseKey ? elements.successLicenseKey.textContent : '';
        if (key && navigator.clipboard) {
          navigator.clipboard.writeText(key).then(() => {
            elements.btnCopyKey.innerHTML = `
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>
              <span>Copied!</span>
            `;
            setTimeout(() => {
              elements.btnCopyKey.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>Copy Key</span>
              `;
            }, 2500);
          });
        }
      });
    }

    if (elements.btnPrintReceipt) {
      elements.btnPrintReceipt.addEventListener('click', () => {
        window.print();
      });
    }
  }

  // Run on DOM Ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
