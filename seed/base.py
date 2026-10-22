"""
Base seed: creates the minimum data needed for most scenarios.

This script sets up:
- An editor account with 2 customers
- A reviewer linked to the editor
- Products in the catalog
- An admin account

All operations are idempotent — safe to run multiple times.
"""

import asyncio
import sys

import httpx

# API endpoints (adjust these based on your docker-compose setup)
ADMIN_API = "http://localhost:8000"
EDITOR_API = "http://localhost:8001"
REVIEWER_API = "http://localhost:8002"


async def seed():
    """Set up test data for scenarios."""
    print("Running base seed script...")

    async with httpx.AsyncClient(timeout=30.0) as client:  # noqa: F841 -- used by the commented examples below
        try:
            # ──────────────────────────────────────────────────────────────
            # TODO: Create or ensure editor account exists
            # ──────────────────────────────────────────────────────────────
            # Example (adjust to your API):
            # editor_response = await client.post(
            #     f"{EDITOR_API}/api/users",
            #     json={
            #         "email": "editor@test.com",
            #         "password": "test123",
            #         "role": "editor",
            #         "first_name": "Test",
            #         "last_name": "Editor",
            #     },
            # )
            # if editor_response.status_code in (200, 201, 409):  # 409 = already exists
            #     print("✓ Editor account created or already exists")
            # else:
            #     raise Exception(f"Failed to create editor: {editor_response.text}")

            print("  TODO: Create or ensure editor account exists")

            # ──────────────────────────────────────────────────────────────
            # TODO: Create or ensure customers exist
            # ──────────────────────────────────────────────────────────────
            # Example customers:
            # customers = [
            #     {"first_name": "Jean", "last_name": "Dupont", "dob": "1950-01-15"},
            #     {"first_name": "Marie", "last_name": "Martin", "dob": "1965-03-22"},
            # ]
            # for customer in customers:
            #     response = await client.post(
            #         f"{EDITOR_API}/api/customers",
            #         json=customer,
            #     )
            #     if response.status_code in (200, 201, 409):
            #         print(f"✓ Customer {customer['first_name']} {customer['last_name']} created or exists")
            #     else:
            #         raise Exception(f"Failed to create customer: {response.text}")

            print("  TODO: Create or ensure customers exist (Jean Dupont, Marie Martin)")

            # ──────────────────────────────────────────────────────────────
            # TODO: Create or ensure reviewer exists and is linked
            # ──────────────────────────────────────────────────────────────
            # Example:
            # reviewer_response = await client.post(
            #     f"{REVIEWER_API}/api/reviewers",
            #     json={
            #         "name": "Central Review Team",
            #         "address": "123 Main Street",
            #     },
            # )
            # if reviewer_response.status_code in (200, 201, 409):
            #     print("✓ Reviewer created or already exists")
            # else:
            #     raise Exception(f"Failed to create reviewer: {reviewer_response.text}")

            print("  TODO: Create or ensure reviewer exists (Central Review Team)")

            # ──────────────────────────────────────────────────────────────
            # TODO: Create or ensure products exist in catalog
            # ──────────────────────────────────────────────────────────────
            # Example products:
            # products = [
            #     {"name": "Standing desk", "code": "DESK-01", "stock": 1000},
            #     {"name": "Office chair", "code": "CHAIR-02", "stock": 500},
            #     {"name": "Monitor arm", "code": "ARM-03", "stock": 250},
            # ]
            # for product in products:
            #     response = await client.post(
            #         f"{ADMIN_API}/api/products",
            #         json=product,
            #     )
            #     if response.status_code in (200, 201, 409):
            #         print(f"✓ Product {product['name']} created or exists")
            #     else:
            #         raise Exception(f"Failed to create product: {response.text}")

            print("  TODO: Create or ensure products exist in catalog")

            # ──────────────────────────────────────────────────────────────
            # TODO: Create or ensure admin account exists
            # ──────────────────────────────────────────────────────────────
            # Example:
            # admin_response = await client.post(
            #     f"{ADMIN_API}/api/users",
            #     json={
            #         "email": "admin@test.com",
            #         "password": "admin123",
            #         "role": "admin",
            #         "first_name": "Admin",
            #         "last_name": "User",
            #     },
            # )
            # if admin_response.status_code in (200, 201, 409):
            #     print("✓ Admin account created or already exists")
            # else:
            #     raise Exception(f"Failed to create admin: {admin_response.text}")

            print("  TODO: Create or ensure admin account exists")

            print("\nBase seed completed successfully!")
            print("Note: Replace TODO sections with actual API calls")

        except httpx.RequestError as e:
            print(f"\nERROR: Network error during seed: {e}", file=sys.stderr)
            print("Make sure the application services are running (docker-compose up)", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"\nERROR: Seed failed: {e}", file=sys.stderr)
            sys.exit(1)


async def teardown():
    """Optional cleanup after scenario."""
    # Usually not needed for idempotent seeds, but can be implemented if required
    pass


if __name__ == "__main__":
    asyncio.run(seed())
