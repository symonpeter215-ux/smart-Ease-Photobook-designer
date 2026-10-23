/**
 * Smart Ease Subscription & Revenue Manager
 * Desktop Frontend Controller
 */

// Direct Online Cloud Connection (NO local server needed)
const PROD_API_BASE = 'https://smart-ease-photobook-designer.onrender.com';
// Served by the Render server -> same origin. Opened elsewhere -> talk to production
// (set localStorage 'se_api_same_origin' = '1' to test against a locally running server).
let _sameOriginApi = false;
try { _sameOriginApi = localStorage.getItem('se_api_same_origin') === '1'; } catch (_) {}
const API_BASE = (window.location.hostname.includes('onrender.com') || _sameOriginApi) ? '' : PROD_API_BASE;

const _nativeFetch = window.fetch;
const ADMIN_TOKEN_KEY = 'se_admin_token';

function getAdminToken() {
  try { return localStorage.getItem(ADMIN_TOKEN_KEY); } catch (_) { return null; }
}

// Returning from "Continue with Google": the server puts the session token (or an error) in the URL fragment.
let _googleAdminError = '';
(function takeGoogleAdminResult() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get('admin_token');
  _googleAdminError = params.get('admin_error') || '';
  if (token) { try { localStorage.setItem(ADMIN_TOKEN_KEY, token); } catch (_) {} }
  if (token || _googleAdminError) history.replaceState(null, '', window.location.pathname + window.location.search);
})();

// All /api/ calls go to the server with the admin's session token; 401/403 opens the sign-in form.
window.fetch = async function(url, options = {}) {
  if (typeof url !== 'string' || !url.startsWith('/api/')) {
    return _nativeFetch(url, options);
  }
  const token = getAdminToken();
  if (token) {
    options = { ...options, headers: { ...(options.headers || {}), Authorization: `Bearer ${token}` } };
  }
  const res = await _nativeFetch(API_BASE + url, options);
  if (res.status === 401 || res.status === 403) {
    showAdminLogin(_googleAdminError || (res.status === 403 ? 'This account is not an administrator.' : ''));
  }
  return res;
};

function showAdminLogin(message) {
  if (document.getElementById('adminLoginOverlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'adminLoginOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(15,23,42,0.92);display:flex;align-items:center;justify-content:center;padding:16px;';
  overlay.innerHTML = `
    <form id="adminLoginForm" style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:24px;width:100%;max-width:360px;color:#e2e8f0;font-family:inherit;display:flex;flex-direction:column;gap:12px;">
      <h2 style="margin:0;font-size:18px;">Admin sign in</h2>
      <p style="margin:0;font-size:13px;color:#94a3b8;">Sign in with your Smart Ease administrator account.</p>
      <a href="${API_BASE}/api/v1/auth/google/admin-start" style="display:flex;align-items:center;justify-content:center;gap:10px;padding:10px;border-radius:8px;background:#fff;color:#1f1f1f;font-weight:700;text-decoration:none;">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z"/><path fill="#FBBC05" d="M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.3.8-4.7l-7.8-6C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.9 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.8 6C6.6 42.6 14.6 48 24 48z"/></svg>
        Continue with Google
      </a>
      <p style="margin:0;font-size:12px;color:#64748b;text-align:center;">or use email and password</p>
      <input id="adminLoginEmail" type="email" placeholder="Email" autocomplete="username" required style="padding:10px;border-radius:8px;border:1px solid #334155;background:#020617;color:#fff;">
      <input id="adminLoginPassword" type="password" placeholder="Password" autocomplete="current-password" required style="padding:10px;border-radius:8px;border:1px solid #334155;background:#020617;color:#fff;">
      <div id="adminLoginError" style="font-size:13px;color:#fca5a5;${message ? '' : 'display:none;'}">${message || ''}</div>
      <button type="submit" style="padding:10px;border:none;border-radius:8px;background:#0284c7;color:#fff;font-weight:700;cursor:pointer;">Sign in</button>
    </form>`;
  document.body.appendChild(overlay);

  overlay.querySelector('#adminLoginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = overlay.querySelector('#adminLoginError');
    errBox.style.display = 'none';
    try {
      const res = await _nativeFetch(API_BASE + '/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: overlay.querySelector('#adminLoginEmail').value.trim(),
          password: overlay.querySelector('#adminLoginPassword').value,
          product: 'Subscription Manager Admin'
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success || !data.token) {
        throw new Error(data.error || data.detail || 'Sign in failed.');
      }
      try { localStorage.setItem(ADMIN_TOKEN_KEY, data.token); } catch (_) {}
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = 'block';
    }
  });
}

async function adminSignOut() {
  try { await fetch('/api/v1/auth/logout', { method: 'POST' }); } catch (_) {}
  try { localStorage.removeItem(ADMIN_TOKEN_KEY); } catch (_) {}
  window.location.reload();
}
window.adminSignOut = adminSignOut;

// Application State
const state = {
  currentTab: 'dashboard',
  chartYear: new Date().getFullYear(),
  chartMonth: new Date().getMonth() + 1,
  dailyDate: new Date().toISOString().split('T')[0],
  monthlyMonth: new Date().toISOString().substring(0, 7),
  users: [],
  packages: [],
  subscriptions: [],
  transactions: []
};

// DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initModals();
  initDashboard();
  initUsers();
  initPackages();
  initSubscriptions();
  initRevenue();

  // Initial Data Load
  loadDashboardData();
});

// Toast notification helper
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 200);
  }, 3500);
}

// Format Currency
function fmtUSD(val) {
  const num = parseFloat(val || 0);
  return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ==========================================
// NAVIGATION CONTROLLER
// ==========================================
function initNavigation() {
  const tabs = document.querySelectorAll('.nav-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      switchTab(target);
    });
  });
}

function switchTab(tabId) {
  state.currentTab = tabId;
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-panel').forEach(p => {
    p.classList.toggle('active', p.id === `tab-${tabId}`);
  });

  // Trigger data refreshes when switching tabs
  if (tabId === 'dashboard') loadDashboardData();
  else if (tabId === 'users') loadUsersData();
  else if (tabId === 'packages') loadPackagesData();
  else if (tabId === 'subscriptions') loadSubscriptionsData();
  else if (tabId === 'revenue') loadRevenueData();
}

