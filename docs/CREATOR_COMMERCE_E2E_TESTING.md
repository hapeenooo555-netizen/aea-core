# Creator commerce E2E test (no merchant account required)

The deterministic E2E suite is deliberately provider-neutral. It exercises the real
FastAPI purchase-intent and buyer-delivery routes with an in-memory Supabase
stand-in and a fake payment provider adapter defined only in
`backend/tests/test_creator_commerce_e2e.py`.

## Run locally

From the repository root:

```bash
PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_creator_store.py \
  backend/tests/test_creator_store_marketplace.py \
  backend/tests/test_creator_commerce_e2e.py
```

## Covered transaction

1. Buyer creates an idempotent purchase intent for a listed, finished product
   with a verified private file.
2. Order starts as `pending`; payment initiation remains disabled.
3. Attempted download is denied while pending.
4. Fake provider creates an HMAC-SHA256-signed success event.
5. Invalid signature is rejected without changing order or entitlements.
6. Valid test event calls the fake in-memory settlement adapter; amount and
   currency must match exactly.
7. Successful settlement changes order to `paid` and grants one item-specific
   entitlement.
8. Replayed provider event is idempotent and does not duplicate event or
   entitlement.
9. Buyer with the active entitlement receives a 5-minute signed private URL.
10. Wrong amount/currency is rejected and does not grant access.

## Safety boundary

- The fake provider and its secret are test fixtures only.
- There is no fake payment route in the production API.
- The E2E adapter settles only an in-memory test database; it cannot access
  production Supabase or issue a real transfer.
- The SQL `settle_creator_order` RPC is service-role-only. A future real
  provider webhook must validate the provider signature, event authenticity,
  transaction reference, amount and currency before calling it.
- This suite does not prove a real provider integration or production storage
  configuration. Those require staging credentials and a merchant account.
