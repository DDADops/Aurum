"""Full-database backup export/import (services/backup_service.py) — only
the parts this change touched: subcategories (self-referential parent_id)
and tags (many-to-many) surviving a round trip.
"""
from httpx import AsyncClient

from tests.helpers import txn_payload as _txn


async def test_backup_roundtrip_preserves_subcategories_and_tags(client: AsyncClient, account_id, categories):
    parent = await client.post("/categories", json={"name": "Custom Parent", "kind": "expense", "color": "#e34948"})
    parent_id = parent.json()["id"]
    child = await client.post(
        "/categories", json={"name": "Custom Child", "kind": "expense", "color": "#e34948", "parent_id": parent_id}
    )
    child_id = child.json()["id"]

    tag = (await client.post("/tags", json={"name": "Roundtrip"})).json()["id"]
    created = await client.post("/transactions", json=_txn(account_id, category_id=child_id, tag_ids=[tag]))
    txn_id = created.json()["id"]

    export_resp = await client.get("/backup/export")
    assert export_resp.status_code == 200
    payload = export_resp.json()

    import_resp = await client.post("/backup/import", json=payload)
    assert import_resp.status_code == 200, import_resp.text

    refetched_categories = {c["id"]: c for c in (await client.get("/categories")).json()}
    assert refetched_categories[child_id]["parent_id"] == parent_id

    refetched_txn = next(t for t in (await client.get("/transactions")).json()["items"] if t["id"] == txn_id)
    assert [t["id"] for t in refetched_txn["tags"]] == [tag]


async def test_backup_import_rejects_transaction_with_unknown_tag_id(client: AsyncClient, account_id, categories):
    export_resp = await client.get("/backup/export")
    payload = export_resp.json()

    created = await client.post(
        "/transactions", json=_txn(account_id, category_id=categories["Groceries"]["id"], date="2026-01-01")
    )
    payload["transactions"] = (await client.get("/transactions")).json()["items"]
    # Reshape to the backup wire format (flat FKs, not nested account/category
    # objects) and inject a tag_id that doesn't exist in payload["tags"].
    payload["transactions"] = [
        {
            "id": t["id"],
            "account_id": t["account_id"],
            "category_id": t["category_id"],
            "transfer_account_id": t["transfer_account_id"],
            "type": t["type"],
            "amount": t["amount"],
            "transfer_amount": t["transfer_amount"],
            "description": t["description"],
            "merchant": t["merchant"],
            "notes": t["notes"],
            "date": t["date"],
            "tag_ids": [999999] if t["id"] == created.json()["id"] else [],
        }
        for t in payload["transactions"]
    ]

    resp = await client.post("/backup/import", json=payload)
    assert resp.status_code == 400


async def test_backup_roundtrip_preserves_transaction_splits(client: AsyncClient, account_id, categories):
    groceries = categories["Groceries"]["id"]
    sweets = (
        await client.post("/categories", json={"name": "Sweets", "kind": "expense", "color": "#7a869a", "parent_id": groceries})
    ).json()["id"]
    created = await client.post(
        "/transactions",
        json=_txn(
            account_id,
            amount="100.00",
            category_id=None,
            splits=[
                {"category_id": groceries, "amount": "70.00"},
                {"category_id": sweets, "amount": "30.00", "note": "candy and snacks"},
            ],
        ),
    )
    txn_id = created.json()["id"]

    payload = (await client.get("/backup/export")).json()
    assert len(payload["transaction_splits"]) == 2

    import_resp = await client.post("/backup/import", json=payload)
    assert import_resp.status_code == 200, import_resp.text

    refetched = next(t for t in (await client.get("/transactions")).json()["items"] if t["id"] == txn_id)
    splits_by_note = {s["note"]: s for s in refetched["splits"]}
    assert splits_by_note["candy and snacks"]["category_id"] == sweets
    assert refetched["category"] is None
