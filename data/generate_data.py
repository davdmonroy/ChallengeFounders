#!/usr/bin/env python3
"""Generate synthetic transaction data for SkyMart fraud detection demo.

Produces 550+ transactions for a simulated 24-hour window (2024-01-15)
with five embedded fraud patterns that the rules engine is designed to catch.

Usage::

    python data/generate_data.py

Output:
    data/transactions.json  -- list of transaction dictionaries.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from faker import Faker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

fake = Faker(["id_ID", "en_US"])

BASE_DATE: Final[datetime] = datetime(2024, 1, 15, 0, 0, 0)

COUNTRIES: Final[list[str]] = ["ID", "SG", "MY", "TH", "PH"]
COUNTRY_WEIGHTS: Final[list[float]] = [0.65, 0.12, 0.10, 0.08, 0.05]

PAYMENT_METHODS: Final[list[str]] = ["CREDIT_CARD", "GOPAY", "OVO", "BANK_TRANSFER"]
PAYMENT_WEIGHTS: Final[list[float]] = [0.45, 0.25, 0.20, 0.10]

PRODUCT_CATEGORIES: Final[list[str]] = ["LAPTOP", "SMARTPHONE", "CAMERA", "ACCESSORIES"]
PRODUCT_WEIGHTS: Final[list[float]] = [0.30, 0.35, 0.15, 0.20]

STATUSES: Final[list[str]] = ["APPROVED", "SOFT_DECLINED", "HARD_DECLINED"]
STATUS_WEIGHTS: Final[list[float]] = [0.80, 0.10, 0.10]

AMOUNT_RANGES: Final[dict[str, tuple[float, float]]] = {
    "LAPTOP": (400.0, 2500.0),
    "SMARTPHONE": (150.0, 1200.0),
    "CAMERA": (200.0, 900.0),
    "ACCESSORIES": (10.0, 150.0),
}

UNIT_PRICE_RANGES: Final[dict[str, tuple[float, float]]] = {
    "LAPTOP": (400.0, 2500.0),
    "SMARTPHONE": (150.0, 1200.0),
    "CAMERA": (200.0, 900.0),
    "ACCESSORIES": (5.0, 75.0),
}

VISA_BINS: Final[list[str]] = ["411111", "424242", "456789", "478012", "492150"]
MASTERCARD_BINS: Final[list[str]] = ["512345", "523456", "545454", "555555", "534210"]

SUSPICIOUS_BINS: Final[list[str]] = [
    "412345",
    "523456",
    "489012",
    "534567",
    "467890",
    "551234",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _random_timestamp(hour_start: int = 0, hour_end: int = 24) -> datetime:
    """Return a random timestamp within a given hour range on BASE_DATE.

    Applies a two-peak distribution that concentrates traffic at
    18:00-23:00 WIB (11:00-16:00 UTC) and 09:00-12:00 WIB (02:00-05:00 UTC).

    Args:
        hour_start: Earliest hour (UTC, inclusive).
        hour_end: Latest hour (UTC, exclusive).

    Returns:
        A datetime within the specified window.
    """
    # WIB peak hours converted to UTC: 18-23 WIB = 11-16 UTC, 09-12 WIB = 02-05 UTC
    peak_ranges = [(11, 16), (2, 5)]
    use_peak = random.random() < 0.55

    if use_peak:
        peak = random.choice(peak_ranges)
        effective_start = max(hour_start, peak[0])
        effective_end = min(hour_end, peak[1])
        if effective_start < effective_end:
            hour = random.randint(effective_start, effective_end - 1)
        else:
            hour = random.randint(hour_start, hour_end - 1)
    else:
        hour = random.randint(hour_start, hour_end - 1)

    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return BASE_DATE.replace(hour=hour, minute=minute, second=second)


def _random_email() -> str:
    """Generate a plausible customer email address."""
    return fake.email()


def _random_ip() -> str:
    """Generate a plausible IPv4 address."""
    return fake.ipv4_public()


def _random_card_bin(payment_method: str) -> str | None:
    """Return a 6-digit card BIN for credit cards, None otherwise.

    Args:
        payment_method: The payment method string.

    Returns:
        A BIN string or None.
    """
    if payment_method != "CREDIT_CARD":
        return None
    all_bins = VISA_BINS + MASTERCARD_BINS
    return random.choice(all_bins)


def _device_fingerprint() -> str | None:
    """Return a random hex device fingerprint or None."""
    if random.random() < 0.15:
        return None
    return uuid.uuid4().hex[:16]


def _build_transaction(
    *,
    timestamp: datetime | None = None,
    email: str | None = None,
    ip: str | None = None,
    billing_country: str | None = None,
    shipping_country: str | None = None,
    payment_method: str | None = None,
    card_bin: str | None = None,
    amount_usd: float | None = None,
    status: str | None = None,
    product_category: str | None = None,
    quantity: int | None = None,
    unit_price: float | None = None,
    is_first_purchase: bool | None = None,
    device_fingerprint: str | None = ...,  # type: ignore[assignment]
) -> dict[str, object]:
    """Build a single transaction dictionary with defaults for unset fields.

    All keyword arguments override the randomly generated defaults.

    Returns:
        A dictionary matching the required transaction schema.
    """
    ts = timestamp or _random_timestamp()
    pm = payment_method or random.choices(PAYMENT_METHODS, weights=PAYMENT_WEIGHTS, k=1)[0]
    cat = product_category or random.choices(PRODUCT_CATEGORIES, weights=PRODUCT_WEIGHTS, k=1)[0]
    bc = billing_country or random.choices(COUNTRIES, weights=COUNTRY_WEIGHTS, k=1)[0]
    sc = shipping_country if shipping_country is not None else bc
    st = status or random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]

    low, high = AMOUNT_RANGES[cat]
    amt = amount_usd if amount_usd is not None else round(random.uniform(low, high), 2)

    up_low, up_high = UNIT_PRICE_RANGES[cat]
    up = unit_price if unit_price is not None else round(random.uniform(up_low, up_high), 2)
    qty = quantity if quantity is not None else random.choices([1, 2, 3], weights=[0.7, 0.2, 0.1], k=1)[0]

    cb = card_bin if card_bin is not None else _random_card_bin(pm)
    fp = is_first_purchase if is_first_purchase is not None else (random.random() < 0.30)

    # Handle sentinel for device_fingerprint
    dfp: str | None
    if device_fingerprint is ...:
        dfp = _device_fingerprint()
    else:
        dfp = device_fingerprint  # type: ignore[assignment]

    return {
        "transaction_id": _uuid(),
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "customer_email": email or _random_email(),
        "customer_ip": ip or _random_ip(),
        "billing_country": bc,
        "shipping_country": sc,
        "card_bin": cb,
        "payment_method": pm,
        "amount_usd": round(float(amt), 2),
        "status": st,
        "product_category": cat,
        "quantity": qty,
        "unit_price": round(float(up), 2),
        "device_fingerprint": dfp,
        "is_first_purchase": fp,
    }


# ---------------------------------------------------------------------------
# Fraud pattern generators
# ---------------------------------------------------------------------------


def generate_velocity_attacks() -> list[dict[str, object]]:
    """Pattern 1: Velocity attacks -- 8 attackers, each with 4-5 rapid transactions.

    Each attacker fires 4-5 transactions within a 5-minute window using the
    same email address, simulating automated card testing.

    Returns:
        List of transaction dicts (32-40 total).
    """
    transactions: list[dict[str, object]] = []

    for _ in range(8):
        attacker_email = fake.email()
        attacker_ip = _random_ip()
        base_ts = _random_timestamp(hour_start=1, hour_end=22)
        num_txns = random.randint(4, 5)

        for j in range(num_txns):
            offset_seconds = random.randint(0, 300)  # within 5 minutes
            ts = base_ts + timedelta(seconds=offset_seconds)
            amount = round(random.uniform(50.0, 500.0), 2)
            category = random.choices(PRODUCT_CATEGORIES, weights=PRODUCT_WEIGHTS, k=1)[0]
            up_low, up_high = UNIT_PRICE_RANGES[category]

            tx = _build_transaction(
                timestamp=ts,
                email=attacker_email,
                ip=attacker_ip,
                billing_country="ID",
                shipping_country="ID",
                amount_usd=amount,
                status="APPROVED",
                product_category=category,
                quantity=1,
                unit_price=round(random.uniform(up_low, up_high), 2),
                is_first_purchase=(j == 0),
            )
            transactions.append(tx)

    return transactions


def generate_high_value_first_purchases() -> list[dict[str, object]]:
    """Pattern 2: High-value first purchases above the $1000 threshold.

    Creates 12 unique first-time buyers making large purchases of laptops
    or smartphones -- a common fraud vector.

    Returns:
        List of 12 transaction dicts.
    """
    transactions: list[dict[str, object]] = []

    for _ in range(12):
        category = random.choice(["LAPTOP", "SMARTPHONE"])
        amount = round(random.uniform(1100.0, 2400.0), 2)
        up_low, up_high = UNIT_PRICE_RANGES[category]

        tx = _build_transaction(
            email=fake.email(),
            billing_country="ID",
            shipping_country="ID",
            amount_usd=amount,
            status="APPROVED",
            product_category=category,
            quantity=1,
            unit_price=round(random.uniform(up_low, up_high), 2),
            is_first_purchase=True,
        )
        transactions.append(tx)

    return transactions


def generate_decline_sequences() -> list[dict[str, object]]:
    """Pattern 3: Multiple declines followed by a successful approval.

    Simulates 8 'test-and-hit' attackers who probe with declined transactions
    before getting one approved -- a signature of stolen card testing.

    Returns:
        List of transaction dicts (~32 total: 3 declines + 1 approval per attacker).
    """
    transactions: list[dict[str, object]] = []

    for _ in range(8):
        email = fake.email()
        ip = _random_ip()
        base_ts = _random_timestamp(hour_start=1, hour_end=22)
        category = random.choice(["LAPTOP", "SMARTPHONE", "CAMERA"])
        up_low, up_high = UNIT_PRICE_RANGES[category]

        # 3 declined transactions within 30 minutes
        for j in range(3):
            offset_minutes = random.randint(0, 30)
            ts = base_ts + timedelta(minutes=offset_minutes)
            decline_status = random.choice(["HARD_DECLINED", "SOFT_DECLINED"])

            tx = _build_transaction(
                timestamp=ts,
                email=email,
                ip=ip,
                billing_country="ID",
                shipping_country="ID",
                amount_usd=round(random.uniform(100.0, 600.0), 2),
                status=decline_status,
                product_category=category,
                quantity=1,
                unit_price=round(random.uniform(up_low, up_high), 2),
                is_first_purchase=(j == 0),
            )
            transactions.append(tx)

        # 1 final approved transaction within 1 hour of start
        final_offset = random.randint(35, 60)
        final_ts = base_ts + timedelta(minutes=final_offset)
        tx = _build_transaction(
            timestamp=final_ts,
            email=email,
            ip=ip,
            billing_country="ID",
            shipping_country="ID",
            amount_usd=round(random.uniform(100.0, 600.0), 2),
            status="APPROVED",
            product_category=category,
            quantity=1,
            unit_price=round(random.uniform(up_low, up_high), 2),
            is_first_purchase=False,
        )
        transactions.append(tx)

    return transactions


def generate_geo_mismatches() -> list[dict[str, object]]:
    """Pattern 4: Geographic mismatch between billing and shipping countries.

    Creates 8 transactions where the billing country is a Southeast Asian
    neighbour but the goods ship to Indonesia.

    Returns:
        List of 8 transaction dicts.
    """
    transactions: list[dict[str, object]] = []
    foreign_countries = ["SG", "MY", "TH"]

    for _ in range(8):
        billing = random.choice(foreign_countries)
        status = random.choices(["APPROVED", "SOFT_DECLINED"], weights=[0.7, 0.3], k=1)[0]
        category = random.choices(PRODUCT_CATEGORIES, weights=PRODUCT_WEIGHTS, k=1)[0]
        up_low, up_high = UNIT_PRICE_RANGES[category]

        tx = _build_transaction(
            email=fake.email(),
            billing_country=billing,
            shipping_country="ID",
            amount_usd=round(random.uniform(300.0, 1200.0), 2),
            status=status,
            product_category=category,
            quantity=1,
            unit_price=round(random.uniform(up_low, up_high), 2),
        )
        transactions.append(tx)

    return transactions


def generate_bin_patterns() -> list[dict[str, object]]:
    """Pattern 5: Suspicious BIN clusters -- same card prefix, many emails.

    Defines 6 suspicious BINs, each used by 3-4 different email addresses
    within a 2-hour window.  This mimics mass-produced counterfeit cards
    sharing a BIN range.

    Returns:
        List of transaction dicts (18-24 total).
    """
    transactions: list[dict[str, object]] = []

    for suspicious_bin in SUSPICIOUS_BINS:
        num_users = random.randint(3, 4)
        base_ts = _random_timestamp(hour_start=1, hour_end=21)

        for _ in range(num_users):
            offset_minutes = random.randint(0, 120)
            ts = base_ts + timedelta(minutes=offset_minutes)
            category = random.choices(PRODUCT_CATEGORIES, weights=PRODUCT_WEIGHTS, k=1)[0]
            up_low, up_high = UNIT_PRICE_RANGES[category]

            status = random.choices(
                ["APPROVED", "SOFT_DECLINED"],
                weights=[0.8, 0.2],
                k=1,
            )[0]

            tx = _build_transaction(
                timestamp=ts,
                email=fake.email(),
                payment_method="CREDIT_CARD",
                card_bin=suspicious_bin,
                amount_usd=round(random.uniform(200.0, 800.0), 2),
                status=status,
                product_category=category,
                quantity=1,
                unit_price=round(random.uniform(up_low, up_high), 2),
            )
            transactions.append(tx)

    return transactions


# ---------------------------------------------------------------------------
# Clean transaction generator
# ---------------------------------------------------------------------------


def generate_clean_transactions(count: int) -> list[dict[str, object]]:
    """Generate legitimate-looking transactions with natural distribution.

    Args:
        count: Number of clean transactions to produce.

    Returns:
        List of *count* transaction dicts.
    """
    transactions: list[dict[str, object]] = []

    for _ in range(count):
        tx = _build_transaction()
        transactions.append(tx)

    return transactions


# ---------------------------------------------------------------------------
# Dataset assembly
# ---------------------------------------------------------------------------


def generate_dataset(total: int = 550) -> list[dict[str, object]]:
    """Assemble the full synthetic dataset with embedded fraud patterns.

    Generates fraud patterns first (they require specific timing constraints),
    then fills the remainder with clean transactions to reach *total*.
    The final list is sorted chronologically by timestamp.

    Args:
        total: Minimum total number of transactions to produce.

    Returns:
        A list of at least *total* transaction dicts sorted by timestamp.
    """
    transactions: list[dict[str, object]] = []

    # 1. Generate fraud patterns first (they need specific timing)
    velocity = generate_velocity_attacks()
    high_value = generate_high_value_first_purchases()
    declines = generate_decline_sequences()
    geo = generate_geo_mismatches()
    bins = generate_bin_patterns()

    transactions.extend(velocity)
    transactions.extend(high_value)
    transactions.extend(declines)
    transactions.extend(geo)
    transactions.extend(bins)

    fraud_count = len(transactions)

    # 2. Fill remainder with clean transactions
    clean_needed = max(0, total - fraud_count)
    clean = generate_clean_transactions(clean_needed)
    transactions.extend(clean)

    # 3. Sort by timestamp
    transactions.sort(key=lambda x: str(x["timestamp"]))

    return transactions


def _print_summary(transactions: list[dict[str, object]]) -> None:
    """Print a summary of the generated dataset to stdout.

    Args:
        transactions: The full list of generated transactions.
    """
    total = len(transactions)
    statuses = {}
    methods = {}
    categories = {}
    countries = {}
    first_purchase_count = 0

    for tx in transactions:
        st = str(tx["status"])
        statuses[st] = statuses.get(st, 0) + 1

        pm = str(tx["payment_method"])
        methods[pm] = methods.get(pm, 0) + 1

        cat = str(tx["product_category"])
        categories[cat] = categories.get(cat, 0) + 1

        bc = str(tx["billing_country"])
        countries[bc] = countries.get(bc, 0) + 1

        if tx["is_first_purchase"]:
            first_purchase_count += 1

    amounts = [float(tx["amount_usd"]) for tx in transactions]
    avg_amount = sum(amounts) / len(amounts) if amounts else 0.0
    min_amount = min(amounts) if amounts else 0.0
    max_amount = max(amounts) if amounts else 0.0

    geo_mismatch_count = sum(
        1 for tx in transactions if tx["billing_country"] != tx["shipping_country"]
    )

    print(f"\n{'=' * 60}")
    print(f"  SkyMart Synthetic Transaction Dataset Summary")
    print(f"{'=' * 60}")
    print(f"  Total transactions:      {total}")
    print(f"  Date range:              {BASE_DATE.strftime('%Y-%m-%d')} (24h)")
    print()
    print(f"  --- Status Distribution ---")
    for st_key, count in sorted(statuses.items()):
        pct = count / total * 100
        print(f"    {st_key:<20s} {count:>4d}  ({pct:5.1f}%)")
    print()
    print(f"  --- Payment Methods ---")
    for pm_key, count in sorted(methods.items()):
        pct = count / total * 100
        print(f"    {pm_key:<20s} {count:>4d}  ({pct:5.1f}%)")
    print()
    print(f"  --- Product Categories ---")
    for cat_key, count in sorted(categories.items()):
        pct = count / total * 100
        print(f"    {cat_key:<20s} {count:>4d}  ({pct:5.1f}%)")
    print()
    print(f"  --- Amount Statistics ---")
    print(f"    Min:  ${min_amount:>10.2f}")
    print(f"    Max:  ${max_amount:>10.2f}")
    print(f"    Avg:  ${avg_amount:>10.2f}")
    print()
    print(f"  --- Fraud Pattern Indicators ---")
    print(f"    First purchases:       {first_purchase_count}")
    print(f"    Geo mismatches:        {geo_mismatch_count}")
    print()
    print(f"  --- Billing Countries ---")
    for c_key, count in sorted(countries.items()):
        pct = count / total * 100
        print(f"    {c_key:<5s} {count:>4d}  ({pct:5.1f}%)")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate synthetic SkyMart transaction data")
    parser.add_argument(
        "--count", type=int, default=550,
        help="Total number of transactions to generate (default: 550)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output file path (default: data/transactions.json)",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)

    print(f"Generating {args.count} transactions (seed={args.seed})...")
    dataset = generate_dataset(total=args.count)

    script_dir = Path(__file__).resolve().parent
    output_path = Path(args.output) if args.output else script_dir / "transactions.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, default=str)

    print(f"Generated {len(dataset)} transactions -> {output_path}")
    _print_summary(dataset)
