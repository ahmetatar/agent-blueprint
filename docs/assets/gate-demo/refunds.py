def issue_refund(invoice_id: str) -> dict:
    return {"invoice_id": invoice_id, "status": "refunded"}
