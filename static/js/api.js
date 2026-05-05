const API = {
    async bootstrap() {
        const res = await fetch('/web/api/stats'); // Stats also check auth implicitly
        if (res.status === 401) return { authenticated: false };
        const data = await res.json();
        return { authenticated: true, ...data };
    },

    async login(username, password) {
        const res = await fetch('/web/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Login failed');
        return data;
    },

    async logout() {
        await fetch('/web/api/logout', { method: 'POST' });
    },

    async getStats(selectedUserId = null) {
        const query = selectedUserId ? `?user_id=${selectedUserId}` : '';
        const res = await fetch(`/web/api/stats${query}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Refresh failed');
        return data;
    },

    async updateConfig(targetId, useAdmin) {
        const res = await fetch(`/web/api/users/${targetId}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ use_admin_numbers: useAdmin })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Update failed');
        return data;
    },

    async importNumbers(text, targetUserId = null) {
        const body = { numbers: text };
        if (targetUserId) body.user_id = targetUserId;
        const res = await fetch('/web/api/add-numbers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Import failed');
        return data;
    },

    async clearData(targetUserId = null) {
        const body = {};
        if (targetUserId) body.user_id = targetUserId;
        const res = await fetch('/web/api/clear-numbers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Clear failed');
        return data;
    },

    async createUser(payload) {
        const res = await fetch('/web/api/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'User create failed');
        return data;
    },

    async resetKey(userId) {
        const res = await fetch(`/web/api/admin/users/${userId}/reset-key`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'API key reset failed');
        return data;
    }
};
