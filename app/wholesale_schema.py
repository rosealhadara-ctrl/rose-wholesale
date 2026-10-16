"""Additive SQLite schema for the Rose wholesale module (روز جملة).

Every table is prefixed ``ws_`` and lives only in the Rose database.  Wholesale
is fully separate from Yaseer: nothing here reads from or writes to Yaseer.
The migration is idempotent and only ever creates missing tables/rows.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .db import connect, db_path, utc_now


WHOLESALE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_column_templates (
    supplier_id INTEGER PRIMARY KEY REFERENCES ws_suppliers(id) ON DELETE CASCADE,
    mapping_json TEXT NOT NULL,
    header_row INTEGER NOT NULL DEFAULT 1,
    sheet_name TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    section_id INTEGER REFERENCES ws_sections(id) ON DELETE SET NULL,
    pieces_per_dozen INTEGER CHECK(pieces_per_dozen IS NULL OR pieces_per_dozen > 0),
    pieces_per_carton INTEGER CHECK(pieces_per_carton IS NULL OR pieces_per_carton > 0),
    image_path TEXT NOT NULL DEFAULT '',
    is_visible INTEGER NOT NULL DEFAULT 1 CHECK(is_visible IN (0, 1)),
    source_mode TEXT NOT NULL DEFAULT 'cheapest'
        CHECK(source_mode IN ('cheapest', 'manual')),
    manual_supplier_id INTEGER REFERENCES ws_suppliers(id) ON DELETE SET NULL,
    margin_percent REAL CHECK(margin_percent IS NULL OR margin_percent > -100),
    fixed_price_piece REAL CHECK(fixed_price_piece IS NULL OR fixed_price_piece >= 0),
    fixed_price_dozen REAL CHECK(fixed_price_dozen IS NULL OR fixed_price_dozen >= 0),
    fixed_price_carton REAL CHECK(fixed_price_carton IS NULL OR fixed_price_carton >= 0),
    created_by TEXT NOT NULL DEFAULT 'import' CHECK(created_by IN ('import', 'manual')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_products_name_key ON ws_products(name_key);
CREATE INDEX IF NOT EXISTS idx_ws_products_section ON ws_products(section_id);

CREATE TABLE IF NOT EXISTS ws_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES ws_products(id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES ws_suppliers(id) ON DELETE CASCADE,
    supplier_code TEXT NOT NULL DEFAULT '',
    supplier_name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    section_text TEXT NOT NULL DEFAULT '',
    price_piece REAL CHECK(price_piece IS NULL OR price_piece >= 0),
    price_dozen REAL CHECK(price_dozen IS NULL OR price_dozen >= 0),
    price_carton REAL CHECK(price_carton IS NULL OR price_carton >= 0),
    pieces_per_dozen INTEGER CHECK(pieces_per_dozen IS NULL OR pieces_per_dozen > 0),
    pieces_per_carton INTEGER CHECK(pieces_per_carton IS NULL OR pieces_per_carton > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    last_import_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(product_id, supplier_id)
);

CREATE INDEX IF NOT EXISTS idx_ws_offers_supplier_code ON ws_offers(supplier_id, supplier_code);
CREATE INDEX IF NOT EXISTS idx_ws_offers_supplier_name ON ws_offers(supplier_id, name_key);
CREATE INDEX IF NOT EXISTS idx_ws_offers_product ON ws_offers(product_id, is_active);

CREATE TABLE IF NOT EXISTS ws_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL REFERENCES ws_suppliers(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    sheet_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'uploaded'
        CHECK(status IN ('uploaded', 'previewed', 'applied', 'cancelled')),
    matrix_json TEXT NOT NULL,
    mapping_json TEXT NOT NULL DEFAULT '{}',
    header_row INTEGER NOT NULL DEFAULT 1,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_imports_supplier ON ws_imports(supplier_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ws_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES ws_offers(id) ON DELETE CASCADE,
    unit TEXT NOT NULL CHECK(unit IN ('piece', 'dozen', 'carton')),
    old_price REAL,
    new_price REAL,
    import_id INTEGER,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_price_history_offer ON ws_price_history(offer_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS ws_customer_tiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    margin_percent REAL CHECK(margin_percent IS NULL OR margin_percent > -100),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
    is_subscriber INTEGER NOT NULL DEFAULT 0 CHECK(is_subscriber IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_trader_tiers (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    tier_id INTEGER NOT NULL REFERENCES ws_customer_tiers(id) ON DELETE CASCADE,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_tier_prices (
    product_id INTEGER NOT NULL REFERENCES ws_products(id) ON DELETE CASCADE,
    tier_id INTEGER NOT NULL REFERENCES ws_customer_tiers(id) ON DELETE CASCADE,
    unit TEXT NOT NULL CHECK(unit IN ('piece', 'dozen', 'carton')),
    price REAL NOT NULL CHECK(price >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(product_id, tier_id, unit)
);

CREATE TABLE IF NOT EXISTS ws_subscription_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'rejected')),
    note TEXT NOT NULL DEFAULT '',
    tier_id INTEGER REFERENCES ws_customer_tiers(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ws_subscription_requests_status
ON ws_subscription_requests(status, created_at DESC);

-- Phase 3: traders, orders, purchase orders, shortages, in-site notifications.

CREATE TABLE IF NOT EXISTS ws_traders (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'rejected', 'suspended')),
    shop_name TEXT NOT NULL DEFAULT '',
    area TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ws_traders_status ON ws_traders(status, created_at DESC);

CREATE TABLE IF NOT EXISTS ws_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'submitted',
    tier_id INTEGER,
    items_total REAL NOT NULL DEFAULT 0,
    final_total REAL NOT NULL DEFAULT 0,
    customer_note TEXT NOT NULL DEFAULT '',
    cancel_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    received_at TEXT,
    cancelled_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_orders_status ON ws_orders(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_ws_orders_customer ON ws_orders(customer_id, id DESC);

CREATE TABLE IF NOT EXISTS ws_purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES ws_orders(id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES ws_suppliers(id),
    suffix TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'sent', 'confirmed', 'received', 'cancelled')),
    sent_at TEXT,
    confirmed_at TEXT,
    received_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(order_id, supplier_id)
);

CREATE INDEX IF NOT EXISTS idx_ws_purchase_orders_status ON ws_purchase_orders(status, id DESC);

CREATE TABLE IF NOT EXISTS ws_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES ws_orders(id) ON DELETE CASCADE,
    purchase_order_id INTEGER REFERENCES ws_purchase_orders(id) ON DELETE SET NULL,
    product_id INTEGER REFERENCES ws_products(id) ON DELETE SET NULL,
    supplier_id INTEGER REFERENCES ws_suppliers(id),
    name TEXT NOT NULL,
    supplier_item_name TEXT NOT NULL DEFAULT '',
    supplier_code TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL CHECK(unit IN ('piece', 'dozen', 'carton')),
    pieces INTEGER,
    qty INTEGER NOT NULL CHECK(qty > 0),
    unit_price REAL NOT NULL,
    unit_cost REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending', 'available', 'partial', 'unavailable')),
    confirmed_qty INTEGER,
    received_qty INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_order_items_order ON ws_order_items(order_id, id);

CREATE TABLE IF NOT EXISTS ws_shortages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES ws_products(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'piece',
    qty INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL CHECK(source IN ('review', 'receipt', 'picking', 'request')),
    order_id INTEGER REFERENCES ws_orders(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting'
        CHECK(status IN ('waiting', 'available', 'closed')),
    created_at TEXT NOT NULL,
    notified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_shortages_status ON ws_shortages(status, product_id);
CREATE INDEX IF NOT EXISTS idx_ws_shortages_customer ON ws_shortages(customer_id, status);

CREATE TABLE IF NOT EXISTS ws_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    order_id INTEGER,
    product_id INTEGER,
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_notifications_customer ON ws_notifications(customer_id, read_at, id DESC);

-- Phase 4: payment methods, payments (transfer proofs), trader credit (آجل).

CREATE TABLE IF NOT EXISTS ws_payment_methods (
    code TEXT PRIMARY KEY CHECK(code IN ('cash', 'instapay', 'wallet', 'credit')),
    label TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 0 CHECK(is_enabled IN (0, 1)),
    account TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    fee_type TEXT NOT NULL DEFAULT 'percent' CHECK(fee_type IN ('percent', 'fixed')),
    fee_value REAL NOT NULL DEFAULT 0 CHECK(fee_value >= 0),
    fee_payer TEXT NOT NULL DEFAULT 'customer' CHECK(fee_payer IN ('customer', 'store')),
    max_amount REAL NOT NULL DEFAULT 0 CHECK(max_amount >= 0),
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_id INTEGER REFERENCES ws_orders(id) ON DELETE SET NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('order', 'credit')),
    method TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    proof_path TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'rejected')),
    reject_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ws_payments_status ON ws_payments(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_ws_payments_order ON ws_payments(order_id);

CREATE TABLE IF NOT EXISTS ws_credit_accounts (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    credit_limit REAL NOT NULL DEFAULT 0 CHECK(credit_limit >= 0),
    period_days INTEGER CHECK(period_days IS NULL OR period_days > 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_credit_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('charge', 'payment', 'adjustment')),
    amount REAL NOT NULL,
    order_id INTEGER REFERENCES ws_orders(id) ON DELETE SET NULL,
    payment_id INTEGER REFERENCES ws_payments(id) ON DELETE SET NULL,
    method TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_credit_entries_customer ON ws_credit_entries(customer_id, id);

-- Phase 5: prep employees (mobile login with phone + 4-digit code).

CREATE TABLE IF NOT EXISTS ws_pickers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    pin_hash TEXT NOT NULL,
    pin_salt TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_delivery_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    fee REAL NOT NULL DEFAULT 0 CHECK(fee >= 0),
    min_order REAL NOT NULL DEFAULT 0 CHECK(min_order >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_driver_settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_id INTEGER NOT NULL REFERENCES ws_pickers(id),
    amount REAL NOT NULL,
    orders INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES ws_orders(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK(status IN ('requested', 'accepted', 'rejected', 'picked_up', 'inspected', 'refunded')),
    note TEXT NOT NULL DEFAULT '',
    photo_path TEXT NOT NULL DEFAULT '',
    pickup TEXT NOT NULL DEFAULT 'driver' CHECK(pickup IN ('driver', 'store')),
    driver_id INTEGER REFERENCES ws_pickers(id),
    reject_reason TEXT NOT NULL DEFAULT '',
    max_refund REAL NOT NULL DEFAULT 0,
    refund_amount REAL NOT NULL DEFAULT 0,
    refund_cash REAL NOT NULL DEFAULT 0,
    refund_credit REAL NOT NULL DEFAULT 0,
    points_removed INTEGER NOT NULL DEFAULT 0,
    inspected_by INTEGER,
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    picked_up_at TEXT,
    inspected_at TEXT,
    refunded_at TEXT,
    decided_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ws_returns_status ON ws_returns(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_ws_returns_order ON ws_returns(order_id);

CREATE TABLE IF NOT EXISTS ws_return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id INTEGER NOT NULL REFERENCES ws_returns(id) ON DELETE CASCADE,
    order_item_id INTEGER NOT NULL REFERENCES ws_order_items(id),
    qty INTEGER NOT NULL CHECK(qty > 0),
    reason TEXT NOT NULL CHECK(reason IN ('damaged', 'wrong_item', 'near_expiry', 'not_needed')),
    sound_qty INTEGER,
    damaged_qty INTEGER,
    missing_qty INTEGER
);

CREATE TABLE IF NOT EXISTS ws_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES ws_orders(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
    liked TEXT NOT NULL DEFAULT '',
    improve TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    kind TEXT NOT NULL CHECK(kind IN ('complaint', 'suggestion', 'rating')),
    order_id INTEGER REFERENCES ws_orders(id),
    rating_id INTEGER REFERENCES ws_ratings(id),
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
    reply TEXT NOT NULL DEFAULT '',
    replied_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_feedback_status ON ws_feedback(status, kind, id DESC);

CREATE TABLE IF NOT EXISTS ws_plus_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    plan TEXT NOT NULL CHECK(plan IN ('month', 'quarter')),
    price REAL NOT NULL DEFAULT 0,
    method TEXT NOT NULL CHECK(method IN ('cash', 'instapay', 'wallet', 'admin')),
    proof_path TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK(status IN ('requested', 'active', 'rejected', 'cancelled')),
    starts_at TEXT,
    ends_at TEXT,
    reject_reason TEXT NOT NULL DEFAULT '',
    reminded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ws_plus_customer ON ws_plus_subscriptions(customer_id, status, ends_at);

CREATE TABLE IF NOT EXISTS ws_notebook_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    product_id INTEGER REFERENCES ws_products(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'piece' CHECK(unit IN ('piece', 'dozen', 'carton')),
    qty INTEGER NOT NULL DEFAULT 1 CHECK(qty > 0),
    note TEXT NOT NULL DEFAULT '',
    voice_path TEXT NOT NULL DEFAULT '',
    request_id INTEGER REFERENCES ws_shortages(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'moved', 'removed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(customer_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_ws_notebook_customer ON ws_notebook_items(customer_id, status, id);

CREATE TABLE IF NOT EXISTS ws_notebook_settings (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    reminder_day INTEGER CHECK(reminder_day BETWEEN 0 AND 6),
    reminder_hour INTEGER NOT NULL DEFAULT 10 CHECK(reminder_hour BETWEEN 0 AND 23),
    last_reminded_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_social_profiles (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    visible INTEGER NOT NULL DEFAULT 0 CHECK(visible IN (0, 1)),
    show_phone INTEGER NOT NULL DEFAULT 0 CHECK(show_phone IN (0, 1)),
    bio TEXT NOT NULL DEFAULT '',
    chat_suspended INTEGER NOT NULL DEFAULT 0 CHECK(chat_suspended IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_chat_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    a_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    b_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_message_at TEXT,
    UNIQUE(a_id, b_id),
    CHECK(a_id < b_id)
);

CREATE TABLE IF NOT EXISTS ws_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES ws_chat_threads(id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'text' CHECK(kind IN ('text', 'system', 'split')),
    body TEXT NOT NULL DEFAULT '',
    split_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_chat_messages_thread ON ws_chat_messages(thread_id, id);

CREATE TABLE IF NOT EXISTS ws_chat_reads (
    thread_id INTEGER NOT NULL REFERENCES ws_chat_threads(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(thread_id, customer_id)
);

CREATE TABLE IF NOT EXISTS ws_chat_blocks (
    blocker_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(blocker_id, blocked_id)
);

CREATE TABLE IF NOT EXISTS ws_chat_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    reported_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    thread_id INTEGER REFERENCES ws_chat_threads(id) ON DELETE SET NULL,
    message_id INTEGER REFERENCES ws_chat_messages(id) ON DELETE SET NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    admin_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS ws_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES ws_products(id) ON DELETE CASCADE,
    pieces_total INTEGER NOT NULL CHECK(pieces_total > 1),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'full', 'ordered', 'cancelled')),
    zone_id INTEGER,
    area_key TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_splits_status ON ws_splits(status, expires_at);

CREATE TABLE IF NOT EXISTS ws_split_shares (
    split_id INTEGER NOT NULL REFERENCES ws_splits(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    pieces INTEGER NOT NULL CHECK(pieces > 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY(split_id, customer_id)
);

CREATE TABLE IF NOT EXISTS ws_trader_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    pin_hash TEXT NOT NULL,
    pin_salt TEXT NOT NULL,
    can_see_prices INTEGER NOT NULL DEFAULT 0 CHECK(can_see_prices IN (0, 1)),
    can_send_direct INTEGER NOT NULL DEFAULT 0 CHECK(can_send_direct IN (0, 1)),
    can_use_notebook INTEGER NOT NULL DEFAULT 1 CHECK(can_use_notebook IN (0, 1)),
    direct_method TEXT NOT NULL DEFAULT 'cash' CHECK(direct_method IN ('cash', 'credit')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_trader_staff_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL REFERENCES ws_trader_staff(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_team_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES ws_trader_staff(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'ordered')),
    note TEXT NOT NULL DEFAULT '',
    manager_note TEXT NOT NULL DEFAULT '',
    order_id INTEGER REFERENCES ws_orders(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_team_requests_owner ON ws_team_requests(owner_id, status, id DESC);

CREATE TABLE IF NOT EXISTS ws_team_request_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES ws_team_requests(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES ws_products(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL CHECK(unit IN ('piece', 'dozen', 'carton')),
    qty INTEGER NOT NULL CHECK(qty > 0),
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ws_trader_staff_notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL REFERENCES ws_trader_staff(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    product_id INTEGER,
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_point_accounts (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    lifetime_earned INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_point_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('earn', 'return', 'redeem', 'refund', 'adjust')),
    points INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ws_point_entries_customer ON ws_point_entries(customer_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_ws_point_entries_source ON ws_point_entries(source_type, source_id, kind);

CREATE TABLE IF NOT EXISTS ws_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cost_points INTEGER NOT NULL CHECK(cost_points > 0),
    stock INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ws_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    reward_id INTEGER NOT NULL REFERENCES ws_rewards(id),
    points INTEGER NOT NULL,
    code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'used', 'cancelled')),
    created_at TEXT NOT NULL,
    used_at TEXT,
    used_by INTEGER
);

CREATE TABLE IF NOT EXISTS ws_picker_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    picker_id INTEGER NOT NULL REFERENCES ws_pickers(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added to tables that may already exist from an earlier phase.
ADDED_COLUMNS = [
    ("ws_orders", "payment_method", "TEXT NOT NULL DEFAULT 'cash'"),
    ("ws_orders", "payment_status", "TEXT NOT NULL DEFAULT 'unpaid'"),
    ("ws_orders", "delivery_fee", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "payment_fee", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "fee_payer", "TEXT NOT NULL DEFAULT 'customer'"),
    ("ws_orders", "grand_total", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "paid_amount", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "refunded_amount", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "points_awarded", "INTEGER NOT NULL DEFAULT 0"),
    ("ws_orders", "delivered_at", "TEXT"),
    ("ws_orders", "picker_id", "INTEGER"),
    ("ws_orders", "pick_started_at", "TEXT"),
    ("ws_orders", "pick_finished_at", "TEXT"),
    ("ws_orders", "pick_seconds", "INTEGER"),
    ("ws_order_items", "picked_qty", "INTEGER"),
    ("ws_order_items", "pick_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("ws_purchase_orders", "received_by_picker", "INTEGER"),
    # Phase 6: delivery.
    ("ws_pickers", "role", "TEXT NOT NULL DEFAULT 'picker'"),
    ("ws_traders", "zone_id", "INTEGER"),
    ("ws_traders", "address", "TEXT NOT NULL DEFAULT ''"),
    ("ws_orders", "zone_id", "INTEGER"),
    ("ws_orders", "delivery_address", "TEXT NOT NULL DEFAULT ''"),
    ("ws_orders", "driver_id", "INTEGER"),
    ("ws_orders", "delivery_status", "TEXT NOT NULL DEFAULT ''"),
    ("ws_orders", "delivery_code", "TEXT NOT NULL DEFAULT ''"),
    ("ws_orders", "delivery_eta", "TEXT NOT NULL DEFAULT ''"),
    ("ws_orders", "delivery_note", "TEXT NOT NULL DEFAULT ''"),
    ("ws_orders", "code_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("ws_orders", "assigned_at", "TEXT"),
    ("ws_orders", "out_at", "TEXT"),
    ("ws_orders", "arrived_at", "TEXT"),
    ("ws_orders", "cash_collected", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "settlement_id", "INTEGER"),
    # Phase 8: Rose Plus, locked on the order when it is sent.
    ("ws_orders", "is_plus", "INTEGER NOT NULL DEFAULT 0"),
    ("ws_orders", "plus_delivery_saved", "REAL NOT NULL DEFAULT 0"),
    ("ws_orders", "plus_fee_saved", "REAL NOT NULL DEFAULT 0"),
]

PAYMENT_METHODS = [
    # code, label, enabled by default, sort
    ("cash", "كاش عند الاستلام", 1, 10),
    ("instapay", "انستاباي", 0, 20),
    ("wallet", "محفظة إلكترونية", 0, 30),
    ("credit", "على الحساب (آجل)", 0, 40),
]

DEFAULT_SETTINGS = {
    "global_margin_percent": "15",
    "default_source_mode": "cheapest",
    "announcement_enabled": "0",
    "announcement_title": "اشترك واطلب بسعر المورد",
    "announcement_text": "اشتراك روز للتجار بيخليك تشتري بأسعار أقل على كل الأصناف. اطلب الاشتراك والإدارة هتراجع طلبك.",
    # Phase 4. Zero means "off" until the owner sets a real number.
    "min_order_total": "0",
    "delivery_fee": "0",
    "points_egp_per_point": "0",
    "credit_period_days": "30",
    "credit_block_overdue": "1",
    "return_days": "15",
    # Phase 8: Rose Plus. Off and priced at zero until the owner sets real prices.
    "plus_enabled": "0",
    "plus_price_month": "0",
    "plus_price_quarter": "0",
    "plus_free_delivery": "1",
    "plus_fee_share": "50",  # percent of the payment commission a Plus member pays
}

_migrated: set[str] = set()
_lock = threading.Lock()


def migrate_wholesale_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(WHOLESALE_SCHEMA)
    now = utc_now()
    for table, column, definition in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    for code, label, enabled, sort in PAYMENT_METHODS:
        conn.execute(
            "INSERT OR IGNORE INTO ws_payment_methods(code, label, is_enabled, sort_order, updated_at) VALUES(?, ?, ?, ?, ?)",
            (code, label, enabled, sort, now),
        )
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO ws_settings(key, value, updated_at) VALUES(?, ?, ?)",
            (key, value, now),
        )
    has_tiers = conn.execute("SELECT 1 FROM ws_customer_tiers LIMIT 1").fetchone()
    if not has_tiers:
        conn.execute(
            """
            INSERT INTO ws_customer_tiers(
                name, margin_percent, is_default, is_subscriber, sort_order, created_at, updated_at
            ) VALUES('عادي', NULL, 1, 0, 0, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO ws_customer_tiers(
                name, margin_percent, is_default, is_subscriber, sort_order, created_at, updated_at
            ) VALUES('مشترك', 0, 0, 1, 10, ?, ?)
            """,
            (now, now),
        )


@contextmanager
def ws_connect() -> Iterator[sqlite3.Connection]:
    """Open the Rose database, creating the wholesale tables on first use.

    The wholesale tables are created lazily here so the shared startup code in
    ``app/db.py`` does not need to change.
    """
    key = str(db_path())
    with connect() as conn:
        if key not in _migrated:
            with _lock:
                if key not in _migrated:
                    migrate_wholesale_schema(conn)
                    conn.commit()
                    _migrated.add(key)
        yield conn
