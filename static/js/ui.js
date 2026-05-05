const UI = {
    toastTimer: null,

    showLogin() {
        document.getElementById('login-view').classList.remove('hidden');
        document.getElementById('app-view').classList.add('hidden');
    },

    showDashboard() {
        document.getElementById('login-view').classList.add('hidden');
        document.getElementById('app-view').classList.remove('hidden');
    },

    switchTab(tabId) {
        document.querySelectorAll('#main-nav .nav-item').forEach(nav => {
            nav.classList.toggle('active', nav.getAttribute('data-tab') === tabId);
        });
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('hidden', content.id !== tabId);
        });
    },

    renderHeader(state) {
        const current = state.currentUser || {};
        const scoped = state.scopedUser || current;
        
        document.getElementById('sidebar-user-name').innerText = current.full_name || current.username || 'Dashboard';
        document.getElementById('sidebar-user-role').innerText = (current.role || 'user').toUpperCase();
        document.getElementById('current-user-box').innerText = `${current.full_name || '-'} (${current.username || '-'})`;
        document.getElementById('scoped-user-box').innerText = `${scoped.full_name || '-'} (${scoped.username || '-'})`;
        document.getElementById('selected-user-label').innerText = scoped.username || 'Profile';
        document.getElementById('api-key-display').innerText = scoped.api_key || '-';

        const checkAdmin = document.getElementById('check-use-admin');
        const configPill = document.getElementById('config-status-pill');
        if (checkAdmin) {
            checkAdmin.checked = !!scoped.use_admin_numbers;
            configPill.innerText = scoped.use_admin_numbers ? 'Using Admin Numbers' : 'Using Self Numbers';
            configPill.classList.toggle('ok', !scoped.use_admin_numbers);
            configPill.classList.toggle('bad', !!scoped.use_admin_numbers);
        }

        const adminWrap = document.getElementById('admin-scope-wrap');
        const navAdmin = document.getElementById('nav-admin');
        if (current.role === 'admin') {
            adminWrap.classList.remove('hidden');
            if (navAdmin) navAdmin.classList.remove('hidden');
            const select = document.getElementById('scope-user-select');
            select.innerHTML = (state.users || []).map(user => `
                <option value="${user.id}" ${Number(user.id) === Number(state.selectedUserId) ? 'selected' : ''}>
                    ${user.username} (${user.role})
                </option>
            `).join('');
        } else {
            adminWrap.classList.add('hidden');
            if (navAdmin) navAdmin.classList.add('hidden');
        }
    },

    renderCounters(counters) {
        this.animateCount('count-total', counters.total || 0);
        this.animateCount('count-ready', counters.ready || 0);
        this.animateCount('count-sent', counters.sent || 0);
        this.animateCount('count-failed', counters.failed || 0);
    },

    renderChart(mainChart, rows) {
        if (!mainChart) return;
        mainChart.data.labels = rows.map(row => this.formatShortDate(row.date));
        mainChart.data.datasets[0].data = rows.map(row => row.success_count || 0);
        mainChart.data.datasets[1].data = rows.map(row => row.fail_count || 0);
        mainChart.update();
    },

    renderTable(rows) {
        const tbody = document.getElementById('activity-body');
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="loading-row">No numbers found.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map((row, i) => `
            <tr>
                <td>${i + 1}</td>
                <td class="mono">${this.escapeHtml(row.phone_number || '-')}</td>
                <td>${this.badgeFor(row.status)}</td>
                <td>${this.escapeHtml(row.device_id || '-')}</td>
                <td>${this.formatTime(row.updated_at)}</td>
            </tr>
        `).join('');
    },

    renderLogs(rows) {
        const wrap = document.getElementById('live-log-list');
        if (!rows.length) {
            wrap.innerHTML = '<div class="log-card">No live logs yet.</div>';
            return;
        }
        wrap.innerHTML = rows.map(row => `
            <div class="log-card">
                <div class="status-top">
                    <span class="pill ${row.level === 'ERROR' ? 'bad' : row.level === 'SUCCESS' ? 'ok' : ''}">${this.escapeHtml(row.level || 'INFO')}</span>
                    <span class="small">${this.formatTime(row.created_at)}</span>
                </div>
                <div style="margin-top:10px;">${this.escapeHtml(row.message || '')}</div>
                <div class="small" style="margin-top:8px;">Device: ${this.escapeHtml(row.device_id || '-')} | Phone: ${this.escapeHtml(row.phone_number || '-')}</div>
            </div>
        `).join('');
    },

    renderBotStatus(rows) {
        const wrap = document.getElementById('bot-status-list');
        if (!rows.length) {
            wrap.innerHTML = '<div class="status-card">No bot heartbeat yet.</div>';
            return;
        }
        wrap.innerHTML = rows.map(row => `
            <div class="status-card">
                <div class="status-top">
                    <strong>${this.escapeHtml(row.device_id || 'Unknown')}</strong>
                    <span class="pill ${row.bot_status === 'RUNNING' ? 'ok' : ''}">${this.escapeHtml(row.bot_status || 'IDLE')}</span>
                </div>
                <div class="small" style="margin-top:10px;">Last phone: ${this.escapeHtml(row.last_phone || '-')}</div>
                <div style="margin-top:8px;">${this.escapeHtml(row.last_message || 'No message')}</div>
                <div class="small" style="margin-top:8px;">Seen: ${this.formatTime(row.last_seen)}</div>
            </div>
        `).join('');
    },

    renderUsers(users, selectedUserId, onSelect) {
        const tbody = document.getElementById('user-list-body');
        if (!tbody || !users.length) return;
        tbody.innerHTML = users.map(user => `
            <tr>
                <td>
                    <strong>${this.escapeHtml(user.full_name)}</strong>
                    <div class="small">${this.escapeHtml(user.username)}</div>
                </td>
                <td><span class="pill ${user.role === 'admin' ? '' : 'ok'}">${user.role.toUpperCase()}</span></td>
                <td class="mono">${this.escapeHtml(user.api_key)}</td>
                <td>${user.ready_count} ready / ${user.sent_count} sent</td>
                <td>
                    <button class="ghost-btn" onclick="UI.handleSelectUser(${user.id})">Open</button>
                </td>
            </tr>
        `).join('');
        this._onSelectUser = onSelect;
    },

    handleSelectUser(id) {
        if (this._onSelectUser) this._onSelectUser(id);
    },

    showToast(msg, type = 'success') {
        const toast = document.getElementById('toast');
        const toastMsg = document.getElementById('toast-msg');
        const icon = toast.querySelector('i');
        toastMsg.textContent = msg;
        icon.className = type === 'warn' ? 'fas fa-triangle-exclamation' : 'fas fa-check-circle';
        icon.style.color = type === 'warn' ? '#f59e0b' : '#22c55e';
        toast.classList.add('show');
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
    },

    animateCount(id, target) {
        const el = document.getElementById(id);
        if (!el) return;
        const current = parseInt(String(el.innerText).replace(/,/g, ''), 10) || 0;
        if (current === target) { el.innerText = Number(target).toLocaleString(); return; }
        const step = Math.max(1, Math.ceil(Math.abs(target - current) / 12));
        let value = current;
        const timer = setInterval(() => {
            value += target > current ? step : -step;
            if ((target > current && value >= target) || (target < current && value <= target)) {
                value = target; clearInterval(timer);
            }
            el.innerText = Number(value).toLocaleString();
        }, 20);
    },

    badgeFor(status) {
        const value = (status || 'READY').toUpperCase();
        const ok = value === 'SENT' || value === 'READY' || value === 'RUNNING';
        return `<span class="pill ${ok ? 'ok' : 'bad'}">${this.escapeHtml(value)}</span>`;
    },

    formatShortDate(dateStr) {
        if (!dateStr) return '';
        const parts = String(dateStr).split('-');
        return parts.length === 3 ? `${parts[1]}/${parts[2]}` : dateStr;
    },

    formatTime(dateStr) {
        if (!dateStr) return '-';
        return String(dateStr).replace('T', ' ').split('.')[0];
    },

    escapeHtml(v) {
        return String(v).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[m]));
    }
};
