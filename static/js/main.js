let mainChart = null;
let refreshTimer = null;

const state = {
    authenticated: false,
    currentUser: null,
    scopedUser: null,
    selectedUserId: null,
    users: []
};

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    bindEvents();
    bootstrapApp();
});

function bindEvents() {
    document.getElementById('btn-login').addEventListener('click', handleLogin);
    document.getElementById('btn-logout').addEventListener('click', handleLogout);
    document.getElementById('btn-import').addEventListener('click', handleImport);
    document.getElementById('btn-clear').addEventListener('click', handleClear);
    document.getElementById('btn-create-user').addEventListener('click', handleCreateUser);
    document.getElementById('btn-reset-key').addEventListener('click', handleResetKey);
    document.getElementById('check-use-admin').addEventListener('change', handleUpdateConfig);
    
    document.querySelectorAll('#main-nav .nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tabId = item.getAttribute('data-tab');
            if (tabId) UI.switchTab(tabId);
        });
    });

    document.getElementById('scope-user-select').addEventListener('change', e => {
        state.selectedUserId = e.target.value ? Number(e.target.value) : null;
        refreshStats();
    });
}

async function bootstrapApp() {
    try {
        const data = await API.bootstrap();
        if (!data.authenticated) {
            UI.showLogin();
            return;
        }
        applyPayload(data);
        UI.showDashboard();
    } catch (err) {
        UI.showLogin();
    }
}

async function handleLogin() {
    const btn = document.getElementById('btn-login');
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value.trim();
    if (!username || !password) {
        UI.showToast('Username ar password dorkar', 'warn');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>Logging in...</span>';
    try {
        const data = await API.login(username, password);
        applyPayload(data);
        UI.showDashboard();
        UI.showToast('Login successful');
    } catch (err) {
        UI.showToast(err.message, 'warn');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-right-to-bracket"></i><span>Login</span>';
    }
}

async function handleLogout() {
    await API.logout();
    clearInterval(refreshTimer);
    state.authenticated = false;
    UI.showLogin();
}

async function handleUpdateConfig() {
    const checked = document.getElementById('check-use-admin').checked;
    const targetId = state.selectedUserId || state.currentUser.id;
    try {
        const data = await API.updateConfig(targetId, checked);
        state.scopedUser = data.user;
        UI.renderHeader(state);
        UI.showToast(`Config updated: ${checked ? 'Admin Pool' : 'Self Pool'}`);
    } catch (err) {
        UI.showToast(err.message, 'warn');
        document.getElementById('check-use-admin').checked = !checked;
    }
}

async function refreshStats() {
    if (!state.authenticated) return;
    const refreshIcon = document.getElementById('refresh-icon');
    if (refreshIcon) refreshIcon.classList.add('fa-spin');
    try {
        const data = await API.getStats(state.selectedUserId);
        applyPayload(data);
    } catch (err) {
        UI.showToast(err.message, 'warn');
    } finally {
        if (refreshIcon) refreshIcon.classList.remove('fa-spin');
    }
}

function applyPayload(data) {
    state.authenticated = true;
    state.currentUser = data.current_user || state.currentUser;
    state.scopedUser = data.scoped_user || state.scopedUser;
    state.users = data.users || state.users;
    
    if (state.currentUser && state.currentUser.role !== 'admin') {
        state.selectedUserId = state.currentUser.id;
    } else if (state.scopedUser) {
        state.selectedUserId = state.scopedUser.id;
    }

    UI.renderHeader(state);
    if (data.counters) UI.renderCounters(data.counters);
    if (data.chart_data) UI.renderChart(mainChart, data.chart_data);
    if (data.recent_activity) UI.renderTable(data.recent_activity);
    if (data.live_logs) UI.renderLogs(data.live_logs);
    if (data.bot_status) UI.renderBotStatus(data.bot_status);
    if (state.users.length) UI.renderUsers(state.users, state.selectedUserId, (id) => {
        state.selectedUserId = id;
        document.getElementById('scope-user-select').value = id;
        UI.switchTab('tab-dashboard');
        refreshStats();
    });
    
    if (!refreshTimer) refreshTimer = setInterval(refreshStats, 5000);
}

async function handleImport() {
    const text = document.getElementById('bulk-numbers').value.trim();
    if (!text) { UI.showToast('Numbers paste korun', 'warn'); return; }
    try {
        const data = await API.importNumbers(text, state.selectedUserId);
        document.getElementById('bulk-numbers').value = '';
        UI.showToast(`${data.added} number import hoyeche`);
        refreshStats();
    } catch (err) { UI.showToast(err.message, 'warn'); }
}

async function handleClear() {
    if (!confirm('Data clear korte chan?')) return;
    try {
        await API.clearData(state.selectedUserId);
        UI.showToast('Data cleared');
        refreshStats();
    } catch (err) { UI.showToast(err.message, 'warn'); }
}

async function handleCreateUser() {
    const payload = {
        username: document.getElementById('new-username').value.trim(),
        full_name: document.getElementById('new-full-name').value.trim(),
        password: document.getElementById('new-password').value.trim(),
        role: document.getElementById('new-role').value
    };
    try {
        const data = await API.createUser(payload);
        UI.showToast(`User ${data.user.username} created`);
        refreshStats();
    } catch (err) { UI.showToast(err.message, 'warn'); }
}

async function handleResetKey() {
    if (!state.selectedUserId) return;
    try {
        await API.resetKey(state.selectedUserId);
        UI.showToast('API Key reset successful');
        refreshStats();
    } catch (err) { UI.showToast(err.message, 'warn'); }
}

function copyApiKey() {
    const key = document.getElementById('api-key-display').innerText;
    if (!key || key === '-') return;
    navigator.clipboard.writeText(key).then(() => {
        UI.showToast('API Key copied to clipboard');
    }).catch(err => {
        UI.showToast('Failed to copy', 'warn');
    });
}

function initChart() {
    const canvas = document.getElementById('mainChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    mainChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                { label: 'Sent', data: [], borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', fill: true, tension: 0.3 },
                { label: 'Failed', data: [], borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.1)', fill: true, tension: 0.3 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#cbd5e1' } } },
            scales: {
                x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' }, beginAtZero: true }
            }
        }
    });
}
