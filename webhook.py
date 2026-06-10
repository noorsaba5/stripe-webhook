import os
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client
import stripe

app = FastAPI()

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

PLAN_MAP = {
    os.environ["STRIPE_STARTER_PRICE_ID"]: "starter",
    os.environ["STRIPE_PRO_PRICE_ID"]: "pro",
    os.environ["STRIPE_PREMIUM_PRICE_ID"]: "premium",
}


@app.get("/")
def health_check():
    return {"status": "ok"}


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        user_id = session.get("client_reference_id")
        subscription_id = session.get("subscription")

        if not user_id:
            return {"ok": False, "reason": "No client_reference_id found"}

        if not subscription_id:
            return {"ok": False, "reason": "No subscription found"}

        subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["items.data.price"]
        )

        price_id = subscription["items"]["data"][0]["price"]["id"]
        plan = PLAN_MAP.get(price_id, "starter")

        supabase.table("profiles").update({
            "plan": plan
        }).eq("id", user_id).execute()

        return {
            "ok": True,
            "user_id": user_id,
            "price_id": price_id,
            "plan": plan
        }

    return {"ok": True}