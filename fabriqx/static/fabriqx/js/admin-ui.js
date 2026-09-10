(() => {
    const objectURLs = new Map();
    const fileInput = name => Array.from(document.querySelectorAll('input[type=file]')).find(input => input.name === name);
    const preview = name => Array.from(document.querySelectorAll('[data-image-preview]')).find(image => image.dataset.imagePreview === name);
    function updateImage(input) {
        if (objectURLs.has(input)) URL.revokeObjectURL(objectURLs.get(input));
        const image = preview(input.name);
        if (!image) return;
        if (!image.dataset.original) image.dataset.original = image.getAttribute('src') || '';
        const file = input.files?.[0];
        const url = file ? URL.createObjectURL(file) : image.dataset.original;
        if (file) objectURLs.set(input, url);
        if (url) image.src = url; else image.removeAttribute('src');
        image.hidden = !url;
        const empty = Array.from(document.querySelectorAll('[data-image-empty]')).find(node => node.dataset.imageEmpty === input.name);
        if (empty) empty.hidden = Boolean(url);
    }
    const gift = document.getElementById('gift-live-preview');
    function value(name) {
        const input = document.getElementById(`id_${name}`);
        if (!input) return '';
        // Rich text is rendered as text in preview; never execute editor markup.
        if (input.tagName === 'TEXTAREA') {
            const parsed = new DOMParser().parseFromString(input.value, 'text/html');
            return parsed.body.textContent;
        }
        return input.value;
    }
    function imageSource(name) {
        const clear = document.querySelector(`input[name="${name}-clear"]`);
        if (clear?.checked) return '';
        return preview(name)?.getAttribute('src') || '';
    }
    function giftImage(node, name) {
        const source = imageSource(name);
        if (source) node.src = source; else node.removeAttribute('src');
        node.hidden = !source;
    }
    function repeaters(prefix, container, keys) {
        container.replaceChildren();
        const count = Number(document.getElementById(`id_${prefix}-TOTAL_FORMS`)?.value || 0);
        for (let index=0; index<count; index++) {
            const name = `${prefix}-${index}`;
            if (document.getElementById(`id_${name}-DELETE`)?.checked) continue;
            const active = document.getElementById(`id_${name}-is_active`);
            if (active && !active.checked) continue;
            const texts = keys.map(key => value(`${name}-${key}`));
            if (!texts.some(Boolean)) continue;
            const card = document.createElement('div');
            const icon = document.createElement('img'); icon.alt=''; giftImage(icon, `${name}-icon`); card.append(icon);
            texts.forEach((text,index) => { const node=document.createElement(prefix === 'statistics' && index === 1 ? 'strong':'p'); node.textContent=text; card.append(node); });
            container.append(card);
        }
    }
    function updateGift() {
        if (!gift) return;
        gift.querySelectorAll('[data-gift-text]').forEach(node => node.textContent=value(node.dataset.giftText));
        gift.querySelectorAll('[data-gift-image]').forEach(node => giftImage(node,node.dataset.giftImage));
        const background=imageSource('background_image');
        gift.style.backgroundImage = background ? `url(${JSON.stringify(background)})` : '';
        repeaters('features',gift.querySelector('[data-gift-features]'),['text']);
        repeaters('statistics',gift.querySelector('[data-gift-statistics]'),['eyebrow','value','label']);
    }
    function tooltips(root = document) {
        // Limit tooltips to the sidebar menu. Applying Unfold's tooltip class
        // to icon controls or form inputs can render their icon name as UI.
        root.querySelectorAll('#nav-sidebar-apps a').forEach(link => {
            const name = Array.from(link.querySelectorAll('span'))
                .filter(span => !span.classList.contains('material-symbols-outlined'))
                .map(span => span.textContent).join(' ')
                .trim()
                .replace(/\s+/g, ' ');
            if (name) {
                link.classList.remove('tooltip');
                link.title = name;
                link.setAttribute('aria-label', name);
            }
        });

        root.querySelectorAll('#nav-sidebar-apps [data-sidebar-toggle]').forEach(button => {
            const name = button.querySelector('span:first-child')?.textContent
                .trim()
                .replace(/\s+/g, ' ');
            if (name) button.title = `Expand or collapse ${name}.`;
        });
    }
    document.addEventListener('change', event => { if(event.target.matches('input[type=file]')) updateImage(event.target); updateGift(); });
    document.addEventListener('input', updateGift);
    document.addEventListener('formset:added', () => {tooltips(); updateGift();});
    document.addEventListener('formset:removed', updateGift);
    // Wysiwyg editors may update their backing textarea without a native input event.
    if (gift) setInterval(updateGift, 800);
    tooltips(); updateGift();
})();
