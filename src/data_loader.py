"""Read-only, indexed access to the nine Olist CSV files."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DATASET_FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "product_category_translation": "product_category_name_translation.csv",
}


class DataLoadError(RuntimeError):
    """Raised when the expected Olist dataset is missing or malformed."""


class OrderNotFoundError(LookupError):
    """Raised when a claimed order ID does not occur in the orders dataset."""


def _read_csv(path: Path) -> tuple[dict[str, str], ...]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return tuple(dict(row) for row in csv.DictReader(handle))
    except OSError as exc:
        raise DataLoadError(f"Cannot read dataset {path}: {exc}") from exc


def _index_one(rows: tuple[dict[str, str], ...], key: str) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row.get(key)
        if not value:
            raise DataLoadError(f"Missing {key} in dataset row")
        if value in index:
            raise DataLoadError(f"Duplicate {key} in a one-to-one dataset: {value}")
        index[value] = row
    return index


def _index_many(
    rows: tuple[dict[str, str], ...], key: str
) -> dict[str, tuple[dict[str, str], ...]]:
    grouped: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if not value:
            raise DataLoadError(f"Missing {key} in dataset row")
        grouped[value].append(row)
    return {value: tuple(group) for value, group in grouped.items()}


@dataclass(frozen=True)
class OlistDataLoader:
    """All datasets and the indexes agents need for an order-level inquiry."""

    datasets: Mapping[str, tuple[dict[str, str], ...]]
    orders_by_id: Mapping[str, dict[str, str]]
    items_by_order_id: Mapping[str, tuple[dict[str, str], ...]]
    payments_by_order_id: Mapping[str, tuple[dict[str, str], ...]]
    reviews_by_order_id: Mapping[str, tuple[dict[str, str], ...]]
    customers_by_id: Mapping[str, dict[str, str]]
    sellers_by_id: Mapping[str, dict[str, str]]
    products_by_id: Mapping[str, dict[str, str]]

    @classmethod
    def from_directory(cls, data_dir: str | Path) -> "OlistDataLoader":
        base = Path(data_dir)
        datasets: dict[str, tuple[dict[str, str], ...]] = {}
        for dataset_name, filename in DATASET_FILES.items():
            path = base / filename
            if not path.is_file():
                raise DataLoadError(f"Expected dataset is missing: {path}")
            datasets[dataset_name] = _read_csv(path)

        return cls(
            datasets=datasets,
            orders_by_id=_index_one(datasets["orders"], "order_id"),
            items_by_order_id=_index_many(datasets["order_items"], "order_id"),
            payments_by_order_id=_index_many(datasets["order_payments"], "order_id"),
            reviews_by_order_id=_index_many(datasets["order_reviews"], "order_id"),
            customers_by_id=_index_one(datasets["customers"], "customer_id"),
            sellers_by_id=_index_one(datasets["sellers"], "seller_id"),
            products_by_id=_index_one(datasets["products"], "product_id"),
        )

    def require_order(self, order_id: str) -> Mapping[str, str]:
        try:
            return self.orders_by_id[order_id]
        except KeyError as exc:
            raise OrderNotFoundError(f"Unknown claimed_order_id: {order_id}") from exc

    def order_items(self, order_id: str) -> tuple[Mapping[str, str], ...]:
        self.require_order(order_id)
        return self.items_by_order_id.get(order_id, ())

    def order_payments(self, order_id: str) -> tuple[Mapping[str, str], ...]:
        self.require_order(order_id)
        return self.payments_by_order_id.get(order_id, ())

    def order_reviews(self, order_id: str) -> tuple[Mapping[str, str], ...]:
        self.require_order(order_id)
        return self.reviews_by_order_id.get(order_id, ())

    def seller(self, seller_id: str) -> Mapping[str, str] | None:
        return self.sellers_by_id.get(seller_id)
