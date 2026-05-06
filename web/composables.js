// Reusable Vue 3 composables and small UI helpers shared by app.js views.
// Loaded before app.js (see index.html) and exposed on window.GDPHub.
//
// The helpers below are extracted verbatim from the previous monolithic
// app.js — same names, same signatures, same return shapes — so app.js can
// import them without changing any reactive behavior or DOM interaction.
(function (global) {
    'use strict';
    const { ref, computed } = Vue;

    // -----------------------------------------------------------------
    // useSortableTable — generic table sort by string-coerced column key.
    // -----------------------------------------------------------------
    function useSortableTable(dataRef) {
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
    }

    // -----------------------------------------------------------------
    // Date / time formatting helpers — pure, side-effect-free.
    // -----------------------------------------------------------------
    function formatTime(seconds) {
        if (isNaN(seconds) || seconds < 0) return "--:--";
        const m = Math.floor(seconds / 60).toString().padStart(2, '0');
        const s = (seconds % 60).toString().padStart(2, '0');
        return `${m}:${s}`;
    }

    function formatDate(val) {
        if (!val || val === '-') return '-';
        const sVal = String(val).toUpperCase();
        if (sVal === 'NONE' || sVal === 'NAT' || sVal === 'NULL') return '-';
        if (val.includes(' ')) return val.split(' ')[0];
        if (val.includes('T')) return val.split('T')[0];
        return val;
    }

    function logTs() {
        return new Date().toLocaleTimeString();
    }

    // -----------------------------------------------------------------
    // readSSEStream — reads an SSE response body line-by-line and routes
    // events to the supplied handler. Used by executeScript to mirror the
    // backend's `data: ...` framing into the live terminal.
    // The handler receives (kind, payload) where kind ∈ {'progress','line','end'}.
    // -----------------------------------------------------------------
    async function readSSEStream(response, handler) {
        if (!response.body) throw new Error("ReadableStream not supported");
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                handler('end', null);
                break;
            }
            if (!value) continue;
            buffer += decoder.decode(value, { stream: true });
            let lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const actualLine = line.substring(6);
                if (actualLine.startsWith('__PROGRESS__:')) {
                    const parts = actualLine.substring(13).split('::');
                    handler('progress', { percent: parseFloat(parts[0]), text: parts[1] || '' });
                } else if (actualLine !== '[END]') {
                    handler('line', actualLine);
                }
            }
        }
    }

    global.GDPHub = global.GDPHub || {};
    global.GDPHub.useSortableTable = useSortableTable;
    global.GDPHub.formatTime = formatTime;
    global.GDPHub.formatDate = formatDate;
    global.GDPHub.logTs = logTs;
    global.GDPHub.readSSEStream = readSSEStream;
})(window);
