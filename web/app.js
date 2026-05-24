const { createApp, ref, reactive, computed, onMounted, nextTick, watch } = Vue;

// Reusable composables and helpers live in web/composables.js (loaded first
// in index.html). Pull them into the local lexical scope so the rest of this
// file reads exactly like the previous monolithic version.
const { useSortableTable, formatTime, formatDate, logTs, readSSEStream } = window.GDPHub;

const app = createApp({
    setup() {
        const currentLang = ref('en');
        const translations = ref({});

        const loadTranslations = async () => {
            try {
                translations.value = await GDPHubAPI.locales.load(currentLang.value);
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
            ext: { tesseract_path: '', max_workers: 4 },
            gpu_profile: '12gb',
            cls: { ollama_url: 'http://localhost:11434', ollama_model_default: '', title_max_length: 500, text_max_length: 1500, timeout_seconds: 30, api_request_timeout: 25, options: { temperature: 0.2, num_ctx: 2048, num_batch: 256, top_p: 0.9, top_k: 40, num_predict: 64 } },
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
                const data = await GDPHubAPI.config.get();
                config.active_source = data.active_source || 'gmail';
                config.input_folder = data.input_folder || '';
                config.database_folder = data.database_folder || '';
                config.log_folder = data.log_folder || '';
                config.log_level = data.log_level || 'INFO';

                const gmail = data['extract_mail'] || {};
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
                config.ext.max_workers = ext.max_workers || 4;

                const cls = data['classify_text.py'] || {};
                config.cls.ollama_url = cls.ollama_url || 'http://localhost:11434';
                config.cls.ollama_model_default = cls.ollama_model_default || '';
                config.cls.title_max_length = cls.title_max_length || 500;
                config.cls.text_max_length = cls.text_max_length || 1500;
                config.cls.timeout_seconds = cls.timeout_seconds || 30;
                config.cls.api_request_timeout = cls.api_request_timeout || 25;
                config.gpu_profile = data['gpu_profile'] || 'custom';
                const opts = cls.ollama_options || {};
                config.cls.options.num_predict = opts.num_predict || 64;
                config.cls.options.temperature = opts.temperature || 0.2;
                config.cls.options.num_ctx = opts.num_ctx || 2048;
                config.cls.options.num_batch = opts.num_batch || 256;
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
                gpu_profile: config.gpu_profile,
                "0_extract_mail_gmail_auth": { client_id: config.mail.client_id, client_secret: config.mail.client_secret },
                "extract_mail": { 
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
                await GDPHubAPI.config.save(dataToSave);
                showToast(currentLang.value === 'it' ? "Configurazione Salvata!" : "Config Successfully Saved!");
                loadModels();
            } catch(e) {
                showToast("Failed to save config.");
            }
        };

        const GPU_PROFILES = {
            "8gb":  { num_predict: 64, temperature: 0.2, num_ctx: 1536, num_batch: 128, top_p: 0.9, top_k: 40 },
            "12gb": { num_predict: 64, temperature: 0.2, num_ctx: 2048, num_batch: 256, top_p: 0.9, top_k: 40 },
            "24gb": { num_predict: 64, temperature: 0.2, num_ctx: 4096, num_batch: 512, top_p: 0.9, top_k: 40 },
        };

        const applyGpuProfile = (profileName) => {
            const p = GPU_PROFILES[profileName];
            if (p) Object.assign(config.cls.options, p);
        };

        const saveInputFolder = async () => {
             try {
                await GDPHubAPI.config.save({
                    active_source: config.active_source,
                    input_folder: config.input_folder,
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
                const data = await GDPHubAPI.utils.browseFolder(currentPath);
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

        const controlProcess = async (action, state) => { await GDPHubAPI.control(action); processState.value = state; };
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
        const isPipelineLoading = ref(false);
        const pipelineProgressPercent = ref(0);
        const isTerminalCollapsed = ref(true);
        const scriptStatuses = reactive({
            'extract_mail': 'idle',
            'extract_text': 'idle',
            'classify_text': 'idle',
            'identify_ropa': 'idle'
        });
        const pipelineStartTime = ref(null);
        const pipelineElapsedTime = ref("00:00");
        const pipelineRemainingTime = ref("--:--");
        let pipelineTimer = null;

        const models = ref([]);
        const classifyModel = ref('');
        const identifyModel = ref('');
        const classifyNoThink = ref(true);
        const identifyNoThink = ref(true);

        const loadModels = async () => {
            try {
                const data = await GDPHubAPI.ollama.models();
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
                if (scriptName.includes("identify_ropa")) {
                    args.push("--model", identifyModel.value);
                    if (identifyNoThink.value) args.push("--no-think");
                }
                if (config.gpu_profile && config.gpu_profile !== 'custom') {
                    args.push("--gpu-profile", config.gpu_profile);
                }
            }
            terminalOutput.value = `>>> Launching ${scriptName}...\n`;
            scriptStatuses[scriptName] = 'running';
            processState.value = 'running';
            showPipelineProgress.value = true;
            isPipelineLoading.value = true;
            pipelineProgressPercent.value = 0;
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
                const response = await GDPHubAPI.run.pipeline(scriptName, args);
                await readSSEStream(response, (kind, payload) => {
                    if (kind === 'progress') {
                        isPipelineLoading.value = false;
                        pipelineProgressPercent.value = payload.percent;
                        showPipelineProgress.value = true;
                        replaceLastTerminalLine(payload.text);
                    } else if (kind === 'line') {
                        terminalOutput.value += payload + '\n';
                    } else if (kind === 'end') {
                        isPipelineLoading.value = false;
                        pipelineProgressPercent.value = 100;
                        setTimeout(() => {
                            showPipelineProgress.value = false;
                            clearInterval(pipelineTimer);
                        }, 2000);
                    }
                });
            } catch(e) {
                terminalOutput.value += `[System Fatal Error]: ${e.message}\n`;
            } finally {
                processState.value = 'stopped';
                isPipelineLoading.value = false;
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

        // Administration
        const isAdminCollapsed = ref(true);
        const adminSelectedOption = ref("1");
        const adminCollection = ref("classifications");
        const adminEntryId = ref("");
        const isAdminRunning = ref(false);
        const adminOutput = ref("");

        const runAdminAction = async () => {
            isAdminRunning.value = true;
            adminOutput.value = "Starting administration action...\n";
            try {
                const res = await GDPHubAPI.admin.clean({
                    option: adminSelectedOption.value,
                    collection: adminSelectedOption.value === "6" ? adminCollection.value : null,
                    entry_id: adminSelectedOption.value === "6" ? adminEntryId.value : null
                });
                adminOutput.value += `Status: ${res.status}\n`;
                if (res.output) {
                    adminOutput.value += `${res.output}\n`;
                }
            } catch (e) {
                adminOutput.value += `[Error]: ${e.message}\n`;
            } finally {
                isAdminRunning.value = false;
            }
        };

        // ROPA Editor
        const ropaDataCache = ref([]);
        const { sortKey: ropaSortKey, sortAsc: ropaSortAsc, sortBy: ropaSortBy, sortedData: sortedRopaData } = useSortableTable(ropaDataCache);
        const loadRopaData = async () => {
            try {
                const d = await GDPHubAPI.ropa.list();
                ropaDataCache.value = d.data || [];
            } catch(e) {}
        };

        const ropaExportFormat = ref('xlsx');
        const exportRopa = () => {
            const format = ropaExportFormat.value;
            window.location.href = `/api/ropa/export?format=${format}`;
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
                await GDPHubAPI.ropa.save(ropaDataCache.value);
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
                 const req = await GDPHubAPI.ropa.upload(fd);
                 if(req.ok) {
                     const data = await req.json();
                     showToast(currentLang.value === 'it' ? "File caricato. Trasformazione..." : "File uploaded. Processing...");
                     selectedFileName.value = ''; // Reset after upload
                     const runRes = await GDPHubAPI.run.pipeline('extract_ropa', ["--file", data.file_path, "--mapping", "{}"]);
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
        const pendingCount = ref(0);
        const docsModelFilter = ref('');
        const idenModelFilter = ref('');
        const classificationModels = ref([]);
        const mappingModels = ref([]);
        const { sortKey: docsSortKey, sortAsc: docsSortAsc, sortBy: docsSortBy, sortedData: sortedDocsData } = useSortableTable(docsData);
        const { sortKey: idenSortKey, sortAsc: idenSortAsc, sortBy: idenSortBy, sortedData: sortedIdenData } = useSortableTable(idenData);

        const loadModelsUsed = async () => {
            try {
                const res = await GDPHubAPI.models.used();
                classificationModels.value = res.classification_models || [];
                mappingModels.value = res.mapping_models || [];
            } catch(e) {}
        };

        const loadDashboardData = async () => {
            try {
                docsData.value = (await GDPHubAPI.documents.list(docsModelFilter.value || undefined)).data || [];
                idenData.value = (await GDPHubAPI.identified.list(idenModelFilter.value || undefined)).data || [];
                loadPendingCount();
                loadModelsUsed();
            } catch(e) {}
        };

        const onDocsModelChange = async () => {
            try {
                docsData.value = (await GDPHubAPI.documents.list(docsModelFilter.value || undefined)).data || [];
            } catch(e) {}
        };

        const onIdenModelChange = async () => {
            try {
                idenData.value = (await GDPHubAPI.identified.list(idenModelFilter.value || undefined)).data || [];
            } catch(e) {}
        };

        const loadPendingCount = async () => {
            try {
                const data = await GDPHubAPI.stats.pending();
                pendingCount.value = data.count || 0;
            } catch(e) { pendingCount.value = 0; }
        };

        // Document Classification Editor
        const isDocsModalOpen = ref(false);
        const docsEditingRow = reactive({ file_id: '', file_name: '', classification_generic: '', description_short: '' });

        const openDocsEditor = (row) => {
            docsEditingRow.file_id = row.file_id || '';
            docsEditingRow.file_name = row.file_name || '';
            docsEditingRow.classification_generic = row.classification_generic || '';
            docsEditingRow.description_short = row.description_short || '';
            isDocsModalOpen.value = true;
        };

        const saveDocsEditor = async () => {
            if (!docsEditingRow.file_id) return;
            try {
                const payload = {
                    classification_generic: docsEditingRow.classification_generic,
                    description_short: docsEditingRow.description_short,
                };
                if (docsModelFilter.value) payload.model_used = docsModelFilter.value;
                await GDPHubAPI.documents.updateClassification(docsEditingRow.file_id, payload);
                showToast(currentLang.value === 'it' ? 'Classificazione aggiornata!' : 'Classification updated!');
                isDocsModalOpen.value = false;
                loadDashboardData();
            } catch(e) { showToast('Error saving classification'); }
        };

        // Identified Mapping Editor
        const isIdenModalOpen = ref(false);
        const idenEditingRow = reactive({ mapping_id: null, parent_id: '', activity: '', description: '', ropa_id: '' });
        const ropaOptions = computed(() => {
            return ropaDataCache.value.map(r => ({ id: r.id || r['id'], label: (r.id || r['id']) + ' — ' + (r['Processing Activity'] || r.activity || '') }));
        });

        const openIdenEditor = (row) => {
            idenEditingRow.mapping_id = row.mapping_id;
            idenEditingRow.parent_id = row.parent_id || '-';
            idenEditingRow.activity = row.Processing_Activity || '-';
            idenEditingRow.description = row.description || row.classification || '-';
            idenEditingRow.ropa_id = row.ROPA_ID || '';
            isIdenModalOpen.value = true;
        };

        const saveIdenEditor = async () => {
            if (!idenEditingRow.mapping_id) return;
            try {
                await GDPHubAPI.identified.update(idenEditingRow.mapping_id, { ropa_id: idenEditingRow.ropa_id || null });
                showToast(currentLang.value === 'it' ? 'Associazione aggiornata!' : 'Mapping updated!');
                isIdenModalOpen.value = false;
                loadDashboardData();
            } catch(e) { showToast('Error saving mapping'); }
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
                lcData.value = (await GDPHubAPI.lifecycle.list()).data || [];
            } catch(e) {}
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
                await GDPHubAPI.lifecycle.update(lcEditingRow.id, { status: lcEditingRow.status, notes: lcEditingRow.notes });
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
                const data = await GDPHubAPI.janitor.deleteManual([docId], true);
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

        const deleteLcDirect = (row) => performDocumentDeletion(row.document_id, false);

        const runningJanitor = ref(false);
        const runJanitor = async () => {
            runningJanitor.value = true;
            janitorLog.value += `[${logTs()}] Automated Janitor started...\n`;
            isJanitorLogCollapsed.value = false;
            try {
                const data = await GDPHubAPI.janitor.run();
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
            if(newVal === 'dashboard-view') { loadDashboardData(); loadRopaData(); }
            if(newVal === 'lifecycle-view') loadLifecycleData();
        });

        watch(currentLang, () => {
            loadTranslations();
        });

        return {
            config, currentLang, t, currentView, viewTitle, setView, isSidebarOpen, handleBrowse, saveConfiguration, saveInputFolder, applyGpuProfile, toasts,
            processState, canStop, canPause, canResume, stopProcess, pauseProcess, resumeProcess, terminalOutput, terminalScrollRef, showPipelineProgress, isPipelineLoading, pipelineProgressPercent, pipelineElapsedTime, pipelineRemainingTime, isTerminalCollapsed, scriptStatuses, models, classifyModel, identifyModel, classifyNoThink, identifyNoThink, executeScript,
            ropaDataCache, sortedRopaData, ropaSortKey, ropaSortAsc, ropaSortBy, isRopaModalOpen, isBasesDropdownOpen, toggleBasesDropdown, ropaEditingRow, openRopaEditor, saveRopaEditor, ropaFileInput, uploadRopa, selectedFileName, onFileSelected, translateBases, ropaExportFormat, exportRopa,
            docsData, sortedDocsData, docsSortKey, docsSortAsc, docsSortBy, isDocsModalOpen, docsEditingRow, openDocsEditor, saveDocsEditor, docsModelFilter, classificationModels, onDocsModelChange, idenData, sortedIdenData, idenSortKey, idenSortAsc, idenSortBy, isIdenModalOpen, idenEditingRow, openIdenEditor, saveIdenEditor, idenModelFilter, mappingModels, onIdenModelChange, ropaOptions, pendingCount, loadPendingCount,
            lcData, activeLcData, deletedLcData, sortedActiveLcData, activeLcSortKey, activeLcSortAsc, activeLcSortBy, sortedDeletedLcData, deletedLcSortKey, deletedLcSortAsc, deletedLcSortBy, isLcModalOpen, lcEditingRow, openLcEditor, saveLcEditor, deleteLcNow, deleteLcDirect, formatDate, runningJanitor, runJanitor, janitorLog, isJanitorLogCollapsed, exportDeletedPdf, lawfulBasesOptions,
            isAdminCollapsed, adminSelectedOption, adminCollection, adminEntryId, isAdminRunning, adminOutput, runAdminAction
        };
    }
});

app.mount('#app');
