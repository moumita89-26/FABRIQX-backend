(() => {
    const objectURLs = new Map();

    const fileInput = name =>
        Array.from(
            document.querySelectorAll('input[type=file]')
        ).find(input => input.name === name);

    const preview = name =>
        Array.from(
            document.querySelectorAll('[data-image-preview]')
        ).find(
            image => image.dataset.imagePreview === name
        );

    function updateImage(input) {
        if (objectURLs.has(input)) {
            URL.revokeObjectURL(
                objectURLs.get(input)
            );
        }

        const image = preview(input.name);

        if (!image) return;

        if (!image.dataset.original) {
            image.dataset.original =
                image.getAttribute('src') || '';
        }

        const file = input.files?.[0];

        const url = file
            ? URL.createObjectURL(file)
            : image.dataset.original;

        if (file) {
            objectURLs.set(input, url);
        }

        if (url) {
            image.src = url;
        } else {
            image.removeAttribute('src');
        }

        image.hidden = !url;

        const empty = Array.from(
            document.querySelectorAll(
                '[data-image-empty]'
            )
        ).find(
            node =>
                node.dataset.imageEmpty ===
                input.name
        );

        if (empty) {
            empty.hidden = Boolean(url);
        }
    }


    // =========================================================
    // GIFT SECTION LIVE PREVIEW
    // =========================================================

    const gift =
        document.getElementById(
            'gift-live-preview'
        );

    function value(name) {
        const input =
            document.getElementById(
                `id_${name}`
            );

        if (!input) return '';

        // Rich text is rendered as text in preview;
        // never execute editor markup.
        if (input.tagName === 'TEXTAREA') {
            const parsed =
                new DOMParser()
                    .parseFromString(
                        input.value,
                        'text/html'
                    );

            return parsed.body.textContent;
        }

        return input.value;
    }


    function imageSource(name) {
        const clear =
            document.querySelector(
                `input[name="${name}-clear"]`
            );

        if (clear?.checked) {
            return '';
        }

        return (
            preview(name)?.getAttribute('src') ||
            ''
        );
    }


    function giftImage(node, name) {
        const source =
            imageSource(name);

        if (source) {
            node.src = source;
        } else {
            node.removeAttribute('src');
        }

        node.hidden = !source;
    }


    function repeaters(
        prefix,
        container,
        keys
    ) {
        container.replaceChildren();

        const count = Number(
            document.getElementById(
                `id_${prefix}-TOTAL_FORMS`
            )?.value || 0
        );

        for (
            let index = 0;
            index < count;
            index++
        ) {
            const name =
                `${prefix}-${index}`;

            if (
                document.getElementById(
                    `id_${name}-DELETE`
                )?.checked
            ) {
                continue;
            }

            const active =
                document.getElementById(
                    `id_${name}-is_active`
                );

            if (
                active &&
                !active.checked
            ) {
                continue;
            }

            const texts =
                keys.map(
                    key =>
                        value(
                            `${name}-${key}`
                        )
                );

            if (!texts.some(Boolean)) {
                continue;
            }

            const card =
                document.createElement(
                    'div'
                );

            const icon =
                document.createElement(
                    'img'
                );

            icon.alt = '';

            giftImage(
                icon,
                `${name}-icon`
            );

            card.append(icon);

            texts.forEach(
                (text, index) => {
                    const node =
                        document.createElement(
                            prefix ===
                                'statistics' &&
                            index === 1
                                ? 'strong'
                                : 'p'
                        );

                    node.textContent = text;

                    card.append(node);
                }
            );

            container.append(card);
        }
    }


    function updateGift() {
        if (!gift) return;

        gift.querySelectorAll(
            '[data-gift-text]'
        ).forEach(
            node =>
                node.textContent =
                    value(
                        node.dataset.giftText
                    )
        );

        gift.querySelectorAll(
            '[data-gift-image]'
        ).forEach(
            node =>
                giftImage(
                    node,
                    node.dataset.giftImage
                )
        );

        const background =
            imageSource(
                'background_image'
            );

        gift.style.backgroundImage =
            background
                ? `url(${JSON.stringify(
                    background
                )})`
                : '';

        repeaters(
            'features',
            gift.querySelector(
                '[data-gift-features]'
            ),
            ['text']
        );

        repeaters(
            'statistics',
            gift.querySelector(
                '[data-gift-statistics]'
            ),
            [
                'eyebrow',
                'value',
                'label'
            ]
        );
    }


    // =========================================================
    // TOOLTIPS
    // =========================================================

    function tooltips(root = document) {
        root.querySelectorAll(
            'a, button, input, select, textarea, label[for]'
        ).forEach(node => {
            if (node.title) return;

            const label =
                node.id
                    ? document.querySelector(
                        `label[for="${CSS.escape(
                            node.id
                        )}"]`
                    )
                    : null;

            const text =
                node.getAttribute(
                    'aria-label'
                ) ||
                label?.textContent ||
                (
                    node.matches(
                        'a,button,label'
                    )
                        ? node.textContent
                        : ''
                ) ||
                node.placeholder;

            if (text?.trim()) {
                node.title =
                    text
                        .trim()
                        .replace(
                            /\s+/g,
                            ' '
                        );
            }
        });

        document.querySelectorAll(
            'th.sortable a'
        ).forEach(link => {
            link.title =
                `Sort by ${link.textContent.trim()}`;
        });
    }


    // =========================================================
    // EVENTS
    // =========================================================

    document.addEventListener(
        'change',
        event => {
            if (
                event.target.matches(
                    'input[type=file]'
                )
            ) {
                updateImage(
                    event.target
                );
            }

            updateGift();
        }
    );

    document.addEventListener(
        'input',
        updateGift
    );

    document.addEventListener(
        'formset:added',
        () => {
            tooltips();
            updateGift();
        }
    );

    document.addEventListener(
        'formset:removed',
        updateGift
    );

    // Wysiwyg editors may update their
    // backing textarea without a native input event.
    if (gift) {
        setInterval(
            updateGift,
            800
        );
    }

    tooltips();
    updateGift();
})();


