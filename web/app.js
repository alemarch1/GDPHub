const { createApp, ref, reactive, computed, onMounted, nextTick, watch } = Vue;

const useSortableTable = (dataRef) => {
    const sortKey = ref('');
    const sortAsc = ref(true);

    const sortBy = (key) => {
        if (sortKey.value === key) {
            sortAsc.value = !sortAsc.value;
        } else {
            sortKey.value = key;
            sortAsc.value = true;
        }
    };

    const sortedData = computed(() => {
        const key = sortKey.value;
        const asc = sortAsc.value;
        const rawData = dataRef.value || [];
        
        if (!key || !Array.isArray(rawData) || rawData.length === 0) {
            return rawData;
        }

        return [...rawData].sort((a, b) => {
            let valA = a[key] ?? '';
            let valB = b[key] ?? '';
            
            valA = String(valA).toLowerCase();
            valB = String(valB).toLowerCase();
            
            if (valA < valB) return asc ? -1 : 1;
            if (valA > valB) return asc ? 1 : -1;
            return 0;
        });
    });

    return { sortKey, sortAsc, sortBy, sortedData };
};




const app = createApp({
    setup() {
        const currentLang = ref('en');
        const translations = ref({});

        const loadTranslations = async () => {
            try {
                const res = await fetch('/locales/' + currentLang.value + '.json');
                translations.value = await res.json();
            } catch (err) {
                console.error("Failed to load translations:", err);
            }
        };

        const t = (key) => translations.value[key] || key;

        const isSidebarOpen = ref(false);

        const currentView = ref('pipeline-view');
        const viewTitle = computed(() => {
            const map = {
                'pipeline-view': 'nav_pipeline',
                'ropa-view': 'nav_ropa',
                'dashboard-view': 'nav_dashboard',
                'lifecycle-view': 'nav_lifecycle',
                'config-view': 'nav_config'
            };
            return t(map[currentView.value]);
        });
        
        const setView = (v) => { 
            currentView.value = v; 
            isSidebarOpen.value = false;
        };

        // Configuration State
        const config = reactive({
            active_source: 'gmail',
            input_folder: '',
            database_folder: '',
            log_folder: '',
            log_level: 'INFO',
            mail: { client_id: '', client_secret: '', query: '', max_emails: 50, import_override_days: 0, delete_after_processing: false, import_override_ignore_processed: false },
            ext: { tesseract_path: '', max_workers: 12 },
            cls: { ollama_url: 'http://localhost:11434', ollama_model_default: '', title_max_length: 500, text_max_length: 1500, timeout_seconds: 30, api_request_timeout: 25, options: { temperature: 0.2, num_ctx: 4096, top_p: 0.9, top_k: 40, num_predict: 64 } },
            ropa: { ropa_folder: '' },
            outlook: { client_id: '', tenant_id: 'common', query_filter: 'isRead eq false', max_emails: 50, import_override_days: 0, delete_after_processing: false, import_override_ignore_processed: false }
        });

        // Toasts
        const toasts = ref([]);
        const showToast = (message, type = "info") => {
            const id = Date.now() + Math.random();
            toasts.value.push({ id, message, type });
            setTimeout(() => {
                toasts.value = toasts.value.filter(t => t.id !== id);
            }, 3300);
        };

        const loadConfiguration = async () => {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                config.active_source = data.active_source || 'gmail';
                config.input_folder = data.input_folder || '';
                config.database_folder = data.database_folder || '';
                config.log_folder = data.log_folder || '';
                config.log_level = data.log_level || 'INFO';

                const gmail = data['0_extract_mail.py'] || {};
                const gmailAuth = data['0_extract_mail_gmail_auth'] || {};
                config.mail.client_id = gmailAuth.client_id || '';
                config.mail.client_secret = gmailAuth.client_secret || '';
                config.mail.query = gmail.query || '';
                config.mail.max_emails = gmail.max_emails || 50;
                config.mail.import_override_days = gmail.import_override_days || 0;
                config.mail.delete_after_processing = !!gmail.delete_after_processing;
                config.mail.import_override_ignore_processed = !!gmail.import_override_ignore_processed;

                const ext = data['extract_text.py'] || {};
                config.ext.tesseract_path = ext.tesseract_path || '';
                config.ext.max_workers = ext.max_workers || 12;

                const cls = data['classify_text.py'] || {};
                config.cls.ollama_url = cls.ollama_url || 'http://localhost:11434';
                config.cls.ollama_model_default = cls.ollama_model_default || '';
                config.cls.title_max_length = cls.title_max_length || 500;
                config.cls.text_max_length = cls.text_max_length || 1500;
                config.cls.timeout_seconds = cls.timeout_seconds || 30;
                config.cls.api_request_timeout = cls.api_request_timeout || 25;
                const opts = cls.ollama_options || {};
                config.cls.options.num_predict = opts.num_predict || 64;
                config.cls.options.temperature = opts.temperature || 0.2;
                config.cls.options.num_ctx = opts.num_ctx || 4096;
                config.cls.options.top_p = opts.top_p || 0.9;
                config.cls.options.top_k = opts.top_k || 40;

                const ropa = data['extract_ROPA.py'] || {};
                config.ropa.ropa_folder = ropa.ropa_folder || '';

                const outlook = data['0_extract_mail_outlook'] || {};
                config.outlook.client_id = outlook.client_id || '';
                config.outlook.tenant_id = outlook.tenant_id || 'common';
                config.outlook.query_filter = outlook.query_filter || 'isRead eq false';
                config.outlook.max_emails = outlook.max_emails || 50;
                config.outlook.import_override_days = outlook.import_override_days || 0;
                config.outlook.delete_after_processing = !!outlook.delete_after_processing;
                config.outlook.import_override_ignore_processed = !!outlook.import_override_ignore_processed;
            } catch(e) {
                showToast("Error loading config: " + e.message);
            }
        };

        const saveConfiguration = async () => {
             const dataToSave = {
                active_source: config.active_source,
                input_folder: config.input_folder,
                database_folder: config.database_folder,
                log_folder: config.log_folder,
                log_level: config.log_level,
                "0_extract_mail_gmail_auth": { client_id: config.mail.client_id, client_secret: config.mail.client_secret },
                "0_extract_mail.py": { 
                    query: config.mail.query, 
                    max_emails: config.mail.max_emails, 
                    import_override_days: config.mail.import_override_days,
                    delete_after_processing: config.mail.delete_after_processing,
                    import_override_ignore_processed: config.mail.import_override_ignore_processed
                },
                "extract_text.py": { ...config.ext },
                "classify_text.py": {
                    ollama_url: config.cls.ollama_url,
                    ollama_model_default: config.cls.ollama_model_default,
                    title_max_length: config.cls.title_max_length,
                    text_max_length: config.cls.text_max_length,
                    timeout_seconds: config.cls.timeout_seconds,
                    api_request_timeout: config.cls.api_request_timeout,
                    ollama_options: { ...config.cls.options }
                },
                "extract_ROPA.py": { ...config.ropa },
                "0_extract_mail_outlook": { ...config.outlook }
            };
            try {
                const req = await fetch('/api/config', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(dataToSave)
                });
                if(req.ok) {
                    showToast(currentLang.value === 'it' ? "Configurazione Salvata!" : "Config Successfully Saved!");
                    loadModels();
                }
            } catch(e) {
                showToast("Failed to save config.");
            }
        };

        const saveInputFolder = async () => {
             try {
                await fetch('/api/config', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active_source: config.active_source, input_folder: config.input_folder })
                });
            } catch(e) {}
        };

        const handleBrowse = async (field, event) => {
            const btn = event.currentTarget;
            const oldIcon = btn.innerText;
            btn.innerText = "⏳";
            btn.style.opacity = "0.7";

            let pathKey = field.replace('config.', '');
            let currentPath = pathKey.split('.').reduce((o, i) => o[i], config) || '';

            try {
                const res = await fetch(`/api/utils/browse-folder?current_path=${encodeURIComponent(currentPath)}`);
                if (!res.ok) throw new Error("API Connection Error");
                const data = await res.json();
                if (data.path) {
                    const keys = pathKey.split('.');
                    if (keys.length === 1) config[keys[0]] = data.path;
                    else config[keys[0]][keys[1]] = data.path;
                    
                    showToast(currentLang.value === 'it' ? "Percorso aggiornato!" : "Path updated!");
                    if (pathKey === 'input_folder') saveInputFolder();
                }
            } catch (err) {
                showToast("Error: " + err.message);
            } finally {
                btn.innerText = oldIcon;
                btn.style.opacity = "1";
            }
        };

        // Execution & Pipeline
        const processState = ref('stopped'); 
        const canStop = computed(() => processState.value === 'running' || processState.value === 'paused');
        const canPause = computed(() => processState.value === 'running');
        const canResume = computed(() => processState.value === 'paused');

        const controlProcess = async (action, state) => { await fetch(`/api/control/${action}`, { method: 'POST' }); processState.value = state; };
        const stopProcess = () => controlProcess('stop', 'stopped');
        const pauseProcess = () => controlProcess('pause', 'paused');
        const resumeProcess = () => controlProcess('resume', 'running');

        const terminalOutput = ref("");
        const terminalScrollRef = ref(null);
        
        watch(terminalOutput, () => {
            nextTick(() => { if (terminalScrollRef.value) terminalScrollRef.value.scrollTop = terminalScrollRef.value.scrollHeight; });
        });

        const replaceLastTerminalLine = (text) => {
            let val = terminalOutput.value;
            if (val.endsWith('\n')) val = val.slice(0, -1);
            const lastNewlineIdx = val.lastIndexOf('\n');
            if (lastNewlineIdx === -1) terminalOutput.value = text + '\n';
            else terminalOutput.value = val.substring(0, lastNewlineIdx + 1) + text + '\n';
        };

        const showPipelineProgress = ref(false);
        const pipelineProgressPercent = ref(0);
        const isTerminalCollapsed = ref(true);
        const scriptStatuses = reactive({
            '0_extract_mail.py': 'idle',
            '1_extract_text.py': 'idle',
            '2_classify_text.py': 'idle',
            '4_identify_ROPA.py': 'idle'
        });
        const pipelineStartTime = ref(null);
        const pipelineElapsedTime = ref("00:00");
        const pipelineRemainingTime = ref("--:--");
        let pipelineTimer = null;

        const formatTime = (seconds) => {
            if (isNaN(seconds) || seconds < 0) return "--:--";
            const m = Math.floor(seconds / 60).toString().padStart(2, '0');
            const s = (seconds % 60).toString().padStart(2, '0');
            return `${m}:${s}`;
        };

        const models = ref([]);
        const classifyModel = ref('');
        const identifyModel = ref('');
        const classifyNoThink = ref(false);
        const identifyNoThink = ref(false);

        const loadModels = async () => {
            try {
                const res = await fetch('/api/ollama/models');
                const data = await res.json();
                if(data.models) models.value = data.models;
                if(data.default) {
                    classifyModel.value = data.default;
                    identifyModel.value = data.default;
                }
            } catch(e) {}
        };

        const executeScript = async (scriptName, dynamicArgs) => {
            let args = [];
            if (dynamicArgs) {
                if (scriptName.includes("classify")) {
                    args.push("--run-all", "--model", classifyModel.value);
                    if (classifyNoThink.value) args.push("--no-think");
                }
                if (scriptName.includes("identify_ROPA")) {
                    args.push("--model", identifyModel.value);
                    if (identifyNoThink.value) args.push("--no-think");
                }
            }
            terminalOutput.value = `>>> Launching ${scriptName}...\n`;
            scriptStatuses[scriptName] = 'running';
            processState.value = 'running';
            showPipelineProgress.value = false;
            pipelineStartTime.value = Date.now();
            pipelineElapsedTime.value = "00:00";
            pipelineRemainingTime.value = "--:--";
            if (pipelineTimer) clearInterval(pipelineTimer);
            pipelineTimer = setInterval(() => {
                if (!pipelineStartTime.value) return;
                const elapsedS = Math.floor((Date.now() - pipelineStartTime.value) / 1000);
                pipelineElapsedTime.value = formatTime(elapsedS);
                if (pipelineProgressPercent.value > 2 && pipelineProgressPercent.value < 100) {
                    const totalEstS = elapsedS / (pipelineProgressPercent.value / 100);
                    const remainingS = Math.max(0, Math.floor(totalEstS - elapsedS));
                    pipelineRemainingTime.value = formatTime(remainingS);
                } else if (pipelineProgressPercent.value >= 100) {
                    pipelineRemainingTime.value = "00:00";
                }
            }, 1000);

            try {
                const response = await fetch(`/api/run/${scriptName}`, {
                     method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ args })
                });
                if (!response.body) throw new Error("ReadableStream not supported");
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                while(true) {
                    const { done, value } = await reader.read();
                    if(done) {
                        pipelineProgressPercent.value = 100;
                        setTimeout(() => {
                            showPipelineProgress.value = false;
                            clearInterval(pipelineTimer);
                        }, 2000);
                        break;
                    }
                    if(!value) continue;
                    const chunk = decoder.decode(value, {stream: true});
                    buffer += chunk;
                    let lines = buffer.split('\n');
                    buffer = lines.pop(); 
                    
                    let outBuf = '';
                    lines.forEach(line => {
                        if (line.startsWith('data: ')) {
                            const actualLine = line.substring(6);
                            if (actualLine.startsWith('__PROGRESS__:')) {
                                const parts = actualLine.substring(13).split('::');
                                pipelineProgressPercent.value = parseFloat(parts[0]);
                                showPipelineProgress.value = true;
                                replaceLastTerminalLine(parts[1] || '');
                            } else if (actualLine !== '[END]') {
                                outBuf += actualLine + '\n';
                            }
                        }
                    });
                    if (outBuf) terminalOutput.value += outBuf;
                }
            } catch(e) {
                terminalOutput.value += `[System Fatal Error]: ${e.message}\n`;
            } finally {
                processState.value = 'stopped';
                if (pipelineProgressPercent.value === 100) {
                    scriptStatuses[scriptName] = 'success';
                    setTimeout(() => { if (scriptStatuses[scriptName] === 'success') scriptStatuses[scriptName] = 'idle'; }, 3000);
                } else {
                    scriptStatuses[scriptName] = 'idle';
                }
                if (pipelineProgressPercent.value === 100 || !showPipelineProgress.value) {
                    clearInterval(pipelineTimer);
                }
            }
        };

        // ROPA Editor
        const ropaDataCache = ref([]);
        const { sortKey: ropaSortKey, sortAsc: ropaSortAsc, sortBy: ropaSortBy, sortedData: sortedRopaData } = useSortableTable(ropaDataCache);
        const loadRopaData = async () => {
            try {
                const res = await fetch('/api/ropa');
                const d = await res.json();
                ropaDataCache.value = d.data || [];
            } catch(e) {}
        };

        const isRopaModalOpen = ref(false);
        const isBasesDropdownOpen = ref(false);
        const toggleBasesDropdown = (e) => {
            if (e) e.stopPropagation();
            isBasesDropdownOpen.value = !isBasesDropdownOpen.value;
        };
        const currentEditingIdx = ref(null);
        const ropaEditingRow = reactive({ id: '', activity: '', purpose: '', subjects: '', personal: '', recipients: '', transfers: '', num: 0, unit: 'Days', selectedBases: [] });

        const lawfulBasesOptions = computed(() => [
            { id: 'a', label: t('lb_a') },
            { id: 'b', label: t('lb_b') },
            { id: 'c', label: t('lb_c') },
            { id: 'd', label: t('lb_d') },
            { id: 'e', label: t('lb_e') },
            { id: 'f', label: t('lb_f') }
        ]);

        const openRopaEditor = (idx) => {
            currentEditingIdx.value = idx;
            const row = ropaDataCache.value[idx];
            ropaEditingRow.id = row["id"] || idx;
            ropaEditingRow.activity = row["Processing Activity"] || '';
            ropaEditingRow.purpose = row["Lawful Bases"] || row["Processing Purpose"] || '';
            ropaEditingRow.subjects = row["Data Subject Categories"] || '';
            ropaEditingRow.personal = row["Personal Data Categories"] || '';
            ropaEditingRow.recipients = row["Recipients Categories"] || '';
            ropaEditingRow.transfers = row["International Transfers"] || '';
            
            // Parse stored comma-separated IDs (e.g. "a, b, f") into selected array
            const storedIds = (ropaEditingRow.purpose || '').split(',').map(s => s.trim()).filter(Boolean);
            ropaEditingRow.selectedBases.splice(0, ropaEditingRow.selectedBases.length, ...storedIds);

            let retValue = row["Retention Periods"] || '';
            let numOnly = parseInt(retValue) || 0;
            ropaEditingRow.num = numOnly;
            ropaEditingRow.unit = 'Days';
            isRopaModalOpen.value = true;
        };

        const translateBases = (basesStr) => {
            if (!basesStr) return '';
            const ids = basesStr.split(',').map(s => s.trim()).filter(Boolean);
            return ids.map(id => {
                const opt = lawfulBasesOptions.value.find(o => o.id === id);
                return opt ? opt.label : id;
            }).join(', ');
        };

        onMounted(() => {
            window.addEventListener('click', (e) => {
                if (!e.target.closest('.custom-multiselect')) {
                    isBasesDropdownOpen.value = false;
                }
            });
        });

        const saveRopaEditor = async () => {
            if(currentEditingIdx.value === null) return;
            const row = ropaDataCache.value[currentEditingIdx.value];
            row["Processing Activity"] = ropaEditingRow.activity;
            
            // Store only comma-separated letter IDs in DB (e.g. "a, b, f")
            row["Lawful Bases"] = ropaEditingRow.selectedBases.slice().sort().join(', ');
            row["Processing Purpose"] = row["Lawful Bases"];

            row["Data Subject Categories"] = ropaEditingRow.subjects;
            row["Personal Data Categories"] = ropaEditingRow.personal;
            row["Recipients Categories"] = ropaEditingRow.recipients;
            row["International Transfers"] = ropaEditingRow.transfers;

            let multiplier = 1;
            if(ropaEditingRow.unit === 'Months') multiplier = 30;
            if(ropaEditingRow.unit === 'Years') multiplier = 365;
            row["Retention Periods"] = "+" + (ropaEditingRow.num * multiplier) + " days";

            try {
                await fetch('/api/ropa', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: ropaDataCache.value })
                });
                showToast(currentLang.value === 'it' ? "Salvataggio riga completato": "Row saved successfully");
                isRopaModalOpen.value = false;
                loadRopaData();
            } catch(e) { showToast("Error saving row"); }
        };

        const selectedFileName = ref('');
        const onFileSelected = (event) => {
            const file = event.target.files[0];
            if (file) {
                selectedFileName.value = file.name;
            }
        };

        const ropaFileInput = ref(null);
        const uploadRopa = async () => {
            if(!ropaFileInput.value || !ropaFileInput.value.files.length) {
                showToast(currentLang.value === 'it' ? "Nessun file selezionato" : "Please select a file first"); return;
            }
            const fd = new FormData();
            fd.append('file', ropaFileInput.value.files[0]);
            try {
                 const req = await fetch('/api/upload_ropa', { method: 'POST', body: fd });
                 if(req.ok) {
                     const data = await req.json();
                     showToast(currentLang.value === 'it' ? "File caricato. Trasformazione..." : "File uploaded. Processing...");
                     selectedFileName.value = ''; // Reset after upload
                     const runRes = await fetch('/api/run/3_extract_ROPA.py', {
                        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ args: ["--file", data.file_path, "--mapping", "{}"] })
                     });
                     if (runRes.body) {
                        const reader = runRes.body.getReader();
                        while(true) { const {done} = await reader.read(); if(done) break; }
                     }
                     showToast(currentLang.value === 'it' ? "Estrazione completata!" : "Extraction complete!");
                     await loadRopaData();
                     ropaFileInput.value.value = '';
                 }
            } catch(e) { showToast("Network Error Uploading"); }
        };

        // Dashboard
        const docsData = ref([]);
        const idenData = ref([]);
        const { sortKey: docsSortKey, sortAsc: docsSortAsc, sortBy: docsSortBy, sortedData: sortedDocsData } = useSortableTable(docsData);
        const { sortKey: idenSortKey, sortAsc: idenSortAsc, sortBy: idenSortBy, sortedData: sortedIdenData } = useSortableTable(idenData);
        const loadDashboardData = async () => {
            try {
                const dRes = await fetch('/api/documents');
                docsData.value = (await dRes.json()).data || [];
                const iRes = await fetch('/api/identified');
                idenData.value = (await iRes.json()).data || [];
            } catch(e) {}
        };

        // Lifecycle
        const lcData = ref([]);
        const activeLcData = computed(() => lcData.value.filter(r => r.status !== 'DELETED'));
        const deletedLcData = computed(() => lcData.value.filter(r => r.status === 'DELETED'));
        const { sortKey: activeLcSortKey, sortAsc: activeLcSortAsc, sortBy: activeLcSortBy, sortedData: sortedActiveLcData } = useSortableTable(activeLcData);
        const { sortKey: deletedLcSortKey, sortAsc: deletedLcSortAsc, sortBy: deletedLcSortBy, sortedData: sortedDeletedLcData } = useSortableTable(deletedLcData);
        
        const isLcModalOpen = ref(false);
        const currentLcEditingIdx = ref(null);
        const lcEditingRow = reactive({ id: '', status: 'PENDING', notes: '', document_id: '', document_type: '', classification: '', creation_date: '' });

        const loadLifecycleData = async () => {
            try {
                const res = await fetch('/api/lifecycle');
                lcData.value = (await res.json()).data || [];
            } catch(e) {}
        };

        const formatDate = (val) => {
            if(!val || val === '-') return '-';
            const sVal = String(val).toUpperCase();
            if(sVal === 'NONE' || sVal === 'NAT' || sVal === 'NULL') return '-';
            
            if(val.includes(' ')) return val.split(' ')[0];
            if(val.includes('T')) return val.split('T')[0];
            return val;
        };

        const openLcEditor = (row) => {
            lcEditingRow.id = row.lifecycle_id;
            lcEditingRow.status = row.status || 'PENDING';
            lcEditingRow.notes = row.notes || '';
            lcEditingRow.document_id = row.document_id;
            lcEditingRow.document_type = row.document_type || 'File';
            lcEditingRow.classification = row.classification || 'Unknown';
            lcEditingRow.creation_date = formatDate(row.creation_date);
            isLcModalOpen.value = true;
        };

        const saveLcEditor = async () => {
            if(!lcEditingRow.id) return;
            try {
                await fetch('/api/lifecycle/' + lcEditingRow.id, {
                    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ status: lcEditingRow.status, notes: lcEditingRow.notes })
                });
                showToast(currentLang.value === 'it' ? "Stato aggiornato!" : "Status updated!");
                isLcModalOpen.value = false;
                loadLifecycleData();
            } catch(e) { showToast("Error saving lifecycle"); }
        };

        const performDocumentDeletion = async (docId, isFromModal = false) => {
            const msg = currentLang.value === 'it'
                ? `Sei sicuro di voler eliminare definitivamente il documento ${docId}?`
                : `Are you sure you want to permanently delete document ${docId}?`;
            if (!confirm(msg)) return;
            
            if (!isFromModal) {
                janitorLog.value += `[${logTs()}] Deleting document ${docId}...\n`;
                isJanitorLogCollapsed.value = false;
            }
            
            try {
                const res = await fetch('/api/janitor/delete-manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ document_ids: [docId], force: true })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    if (!isFromModal) janitorLog.value += `[${logTs()}] ✓ Document ${docId} permanently removed.\n`;
                    showToast(currentLang.value === 'it' ? "Documento rimosso permanentemente!" : "Document permanently removed!");
                    if (isFromModal) isLcModalOpen.value = false;
                    loadLifecycleData();
                } else {
                    if (!isFromModal) janitorLog.value += `[${logTs()}] ✗ Failed: ${data.detail || 'Unknown error'}\n`;
                    showToast("Error: " + (data.detail || "Deletion failed"));
                }
            } catch(e) {
                if (!isFromModal) janitorLog.value += `[${logTs()}] ✗ Connection Error\n`;
                showToast("Connection Error"); 
            }
        };

        const deleteLcNow = () => {
             if(!lcEditingRow.document_id) return;
             performDocumentDeletion(lcEditingRow.document_id, true);
        };

        const janitorLog = ref('');
        const isJanitorLogCollapsed = ref(true);
        const logTs = () => new Date().toLocaleTimeString();

        const deleteLcDirect = (row) => performDocumentDeletion(row.document_id, false);

        const runningJanitor = ref(false);
        const runJanitor = async () => {
            runningJanitor.value = true;
            janitorLog.value += `[${logTs()}] Automated Janitor started...\n`;
            isJanitorLogCollapsed.value = false;
            try {
                const res = await fetch('/api/janitor/run', { method: 'POST' });
                const data = await res.json();
                if(data.status === 'success') {
                    const s = data.summary;
                    janitorLog.value += `[${logTs()}] ✓ Janitor complete — Success: ${s.success}, Failed: ${s.failed}\n`;
                    showToast(currentLang.value === 'it' ? `Pulizia completata! Successi: ${s.success}, Errori: ${s.failed}` : `Janitor finished! Success: ${s.success}, Failed: ${s.failed}`);
                    loadLifecycleData();
                } else {
                    janitorLog.value += `[${logTs()}] ✗ Janitor failed: ${data.detail || 'Unknown error'}\n`;
                    showToast("Janitor failed: " + (data.detail || "Unknown error"));
                }
            } catch(e) {
                janitorLog.value += `[${logTs()}] ✗ Network Error\n`;
                showToast("Network Error");
            }
            finally { runningJanitor.value = false; }
        };

        const exportDeletedPdf = () => {
            if (deletedLcData.value.length === 0) {
                showToast(currentLang.value === 'it' ? "Nessun record da esportare" : "No records to export");
                return;
            }
            
            try {
                const { jsPDF } = window.jspdf;
                const doc = new jsPDF('landscape');
                
                // Report header
                doc.setFontSize(18);
                doc.setTextColor(40, 40, 45);
                doc.text("GDPR Document Erasure Audit Report", 14, 20);
                doc.setFontSize(10);
                doc.setTextColor(100, 100, 105);
                doc.text("Generated: " + new Date().toLocaleString(), 14, 28);
                doc.text(`Total records: ${deletedLcData.value.length}`, 14, 33);
                
                // Compliance notice
                doc.setFontSize(8);
                doc.setTextColor(120, 120, 125);
                doc.text(
                    "This report serves as proof of GDPR Art. 17 erasure compliance. " +
                    "All personal data has been permanently removed from Cloud, Filesystem, and Database.",
                    14, 39
                );

                // Full lifecycle table
                doc.autoTable({
                    startY: 45,
                    head: [['Doc ID', 'Doc Type', 'Created', 'Scheduled Deletion', 'Actual Deletion', 'Retention', 'Status', 'Notes']],
                    body: deletedLcData.value.map(row => {
                        // Calculate retention duration in days
                        let retentionDays = '-';
                        const created = row.creation_date && row.creation_date !== 'None' ? new Date(row.creation_date) : null;
                        const deleted = row.actual_deletion_date && row.actual_deletion_date !== 'None' ? new Date(row.actual_deletion_date) : null;
                        if (created && deleted && !isNaN(created) && !isNaN(deleted)) {
                            retentionDays = Math.round((deleted - created) / (1000 * 60 * 60 * 24)) + 'd';
                        }
                        
                        return [
                            row.document_id || '-',
                            row.document_type || 'File',
                            formatDate(row.creation_date),
                            formatDate(row.scheduled_deletion_date),
                            formatDate(row.actual_deletion_date),
                            retentionDays,
                            row.status,
                            (row.notes || '-').substring(0, 60)
                        ];
                    }),
                    theme: 'striped',
                    headStyles: { fillColor: [60, 60, 67], fontSize: 8 },
                    styles: { fontSize: 7, cellPadding: 2 },
                    columnStyles: {
                        0: { cellWidth: 50 },
                        7: { cellWidth: 45 }
                    }
                });
                
                // Footer with page numbers
                const pageCount = doc.internal.getNumberOfPages();
                for (let i = 1; i <= pageCount; i++) {
                    doc.setPage(i);
                    doc.setFontSize(8);
                    doc.setTextColor(150, 150, 155);
                    doc.text(
                        `Page ${i} of ${pageCount} — GDPHub Audit Export`,
                        doc.internal.pageSize.getWidth() / 2, doc.internal.pageSize.getHeight() - 10,
                        { align: 'center' }
                    );
                }
                
                doc.save('GDPR_Erasure_Audit_Report.pdf');
                showToast(currentLang.value === 'it' ? "Esportazione PDF completata!" : "PDF Export complete!");
            } catch(e) {
                console.error(e);
                showToast(currentLang.value === 'it' ? "Errore durante la generazione del PDF" : "Error generating PDF");
            }
        };

        onMounted(() => {
            loadTranslations();
            loadConfiguration();
            loadModels();
            loadDashboardData();
            loadLifecycleData();
        });
        
        watch(currentView, (newVal) => {
            if(newVal === 'ropa-view') loadRopaData();
            if(newVal === 'dashboard-view') loadDashboardData();
            if(newVal === 'lifecycle-view') loadLifecycleData();
        });

        watch(currentLang, () => {
            loadTranslations();
        });

        return {
            config, currentLang, t, currentView, viewTitle, setView, isSidebarOpen, handleBrowse, saveConfiguration, saveInputFolder, toasts,
            processState, canStop, canPause, canResume, stopProcess, pauseProcess, resumeProcess, terminalOutput, terminalScrollRef, showPipelineProgress, pipelineProgressPercent, pipelineElapsedTime, pipelineRemainingTime, isTerminalCollapsed, scriptStatuses, models, classifyModel, identifyModel, classifyNoThink, identifyNoThink, executeScript,
            ropaDataCache, sortedRopaData, ropaSortKey, ropaSortAsc, ropaSortBy, isRopaModalOpen, isBasesDropdownOpen, toggleBasesDropdown, ropaEditingRow, openRopaEditor, saveRopaEditor, ropaFileInput, uploadRopa, selectedFileName, onFileSelected, translateBases,
            docsData, sortedDocsData, docsSortKey, docsSortAsc, docsSortBy, idenData, sortedIdenData, idenSortKey, idenSortAsc, idenSortBy,
            lcData, activeLcData, deletedLcData, sortedActiveLcData, activeLcSortKey, activeLcSortAsc, activeLcSortBy, sortedDeletedLcData, deletedLcSortKey, deletedLcSortAsc, deletedLcSortBy, isLcModalOpen, lcEditingRow, openLcEditor, saveLcEditor, deleteLcNow, deleteLcDirect, formatDate, runningJanitor, runJanitor, janitorLog, isJanitorLogCollapsed, exportDeletedPdf, lawfulBasesOptions
        };
    }
});

app.mount('#app');
