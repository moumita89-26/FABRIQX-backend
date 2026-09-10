(function () {
    "use strict";

    /**
     * Build URL for the requested visible page number.
     *
     * Your custom pagination uses:
     * Page 1 -> no p parameter
     * Page 2 -> ?p=2
     * Page 3 -> ?p=3
     */
    function pageUrl(pageNumber) {
        const url = new URL(window.location.href);

        if (pageNumber <= 1) {
            url.searchParams.delete("p");
        } else {
            url.searchParams.set("p", String(pageNumber));
        }

        url.hash = "";

        return url.pathname + (url.search ? url.search : "");
    }


    function getPageNumber(element) {
        if (!element) {
            return null;
        }

        const text = element.textContent.trim();

        if (!/^\d+$/.test(text)) {
            return null;
        }

        const value = parseInt(text, 10);

        return Number.isFinite(value)
            ? value
            : null;
    }


    function createDisabledArrow(label, symbol) {
        const arrow = document.createElement("span");

        arrow.className =
            "fabriqx-page-arrow disabled";

        arrow.setAttribute(
            "aria-label",
            label
        );

        arrow.setAttribute(
            "aria-disabled",
            "true"
        );

        arrow.innerHTML = symbol;

        arrow.style.pointerEvents = "none";
        arrow.style.cursor = "not-allowed";
        arrow.style.opacity = "0.35";

        return arrow;
    }


    function createEnabledArrow(
        label,
        symbol,
        href
    ) {
        const arrow =
            document.createElement("a");

        arrow.className =
            "fabriqx-page-arrow";

        arrow.setAttribute(
            "aria-label",
            label
        );

        arrow.href = href;

        arrow.innerHTML = symbol;

        return arrow;
    }


    function decoratePaginator(paginator) {
        if (!paginator) {
            return;
        }

        /*
         * IMPORTANT:
         *
         * Do not continuously recreate arrows.
         * If pagination is already prepared,
         * leave it alone.
         */
        if (
            paginator.dataset
                .fabriqxPaginationReady === "1"
        ) {
            return;
        }


        /*
         * Remove arrows rendered by the template
         * before creating our controlled arrows.
         */
        paginator
            .querySelectorAll(
                ".fabriqx-page-arrow"
            )
            .forEach(function (arrow) {
                arrow.remove();
            });


        /*
         * Find currently active page.
         */
        const current =
            paginator.querySelector(
                ".this-page, " +
                "[aria-current='page']"
            );

        if (!current) {
            return;
        }


        const currentPage =
            getPageNumber(current);

        if (currentPage === null) {
            return;
        }


        /*
         * Find all numbered page controls.
         */
        const pageNumbers =
            Array.from(
                paginator.querySelectorAll(
                    "a, span"
                )
            )
                .map(function (element) {
                    return getPageNumber(
                        element
                    );
                })
                .filter(function (value) {
                    return value !== null;
                });


        if (!pageNumbers.length) {
            return;
        }


        const lastPage =
            Math.max.apply(
                Math,
                pageNumbers
            );


        /*
         * ========================
         * PREVIOUS
         * ========================
         */

        let previousArrow;

        if (currentPage <= 1) {

            previousArrow =
                createDisabledArrow(
                    "Previous page",
                    "&#8249;"
                );

        } else {

            previousArrow =
                createEnabledArrow(
                    "Previous page",
                    "&#8249;",
                    pageUrl(
                        currentPage - 1
                    )
                );
        }


        /*
         * ========================
         * NEXT
         * ========================
         */

        let nextArrow;

        if (currentPage >= lastPage) {

            nextArrow =
                createDisabledArrow(
                    "Next page",
                    "&#8250;"
                );

        } else {

            nextArrow =
                createEnabledArrow(
                    "Next page",
                    "&#8250;",
                    pageUrl(
                        currentPage + 1
                    )
                );
        }


        /*
         * Add arrows only once.
         */
        paginator.prepend(
            previousArrow
        );

        paginator.append(
            nextArrow
        );


        paginator.dataset
            .fabriqxPaginationReady = "1";
    }


    function decoratePagination() {
        document
            .querySelectorAll(
                "#fabriqx-search-pagination " +
                ".fabriqx-pagination, " +
                "#fabriqx-search-pagination " +
                ".paginator"
            )
            .forEach(function (paginator) {
                decoratePaginator(
                    paginator
                );
            });
    }


    /*
     * Initial page load.
     */
    if (
        document.readyState ===
        "loading"
    ) {

        document.addEventListener(
            "DOMContentLoaded",
            decoratePagination
        );

    } else {

        decoratePagination();
    }


    /*
     * Live search can completely replace
     * #fabriqx-search-pagination.
     *
     * Observe only for NEW pagination elements.
     * Do NOT continuously rebuild existing arrows.
     */
    const observer =
        new MutationObserver(
            function (mutations) {

                let paginationChanged =
                    false;

                mutations.forEach(
                    function (mutation) {

                        mutation.addedNodes
                            .forEach(
                                function (node) {

                                    if (
                                        node.nodeType !==
                                        Node.ELEMENT_NODE
                                    ) {
                                        return;
                                    }


                                    if (
                                        node.matches &&
                                        (
                                            node.matches(
                                                "#fabriqx-search-pagination"
                                            ) ||
                                            node.matches(
                                                ".fabriqx-pagination"
                                            ) ||
                                            node.matches(
                                                ".paginator"
                                            )
                                        )
                                    ) {
                                        paginationChanged =
                                            true;
                                    }


                                    if (
                                        node.querySelector &&
                                        node.querySelector(
                                            "#fabriqx-search-pagination, " +
                                            ".fabriqx-pagination, " +
                                            ".paginator"
                                        )
                                    ) {
                                        paginationChanged =
                                            true;
                                    }
                                }
                            );
                    }
                );


                if (paginationChanged) {
                    decoratePagination();
                }
            }
        );


    observer.observe(
        document.body,
        {
            childList: true,
            subtree: true
        }
    );

})();