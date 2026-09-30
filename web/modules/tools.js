/**
 * modules/tools.js
 * Tools, Jobs, Reports, tool modal helpers, and health check.
 */
(function (global) {
    'use strict';

    const ToolsMethods = {
        async loadTools() {
            try {
                this.tools = await API.tools();
            } catch (err) {
                console.error('Failed to load tools:', err);
            }
        },
        async loadJobs() {
            try {
                const jobs = await API.jobs();
                this.jobs = jobs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
            } catch (err) {
                console.error('Failed to load jobs:', err);
            }
        },
        async deleteJob(jobId) {
            try {
                await API.deleteJob(jobId);
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
                await Promise.all(ids.map(id => API.deleteJob(id)));
                this.selectedJobIds = [];
                await this.loadJobs();
            } catch (err) {
                alert('Could not delete selected jobs: ' + (err.response?.data?.detail || err.message));
            }
        },
        async clearJobs() {
            if (!this.jobs.length || !confirm('Clear all tool job history? This cannot be undone.')) return;
            try {
                await API.clearAllJobs();
                this.selectedJobIds = [];
                await this.loadJobs();
            } catch (err) {
                alert('Could not clear job history: ' + (err.response?.data?.detail || err.message));
            }
        },
        async loadReports() {
            try {
                this.reports = await API.reports();
            } catch (err) {
                console.error('Failed to load reports:', err);
            }
        },
        async runToolRegression() {
            this.regressionRunning = true;
            try {
                this.regressionResult = await API.runToolRegression();
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
                const data = await API.executeTool({
                    tool_name: tool.name,
                    arguments: args,
                    silent: false
                });
                this.currentTab = 'jobs';
                this.loadJobs();
                alert('Tool execution queued: ' + data.job_id.slice(0, 8));
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
            API.health()
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
