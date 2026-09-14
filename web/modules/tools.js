/**
 * modules/tools.js
 * Tools, Jobs, Reports, tool modal helpers, and health check.
 */
(function (global) {
    'use strict';

    const ToolsMethods = {
        async loadTools() {
            try {
                const res = await axios.get(this.apiUrl + '/tools');
                this.tools = res.data;
            } catch (err) {
                console.error('Failed to load tools:', err);
            }
        },
        async loadJobs() {
            try {
                const res = await axios.get(this.apiUrl + '/jobs');
                this.jobs = res.data.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
            } catch (err) {
                console.error('Failed to load jobs:', err);
            }
        },
        async deleteJob(jobId) {
            try {
                await axios.delete(this.apiUrl + '/jobs/' + encodeURIComponent(jobId));
                this.selectedJobIds = (this.selectedJobIds || []).filter(id => id !== jobId);
                await this.loadJobs();
            } catch (err) {
                alert('Could not delete job: ' + (err.response?.data?.detail || err.message));
            }
        },
        async deleteSelectedJobs() {
            const ids = this.selectedJobIds || [];
            if (!ids.length || !confirm(`Delete ${ids.length} selected job(s)?`)) return;
            try {
                await Promise.all(ids.map(id => axios.delete(this.apiUrl + '/jobs/' + encodeURIComponent(id))));
                this.selectedJobIds = [];
                await this.loadJobs();
            } catch (err) {
                alert('Could not delete selected jobs: ' + (err.response?.data?.detail || err.message));
            }
        },
        async clearJobs() {
            if (!this.jobs.length || !confirm('Clear all tool job history? This cannot be undone.')) return;
            try {
                await axios.delete(this.apiUrl + '/jobs');
                this.selectedJobIds = [];
                await this.loadJobs();
            } catch (err) {
                alert('Could not clear job history: ' + (err.response?.data?.detail || err.message));
            }
        },
        async loadReports() {
            try {
                const res = await axios.get(this.apiUrl + '/reports');
                this.reports = res.data;
            } catch (err) {
                console.error('Failed to load reports:', err);
            }
        },
        async runToolRegression() {
            this.regressionRunning = true;
            try {
                const res = await axios.post(this.apiUrl + '/tools/regression');
                this.regressionResult = res.data;
            } catch (err) {
                this.regressionResult = { error: err.response?.data?.detail || err.message };
            } finally {
                this.regressionRunning = false;
            }
        },
        async executeTool(tool) {
            const args = {};
            if (tool.arguments) {
                for (const arg of tool.arguments) {
                    const key = tool.name + '_' + arg.flag;
                    if (this.toolArgs[key]) {
                        args[arg.flag.replace('--', '')] = this.toolArgs[key];
                    }
                }
            }

            try {
                const res = await axios.post(this.apiUrl + '/execute', {
                    tool_name: tool.name,
                    arguments: args,
                    silent: false
                });
                this.currentTab = 'jobs';
                this.loadJobs();
                alert('Tool execution queued: ' + res.data.job_id.slice(0, 8));
            } catch (err) {
                alert('Error: ' + err.response?.data?.detail || err.message);
            }
        },
        selectToolForExecution(tool) {
            this.selectedToolForExecution = tool;
        },
        closeToolModal() {
            this.selectedToolForExecution = null;
        },
        changeTab(tab) {
            this.currentTab = tab;
            this.selectedToolForExecution = null;
        },

        checkHealth() {
            axios.get(this.apiUrl + '/health')
                .then(() => {
                    this.apiHealthy = true;
                    const el = document.getElementById('status');
                    if (el) { el.textContent = '●'; el.style.color = '#4ade80'; }
                })
                .catch(() => {
                    this.apiHealthy = false;
                    const el = document.getElementById('status');
                    if (el) { el.textContent = '●'; el.style.color = '#ef4444'; }
                });
        }
    };

    global.ToolsMethods = ToolsMethods;

})(typeof window !== 'undefined' ? window : globalThis);
