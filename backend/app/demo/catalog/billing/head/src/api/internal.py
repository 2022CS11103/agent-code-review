from fastapi import APIRouter

router = APIRouter()


@router.post("/internal/refunds")
def create_refund(invoice_id: str, amount: int):
    return {"invoice_id": invoice_id, "amount": amount, "status": "issued"}