// =============================================================
// LIVE ADMIN SEARCH
// Search without full page refresh
// =============================================================

(() => {
    const searchForm =
        document.getElementById(
            'changelist-search'
        );

    if (!searchForm) {
        return;
    }

    const searchInput =
        searchForm.querySelector(
            'input[name="q"]'
        );

    if (!searchInput) {
        return;
    }

    let timer = null;
    let controller = null;


    // ---------------------------------------------------------
    // Replace result table
    // ---------------------------------------------------------

    const replaceResults = doc => {
        const currentForm =
            document.getElementById(
                'changelist-form'
            );

        const nextForm =
            doc.getElementById(
                'changelist-form'
            );

        if (
            currentForm &&
            nextForm
        ) {
            currentForm.replaceWith(
                nextForm
            );
        }


        // -----------------------------------------------------
        // Pagination
        // -----------------------------------------------------

        const currentPaginator =
            document.querySelector(
                '.paginator'
            );

        const nextPaginator =
            doc.querySelector(
                '.paginator'
            );

        if (
            currentPaginator &&
            nextPaginator
        ) {
            currentPaginator.replaceWith(
                nextPaginator
            );
        } else if (
            currentPaginator &&
            !nextPaginator
        ) {
            currentPaginator.remove();
        }


        // -----------------------------------------------------
        // Result count
        // -----------------------------------------------------

        const currentCount =
            document.querySelector(
                '[data-changelist-result-count]'
            );

        const nextCount =
            doc.querySelector(
                '[data-changelist-result-count]'
            );

        if (
            currentCount &&
            nextCount
        ) {
            currentCount.textContent =
                nextCount.textContent;
        }
    };


    // ---------------------------------------------------------
    // AJAX Search
    // ---------------------------------------------------------

    const runSearch =
        async () => {

        // Cancel previous search request
        if (controller) {
            controller.abort();
        }

        controller =
            new AbortController();

        const url =
            new URL(
                window.location.href
            );

        const query =
            searchInput.value.trim();


        // Add/remove Django q parameter
        if (query) {
            url.searchParams.set(
                'q',
                query
            );
        } else {
            url.searchParams.delete(
                'q'
            );
        }


        // Always start search from page 1
        url.searchParams.delete(
            'p'
        );


        searchInput.setAttribute(
            'aria-busy',
            'true'
        );


        try {

            const response =
                await fetch(
                    url.toString(),
                    {
                        method: 'GET',

                        headers: {
                            'X-Requested-With':
                                'XMLHttpRequest',
                        },

                        signal:
                            controller.signal,

                        credentials:
                            'same-origin',
                    }
                );


            if (!response.ok) {
                throw new Error(
                    `Search request failed: ${response.status}`
                );
            }


            const html =
                await response.text();


            const doc =
                new DOMParser()
                    .parseFromString(
                        html,
                        'text/html'
                    );


            // Update only results
            replaceResults(doc);


            // Update URL without refreshing browser
            window.history.replaceState(
                {},
                '',
                url.toString()
            );

        } catch (error) {

            // Ignore cancelled requests
            if (
                error.name !==
                'AbortError'
            ) {
                console.error(
                    'FABRIQX live admin search failed.',
                    error
                );
            }

        } finally {

            searchInput.removeAttribute(
                'aria-busy'
            );
        }
    };


    // ---------------------------------------------------------
    // Search while typing
    // ---------------------------------------------------------

    searchInput.addEventListener(
        'input',
        () => {

            window.clearTimeout(
                timer
            );

            // Wait 300ms after typing
            timer =
                window.setTimeout(
                    runSearch,
                    300
                );
        }
    );


    // ---------------------------------------------------------
    // Enter key should also use AJAX
    // ---------------------------------------------------------

    searchForm.addEventListener(
        'submit',
        event => {

            event.preventDefault();

            window.clearTimeout(
                timer
            );

            runSearch();
        }
    );
})();
