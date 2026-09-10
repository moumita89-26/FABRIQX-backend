(() => {
    const input = document.getElementById('sidebar-search');
    const navigation = document.getElementById('nav-sidebar-apps');
    const empty = document.getElementById('sidebar-search-empty');
    if (!input || !navigation) return;
    const normalize = (text) => text.trim().toLocaleLowerCase();
    const groups = Array.from(navigation.children).map(group => ({
        element: group,
        title: normalize(group.querySelector('[data-sidebar-toggle], h2')?.textContent || ''),
        items: Array.from(group.querySelectorAll('li > a')).map(link => ({
            element: link.parentElement,
            text: normalize(link.textContent),
        })),
        panel: group.querySelector('.sidebar-menu-items'),
        toggle: group.querySelector('[data-sidebar-toggle]'),
        initiallyOpen: group.querySelector('[data-sidebar-toggle]')?.getAttribute('aria-expanded') === 'true',
    }));
    function filter() {
        const words = normalize(input.value).split(/\s+/).filter(Boolean);
        let matches = 0;
        for (const group of groups) {
            let visible = 0;
            for (const item of group.items) {
                // Search menu entries themselves. Including the group heading
                // made every Content Management item match a query like "ad".
                const show = words.every(word => item.text.includes(word));
                if (show) { item.element.style.removeProperty('display'); visible++; }
                else item.element.style.setProperty('display', 'none', 'important');
            }
            if (visible || !words.length) group.element.style.removeProperty('display');
            else group.element.style.setProperty('display', 'none', 'important');
            if (group.panel && group.toggle) {
                const open = words.length ? visible > 0 : group.initiallyOpen;
                group.panel.hidden = !open;
                group.toggle.setAttribute('aria-expanded', String(open));
            }
            matches += visible;
        }
        empty.hidden = !words.length || matches > 0;
    }
    input.addEventListener('input', filter);
    input.addEventListener('keydown', event => {
        if (event.key === 'Escape') { input.value = ''; filter(); }
        if (event.key === 'Enter') event.preventDefault();
    });
})();
