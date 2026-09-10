import csv
import hashlib
import ipaddress
import socket
from decimal import Decimal, InvalidOperation
from io import BytesIO, TextIOWrapper
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError

from . import models as m


SAMPLE_COLUMNS = (
    "category", "category_audience", "product_name", "slug", "brand",
    "short_description", "description", "regular_price", "sale_price", "status",
    "is_featured", "is_trending", "is_new_arrival", "sku", "size", "color",
    "color_code", "price_override", "stock_quantity", "low_stock_threshold",
    "image_url", "image_path", "image_alt", "is_primary_image",
)

SAMPLE_ROW = {
    "category": "Dresses", "category_audience": "women", "product_name": "Silk Celebration Dress",
    "slug": "silk-celebration-dress", "brand": "FABRIQX", "short_description": "Premium silk dress",
    "description": "Detailed product description", "regular_price": "2999.00", "sale_price": "2499.00",
    "status": "active", "is_featured": "true", "is_trending": "true", "is_new_arrival": "true",
    "sku": "SILK-DRESS-M-RED", "size": "M", "color": "Red", "color_code": "#9B1C1C",
    "price_override": "", "stock_quantity": "25", "low_stock_threshold": "5",
    "image_url": "", "image_path": "", "image_alt": "Red silk celebration dress",
    "is_primary_image": "true",
}


