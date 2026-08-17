from fastapi import APIRouter, Form, Request

from mock_app import db
from mock_app.money import parse_dollar_amount
from mock_app.simulate import check_simulate
from mock_app.templating import templates

router = APIRouter()


@router.get("/accounts/{account_id}/deposit")
def deposit_form(request: Request, account_id: int):
    if (resp := check_simulate(request)) is not None:
        return resp
    with db.connection() as conn:
        account = db.get_account(conn, account_id)
        if account is None:
            return templates.TemplateResponse(
                request,
                "account_not_found.html",
                {"message": f"No account record exists for account ID {account_id}."},
                status_code=404,
            )
        return templates.TemplateResponse(request, "deposit_form.html", {"account": account})


@router.post("/accounts/{account_id}/deposit")
async def deposit_submit(request: Request, account_id: int, amount: str = Form(...)):
    if (resp := check_simulate(request, dismiss_fields={"amount": amount})) is not None:
        return resp
    with db.connection() as conn:
        account = db.get_account(conn, account_id)
        if account is None:
            return templates.TemplateResponse(
                request,
                "account_not_found.html",
                {"message": f"No account record exists for account ID {account_id}."},
                status_code=404,
            )
        amount_cents = parse_dollar_amount(amount)
        if amount_cents is None or amount_cents <= 0:
            return templates.TemplateResponse(
                request,
                "deposit_form.html",
                {
                    "account": account,
                    "amount": amount,
                    "error": "Deposit amount must be a positive dollar amount.",
                },
            )
        return templates.TemplateResponse(
            request, "deposit_confirm.html", {"account": account, "amount_cents": amount_cents}
        )


@router.post("/accounts/{account_id}/deposit/confirm")
async def deposit_confirm(request: Request, account_id: int, amount_cents: int = Form(...)):
    if (
        resp := check_simulate(request, dismiss_fields={"amount_cents": amount_cents})
    ) is not None:
        return resp
    with db.connection() as conn:
        account = db.get_account(conn, account_id)
        if account is None:
            return templates.TemplateResponse(
                request,
                "account_not_found.html",
                {"message": f"No account record exists for account ID {account_id}."},
                status_code=404,
            )
        if amount_cents <= 0:
            return templates.TemplateResponse(
                request,
                "deposit_form.html",
                {"account": account, "error": "Deposit amount must be a positive dollar amount."},
                status_code=400,
            )
        new_balance = account["balance_cents"] + amount_cents
        db.update_balance(conn, account_id, new_balance)
        account = db.get_account(conn, account_id)
        return templates.TemplateResponse(
            request, "deposit_success.html", {"account": account, "amount_cents": amount_cents}
        )


@router.get("/accounts/{account_id}/transfer")
def transfer_form(request: Request, account_id: int):
    if (resp := check_simulate(request)) is not None:
        return resp
    with db.connection() as conn:
        account = db.get_account(conn, account_id)
        if account is None:
            return templates.TemplateResponse(
                request,
                "account_not_found.html",
                {"message": f"No account record exists for account ID {account_id}."},
                status_code=404,
            )
        return templates.TemplateResponse(request, "transfer_form.html", {"account": account})


@router.post("/accounts/{account_id}/transfer")
async def transfer_submit(
    request: Request, account_id: int, to_account_id: str = Form(...), amount: str = Form(...)
):
    if (
        resp := check_simulate(
            request, dismiss_fields={"to_account_id": to_account_id, "amount": amount}
        )
    ) is not None:
        return resp
    with db.connection() as conn:
        from_account = db.get_account(conn, account_id)
        if from_account is None:
            return templates.TemplateResponse(
                request,
                "account_not_found.html",
                {"message": f"No account record exists for account ID {account_id}."},
                status_code=404,
            )
        amount_cents = parse_dollar_amount(amount)
        if amount_cents is None or amount_cents <= 0:
            return templates.TemplateResponse(
                request,
                "transfer_form.html",
                {
                    "account": from_account,
                    "to_account_id": to_account_id,
                    "amount": amount,
                    "error": "Transfer amount must be a positive dollar amount.",
                },
            )
        if not to_account_id.strip().isdigit():
            return templates.TemplateResponse(
                request,
                "transfer_form.html",
                {
                    "account": from_account,
                    "to_account_id": to_account_id,
                    "amount": amount,
                    "error": f"Destination account {to_account_id!r} was not found.",
                },
            )
        to_account = db.get_account(conn, int(to_account_id))
        if to_account is None:
            return templates.TemplateResponse(
                request,
                "transfer_form.html",
                {
                    "account": from_account,
                    "to_account_id": to_account_id,
                    "amount": amount,
                    "error": f"Destination account {to_account_id} was not found.",
                },
            )
        if amount_cents > from_account["balance_cents"]:
            return templates.TemplateResponse(
                request,
                "transfer_form.html",
                {
                    "account": from_account,
                    "to_account_id": to_account_id,
                    "amount": amount,
                    "error": "Insufficient funds to complete this transfer.",
                },
            )
        return templates.TemplateResponse(
            request,
            "transfer_confirm.html",
            {"from_account": from_account, "to_account": to_account, "amount_cents": amount_cents},
        )


@router.post("/accounts/{account_id}/transfer/confirm")
async def transfer_confirm(
    request: Request,
    account_id: int,
    to_account_id: int = Form(...),
    amount_cents: int = Form(...),
):
    if (
        resp := check_simulate(
            request,
            dismiss_fields={"to_account_id": to_account_id, "amount_cents": amount_cents},
        )
    ) is not None:
        return resp
    with db.connection() as conn:
        from_account = db.get_account(conn, account_id)
        to_account = db.get_account(conn, to_account_id)
        if from_account is None or to_account is None:
            return templates.TemplateResponse(
                request,
                "account_not_found.html",
                {"message": "One or more accounts in this transfer no longer exist."},
                status_code=404,
            )
        if amount_cents <= 0 or amount_cents > from_account["balance_cents"]:
            return templates.TemplateResponse(
                request,
                "transfer_form.html",
                {
                    "account": from_account,
                    "error": "This transfer can no longer be completed as submitted. Please retry.",
                },
                status_code=400,
            )
        db.transfer_funds(conn, account_id, to_account_id, amount_cents)
        from_account = db.get_account(conn, account_id)
        to_account = db.get_account(conn, to_account_id)
        return templates.TemplateResponse(
            request,
            "transfer_success.html",
            {"from_account": from_account, "to_account": to_account, "amount_cents": amount_cents},
        )
