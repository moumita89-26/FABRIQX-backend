# FABRIQX Database and Business Flow

This document reflects the Django models currently implemented in the project.
Most business tables are defined in `fabriqx/models.py`. The `customers`,
`products`, `influencers`, and `content_management` apps use proxy models to
organize the Django admin and do not create duplicate database tables.

## End-to-end business flow

```mermaid
flowchart LR
    A[User account] --> R{User role}
    R -->|Customer| C[Customer profile]
    R -->|Influencer| I[Influencer profile]
    R -->|Admin| AD[Admin and staff access]

    CAT[Category] --> P[Product]
    P --> V[Product variant / SKU]
    P --> IMG[Product images]
    V --> STOCK[Inventory movements]

    C --> WL[Wishlist]
    C --> CART[Cart]
    V --> WL
    CART --> CI[Cart items]
    V --> CI
    COUPON[Coupon] --> CART

    CART --> CHECKOUT[Checkout]
    C --> CHECKOUT
    CHECKOUT --> O[Order]
    COUPON --> O
    I -->|Affiliate ID| O
    O --> OI[Order items]
    V --> OI
    O --> HIST[Order status history]
    O --> PAY[Payments]
    PAY --> REF[Refunds]
    O --> INV[Invoice]
    O --> SHIP[Shipment]
    O --> COMM[Influencer commission]
    I --> COMM
    O --> CU[Coupon usage]

    C --> REV[Reviews and questions]
    P --> REV
    OI -->|Verified purchase| REV

    AD --> AUDIT[Audit log]
    EXT[External providers] --> EVENTS[Integration events]
    SHIP --> EXT
    PAY --> EXT
```

## Entity relationship diagram

```mermaid
erDiagram
    AUTH_USER ||--|| USER_ROLE : has
    AUTH_USER ||--o| CUSTOMER_PROFILE : owns
    AUTH_USER ||--o| INFLUENCER_PROFILE : owns
    AUTH_USER ||--o{ INVENTORY_MOVEMENT : records
    AUTH_USER ||--o{ ORDER_STATUS_HISTORY : changes
    AUTH_USER ||--o{ AUDIT_LOG : performs

    CATEGORY o|--o{ CATEGORY : parent_of
    CATEGORY ||--o{ PRODUCT : contains
    PRODUCT ||--o{ PRODUCT_IMAGE : has
    PRODUCT ||--o{ PRODUCT_VARIANT : has
    PRODUCT }o--o{ PRODUCT : related_to
    PRODUCT_VARIANT ||--o{ INVENTORY_MOVEMENT : tracks

    CUSTOMER_PROFILE ||--o{ ADDRESS : has
    CUSTOMER_PROFILE ||--o{ WISHLIST_ITEM : saves
    PRODUCT ||--o{ WISHLIST_ITEM : appears_in
    PRODUCT_VARIANT o|--o{ WISHLIST_ITEM : selects
    CUSTOMER_PROFILE ||--o| CART : owns
    CART ||--o{ CART_ITEM : contains
    PRODUCT_VARIANT ||--o{ CART_ITEM : selected_as
    COUPON o|--o{ CART : applied_to

    CUSTOMER_PROFILE o|--o{ ORDER : places
    COUPON o|--o{ ORDER : discounts
    INFLUENCER_PROFILE o|--o{ ORDER : attributes
    ORDER ||--|{ ORDER_ITEM : contains
    PRODUCT_VARIANT ||--o{ ORDER_ITEM : purchased_as
    ORDER ||--o{ ORDER_STATUS_HISTORY : records
    ORDER ||--o| COUPON_USAGE : consumes
    COUPON ||--o{ COUPON_USAGE : tracks

    ORDER ||--o{ PAYMENT : receives
    PAYMENT ||--o{ REFUND : has
    ORDER ||--o| INVOICE : generates
    ORDER ||--o| SHIPMENT : ships_as
    ORDER ||--o| INFLUENCER_COMMISSION : earns
    INFLUENCER_PROFILE ||--o{ INFLUENCER_COMMISSION : receives

    PRODUCT ||--o{ REVIEW : receives
    CUSTOMER_PROFILE ||--o{ REVIEW : writes
    ORDER_ITEM o|--o| REVIEW : verifies
    PRODUCT ||--o{ PRODUCT_QUESTION : receives
    CUSTOMER_PROFILE ||--o{ PRODUCT_QUESTION : asks

    COUPON }o--o{ CATEGORY : limited_to
    COUPON }o--o{ PRODUCT : limited_to
    COUPON }o--o{ INFLUENCER_PROFILE : assigned_to
```

## Main table responsibilities

| Area | Tables/models | Purpose |
|---|---|---|
| Authentication | `auth_user`, `UserRole` | Login identity and Admin, Customer, or Influencer role |
| Customers | `CustomerProfile`, `Address`, `WishlistItem` | Customer information, delivery/billing addresses, saved products |
| Catalog | `Category`, `Product`, `ProductImage`, `ProductVariant` | Hierarchical catalog, merchandising, images, SKU/size/color and pricing |
| Inventory | `InventoryMovement` | Auditable stock increases, sales, returns, adjustments, and damage |
| Shopping | `Cart`, `CartItem` | One active cart per customer and its selected SKUs |
| Promotions | `Coupon`, `CouponUsage` | Coupon rules, applicability, attribution, and usage history |
| Orders | `Order`, `OrderItem`, `OrderStatusHistory` | Checkout snapshot, purchased SKUs, totals, and status trail |
| Finance | `Payment`, `Refund`, `Invoice` | Gateway transactions, refunds, reconciliation, and invoices |
| Fulfilment | `Shipment`, `ServiceablePincode` | Courier tracking and delivery availability |
| Influencers | `InfluencerProfile`, `InfluencerCommission` | Affiliate IDs, commission rules, order attribution, and payouts |
| Engagement | `Review`, `ProductQuestion` | Moderated reviews, verified purchases, and product Q&A |
| CMS | `Banner`, `HomepageSection`, `Testimonial`, `NewsletterSubscription` | Storefront-managed content and newsletter audience |
| Operations | `IntegrationEvent`, `AuditLog` | External API/webhook history and admin activity history |

## Important data rules

- Every user has one `UserRole`. Admin users receive staff access; customer and
  influencer users are created through their dedicated flows.
- A product belongs to one category and has one or more SKU variants. Stock is
  held on `ProductVariant`, while every adjustment is recorded in
  `InventoryMovement`.
- Address data and item names/prices are copied into an order so historical
  orders remain accurate if catalog or customer data later changes.
- An order can have multiple payment attempts and refunds, but only one invoice,
  shipment, coupon-usage record, and influencer-commission record.
- Affiliate IDs connect an order to an active influencer. Commission may be a
  percentage of the eligible amount or a fixed amount.
- `SalesReport`, `InventoryReport`, and `InfluencerReport` are proxy models used
  for reporting screens; they do not create separate database tables.
- Orders, payments, and marketing models currently remain registered but are
  temporarily hidden from the Django admin sidebar.

## Current database path by user type

```mermaid
flowchart TD
    ADMIN[Admin creation screen] --> AU[auth_user: is_staff = true]
    AU --> AR[UserRole: admin]

    REGISTER[Customer registration API] --> CU[auth_user]
    CU --> CR[UserRole: customer]
    CR --> CP[CustomerProfile]

    INFLUENCER[Influencer admin flow] --> IU[auth_user]
    IU --> IR[UserRole: influencer]
    IR --> IP[InfluencerProfile + affiliate ID]
```