def _validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("image_url must be a public HTTP or HTTPS URL without embedded credentials.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("image_url hostname could not be resolved.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("image_url cannot point to a private, local or reserved network address.")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        _validate_public_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _download_image(url, product_slug):
    _validate_public_url(url)
    max_bytes = int(getattr(settings, "PRODUCT_IMPORT_IMAGE_MAX_BYTES", 10 * 1024 * 1024))
    timeout = int(getattr(settings, "PRODUCT_IMPORT_IMAGE_TIMEOUT", 15))
    request = Request(url, headers={"User-Agent": "FABRIQX-Product-Importer/1.0", "Accept": "image/*"})
    with build_opener(_SafeRedirectHandler()).open(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError("Remote image exceeds the allowed size.")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Remote image exceeds the allowed size.")
    try:
        image = Image.open(BytesIO(data))
        image.verify()
        extension = (image.format or "JPEG").lower().replace("jpeg", "jpg")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("image_url did not return a valid image.") from exc
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    # Pass only a filename to ImageField.save(); the field's upload_to setting
    # chooses the local MEDIA_ROOT directory.
    return f"{product_slug}-{digest}.{extension}", ContentFile(data)


def _rows(upload):
    suffix = Path(upload.name).suffix.lower()
    if suffix == ".csv":
        yield from csv.DictReader(TextIOWrapper(upload.file, encoding="utf-8-sig"))
        return
    if suffix == ".xlsx":
        workbook = load_workbook(upload, read_only=True, data_only=True)
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(iterator)]
        for values in iterator:
            yield dict(zip(headers, values))
        return
    raise ValueError("Upload a .csv or .xlsx file.")


def _bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _decimal(value, default=None):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def _integer(value, default=0):
    if value in (None, ""):
        return default
    return int(value)


def _format_error(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            messages = []
            for field, field_errors in exc.message_dict.items():
                label = "Product" if field == "__all__" else field.replace("_", " ").capitalize()
                messages.append(f"{label}: {'; '.join(field_errors)}")
            return " ".join(messages)
        return "; ".join(exc.messages)
    return str(exc)


def _row_list(row_numbers):
    ranges = []
    start = previous = row_numbers[0]
    for number in row_numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def _process_row(raw):
    row = {str(key).strip(): value for key, value in raw.items() if key}
    required = ("category", "product_name", "regular_price", "sku")
    missing = [name for name in required if not str(row.get(name, "")).strip()]
    if missing:
        raise ValueError(f"Missing required column values: {', '.join(missing)}")

    audience = str(row.get("category_audience") or m.Category.Audience.WOMEN).strip().lower()
    if audience not in m.Category.Audience.values:
        raise ValueError(f"Invalid category_audience: {audience}")
    category, _ = m.Category.objects.get_or_create(
        name=str(row["category"]).strip(), parent=None,
        defaults={"audience": audience, "is_active": True},
    )

    slug = str(row.get("slug") or slugify(str(row["product_name"]))).strip()
    product_defaults = {
        "category": category,
        "name": str(row["product_name"]).strip(),
        "brand": str(row.get("brand") or "").strip(),
        "short_description": str(row.get("short_description") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "regular_price": _decimal(row["regular_price"], Decimal("0")),
        "sale_price": _decimal(row.get("sale_price")),
        "status": str(row.get("status") or m.Product.Status.DRAFT).strip().lower(),
        "is_featured": _bool(row.get("is_featured")),
        "is_trending": _bool(row.get("is_trending")),
        "is_new_arrival": _bool(row.get("is_new_arrival")),
    }
    if product_defaults["status"] not in m.Product.Status.values:
        raise ValueError(f"Invalid product status: {product_defaults['status']}")
    product = m.Product.objects.filter(slug=slug).first()
    created_product = product is None
    if product:
        for key, value in product_defaults.items():
            setattr(product, key, value)
    else:
        product = m.Product(slug=slug, **product_defaults)
    product.full_clean()
    product.save()

    variant_defaults = {
        "product": product,
        "size": str(row.get("size") or "").strip(),
        "color": str(row.get("color") or "").strip(),
        "color_code": str(row.get("color_code") or "").strip(),
        "price_override": _decimal(row.get("price_override")),
        "stock_quantity": _integer(row.get("stock_quantity")),
        "low_stock_threshold": _integer(row.get("low_stock_threshold"), 5),
        "is_active": True,
    }
    variant, variant_created = m.ProductVariant.objects.update_or_create(sku=str(row["sku"]).strip(), defaults=variant_defaults)

    image_url = str(row.get("image_url") or "").strip()
    image_path = str(row.get("image_path") or "").strip().replace("\\", "/")
    if not image_url and urlparse(image_path).scheme in {"http", "https"}:
        image_url, image_path = image_path, ""
    image_created = False
    if image_url:
        generated_name, content = _download_image(image_url, product.slug)
        existing_image = m.ProductImage.objects.filter(
            product=product,
            image__endswith=f"/{generated_name}",
        ).exists()
        if not existing_image:
            image = m.ProductImage(product=product, alt_text=str(row.get("image_alt") or product.name), is_primary=_bool(row.get("is_primary_image")))
            image.image.save(generated_name, content, save=True)
            image_created = True
    elif image_path:
        candidate = Path(image_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("image_path must be relative to MEDIA_ROOT and cannot contain '..'.")
        if not (settings.MEDIA_ROOT / candidate).is_file():
            raise ValueError(f"Image not found under MEDIA_ROOT: {image_path}")
        _, image_created = m.ProductImage.objects.get_or_create(
            product=product, image=image_path,
            defaults={"alt_text": str(row.get("image_alt") or product.name), "is_primary": _bool(row.get("is_primary_image"))},
        )
    return created_product, variant_created, image_created


def import_products(upload, batch_size=500):
    counts = {"rows": 0, "products": 0, "variants": 0, "images": 0, "failed": 0}
    error_rows = {}
    batch = []

    def process_batch(rows):
        for row_number, row in rows:
            try:
                with transaction.atomic():
                    product_created, variant_created, image_created = _process_row(row)
                counts["products"] += int(product_created)
                counts["variants"] += int(variant_created)
                counts["images"] += int(image_created)
            except Exception as exc:
                counts["failed"] += 1
                error = _format_error(exc)
                if error in error_rows:
                    error_rows[error].append(row_number)
                elif len(error_rows) < 50:
                    error_rows[error] = [row_number]

    for row_number, row in enumerate(_rows(upload), start=2):
        counts["rows"] += 1
        batch.append((row_number, row))
        if len(batch) >= batch_size:
            process_batch(batch)
            batch.clear()
    process_batch(batch)
    errors = [
        f"{'Row' if len(rows) == 1 else 'Rows'} {_row_list(rows)}: {error}"
        for error, rows in error_rows.items()
    ]
    return counts, errors
