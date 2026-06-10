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

        session_id = session["id"]
        customer_email = session["customer_details"]["email"]

        line_items = stripe.checkout.Session.list_line_items(
            session_id,
            limit=1
        )

        price_id = line_items["data"][0]["price"]["id"]
        plan = PLAN_MAP.get(price_id, "starter")

        result = supabase.table("profiles").update({
            "plan": plan
        }).eq("email", customer_email).execute()

        return {
            "ok": True,
            "email": customer_email,
            "price_id": price_id,
            "plan": plan,
            "result": str(result)
        }

    return {"ok": True}