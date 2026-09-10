(() => {
    let pending = false;

    /*
     * -------------------------------------------------------
     * NORMAL DELETE CONFIRMATION
     * -------------------------------------------------------
     */
    function showConfirmation(content, direct = false) {
        const dialog = document.createElement("dialog");

        dialog.className = "fabriqx-delete-dialog";

        dialog.setAttribute(
            "aria-labelledby",
            "delete-dialog-title"
        );

        dialog.append(content);

        document.body.append(dialog);


        const cancel = () => {
            /*
             * If user directly opened /delete/ URL,
             * return to the changelist page.
             */
            if (direct) {
                const returnLink =
                    content.querySelector(
                        "[data-delete-return]"
                    );

                if (returnLink) {
                    window.location.assign(
                        returnLink.href
                    );
                }

                return;
            }

            dialog.close();
            dialog.remove();
        };


        const cancelButton =
            content.querySelector(
                "[data-delete-cancel]"
            );

        if (cancelButton) {
            cancelButton.addEventListener(
                "click",
                cancel
            );
        }


        dialog.addEventListener(
            "cancel",
            (event) => {
                event.preventDefault();
                cancel();
            }
        );


        dialog.addEventListener(
            "close",
            () => {
                if (dialog.isConnected) {
                    dialog.remove();
                }
            }
        );


        /*
         * Disable Yes button after submit
         * to prevent double-click/double-delete.
         */
        const form =
            content.querySelector("form");

        if (form) {
            form.addEventListener(
                "submit",
                () => {
                    const submitButton =
                        content.querySelector(
                            'button[type="submit"]'
                        );

                    if (submitButton) {
                        submitButton.disabled = true;
                    }
                }
            );
        }


        dialog.showModal();
    }


    /*
     * -------------------------------------------------------
     * CATEGORY DELETE BLOCKED POPUP
     * -------------------------------------------------------
     *
     * Used when a Category already has Products assigned.
     *
     * We intentionally KEEP Django's PROTECT behavior.
     * We only replace Django's protected-object page
     * with a cleaner popup.
     */
    function showBlockedDelete(
        content,
        direct = false
    ) {
        const dialog =
            document.createElement("dialog");

        dialog.className =
            "fabriqx-delete-dialog";

        dialog.setAttribute(
            "aria-labelledby",
            "delete-dialog-title"
        );

        dialog.append(content);

        document.body.append(dialog);


        const closeDialog = () => {

            /*
             * Direct visit:
             *
             * /admin/products/category/ID/delete/
             *
             * Return to Categories page.
             */
            if (direct) {
                const returnLink =
                    content.querySelector(
                        "[data-delete-return]"
                    );

                if (returnLink) {
                    window.location.assign(
                        returnLink.href
                    );
                }

                return;
            }


            /*
             * Delete clicked from category list:
             * simply close popup and stay there.
             */
            dialog.close();

            if (dialog.isConnected) {
                dialog.remove();
            }
        };


        const button =
            content.querySelector(
                "[data-delete-cancel]"
            );

        if (button) {
            button.addEventListener(
                "click",
                closeDialog
            );
        }


        /*
         * ESC key also closes the popup.
         */
        dialog.addEventListener(
            "cancel",
            (event) => {
                event.preventDefault();
                closeDialog();
            }
        );


        dialog.addEventListener(
            "close",
            () => {
                if (dialog.isConnected) {
                    dialog.remove();
                }
            }
        );


        dialog.showModal();
    }


    /*
     * -------------------------------------------------------
     * LOAD DELETE PAGE USING AJAX
     * -------------------------------------------------------
     */
    async function loadConfirmation(
        url,
        options,
        fallback
    ) {
        if (pending) {
            return;
        }

        pending = true;


        try {
            const response =
                await fetch(
                    url,
                    options
                );


            const html =
                await response.text();


            const page =
                new DOMParser()
                    .parseFromString(
                        html,
                        "text/html"
                    );


            /*
             * Category with assigned products.
             */
            const blocked =
                page.querySelector(
                    "[data-delete-blocked]"
                );


            if (
                response.ok &&
                blocked
            ) {
                showBlockedDelete(
                    blocked
                );

                return;
            }


            /*
             * Normal delete confirmation.
             */
            const confirmation =
                page.querySelector(
                    "[data-delete-confirmation]"
                );


            if (
                response.ok &&
                confirmation
            ) {
                showConfirmation(
                    confirmation
                );

                return;
            }


            /*
             * Unknown delete response:
             * use normal Django page.
             */
            fallback();

        } catch (error) {

            fallback();

        } finally {

            pending = false;
        }
    }


    /*
     * -------------------------------------------------------
     * ROW DELETE LINK
     * -------------------------------------------------------
     *
     * Intercept URLs ending with /delete/
     */
    document.addEventListener(
        "click",
        (event) => {

            const link =
                event.target.closest(
                    "a[href]"
                );


            if (!link) {
                return;
            }


            if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.ctrlKey ||
                event.metaKey ||
                event.shiftKey ||
                event.altKey
            ) {
                return;
            }


            const url =
                new URL(
                    link.href,
                    window.location.href
                );


            /*
             * External URL: ignore.
             */
            if (
                url.origin !==
                window.location.origin
            ) {
                return;
            }


            /*
             * Only intercept Django delete URLs.
             */
            if (
                !url.pathname.endsWith(
                    "/delete/"
                )
            ) {
                return;
            }


            event.preventDefault();


            loadConfirmation(
                url.href,
                {},
                () => {
                    window.location.assign(
                        url.href
                    );
                }
            );
        }
    );


    /*
     * -------------------------------------------------------
     * BULK DELETE
     * -------------------------------------------------------
     */
    document.addEventListener(
        "submit",
        (event) => {

            const form =
                event.target;


            if (
                form.id !==
                "changelist-form"
            ) {
                return;
            }


            const data =
                new FormData(form);


            if (
                event.submitter &&
                event.submitter.name
            ) {
                data.set(
                    event.submitter.name,
                    event.submitter.value
                );
            }


            const actions =
                data.getAll(
                    "action"
                );


            const actionIndex =
                Number(
                    data.get("index") || 0
                );


            const action =
                actions[actionIndex] ||
                actions.find(Boolean);


            if (
                action !==
                "delete_selected"
            ) {
                return;
            }


            if (
                !data.has(
                    "_selected_action"
                )
            ) {
                return;
            }


            event.preventDefault();


            data.set(
                "action",
                action
            );


            data.delete(
                "index"
            );


            loadConfirmation(

                form.action,

                {
                    method: "POST",
                    body: data
                },

                () => {

                    const input =
                        document.createElement(
                            "input"
                        );

                    input.type =
                        "hidden";

                    input.name =
                        "index";

                    input.value =
                        event.submitter?.value ||
                        "0";


                    form.append(input);


                    HTMLFormElement
                        .prototype
                        .submit
                        .call(form);
                }
            );
        }
    );


    /*
     * -------------------------------------------------------
     * DIRECT DELETE PAGE
     * -------------------------------------------------------
     *
     * If somebody manually opens:
     *
     * /admin/products/category/39/delete/
     *
     * show popup too.
     */
    const directBlocked =
        document.querySelector(
            "[data-delete-blocked]"
        );


    if (directBlocked) {

        showBlockedDelete(
            directBlocked,
            true
        );

        return;
    }


    /*
     * Normal direct delete page.
     */
    const directConfirmation =
        document.querySelector(
            "[data-delete-confirmation]"
        );


    if (directConfirmation) {

        showConfirmation(
            directConfirmation,
            true
        );
    }
})();


