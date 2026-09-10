(() => {
    "use strict";

    const STYLE_ID = "fabriqx-password-toggle-style";

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) return;

        const style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = `
            .fabriqx-password-wrap {
                position: relative !important;
                display: block !important;
                width: 100% !important;
            }

            .fabriqx-password-wrap > input[type="password"],
            .fabriqx-password-wrap > input[data-fabriqx-password-input="true"] {
                box-sizing: border-box !important;
                padding-right: 3rem !important;
                width: 100% !important;
            }

            .fabriqx-password-toggle {
                align-items: center !important;
                background: transparent !important;
                border: 0 !important;
                box-shadow: none !important;
                color: #8a5d48 !important;
                cursor: pointer !important;
                display: inline-flex !important;
                height: 2.5rem !important;
                justify-content: center !important;
                margin: 0 !important;
                padding: 0 !important;
                position: absolute !important;
                right: .35rem !important;
                top: 50% !important;
                transform: translateY(-50%) !important;
                width: 2.5rem !important;
                z-index: 5 !important;
            }

            .fabriqx-password-toggle:hover,
            .fabriqx-password-toggle:focus {
                background: transparent !important;
                color: #cf7e27 !important;
                outline: none !important;
            }

            .fabriqx-password-toggle:focus-visible {
                outline: 2px solid #cf7e27 !important;
                outline-offset: 1px !important;
                border-radius: .4rem !important;
            }

            .fabriqx-password-toggle svg {
                display: block !important;
                height: 1.25rem !important;
                pointer-events: none !important;
                width: 1.25rem !important;
            }
        `;
        document.head.appendChild(style);
    }

    const eyeOpen = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"></path>
            <circle cx="12" cy="12" r="3"></circle>
        </svg>`;

    const eyeClosed = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="m3 3 18 18"></path>
            <path d="M10.6 10.6A2 2 0 0 0 13.4 13.4"></path>
            <path d="M9.9 4.2A10.8 10.8 0 0 1 12 4c6.5 0 10 8 10 8a17.8 17.8 0 0 1-2.1 3.2"></path>
            <path d="M6.6 6.6C3.7 8.5 2 12 2 12s3.5 8 10 8a10.5 10.5 0 0 0 5.4-1.5"></path>
        </svg>`;

    function enhancePasswordInput(input) {
        if (!(input instanceof HTMLInputElement)) return;
        if (input.dataset.fabriqxPasswordReady === "true") return;
        if (input.type !== "password") return;

        input.dataset.fabriqxPasswordReady = "true";
        input.dataset.fabriqxPasswordInput = "true";

        const parent = input.parentElement;
        if (!parent) return;

        const wrapper = document.createElement("div");
        wrapper.className = "fabriqx-password-wrap";
        parent.insertBefore(wrapper, input);
        wrapper.appendChild(input);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "fabriqx-password-toggle";
        button.setAttribute("aria-label", "Show password");
        button.setAttribute("title", "Show password");
        button.innerHTML = eyeOpen;

        button.addEventListener("click", () => {
            const shouldShow = input.type === "password";
            input.type = shouldShow ? "text" : "password";
            button.innerHTML = shouldShow ? eyeClosed : eyeOpen;
            button.setAttribute("aria-label", shouldShow ? "Hide password" : "Show password");
            button.setAttribute("title", shouldShow ? "Hide password" : "Show password");
            input.focus({ preventScroll: true });

            try {
                const position = input.value.length;
                input.setSelectionRange(position, position);
            } catch (_) {
                // Some browser/input combinations do not support setSelectionRange.
            }
        });

        wrapper.appendChild(button);
    }

    function enhanceAllPasswordInputs(root = document) {
        ensureStyles();
        root.querySelectorAll('input[type="password"]').forEach(enhancePasswordInput);
    }

    function init() {
        enhanceAllPasswordInputs();

        const observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (!(node instanceof Element)) continue;
                    if (node.matches?.('input[type="password"]')) enhancePasswordInput(node);
                    enhanceAllPasswordInputs(node);
                }
            }
        });

        observer.observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();