// ==========================================
// 1. DASHBOARD & REVENUE CHART
// ==========================================
function initDashboard() {
  const btnPrev = document.getElementById('btnPrevMonth');
  const btnNext = document.getElementById('btnNextMonth');
  const btnViewAll = document.getElementById('btnViewAllTxns');

  if (btnPrev) {
    btnPrev.addEventListener('click', () => {
      state.chartMonth -= 1;
      if (state.chartMonth < 1) {
        state.chartMonth = 12;
        state.chartYear -= 1;
      }
      loadRevenueChart();
    });
  }

  if (btnNext) {
    btnNext.addEventListener('click', () => {
      state.chartMonth += 1;
      if (state.chartMonth > 12) {
        state.chartMonth = 1;
        state.chartYear += 1;
      }
      loadRevenueChart();
    });
  }

  if (btnViewAll) {
    btnViewAll.addEventListener('click', () => {
      switchTab('revenue');
    });
  }

  // App Breakdown Chip Quick Filters (Both Apps vs Independent)
  const setupChip = (id, filterVal) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', () => {
        switchTab('users');
        const sel = document.getElementById('filterSubModel');
        if (sel) {
          sel.value = filterVal;
          loadUsersData();
        }
      });
    }
  };

  setupChip('chipFilterBoth', 'both');
  setupChip('chipFilterPb', 'independent_photobook');
  setupChip('chipFilterEs', 'independent_ease_select');
  setupChip('chipFilterDual', 'both_independent');
  setupChip('btnGoToUsersBoth', 'both');

  // Live Presence Quick Filters (Online vs Uses Offline)
  const setupPresenceChip = (id, filterVal) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', () => {
        switchTab('users');
        const sel = document.getElementById('filterConnection');
        if (sel) {
          sel.value = filterVal;
          loadUsersData();
        }
      });
    }
  };

  setupPresenceChip('chipPresenceOnline', 'online');
  setupPresenceChip('chipPresenceOffline', 'offline');
  setupPresenceChip('chipPresenceRecent', 'all');
  setupPresenceChip('btnGoToUsersOnline', 'online');
}

async function loadDashboardData() {
  try {
    const res = await fetch('/api/v1/dashboard/metrics');
    if (!res.ok) throw new Error('Failed to fetch dashboard metrics');
    const data = await res.json();

    // Summary Cards
    document.getElementById('kpiTotalUsers').textContent = data.total_registered_users;
    document.getElementById('kpiActiveSubs').textContent = data.active_subscriptions;
    document.getElementById('kpiExpiringSoon').textContent = data.expiring_soon;
    document.getElementById('kpiExpiredSubs').textContent = data.expired_subscriptions;

    document.getElementById('kpiTodayEarnings').textContent = fmtUSD(data.today_earnings);
    document.getElementById('kpiTodayTxCount').textContent = `${data.today_tx_count} verified payments today`;

    document.getElementById('kpiMonthEarnings').textContent = fmtUSD(data.this_month_earnings);
    document.getElementById('kpiMonthTxCount').textContent = `${data.this_month_tx_count} verified payments this month`;

    document.getElementById('kpiLifetimeRev').textContent = fmtUSD(data.lifetime_revenue);
    document.getElementById('kpiLifetimePro').textContent = data.lifetime_pro_users;

    // Live Presence & Connectivity Counts (Who is Online vs Uses Offline)
    if (data.presence) {
      const elOnline = document.getElementById('countPresenceOnline');
      const elOfflineMode = document.getElementById('countPresenceOfflineMode');
      const elOffline = document.getElementById('countPresenceOffline');
      const elRecent = document.getElementById('countPresenceRecent');

      if (elOnline) elOnline.textContent = data.presence.online_now;
      if (elOfflineMode) elOfflineMode.textContent = data.presence.uses_offline_mode;
      if (elOffline) elOffline.textContent = data.presence.offline_count;
      if (elRecent) elRecent.textContent = data.presence.recent_active_today;
    }

    // App Subscription Breakdown (Both Apps vs Independent)
    if (data.app_breakdown) {
      const elBoth = document.getElementById('countBothAppsTotal');
      const elPb = document.getElementById('countIndPhotobook');
      const elEs = document.getElementById('countIndEaseSelect');
      const elDual = document.getElementById('countBothDualInd');

      if (elBoth) elBoth.textContent = data.app_breakdown.total_both_apps;
      if (elPb) elPb.textContent = data.app_breakdown.independent_photobook;
      if (elEs) elEs.textContent = data.app_breakdown.independent_ease_select;
      if (elDual) elDual.textContent = data.app_breakdown.both_dual_independent;
    }

    // Recent Transactions
    renderRecentTransactions(data.recent_transactions);

    // Chart
    loadRevenueChart();
  } catch (err) {
    console.error('Dashboard load error:', err);
  }
}

function renderRecentTransactions(txns) {
  const tbody = document.getElementById('recentTransactionsBody');
  if (!tbody) return;

  if (!txns || txns.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No transactions recorded yet</td></tr>';
    return;
  }

  tbody.innerHTML = txns.map(t => `
    <tr>
      <td>${t.transaction_date}</td>
      <td><strong>${escapeHtml(t.user_name || 'Customer')}</strong></td>
      <td>${escapeHtml(t.product_or_package)}</td>
      <td><strong>${fmtUSD(t.amount)}</strong></td>
      <td><span class="badge badge-neutral">${escapeHtml(t.payment_provider)}</span></td>
      <td><code>${escapeHtml(t.transaction_reference)}</code></td>
      <td><span class="badge badge-active">${escapeHtml(t.payment_status)}</span></td>
    </tr>
  `).join('');
}

async function loadRevenueChart() {
  try {
    const res = await fetch(`/api/v1/dashboard/chart?year=${state.chartYear}&month=${state.chartMonth}`);
    if (!res.ok) throw new Error('Failed to load chart');
    const data = await res.json();

    document.getElementById('chartMonthLabel').textContent = data.month_label;
    document.getElementById('chartStatSummary').innerHTML = `Total Month Net: <strong>${fmtUSD(data.total_revenue)}</strong>`;

    renderSvgRevenueChart(data.daily);
  } catch (err) {
    console.error('Chart load error:', err);
  }
}

