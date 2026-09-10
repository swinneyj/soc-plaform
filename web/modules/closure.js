/**
 * modules/closure.js
 * Closure Notes domain methods.
 */
(function (global) {
    'use strict';

    const ClosureMethods = {
        onCaseSelected() {
            this.closureForm.ruleId = '';
            this.closureForm.fieldValues = {};
            this.selectedRule = null;
             this.closureSuggestedRuleName = '';
             this.closureSuggestedDisposition = '';

            // Load source notable details for this case so we can
            // prepopulate closure fields (e.g., host, user) from
            // the parsed notable fields.
            const caseId = this.closureForm.caseId;
            this.closureSourceNotable = null;
            if (!caseId) {
                return;
            }

            // Suggest a default disposition based on the triage verdict.
            const triageCase = this.triageData.find(c => c.case_id === caseId);
            if (triageCase) {
                const verdict = (triageCase.verdict || '').toLowerCase();
                if (verdict === 'malicious') {
                    this.closureForm.disposition = 'True Positive';
                    this.closureSuggestedDisposition = 'True Positive';
                } else if (verdict === 'benign') {
                    this.closureForm.disposition = 'Benign Positive';
                    this.closureSuggestedDisposition = 'Benign Positive';
                } else {
                    this.closureForm.disposition = 'Undetermined';
                    this.closureSuggestedDisposition = 'Undetermined';
                }
            } else {
                this.closureForm.disposition = 'Undetermined';
                this.closureSuggestedDisposition = 'Undetermined';
            }

            axios
                .get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/notable')
                .then(res => {
                    this.closureSourceNotable = res.data;

                    // Choose the most appropriate rule for closure based on
                    // the triage case's rule_name when none is selected yet.
                    if (!this.closureForm.ruleId && triageCase && this.availableRules.length) {
                        const triageRuleName = (triageCase.rule_name || '').toLowerCase().trim();
                        let matchingRule = this.availableRules.find(
                            r => (r.rule_name || '').toLowerCase().trim() === triageRuleName
                        );
                        if (!matchingRule && triageRuleName) {
                            matchingRule = this.availableRules.find(r => {
                                const name = (r.rule_name || '').toLowerCase().trim();
                                return !!name && (triageRuleName.includes(name) || name.includes(triageRuleName));
                            });
                        }
                        if (matchingRule) {
                            this.closureForm.ruleId = matchingRule.rule_id;
                            this.closureSuggestedRuleName = matchingRule.rule_name;
                            this.onRuleSelected();
                        }
                    }
                })
                .catch(err => {
                    console.error('Failed to load source notable for closure form:', err);
                    this.closureSourceNotable = null;
                });
        },
        onRuleSelected() {
            this.selectedRule = this.availableRules.find(r => r.rule_id === this.closureForm.ruleId) || null;
            this.closureForm.fieldValues = {};
            if (this.selectedRule) {
                const sourceFields = (this.closureSourceNotable && this.closureSourceNotable.fields) || {};

                const aliasMap = {
                    host: ['host', 'destination_nt_hostname', 'destination'],
                    destination: ['destination', 'destination_ip'],
                    user: ['user', 'username', 'user_identity'],
                    account: ['user', 'username', 'user_identity'],
                };

                for (const field of this.selectedRule.required_closure_fields) {
                    let value = '';

                    // Direct match on field name
                    if (Object.prototype.hasOwnProperty.call(sourceFields, field)) {
                        value = sourceFields[field];
                    } else {
                        // Fallback to simple aliases for common closure fields
                        const aliases = aliasMap[field] || [];
                        for (const key of aliases) {
                            if (sourceFields[key]) {
                                value = sourceFields[key];
                                break;
                            }
                        }
                    }

                    // Leave fields like 'justification' empty when there
                    // is no meaningful value in the source notable.
                    this.closureForm.fieldValues[field] = value || '';
                }
            }
        },
        async generateClosureNote(force = false) {
            this.closureGenerating = true;
            try {
                const res = await axios.post(this.apiUrl + '/db/closure-note', {
                    rule_id: this.closureForm.ruleId,
                    case_id: this.closureForm.caseId,
                    field_values: this.closureForm.fieldValues,
                    analyst_notes: this.closureForm.analystNotes,
                    disposition: this.closureForm.disposition,
                    force_closure: force
                });
                if (res.data && res.data.blocked) {
                    const blockersList = (res.data.readiness && res.data.readiness.blockers) || [];
                    const nl = String.fromCharCode(10);
                    const msg = 'Investigation closure criteria not yet fully met:' + nl + nl +
                        '- ' + blockersList.join(nl + '- ') + nl + nl +
                        'Do you want to override and generate the closure note anyway?';
                    const proceed = confirm(msg);
                    if (proceed) {
                        return this.generateClosureNote(true);
                    }
                    return;
                }
                this.closureResult = res.data;
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            } finally {
                this.closureGenerating = false;
            }
        },
        copyToClipboard() {
            navigator.clipboard.writeText(this.closureResult.generated_note);
            alert('Closure note copied to clipboard!');
        },
        downloadClosureNote() {
            const element = document.createElement('a');
            element.setAttribute('href', 'data:text/plain;charset=utf-8,' + encodeURIComponent(this.closureResult.generated_note));
            element.setAttribute('download', this.closureForm.caseId + '_closure_note.txt');
            element.style.display = 'none';
            document.body.appendChild(element);
            element.click();
            document.body.removeChild(element);
        }

    };

    global.ClosureMethods = ClosureMethods;

})(typeof window !== 'undefined' ? window : globalThis);
