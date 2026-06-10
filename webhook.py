import os
import stripe
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client

app = FastAPI(redirect_slashes=False)

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

# Map your Stripe Payment Link IDs to plans
# We use payment_link ID since mode=payment (not subscription)
PAYMENT_LINK_PLAN_MAP = {
    os.environ.get("STRIPE_STARTER_LINK_ID", ""): "starter",
    os.environ.get("STRIPE_PRO_LINK_ID", ""):     "pro",
    os.environ.get("STRIPE_PREMIUM_LINK_ID", ""): "premium",
}

# Also support price ID mapping as fallback
PRICE_PLAN_MAP = {
    os.environ.get("STRIPE_STARTER_PRICE_ID", ""): "starter",
    os.environ.get("STRIPE_PRO_PRICE_ID", ""):     "pro",
    os.environ.get("STRIPE_PREMIUM_PRICE_ID", ""): "premium",
}


@app.get("/")
def health_check():
    return {"status": "ok", "webhook": "ready"}


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    # Verify the webhook signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        user_id        = session.get("client_reference_id")
        payment_link   = session.get("payment_link")      # e.g. plink_xxx
        subscription_id = session.get("subscription")     # null for one-time
        payment_intent  = session.get("payment_intent")   # present for one-time
        metadata        = session.get("metadata") or {}
        amount_total    = session.get("amount_total", 0)  # in pence

        # ── Determine plan ──────────────────────────────────────────────

        plan = None

        # 1. Try payment_link ID (most reliable for your setup)
        if payment_link and payment_link in PAYMENT_LINK_PLAN_MAP:
            plan = PAYMENT_LINK_PLAN_MAP[payment_link]

        # 2. Try metadata price_id (if you added it to the payment link)
        if not plan and metadata.get("price_id"):
            plan = PRICE_PLAN_MAP.get(metadata["price_id"])

        # 3. Try subscription (for future subscription mode links)
        if not plan and subscription_id:
            try:
                sub = stripe.Subscription.retrieve(
                    subscription_id, expand=["items.data.price"]
                )
                price_id = sub["items"]["data"][0]["price"]["id"]
                plan = PRICE_PLAN_MAP.get(price_id)
            except Exception:
                pass

        # 4. Fallback: infer plan from amount paid (in pence)
        if not plan:
            if amount_total <= 900:
                plan = "starter"
            elif amount_total <= 1900:
                plan = "pro"
            else:
                plan = "premium"

        # ── Identify user ────────────────────────────────────────────────

        if not user_id:
            # No user ID — log it but return 200 so Stripe stops retrying
            print(f"[webhook] WARNING: No client_reference_id. "
                  f"payment_intent={payment_intent}, plan={plan}, "
                  f"email={session.get('customer_details', {}).get('email')}")
            return {
                "ok": False,
                "reason": "No client_reference_id — cannot link to user",
                "fix": "Append ?client_reference_id=USER_ID to your payment link URL"
            }

        # ── Update Supabase ──────────────────────────────────────────────

        try:
            result = supabase.table("profiles").update({
                "plan": plan
            }).eq("id", user_id).execute()

            print(f"[webhook] Updated user {user_id} to plan '{plan}'")

            return {
                "ok": True,
                "user_id": user_id,
                "plan": plan,
                "payment_link": payment_link,
                "amount_total": amount_total,
            }

        except Exception as e:
            print(f"[webhook] Supabase update failed: {e}")
            raise HTTPException(status_code=500, detail=f"Database update failed: {e}")

    # All other event types — return 200 to acknowledge
    return {"ok": True, "event": event["type"]}
