(() => {
    const initializedForms = new WeakSet();

    // Django's stock action script is not always present in customised Unfold
    // changelists. Keep the master checkbox functional, including after live
    // search swaps in a fresh product table.
    function initializeBulkSelection(changelistForm) {
        if (!changelistForm || initializedForms.has(changelistForm)) return;
        initializedForms.add(changelistForm);

        const master = changelistForm.querySelector('#action-toggle');
        if (!master) return;

        const selections = () => Array.from(changelistForm.querySelectorAll('.action-select'));
        const syncMaster = () => {
            const boxes = selections();
            const selected = boxes.filter(box => box.checked).length;
            master.checked = boxes.length > 0 && selected === boxes.length;
            master.indeterminate = selected > 0 && selected < boxes.length;
        };

        master.addEventListener('change', () => {
            selections().forEach(box => {
                box.checked = master.checked;
                box.closest('tr')?.classList.toggle('selected', master.checked);
            });
            master.indeterminate = false;
        });
        changelistForm.addEventListener('change', event => {
            if (!event.target.matches('.action-select')) return;
            event.target.closest('tr')?.classList.toggle('selected', event.target.checked);
            syncMaster();
        });
        syncMaster();
    }

    const form = document.getElementById('changelist-search');
    initializeBulkSelection(document.getElementById('changelist-form'));
    const input = form?.querySelector('input[name="q"]');
    if (!input) return;

    let timer;
    let controller;
    let revision = 0;

    async function search(version) {
        const request = new AbortController();
        controller = request;
        const url = new URL(window.location.href);
        if (input.value.trim()) url.searchParams.set('q', input.value.trim());
        else url.searchParams.delete('q');
        url.searchParams.delete('p');
        input.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(url, {
                signal: request.signal,
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            if (!response.ok || response.redirected) throw new Error('Search unavailable');
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            if (version !== revision) return;
            const ids = ['changelist-form', 'fabriqx-search-pagination'];
            const replacements = ids.map(id => [document.getElementById(id), page.getElementById(id)]);
            if (replacements.some(([current, next]) => !current || !next)) {
                throw new Error('Missing search results');
            }
            replacements.forEach(([current, next]) => current.replaceWith(next));
            initializeBulkSelection(document.getElementById('changelist-form'));
            const checkboxes = document.querySelectorAll('#changelist-form .action-select');
            if (checkboxes.length && window.Actions) window.Actions(checkboxes);
            form.querySelectorAll('input[name="p"]').forEach(node => node.remove());
            document.querySelectorAll('input[type="hidden"][name="q"]').forEach(node => {
                node.value = input.value.trim();
            });
            window.history.replaceState(window.history.state, '', url);
        } catch (error) {
            if (version === revision && error.name !== 'AbortError') {
                console.error('Live search request failed.', error);
            }
        } finally {
            if (version === revision) input.removeAttribute('aria-busy');
        }
    }

    function schedule(immediate = false) {
        clearTimeout(timer);
        controller?.abort();
        const version = ++revision;
        input.removeAttribute('aria-busy');
        if (immediate) search(version);
        else timer = setTimeout(() => search(version), 250);
    }
    // Input handles typing, pasting, and clearing the field.
    input.addEventListener('input', event => {
        if (!event.isComposing) schedule();
    });
    input.addEventListener('compositionend', () => schedule());
    form.addEventListener('submit', event => {
        event.preventDefault();
        schedule(true);
    });
})();