function renderSvgRevenueChart(dailyData) {
  const host = document.getElementById('chartSvgHost');
  if (!host) return;

  const width = host.clientWidth || 900;
  const height = 280;
  const padding = { top: 25, right: 30, bottom: 40, left: 60 };

  const maxVal = Math.max(...dailyData.map(d => d.total), 10);
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;
  const barWidth = Math.max(chartW / dailyData.length - 4, 6);

  // Build SVG bars
  let barsHtml = '';
  dailyData.forEach((d, i) => {
    const x = padding.left + i * (chartW / dailyData.length) + 2;
    const barH = d.total > 0 ? Math.max((d.total / maxVal) * chartH, 4) : 0;
    const y = padding.top + chartH - barH;

    // Color based on prominent tier
    let fill = '#3b82f6';
    if (d.lifetime > 0) fill = '#fbbf24';
    else if (d.bundle > 0) fill = '#6366f1';
    else if (d.ease_select > 0) fill = '#14b8a6';

    barsHtml += `
      <rect x="${x}" y="${y}" width="${barWidth}" height="${barH}" fill="${fill}" rx="3">
        <title>Date: ${d.date}\nTotal: ${fmtUSD(d.total)}\nTransactions: ${d.count}</title>
      </rect>
      ${(i % 5 === 0 || i === dailyData.length - 1) ? `
        <text x="${x + barWidth/2}" y="${height - 12}" fill="#64748b" font-size="11" text-anchor="middle">${d.day}</text>
      ` : ''}
    `;
  });

  // Grid lines
  const gridLines = [0, 0.5, 1].map(ratio => {
    const y = padding.top + chartH * (1 - ratio);
    const label = fmtUSD(maxVal * ratio);
    return `
      <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="3,3" />
      <text x="${padding.left - 8}" y="${y + 4}" fill="#64748b" font-size="10" text-anchor="end">${label}</text>
    `;
  }).join('');

  host.innerHTML = `
    <svg width="100%" height="100%" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      ${gridLines}
      ${barsHtml}
    </svg>
  `;
}

// ==========================================
// 2. USER MANAGEMENT
// ==========================================
function initUsers() {
  const searchInput = document.getElementById('userSearchInput');
  const filterProd = document.getElementById('filterProduct');
  const filterModel = document.getElementById('filterSubModel');
  const filterStat = document.getElementById('filterStatus');
  const filterConn = document.getElementById('filterConnection');
  const btnReset = document.getElementById('btnResetUserFilters');
  const btnExport = document.getElementById('btnExportUsersCsv');

  if (searchInput) searchInput.addEventListener('input', debounce(loadUsersData, 250));
  if (filterProd) filterProd.addEventListener('change', loadUsersData);
  if (filterModel) filterModel.addEventListener('change', loadUsersData);
  if (filterStat) filterStat.addEventListener('change', loadUsersData);
  if (filterConn) filterConn.addEventListener('change', loadUsersData);

  if (btnReset) {
    btnReset.addEventListener('click', () => {
      if (searchInput) searchInput.value = '';
      if (filterProd) filterProd.value = 'all';
      if (filterModel) filterModel.value = 'all';
      if (filterStat) filterStat.value = 'all';
      if (filterConn) filterConn.value = 'all';
      loadUsersData();
    });
  }

  if (btnExport) {
    btnExport.addEventListener('click', () => {
      window.location.href = '/api/v1/export/users.csv';
    });
  }
}

async function loadUsersData() {
  const search = document.getElementById('userSearchInput')?.value || '';
  const product = document.getElementById('filterProduct')?.value || 'all';
  const status = document.getElementById('filterStatus')?.value || 'all';
  const app_subscription_filter = document.getElementById('filterSubModel')?.value || 'all';
  const connection_filter = document.getElementById('filterConnection')?.value || 'all';

  const params = new URLSearchParams({ search, product, status, app_subscription_filter, connection_filter });
  try {
    const res = await fetch(`/api/v1/users?${params}`);
    if (!res.ok) throw new Error('Failed to load users');
    state.users = await res.json();
    renderUsersTable(state.users);
  } catch (err) {
    console.error('Users load error:', err);
  }
}

function renderUsersTable(users) {
  const tbody = document.getElementById('usersTableBody');
  const summary = document.getElementById('userCountSummary');
  if (!tbody) return;

  if (summary) summary.textContent = `Showing ${users.length} registered customer${users.length === 1 ? '' : 's'}`;

  if (!users || users.length === 0) {
    tbody.innerHTML = '<tr><td colspan="14" class="text-center text-muted">No matching users found</td></tr>';
    return;
  }

  tbody.innerHTML = users.map(u => {
    const statusBadge = getStatusBadge(u.subscription_status);
    const modelBadge = `<span class="badge ${u.app_access_badge || 'badge-neutral'}">${escapeHtml(u.app_access_label || 'Unlicensed')}</span>`;
    const multiSubHint = (u.active_subscriptions_count > 1 || u.app_access_code === 'both_independent') ? 
      `<br><span style="font-size:0.7rem; color:#a855f7; font-weight:600;">⚡ 2 Independent Subs</span>` : '';

    let presenceHtml = '';
    if (u.is_online) {
      presenceHtml = `
        <span class="badge badge-online"><span class="pulse-dot"></span> Online</span>
        <div style="font-size:0.72rem; color:#10b981; margin-top:2px; font-weight:500;">${escapeHtml(u.last_app || 'App')} &bull; ${escapeHtml(u.last_seen_relative)}</div>
      `;
    } else {
      presenceHtml = `
        <span class="badge badge-offline">⚪ Disconnected</span>
        <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">Last: ${escapeHtml(u.last_seen_relative)}</div>
      `;
    }

    return `
      <tr onclick="openUserDetail('${escapeHtml(u.id)}')">
        <td><strong>${escapeHtml(u.full_name)}</strong></td>
        <td>${escapeHtml(u.email || u.id)}</td>
        <td><code>${escapeHtml(u.smart_ease_account_id)}</code></td>
        <td>${presenceHtml}</td>
        <td>${modelBadge}${multiSubHint}</td>
        <td>${escapeHtml(u.product_owned || '-')}</td>
        <td>${escapeHtml(u.package_name || '-')}</td>
        <td>${escapeHtml(u.subscription_start_date || '-')}</td>
        <td>${escapeHtml(u.subscription_expiry_date || (u.subscription_status === 'Lifetime' ? 'Permanent' : '-'))}</td>
        <td>${statusBadge}</td>
        <td><strong>${fmtUSD(u.amount_paid)}</strong></td>
        <td>${escapeHtml(u.last_successful_payment_date || '-')}</td>
        <td>${escapeHtml(u.next_renewal_date || '-')}</td>
        <td>
          <button class="btn btn-secondary btn-xs" onclick="event.stopPropagation(); openUserDetail('${escapeHtml(u.id)}')">View</button>
        </td>
      </tr>
    `;
  }).join('');
}

function getStatusBadge(status) {
  const st = (status || '').toLowerCase();
  if (st === 'active') return `<span class="badge badge-active">Active</span>`;
  if (st === 'expiring soon') return `<span class="badge badge-warning">Expiring Soon</span>`;
  if (st === 'expired') return `<span class="badge badge-danger">Expired</span>`;
  if (st === 'lifetime') return `<span class="badge badge-lifetime">Lifetime Pro</span>`;
  if (st === 'cancelled') return `<span class="badge badge-neutral">Cancelled</span>`;
  if (st === 'payment pending') return `<span class="badge badge-warning">Payment Pending</span>`;
  if (st === 'payment failed') return `<span class="badge badge-danger">Payment Failed</span>`;
  return `<span class="badge badge-neutral">${escapeHtml(status || 'Unlicensed')}</span>`;
}

