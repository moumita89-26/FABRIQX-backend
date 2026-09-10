(() => {
    let pending = false;
    function showConfirmation(content, direct = false) {
        const dialog = document.createElement('dialog');
        dialog.className = 'fabriqx-delete-dialog';
        dialog.setAttribute('aria-labelledby', 'delete-dialog-title');
        dialog.append(content);
        document.body.append(dialog);
        const cancel = () => {
            if (direct) window.location.assign(content.querySelector('[data-delete-return]').href);
            else {
                dialog.close();
                dialog.remove();
            }
        };
        content.querySelector('[data-delete-cancel]').addEventListener('click', cancel);
        dialog.addEventListener('cancel', (event) => { event.preventDefault(); cancel(); });
        dialog.addEventListener('close', () => dialog.remove());
        content.querySelector('form').addEventListener('submit', () => {
            content.querySelector('button[type=submit]').disabled = true;
        });
        dialog.showModal();
    }
    async function loadConfirmation(url, options, fallback) {
        if (pending) return;
        pending = true;
        try {
            const response = await fetch(url, options);
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            const content = page.querySelector('[data-delete-confirmation]');
            if (response.ok && content) showConfirmation(content);
            else fallback();
        } catch (error) {
            fallback();
        } finally {
            pending = false;
        }
    }
    document.addEventListener('click', (event) => {
        const link = event.target.closest('a[href]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin || !url.pathname.endsWith('/delete/')) return;
        event.preventDefault();
        loadConfirmation(url.href, {}, () => window.location.assign(url.href));
    });
    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (form.id !== 'changelist-form') return;
        const data = new FormData(form);
        if (event.submitter && event.submitter.name) data.set(event.submitter.name, event.submitter.value);
        const actions = data.getAll('action');
        const action = actions[Number(data.get('index') || 0)] || actions.find(Boolean);
        if (action !== 'delete_selected' || !data.has('_selected_action')) return;
        event.preventDefault();
        data.set('action', action);
        data.delete('index');
        loadConfirmation(form.action, {method: 'POST', body: data}, () => {
            const input = document.createElement('input');
            input.type = 'hidden'; input.name = 'index'; input.value = event.submitter?.value || '0';
            form.append(input);
            HTMLFormElement.prototype.submit.call(form);
        });
    });
    const direct = document.querySelector('[data-delete-confirmation]');
    if (direct) showConfirmation(direct, true);
})();

// Inline records are removed only when their parent form is saved.
(() => {
    const confirmed = new WeakSet();
    function confirmInline(message, yes) {
        const dialog = document.createElement('dialog');
        dialog.className = 'fabriqx-delete-dialog';
        dialog.setAttribute('aria-label', 'Confirm delete');
        const heading = document.createElement('h2'); heading.textContent='Confirm delete';
        const text = document.createElement('p'); text.textContent=message;
        const buttons = document.createElement('div'); buttons.className='dialog-actions';
        const no = document.createElement('button'); no.type='button'; no.textContent='No'; no.autofocus=true;
        const ok = document.createElement('button'); ok.type='button'; ok.textContent='Yes';
        const close = () => {dialog.close(); dialog.remove();};
        no.onclick=close; ok.onclick=() => {close(); yes();};
        dialog.addEventListener('cancel', event => {event.preventDefault(); close();});
        buttons.append(no,ok); dialog.append(heading,text,buttons); document.body.append(dialog); dialog.showModal();
    }
    document.addEventListener('change', event => {
        const input=event.target;
        if (!input.matches('input[type=checkbox][name$="-DELETE"]') || !input.checked) return;
        if (confirmed.has(input)) {confirmed.delete(input); return;}
        input.checked=false;
        confirmInline('Delete this item when you save?', () => {confirmed.add(input); input.checked=true; input.dispatchEvent(new Event('change',{bubbles:true}));});
    }, true);
    document.addEventListener('click', event => {
        const link=event.target.closest('a.inline-deletelink');
        if (!link) return;
        if (confirmed.has(link)) {confirmed.delete(link); return;}
        event.preventDefault(); event.stopImmediatePropagation();
        confirmInline('Remove this item?', () => {confirmed.add(link); link.click();});
    }, true);
})();
