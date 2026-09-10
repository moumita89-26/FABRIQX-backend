document.addEventListener("DOMContentLoaded", function () {
    function validateImage(file) {
        if (!file) {
            return false;
        }

        const allowedTypes = [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        ];

        if (!allowedTypes.includes(file.type)) {
            alert(
                "Please select a JPG, JPEG, PNG, GIF or WEBP image."
            );

            return false;
        }

        const maxSize = 5 * 1024 * 1024;

        if (file.size > maxSize) {
            alert(
                "Image file size must not exceed 5 MB."
            );

            return false;
        }

        return true;
    }

    function readPreview(file, callback) {
        const reader = new FileReader();

        reader.onload = function (event) {
            callback(event.target.result);
        };

        reader.onerror = function () {
            alert(
                "Unable to preview the selected image."
            );
        };

        reader.readAsDataURL(file);
    }


    // =========================================================
    // HERO BANNER
    // =========================================================

    const bannerInput = document.querySelector(
        'input[type="file"][name="image"]'
    );

    const bannerPreview = document.getElementById(
        "banner-image-preview"
    );

    const bannerWrapper = document.getElementById(
        "banner-preview-wrapper"
    );

    const bannerEmpty = document.getElementById(
        "banner-preview-empty"
    );

    if (
        bannerInput &&
        bannerPreview &&
        bannerWrapper
    ) {
        bannerInput.addEventListener(
            "change",
            function () {
                const file =
                    this.files &&
                    this.files[0];

                if (!file) {
                    return;
                }

                if (!validateImage(file)) {
                    this.value = "";
                    return;
                }

                readPreview(
                    file,
                    function (src) {
                        bannerPreview.src = src;

                        bannerWrapper.style.display =
                            "block";

                        if (bannerEmpty) {
                            bannerEmpty.style.display =
                                "none";
                        }
                    }
                );
            }
        );
    }


    // =========================================================
    // OFFER GRID IMAGE PREVIEW
    // =========================================================

    document.addEventListener(
        "change",
        function (event) {
            const input = event.target;

            if (
                !input.matches(
                    'input[type="file"].offer-grid-image-input, ' +
                    'input[type="file"][name$="-desktop_image"]'
                )
            ) {
                return;
            }

            const file =
                input.files &&
                input.files[0];

            if (!file) {
                return;
            }

            if (!validateImage(file)) {
                input.value = "";
                return;
            }

            const row =
                input.closest("tr") ||
                input.closest(".form-row");

            if (!row) {
                return;
            }

            const preview =
                row.querySelector(
                    ".offer-grid-image-preview"
                );

            const empty =
                row.querySelector(
                    ".offer-grid-preview-empty"
                );

            if (!preview) {
                return;
            }

            readPreview(
                file,
                function (src) {
                    preview.src = src;

                    preview.style.display =
                        "block";

                    if (empty) {
                        empty.style.display =
                            "none";
                    }
                }
            );
        }
    );
});