/*
 * =========================================================
 * INLINE DELETE CONFIRMATION
 * =========================================================
 *
 * Inline records are removed only when the parent
 * form is saved.
 */
(() => {

    const confirmed =
        new WeakSet();


    function confirmInline(
        message,
        yes
    ) {

        const dialog =
            document.createElement(
                "dialog"
            );

        dialog.className =
            "fabriqx-delete-dialog";

        dialog.setAttribute(
            "aria-label",
            "Confirm delete"
        );


        const heading =
            document.createElement(
                "h2"
            );

        heading.textContent =
            "Confirm delete";


        const text =
            document.createElement(
                "p"
            );

        text.textContent =
            message;


        const buttons =
            document.createElement(
                "div"
            );

        buttons.className =
            "dialog-actions";


        const no =
            document.createElement(
                "button"
            );

        no.type =
            "button";

        no.textContent =
            "No";

        no.autofocus =
            true;


        const ok =
            document.createElement(
                "button"
            );

        ok.type =
            "button";

        ok.textContent =
            "Yes";


        const close = () => {

            dialog.close();

            if (
                dialog.isConnected
            ) {
                dialog.remove();
            }
        };


        no.onclick =
            close;


        ok.onclick =
            () => {

                close();

                yes();
            };


        dialog.addEventListener(
            "cancel",
            (event) => {

                event.preventDefault();

                close();
            }
        );


        buttons.append(
            no,
            ok
        );


        dialog.append(
            heading,
            text,
            buttons
        );


        document.body.append(
            dialog
        );


        dialog.showModal();
    }


    /*
     * Inline DELETE checkbox.
     */
    document.addEventListener(
        "change",
        (event) => {

            const input =
                event.target;


            if (
                !input.matches(
                    'input[type="checkbox"][name$="-DELETE"]'
                )
            ) {
                return;
            }


            if (!input.checked) {
                return;
            }


            if (
                confirmed.has(
                    input
                )
            ) {

                confirmed.delete(
                    input
                );

                return;
            }


            input.checked =
                false;


            confirmInline(
                "Delete this item when you save?",
                () => {

                    confirmed.add(
                        input
                    );


                    input.checked =
                        true;


                    input.dispatchEvent(
                        new Event(
                            "change",
                            {
                                bubbles: true
                            }
                        )
                    );
                }
            );
        },
        true
    );


    /*
     * Inline Delete link.
     */
    document.addEventListener(
        "click",
        (event) => {

            const link =
                event.target.closest(
                    "a.inline-deletelink"
                );


            if (!link) {
                return;
            }


            if (
                confirmed.has(
                    link
                )
            ) {

                confirmed.delete(
                    link
                );

                return;
            }


            event.preventDefault();

            event.stopImmediatePropagation();


            confirmInline(
                "Remove this item?",
                () => {

                    confirmed.add(
                        link
                    );

                    link.click();
                }
            );
        },
        true
    );
})();