// User Detail Modal
async function openUserDetail(userId) {
  try {
    const res = await fetch(`/api/v1/users/${userId}`);
    if (!res.ok) throw new Error('Failed to load user details');
    const data = await res.json();
    const u = data.user;

    document.getElementById('mudUserName').textContent = u.full_name;
    document.getElementById('mudAccountBadge').textContent = `Account ID: ${u.smart_ease_account_id} | ${u.id}`;

    if (!state.packages || !state.packages.length) {
      try { const pr = await fetch('/api/v1/packages'); if (pr.ok) state.packages = await pr.json(); } catch (_) {}
    }
    const activePass = (data.subscriptions || []).filter(s => s.payment_status === 'Verified' && ['Active', 'Expiring Soon', 'Lifetime'].includes(s.status));
    const passSummary = activePass.length
      ? activePass.map(s => `<strong>${escapeHtml(s.product_or_package)}</strong> &mdash; ${s.expiry_date ? 'until ' + escapeHtml(String(s.expiry_date).slice(0, 10)) : 'lifetime'}`).join('<br>')
      : '<span style="color:#f87171;">No active pass &mdash; the customer\'s app is locked.</span>';
    const passPkgOptions = [...(state.packages || [])]
      .sort((a, b) => (a.sort_order ?? 99) - (b.sort_order ?? 99))
      .map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}${(p.billing_interval === 'lifetime' || p.billing_type === 'one_time') ? ' (lifetime)' : ''}</option>`).join('');

    const content = document.getElementById('mudContent');
    content.innerHTML = `
      <div class="pass-panel">
        <div class="pass-status">
          <div class="pass-label">Current pass</div>
          <div>${passSummary}</div>
        </div>
        <div class="pass-actions">
          <select id="passPkg" class="select-control">${passPkgOptions}</select>
          <input id="passDays" type="number" min="1" max="3650" value="30" class="text-input" style="width:90px;" title="Days of access">
          <span class="text-hint">days</span>
          <button class="btn btn-primary btn-sm" onclick="giveUserPass('${escapeHtml(u.id)}')">Give pass</button>
          <button class="btn btn-danger btn-sm" onclick="endUserPass('${escapeHtml(u.id)}', '${escapeHtml(u.full_name)}')" ${activePass.length ? '' : 'disabled'}>End pass</button>
        </div>
      </div>
      <div class="user-detail-grid" style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; margin-bottom: 20px;">
        <div class="info-box" style="background:rgba(0,0,0,0.2); padding:14px; border-radius:8px; border:1px solid rgba(255,255,255,0.08);">
          <div style="font-size:0.75rem; color:#94a3b8; text-transform:uppercase;">Customer Profile</div>
          <div style="margin-top:6px; font-size:0.86rem;"><strong>Email:</strong> ${escapeHtml(u.email || 'None')}</div>
          <div style="font-size:0.86rem;"><strong>Registered:</strong> ${escapeHtml(u.registered_date)}</div>
          <div style="font-size:0.86rem;"><strong>Source App:</strong> ${escapeHtml(u.source_app || 'Photobook')}</div>
          <div style="font-size:0.86rem;"><strong>Device Host:</strong> <code>${escapeHtml(u.device_hostname || '-')}</code></div>
        </div>
        <div class="info-box" style="background:rgba(0,0,0,0.2); padding:14px; border-radius:8px; border:1px solid rgba(255,255,255,0.08);">
          <div style="font-size:0.75rem; color:#94a3b8; text-transform:uppercase;">Active Package & Entitlement</div>
          <div style="margin-top:6px; font-size:0.86rem;"><strong>Product:</strong> ${escapeHtml(u.product_owned)}</div>
          <div style="font-size:0.86rem;"><strong>Package:</strong> ${escapeHtml(u.package_name || 'None')}</div>
          <div style="font-size:0.86rem; margin-top:4px;"><strong>Subscription Model:</strong> ${(() => {
            const match = state.users.find(x => x.id === u.id);
            return match ? `<span class="badge ${match.app_access_badge || 'badge-neutral'}">${escapeHtml(match.app_access_label || '-')}</span>` : `<span class="badge badge-neutral">${escapeHtml(u.package_name || '-')}</span>`;
          })()}</div>
          <div style="font-size:0.86rem; margin-top:4px;"><strong>Notes:</strong> ${escapeHtml(u.notes || '-')}</div>
        </div>
        <div class="info-box" style="background:rgba(0,0,0,0.2); padding:14px; border-radius:8px; border:1px solid rgba(255,255,255,0.08); border-left: 3px solid ${u.is_online ? '#10b981' : '#64748b'};">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div style="font-size:0.75rem; color:#94a3b8; text-transform:uppercase;">Live Presence &amp; Connectivity</div>
            ${u.is_online ? '<span class="badge badge-online"><span class="pulse-dot"></span> Online Now</span>' : '<span class="badge badge-offline">⚪ Offline</span>'}
          </div>
          <div style="margin-top:8px; font-size:0.86rem;"><strong>Active Client App:</strong> ${escapeHtml(u.last_app || u.source_app || 'Photobook')} <span style="color:#94a3b8; font-size:0.75rem;">(${escapeHtml(u.app_version || 'v2.1')})</span></div>
          <div style="font-size:0.86rem; margin-top:4px;"><strong>Last Heartbeat:</strong> ${escapeHtml(u.last_seen || 'Never')} <span style="color:#94a3b8; font-size:0.75rem;">(${escapeHtml(u.last_seen_relative)})</span></div>
          <div style="font-size:0.86rem; margin-top:4px;"><strong>IP &amp; Device:</strong> <code>${escapeHtml(u.last_ip || '127.0.0.1')}</code> / <code>${escapeHtml(u.device_hostname || '-')}</code></div>
        </div>
      </div>

      <h4 style="font-size:0.9rem; margin-bottom:8px; color:#fff;">Subscription History</h4>
      <div class="table-responsive" style="margin-bottom:20px;">
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Package</th>
              <th>Start Date</th>
              <th>Expiry Date</th>
              <th>Status</th>
              <th>Paid</th>
            </tr>
          </thead>
          <tbody>
            ${data.subscriptions.length === 0 ? '<tr><td colspan="6" class="text-center text-muted">No subscriptions found</td></tr>' : 
              data.subscriptions.map(s => `
                <tr>
                  <td><code>${escapeHtml(s.id)}</code></td>
                  <td>${escapeHtml(s.product_or_package)}</td>
                  <td>${escapeHtml(s.start_date)}</td>
                  <td>${escapeHtml(s.expiry_date || (s.status === 'Lifetime' ? 'Permanent' : '-'))}</td>
                  <td>${getStatusBadge(s.status)}</td>
                  <td><strong>${fmtUSD(s.amount_paid)}</strong></td>
                </tr>
              `).join('')
            }
          </tbody>
        </table>
      </div>

      <div id="mudLoginHistory" style="margin-bottom:20px;"></div>
      <h4 style="font-size:0.9rem; margin-bottom:8px; color:#fff;">Payment Transaction History</h4>
      <div class="table-responsive" style="margin-bottom:20px;">
        <table class="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Reference</th>
              <th>Amount</th>
              <th>Provider</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${data.transactions.length === 0 ? '<tr><td colspan="5" class="text-center text-muted">No payment transactions recorded</td></tr>' : 
              data.transactions.map(t => `
                <tr>
                  <td>${escapeHtml(t.transaction_date)}</td>
                  <td><code>${escapeHtml(t.transaction_reference)}</code></td>
                  <td><strong>${fmtUSD(t.amount)}</strong></td>
                  <td><span class="badge badge-neutral">${escapeHtml(t.payment_provider)}</span></td>
                  <td><span class="badge badge-active">${escapeHtml(t.payment_status)}</span></td>
                </tr>
              `).join('')
            }
          </tbody>
        </table>
      </div>

      <h4 style="font-size:0.9rem; margin-bottom:8px; color:#fff; display:flex; align-items:center; gap:8px;">
        <span class="pulse-dot"></span> Recent Presence &amp; Activity Logs
      </h4>
      <div class="table-responsive">
        <table class="data-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Event</th>
              <th>Mode</th>
              <th>Client Application</th>
              <th>Client IP &amp; Hostname</th>
            </tr>
          </thead>
          <tbody>
            ${(!data.presence_logs || data.presence_logs.length === 0) ? '<tr><td colspan="5" class="text-center text-muted">No presence activity recorded yet</td></tr>' : 
              data.presence_logs.map(l => `
                <tr>
                  <td>${escapeHtml(l.created_at)}</td>
                  <td><span class="badge ${l.event_type === 'mode_switch' ? 'badge-purple' : (l.event_type === 'license_check' ? 'badge-blue' : 'badge-neutral')}">${escapeHtml(l.event_type)}</span></td>
                  <td><span class="badge ${l.connection_mode === 'offline_mode' ? 'badge-amber' : 'badge-online'}">${l.connection_mode === 'offline_mode' ? '🟠 Offline Mode' : '🟢 Online'}</span></td>
                  <td>${escapeHtml(l.source_app || 'Photobook')}</td>
                  <td><code>${escapeHtml(l.ip_address || '-')}</code> &bull; <code>${escapeHtml(l.device_hostname || '-')}</code></td>
                </tr>
              `).join('')
            }
          </tbody>
        </table>
      </div>
    `;

    document.getElementById('modalUserDetail').classList.add('open');
  } catch (err) {
    showToast('Failed to load user details: ' + err.message, 'error');
  }
}

// Admin pass controls (customer detail window)
async function giveUserPass(userId) {
  const pkgId = document.getElementById('passPkg')?.value;
  const pkg = (state.packages || []).find(p => p.id === pkgId) || {};
  const lifetime = pkg.billing_interval === 'lifetime' || pkg.billing_type === 'one_time';
  const days = parseInt(document.getElementById('passDays')?.value, 10);
  if (!lifetime && !(days >= 1)) return showToast('Enter how many days the pass should last.', 'error');
  try {
    const res = await fetch('/api/v1/subscriptions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, package_id: pkgId, days: lifetime ? null : days, payment_provider: 'Admin Grant' })
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not give the pass');
    showToast(lifetime ? 'Lifetime pass given.' : `Pass given for ${days} days.`, 'success');
    await loadUsersData();
    await openUserDetail(userId);
    loadDashboardData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function endUserPass(userId, name) {
  if (!confirm(`End ${name}'s pass now? Their app will lock the next time it goes online.`)) return;
  try {
    const res = await fetch(`/api/v1/users/${encodeURIComponent(userId)}/end-pass`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not end the pass');
    showToast('Pass ended.', 'success');
    await loadUsersData();
    await openUserDetail(userId);
    loadDashboardData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ==========================================
// 3. PRODUCT & PACKAGE MANAGEMENT
// ==========================================
function initPackages() {
  // Handled dynamically
}

async function loadPackagesData() {
  try {
    const res = await fetch('/api/v1/packages');
    if (!res.ok) throw new Error('Failed to load packages');
    state.packages = await res.json();
    renderPackagesGrid(state.packages);
  } catch (err) {
    console.error('Packages error:', err);
  }
}

function renderPackagesGrid(pkgs) {
  const grid = document.getElementById('packagesGrid');
  if (!grid) return;

  // Prices customers pay are the card-payment options set on the server (LivePay needs $10+ per payment).
  const sorted = [...pkgs].sort((a, b) => (a.sort_order ?? 99) - (b.sort_order ?? 99));
  grid.innerHTML = sorted.map(p => {
    const options = Array.isArray(p.options) ? p.options : [];
    const onSale = p.for_sale !== false && options.length > 0;
    return `
      <div class="pkg-card ${onSale ? '' : 'highlight'}">
        <div class="pkg-type-badge ${onSale ? 'pkg-type-recurring' : 'pkg-type-lifetime'}">
          ${onSale ? 'On sale &bull; Card payment' : 'Not for sale &bull; Existing holders keep access'}
        </div>
        <h3 class="pkg-title">${escapeHtml(p.name)}</h3>
        ${onSale
          ? `<div style="display:flex;flex-direction:column;gap:4px;margin:8px 0;">${options.map(o =>
              `<div style="display:flex;justify-content:space-between;font-size:0.9rem;"><span>${o.months} months</span><strong>${fmtUSD(o.price)}</strong></div>`).join('')}</div>`
          : `<p class="pkg-desc">Can still be given from Record Payment &rarr; Free access.</p>`}
        <div class="pkg-stats-strip">
          <div>Customers: <strong>${p.customer_count || 0}</strong></div>
          <div>Total Revenue: <strong>${fmtUSD(p.total_revenue)}</strong></div>
        </div>
      </div>
    `;
  }).join('');
}

function openEditPackageModal(id, name, price, desc) {
  document.getElementById('editPkgId').value = id;
  document.getElementById('editPkgName').value = name;
  document.getElementById('editPkgPrice').value = price;
  document.getElementById('editPkgDesc').value = desc;
  document.getElementById('modalEditPackage').classList.add('open');
}

// ==========================================
// 4. SUBSCRIPTION TRACKING
// ==========================================
function initSubscriptions() {
  const filterSub = document.getElementById('filterSubStatus');
  const filterAccess = document.getElementById('filterSubAccessType');

  const reload = () => {
    loadSubscriptionsData(filterSub?.value || 'all', filterAccess?.value || 'all');
  };

  if (filterSub) filterSub.addEventListener('change', reload);
  if (filterAccess) filterAccess.addEventListener('change', reload);
}

async function loadSubscriptionsData(statusFilter = 'all', accessFilter = 'all') {
  try {
    const res = await fetch(`/api/v1/subscriptions?status_filter=${statusFilter}&access_type_filter=${accessFilter}`);
    if (!res.ok) throw new Error('Failed to load subscriptions');
    state.subscriptions = await res.json();
    renderSubscriptionsTable(state.subscriptions);
  } catch (err) {
    console.error('Subscriptions error:', err);
  }
}

function renderSubscriptionsTable(subs) {
  const tbody = document.getElementById('subscriptionsTableBody');
  if (!tbody) return;

  if (!subs || subs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="13" class="text-center text-muted">No subscriptions found</td></tr>';
    return;
  }

  tbody.innerHTML = subs.map(s => {
    const isLifetime = s.status === 'Lifetime' || s.billing_interval === 'lifetime';
    return `
      <tr>
        <td><code>${escapeHtml(s.id)}</code></td>
        <td><strong>${escapeHtml(s.customer_name)}</strong><br><small class="text-muted">${escapeHtml(s.customer_email || s.customer_account_id)}</small></td>
        <td>${escapeHtml(s.product_or_package)}</td>
        <td><span class="badge ${s.access_badge || 'badge-neutral'}">${escapeHtml(s.access_type_label || '-')}</span></td>
        <td>${escapeHtml(s.start_date)}</td>
        <td>${escapeHtml(isLifetime ? 'Permanent / Lifetime' : (s.expiry_date || '-'))}</td>
        <td><span class="badge badge-neutral">${escapeHtml(s.billing_interval)}</span></td>
        <td>${getStatusBadge(s.status)}</td>
        <td><span class="badge ${s.payment_status === 'Verified' ? 'badge-active' : 'badge-danger'}">${escapeHtml(s.payment_status)}</span></td>
        <td>${escapeHtml(s.next_renewal_date || (isLifetime ? 'N/A' : '-'))}</td>
        <td>${escapeHtml(s.last_successful_payment || '-')}</td>
        <td><code>${escapeHtml(s.payment_transaction_ref || '-')}</code></td>
        <td>
          ${s.status === 'Cancelled' ? '<span class="text-muted">Cancelled</span>' : `
            <button class="btn btn-secondary btn-xs text-red" onclick="cancelSub('${escapeHtml(s.id)}')">Cancel</button>
          `}
        </td>
      </tr>
    `;
  }).join('');
}

async function cancelSub(subId) {
  if (!confirm('Are you sure you want to cancel this customer subscription?')) return;
  try {
    const res = await fetch(`/api/v1/subscriptions/${subId}/cancel`, { method: 'POST' });
    if (!res.ok) throw new Error('Cancellation failed');
    showToast('Subscription cancelled successfully', 'info');
    loadSubscriptionsData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ==========================================
// 5. DAILY & MONTHLY EARNINGS
// ==========================================
function initRevenue() {
  const datePicker = document.getElementById('dailyDatePicker');
  const btnToday = document.getElementById('btnTodayDate');
  const monthPicker = document.getElementById('monthlyMonthPicker');
  const filterTx = document.getElementById('filterTxProvider');

  if (datePicker) {
    datePicker.value = state.dailyDate;
    datePicker.addEventListener('change', () => {
      state.dailyDate = datePicker.value;
      loadDailyRevenue();
    });
  }

  if (btnToday) {
    btnToday.addEventListener('click', () => {
      state.dailyDate = new Date().toISOString().split('T')[0];
      if (datePicker) datePicker.value = state.dailyDate;
      loadDailyRevenue();
    });
  }

  if (monthPicker) {
    monthPicker.value = state.monthlyMonth;
    monthPicker.addEventListener('change', () => {
      state.monthlyMonth = monthPicker.value;
      loadMonthlyRevenue();
    });
  }

  if (filterTx) {
    filterTx.addEventListener('change', () => {
      loadAllTransactions(filterTx.value);
    });
  }
}

async function loadRevenueData() {
  loadDailyRevenue();
  loadMonthlyRevenue();
  loadAllTransactions();
}

async function loadDailyRevenue() {
  try {
    const res = await fetch(`/api/v1/revenue/daily?date=${state.dailyDate}`);
    if (!res.ok) throw new Error('Daily revenue error');
    const d = await res.json();

    document.getElementById('dailyTotalVerified').textContent = fmtUSD(d.total_verified_payments);
    document.getElementById('dailyTxCount').textContent = `${d.successful_transactions_count} successful transactions`;
    document.getElementById('dailyRevPhotobook').textContent = fmtUSD(d.revenue_photobook);
    document.getElementById('dailyRevEaseSelect').textContent = fmtUSD(d.revenue_ease_select);
    document.getElementById('dailyRevCombined').textContent = fmtUSD(d.revenue_combined_packages);
    document.getElementById('dailyRevLifetime').textContent = fmtUSD(d.revenue_lifetime_pro);
  } catch (err) {
    console.error('Daily revenue error:', err);
  }
}

async function loadMonthlyRevenue() {
  const [yearStr, monthStr] = state.monthlyMonth.split('-');
  try {
    const res = await fetch(`/api/v1/revenue/monthly?year=${parseInt(yearStr)}&month=${parseInt(monthStr)}`);
    if (!res.ok) throw new Error('Monthly revenue error');
    const m = await res.json();

    document.getElementById('monthGrossCollections').textContent = fmtUSD(m.total_verified_payments);
    document.getElementById('monthRefunds').textContent = fmtUSD(m.refunds_and_chargebacks);
    document.getElementById('monthProcessingFees').textContent = fmtUSD(m.processing_fees);
    document.getElementById('monthNetRevenue').textContent = fmtUSD(m.net_recorded_revenue);

    document.getElementById('mRevPhotobook').textContent = fmtUSD(m.revenue_by_product.photobook);
    document.getElementById('mRevEaseSelect').textContent = fmtUSD(m.revenue_by_product.ease_select);
    document.getElementById('mRevBundle').textContent = fmtUSD(m.revenue_by_product.combined);
    document.getElementById('mRevLifetime').textContent = fmtUSD(m.revenue_by_product.lifetime);

    document.getElementById('mRevRecurring').textContent = fmtUSD(m.revenue_recurring_subscriptions);
    document.getElementById('mRevLifetimeType').textContent = fmtUSD(m.revenue_lifetime_purchases);
    document.getElementById('mTotalTransactions').textContent = m.total_successful_transactions;
  } catch (err) {
    console.error('Monthly revenue error:', err);
  }
}

async function loadAllTransactions(providerFilter = 'all') {
  try {
    const res = await fetch(`/api/v1/transactions?provider=${providerFilter}`);
    if (!res.ok) throw new Error('Transactions error');
    const txns = await res.json();
    renderAllTransactionsTable(txns);
  } catch (err) {
    console.error('All transactions error:', err);
  }
}

function renderAllTransactionsTable(txns) {
  const tbody = document.getElementById('allTransactionsTableBody');
  if (!tbody) return;

  if (!txns || txns.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No transactions found</td></tr>';
    return;
  }

  tbody.innerHTML = txns.map(t => `
    <tr>
      <td>${escapeHtml(t.transaction_date)}</td>
      <td><strong>${escapeHtml(t.customer_name || 'Customer')}</strong><br><small class="text-muted">${escapeHtml(t.customer_email || t.smart_ease_account_id || '')}</small></td>
      <td>${escapeHtml(t.product_or_package)}</td>
      <td><strong>${fmtUSD(t.amount)}</strong></td>
      <td><strong class="text-emerald">${fmtUSD(t.net_amount)}</strong></td>
      <td><span class="badge ${t.payment_status === 'Verified' ? 'badge-active' : 'badge-danger'}">${escapeHtml(t.payment_status)}</span></td>
      <td><span class="badge badge-neutral">${escapeHtml(t.payment_provider)}</span></td>
      <td><code>${escapeHtml(t.transaction_reference)}</code></td>
    </tr>
  `).join('');
}

// ==========================================
// 7. MODALS CONTROLLER
// ==========================================
function initModals() {
  // Close buttons
  document.getElementById('btnCloseUserDetail')?.addEventListener('click', () => {
    document.getElementById('modalUserDetail').classList.remove('open');
  });

  document.getElementById('btnCloseRecordPayment')?.addEventListener('click', () => {
    document.getElementById('modalRecordPayment').classList.remove('open');
  });
  document.getElementById('btnCancelPayment')?.addEventListener('click', () => {
    document.getElementById('modalRecordPayment').classList.remove('open');
  });

  document.getElementById('btnCloseAddUser')?.addEventListener('click', () => {
    document.getElementById('modalAddUser').classList.remove('open');
  });
  document.getElementById('btnCancelAddUser')?.addEventListener('click', () => {
    document.getElementById('modalAddUser').classList.remove('open');
  });

  document.getElementById('btnCloseEditPackage')?.addEventListener('click', () => {
    document.getElementById('modalEditPackage').classList.remove('open');
  });
  document.getElementById('btnCancelEditPackage')?.addEventListener('click', () => {
    document.getElementById('modalEditPackage').classList.remove('open');
  });

  // Backdrop click-to-close for all modals
  document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) {
        backdrop.classList.remove('open');
      }
    });
  });

  // Helper to dynamically populate and synchronize the Record Payment modal
  const setupRecordPaymentModal = async () => {
    const userSelect = document.getElementById('payUserSelect');
    const pkgSelect = document.getElementById('payPackageSelect');
    const amountInput = document.getElementById('payAmountInput');
    const refInput = document.getElementById('payRefInput');

    if (refInput && !refInput.value) {
      refInput.value = `PAY-${Date.now().toString().slice(-6)}`;
    }

    // Ensure packages are loaded
    if (!state.packages || state.packages.length === 0) {
      try {
        const res = await fetch('/api/v1/packages');
        if (res.ok) state.packages = await res.json();
      } catch (err) {
        console.warn('Could not load packages', err);
      }
    }

    if (pkgSelect && state.packages && state.packages.length > 0) {
      pkgSelect.innerHTML = state.packages.map(p => {
        const lifetime = p.billing_type === 'one_time' || p.billing_interval === 'lifetime';
        return `<option value="${escapeHtml(p.id)}" data-price="0">${escapeHtml(p.name)}${lifetime ? ' (lifetime, no days needed)' : ''}</option>`;
      }).join('');
    }

    // Ensure users are loaded
    if (!state.users || state.users.length === 0) {
      try {
        const res = await fetch('/api/v1/users');
        if (res.ok) state.users = await res.json();
      } catch (err) {
        console.warn('Could not load users', err);
      }
    }

    if (userSelect && state.users && state.users.length > 0) {
      userSelect.innerHTML = '<option value="">Select customer...</option>' + 
        state.users.map(u => `<option value="${escapeHtml(u.id)}">${escapeHtml(u.full_name)} (${escapeHtml(u.smart_ease_account_id || u.email || u.id)})</option>`).join('');
    }

    // Auto-update amount field when package is chosen
    const syncAmount = () => {
      if (!pkgSelect || !amountInput) return;
      const opt = pkgSelect.selectedOptions[0];
      if (opt && opt.dataset.price) {
        amountInput.value = parseFloat(opt.dataset.price).toFixed(2);
      }
    };
    syncAmount();
    pkgSelect?.removeEventListener('change', syncAmount);
    pkgSelect?.addEventListener('change', syncAmount);
  };

  // Open Record Payment Modal
  document.getElementById('btnRecordPayment')?.addEventListener('click', async () => {
    await setupRecordPaymentModal();
    document.getElementById('modalRecordPayment').classList.add('open');
  });

  // Open Add Customer Modal
  document.getElementById('btnAddUserModal')?.addEventListener('click', () => {
    document.getElementById('modalAddUser').classList.add('open');
  });

  // Record Payment Form Submit
  document.getElementById('formRecordPayment')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const userId = document.getElementById('payUserSelect').value;
    const packageId = document.getElementById('payPackageSelect').value;
    const provider = document.getElementById('payProviderSelect').value;
    const amount = provider === 'Admin Grant' ? 0 : (parseFloat(document.getElementById('payAmountInput').value) || 0);
    const daysRaw = document.getElementById('payDaysInput')?.value.trim();
    const days = daysRaw ? parseInt(daysRaw, 10) : null;
    const ref = document.getElementById('payRefInput').value || `PAY-${Date.now().toString().slice(-6)}`;

    if (!userId) {
      showToast('Please select a customer', 'error');
      return;
    }

    try {
      const res = await fetch('/api/v1/subscriptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          package_id: packageId,
          amount: amount,
          days: days,
          payment_provider: provider,
          transaction_reference: ref
        })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to record subscription payment');
      }
      showToast(days ? `Access turned on for ${days} days.` : 'Access turned on.', 'success');
      document.getElementById('modalRecordPayment').classList.remove('open');
      document.getElementById('formRecordPayment').reset();
      loadDashboardData();
      loadUsersData();
      loadSubscriptionsData();
      loadRevenueData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // Add Customer Form Submit
  document.getElementById('formAddUser')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fullName = document.getElementById('addFullName').value;
    const email = document.getElementById('addEmail').value;
    const accountId = document.getElementById('addAccountId').value;
    const prod = document.getElementById('addProductOwned').value;

    try {
      const res = await fetch('/api/v1/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: fullName,
          email: email,
          smart_ease_account_id: accountId,
          product_owned: prod
        })
      });
      if (!res.ok) throw new Error('Failed to register customer');
      showToast(`Customer registered successfully!`, 'success');
      document.getElementById('modalAddUser').classList.remove('open');
      document.getElementById('formAddUser').reset();
      loadUsersData();
      loadDashboardData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // Edit Package Form Submit
  document.getElementById('formEditPackage')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const pkgId = document.getElementById('editPkgId').value;
    const name = document.getElementById('editPkgName').value;
    const price = parseFloat(document.getElementById('editPkgPrice').value);
    const desc = document.getElementById('editPkgDesc').value;

    try {
      const res = await fetch(`/api/v1/packages/${pkgId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, price, description: desc })
      });
      if (!res.ok) throw new Error('Failed to update package');
      showToast('Package pricing updated successfully!', 'success');
      document.getElementById('modalEditPackage').classList.remove('open');
      loadPackagesData();
      loadDashboardData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// Utility: Debounce
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

// Utility: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}


// ==========================================
// LIVE LOGIN ACTIVITY (real-time feed + alerts)
// ==========================================
const loginState = { seen: new Set(), primed: false };

function loginMethodBadge(m) {
  const v = String(m || 'email').toLowerCase();
  if (v.includes('google')) return '<span class="badge badge-blue">Google</span>';
  return '<span class="badge badge-neutral">Email &amp; Password</span>';
}

async function loadRecentLogins() {
  const body = document.getElementById('recentLoginsBody');
  try {
    const res = await fetch('/api/v1/logins/recent?limit=25');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const logins = data.logins || [];

    // Toast + notification for brand-new logins (skip the first load)
    const fresh = logins.filter(l => l.id && !loginState.seen.has(l.id));
    logins.forEach(l => l.id && loginState.seen.add(l.id));
    if (loginState.primed && fresh.length) {
      fresh.slice(0, 3).forEach(l => {
        showToast(`🔐 New login: ${escapeHtml(l.full_name || l.email)} signed in to ${escapeHtml(l.product || 'app')}`, 'success');
        try {
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('Smart Ease – New Login', { body: `${l.full_name || l.email} signed in to ${l.product || 'app'} on ${l.device_hostname || 'a device'}` });
          }
        } catch (e) {}
      });
    }
    loginState.primed = true;

    const lbl = document.getElementById('loginCountLabel');
    if (lbl) lbl.textContent = `${logins.length} recent sign-ins`;
    if (!body) return;
    if (!logins.length) {
      body.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No logins recorded yet. New sign-ins will appear here automatically.</td></tr>';
      return;
    }
    body.innerHTML = logins.map(l => `
      <tr${fresh.some(f => f.id === l.id) ? ' style="background:rgba(16,185,129,0.08);"' : ''}>
        <td>${escapeHtml(l.login_time || '-')}</td>
        <td><strong>${escapeHtml(l.full_name || '-')}</strong></td>
        <td>${escapeHtml(l.email || '-')}</td>
        <td>${escapeHtml(l.product || '-')}</td>
        <td><code>${escapeHtml(l.device_hostname || '-')}</code></td>
        <td>${loginMethodBadge(l.auth_method)}</td>
        <td>${l.alert_dispatched ? '<span class="badge badge-active">✓ Notified</span>' : '<span class="badge badge-neutral">-</span>'}</td>
      </tr>`).join('');
  } catch (err) {
    if (body && !loginState.primed) {
      body.innerHTML = `<tr><td colspan="7" class="text-center text-muted">Could not load login activity (${escapeHtml(err.message)}). Retrying…</td></tr>`;
    }
  }
}

async function loadUserLoginHistory(userId) {
  const box = document.getElementById('mudLoginHistory');
  if (!box) return;
  box.innerHTML = '<h4 style="font-size:0.9rem; margin-bottom:8px; color:#fff;">Login History</h4><div class="text-muted" style="font-size:0.85rem;">Loading…</div>';
  try {
    const res = await fetch(`/api/v1/users/${encodeURIComponent(userId)}/logins?limit=15`);
    const data = await res.json();
    const rows = data.logins || [];
    box.innerHTML = `
      <h4 style="font-size:0.9rem; margin-bottom:8px; color:#fff;">Login History</h4>
      <div class="table-responsive"><table class="data-table">
        <thead><tr><th>Time (UTC)</th><th>App</th><th>Device</th><th>Method</th><th>Alert</th></tr></thead>
        <tbody>${rows.length ? rows.map(l => `
          <tr>
            <td>${escapeHtml(l.login_time || '-')}</td>
            <td>${escapeHtml(l.product || '-')}</td>
            <td><code>${escapeHtml(l.device_hostname || '-')}</code></td>
            <td>${loginMethodBadge(l.auth_method)}</td>
            <td>${l.alert_dispatched ? '<span class="badge badge-active">✓ Emailed</span>' : '-'}</td>
          </tr>`).join('') : '<tr><td colspan="5" class="text-center text-muted">No logins recorded for this customer yet</td></tr>'}
        </tbody></table></div>`;
  } catch (e) {
    box.innerHTML = '';
  }
}

// Hook login history into the customer profile modal
if (typeof openUserDetail === 'function') {
  const _origOpenUserDetail = openUserDetail;
  openUserDetail = async function (userId) {
    await _origOpenUserDetail(userId);
    loadUserLoginHistory(userId);
  };
}

document.addEventListener('DOMContentLoaded', () => {
  try {
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  } catch (e) {}
  loadRecentLogins();
  setInterval(loadRecentLogins, 10000); // poll every 10s for new sign-ins
});
