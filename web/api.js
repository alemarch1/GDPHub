// GDPHub frontend API helper.
//
// Single source of truth for all backend HTTP calls. Loaded *before* app.js
// (see index.html). Exposes a typed-ish facade on window.GDPHubAPI so views
// don't have to know URLs, methods, or request body shapes.
//
// Each method preserves the *exact* network behavior of the original inline
// fetch() it replaces — same URL, same method, same body — so refactoring
// app.js is byte-identical from the server's perspective.
(function (global) {
    'use strict';

    const JSON_HEADERS = { 'Content-Type': 'application/json' };

    function asJson(response) {
        if (!response.ok) {
            // Surface a structured error; callers can decide how to render.
            return response.json().catch(() => ({})).then((body) => {
                const err = new Error(body.detail || ('HTTP ' + response.status));
                err.status = response.status;
                err.body = body;
                throw err;
            });
        }
        return response.json();
    }

    function getJSON(url) {
        return fetch(url).then(asJson);
    }

    function postJSON(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: JSON_HEADERS,
            body: JSON.stringify(payload || {}),
        }).then(asJson);
    }

    const GDPHubAPI = {
        // ----- locales -----
        locales: {
            // Cache-busting version retained from the original code path.
            load: (lang) => fetch('/locales/' + encodeURIComponent(lang) + '.json?v=2012')
                .then((r) => r.json()),
        },

        // ----- /api/config -----
        config: {
            get:  ()        => getJSON('/api/config'),
            save: (payload) => postJSON('/api/config', payload),
        },

        // ----- /api/ollama/models -----
        ollama: {
            models: () => getJSON('/api/ollama/models'),
        },

        // ----- /api/utils -----
        utils: {
            browseFolder: (currentPath) =>
                getJSON('/api/utils/browse-folder?current_path=' +
                        encodeURIComponent(currentPath || '')),
        },

        // ----- pipeline control -----
        control: (action) => fetch('/api/control/' + encodeURIComponent(action), {
            method: 'POST',
        }),

        run: {
            // Returns the raw Response so callers can read the SSE stream.
            pipeline: (scriptName, args) => fetch('/api/run/' + encodeURIComponent(scriptName), {
                method: 'POST',
                headers: JSON_HEADERS,
                body: JSON.stringify({ args: args || [] }),
            }),
        },

        // ----- /api/ropa -----
        ropa: {
            list:   ()    => getJSON('/api/ropa'),
            save:   (rows)=> postJSON('/api/ropa', { data: rows }),
            upload: (formData) => fetch('/api/upload_ropa', { method: 'POST', body: formData }),
        },

        // ----- documents / identified mappings -----
        documents: {
            list: () => getJSON('/api/documents'),
        },
        identified: {
            list:   ()              => getJSON('/api/identified'),
            update: (id, payload)   => postJSON('/api/identified/' + encodeURIComponent(id), payload),
        },
        stats: {
            pending: () => getJSON('/api/stats/pending'),
        },

        // ----- lifecycle -----
        lifecycle: {
            list:   ()             => getJSON('/api/lifecycle'),
            update: (id, payload)  => postJSON('/api/lifecycle/' + encodeURIComponent(id), payload),
        },

        // ----- janitor -----
        janitor: {
            run: () => postJSON('/api/janitor/run', {}),
            deleteManual: (documentIds, force) =>
                postJSON('/api/janitor/delete-manual', {
                    document_ids: documentIds,
                    force: !!force,
                }),
        },
    };

    global.GDPHubAPI = GDPHubAPI;
})(window);
