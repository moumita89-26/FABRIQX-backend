document.addEventListener("DOMContentLoaded", () => {
    const activeToggles = [...document.querySelectorAll('input[name$="-is_active"]')];
    const csrfToken = document.querySelector("#changelist-form input[name=csrfmiddlewaretoken]")?.value;

    activeToggles.forEach((toggle) => {
        toggle.addEventListener("change", async () => {
            const row = toggle.closest("tr");
            const id = row?.querySelector('input[name$="-id"]')?.value;
            if (!id || !csrfToken) return;

            const originalValue = !toggle.checked;
            toggle.disabled = true;
            try {
                const response = await fetch(`${window.location.pathname}${id}/toggle-active/`, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken},
                    body: JSON.stringify({is_active: toggle.checked}),
                });
                if (!response.ok) {
                    throw new Error((await response.json()).error || "Unable to update category status.");
                }
            } catch (error) {
                toggle.checked = originalValue;
                window.alert(error.message);
            } finally {
                toggle.disabled = false;
            }
        });
    });

    const handles = [...document.querySelectorAll(".category-drag-handle")];
    if (!handles.length) return;

    const hasPartialList = window.location.search !== "";
    if (hasPartialList || handles.length < 2) {
        handles.forEach((handle) => {
            handle.draggable = false;
            handle.classList.add("category-sort-disabled");
            handle.title = hasPartialList
                ? "Clear search, filters, and pagination before sorting"
                : "Add another category to enable sorting";
        });
        return;
    }

    const tbody = handles[0].closest("tbody");
    const reorderUrl = handles[0].dataset.reorderUrl;
    let draggedRow = null;

    handles.forEach((handle) => {
        handle.addEventListener("dragstart", (event) => {
            draggedRow = handle.closest("tr");
            draggedRow.classList.add("category-sort-dragging");
            event.dataTransfer.effectAllowed = "move";
        });
        handle.addEventListener("dragend", () => {
            draggedRow?.classList.remove("category-sort-dragging");
        });
    });

    tbody.addEventListener("dragover", (event) => {
        if (!draggedRow) return;
        event.preventDefault();
        const targetRow = event.target.closest("tr");
        if (!targetRow || targetRow === draggedRow) return;
        const targetBox = targetRow.getBoundingClientRect();
        const insertAfter = event.clientY > targetBox.top + targetBox.height / 2;
        tbody.insertBefore(draggedRow, insertAfter ? targetRow.nextSibling : targetRow);
    });

    tbody.addEventListener("drop", async (event) => {
        if (!draggedRow) return;
        event.preventDefault();
        const orderedIds = [...tbody.querySelectorAll(".category-drag-handle")].map(
            (handle) => Number(handle.dataset.categoryId),
        );
        document.body.classList.add("category-sort-saving");
        try {
            const response = await fetch(reorderUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken},
                body: JSON.stringify({ordered_ids: orderedIds}),
            });
            if (!response.ok) throw new Error((await response.json()).error || "Unable to save category order.");
            window.location.reload();
        } catch (error) {
            window.alert(error.message);
            window.location.reload();
        } finally {
            document.body.classList.remove("category-sort-saving");
            draggedRow = null;
        }
    });
});
