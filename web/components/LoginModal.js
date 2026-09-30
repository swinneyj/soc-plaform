/**
 * components/LoginModal.js
 * Session login dialog (SESSION_AUTH_PLAN.md Piece C).
 *
 * Rendered only in AUTH_MODE=session when no live session exists. Emits
 * 'logged-in' with the {user, role, csrf_token} payload on success; the root
 * app stores it and window.__SOC_CSRF__ so utils/auth.js stamps every
 * subsequent mutation. Never touches axios directly — calls go through the
 * API layer (design rule #2).
 */
window.LoginModal = {
    props: ['show'],
    emits: ['close', 'logged-in'],
    data() {
        return {
            username: '',
            password: '',
            error: '',
            busy: false
        };
    },
    template: `
        <div v-if="show" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/70">
            <div class="bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-w-sm p-6">
                <h2 class="text-lg font-bold mb-1">Sign in</h2>
                <p class="text-xs text-gray-400 mb-4">SOC Platform operator access</p>
                <form @submit.prevent="submit">
                    <label class="block text-xs text-gray-400 mb-1">Username</label>
                    <input v-model="username" autofocus autocomplete="username"
                           class="w-full mb-3 px-3 py-2 bg-gray-900 border border-gray-700 rounded text-sm focus:outline-none focus:border-blue-500" />
                    <label class="block text-xs text-gray-400 mb-1">Password</label>
                    <input v-model="password" type="password" autocomplete="current-password"
                           class="w-full mb-3 px-3 py-2 bg-gray-900 border border-gray-700 rounded text-sm focus:outline-none focus:border-blue-500" />
                    <p v-if="error" class="text-xs text-red-400 mb-3">{{ error }}</p>
                    <div class="flex justify-end space-x-2">
                        <button type="button" @click="$emit('close')"
                                class="px-3 py-2 text-sm bg-gray-700 hover:bg-gray-600 rounded">Cancel</button>
                        <button type="submit" :disabled="busy || !username || !password"
                                class="px-3 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded font-semibold">
                            {{ busy ? 'Signing in…' : 'Sign in' }}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    `,
    methods: {
        async submit() {
            this.busy = true;
            this.error = '';
            try {
                const result = await API.sessionLogin({ username: this.username, password: this.password });
                this.$emit('logged-in', result);
            } catch (err) {
                this.error = (err.response && err.response.status === 401)
                    ? 'Invalid username or password'
                    : 'Sign-in failed: ' + (err.message || 'unknown error');
            } finally {
                this.busy = false;
            }
        }
    }
};
