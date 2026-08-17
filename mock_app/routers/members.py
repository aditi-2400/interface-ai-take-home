from fastapi import APIRouter, Form, Request

from mock_app import db
from mock_app.money import parse_dollar_amount
from mock_app.simulate import check_simulate
from mock_app.templating import templates

router = APIRouter()

VALID_ACCOUNT_TYPES = {"checking", "savings"}


@router.get("/members/search")
def member_search(request: Request, query: str = ""):
    if (resp := check_simulate(request)) is not None:
        return resp
    with db.connection() as conn:
        query = query.strip()
        searched = bool(query)
        members = db.search_members(conn, query) if searched else []
        return templates.TemplateResponse(
            request,
            "member_search.html",
            {"query": query, "searched": searched, "members": members},
        )


@router.get("/members/{member_id}")
def member_detail(request: Request, member_id: int):
    if (resp := check_simulate(request)) is not None:
        return resp
    with db.connection() as conn:
        member = db.get_member(conn, member_id)
        if member is None:
            return templates.TemplateResponse(
                request, "member_not_found.html", {"query": str(member_id)}, status_code=404
            )
        accounts = db.get_accounts_for_member(conn, member_id)
        return templates.TemplateResponse(
            request, "member_detail.html", {"member": member, "accounts": accounts}
        )


@router.get("/members/{member_id}/sub-accounts/new")
def sub_account_new_form(request: Request, member_id: int):
    if (resp := check_simulate(request)) is not None:
        return resp
    with db.connection() as conn:
        member = db.get_member(conn, member_id)
        if member is None:
            return templates.TemplateResponse(
                request, "member_not_found.html", {"query": str(member_id)}, status_code=404
            )
        return templates.TemplateResponse(request, "sub_account_form.html", {"member": member})


@router.post("/members/{member_id}/sub-accounts")
async def sub_account_create(
    request: Request,
    member_id: int,
    account_type: str = Form(...),
    initial_deposit: str = Form(...),
):
    if (
        resp := check_simulate(
            request,
            dismiss_fields={"account_type": account_type, "initial_deposit": initial_deposit},
        )
    ) is not None:
        return resp
    with db.connection() as conn:
        member = db.get_member(conn, member_id)
        if member is None:
            return templates.TemplateResponse(
                request, "member_not_found.html", {"query": str(member_id)}, status_code=404
            )
        if account_type not in VALID_ACCOUNT_TYPES:
            return templates.TemplateResponse(
                request,
                "sub_account_form.html",
                {
                    "member": member,
                    "error": f"Invalid account type: {account_type!r}",
                    "account_type": account_type,
                    "initial_deposit": initial_deposit,
                },
            )
        amount_cents = parse_dollar_amount(initial_deposit)
        if amount_cents is None or amount_cents < 0:
            return templates.TemplateResponse(
                request,
                "sub_account_form.html",
                {
                    "member": member,
                    "error": "Initial deposit must be zero or a positive dollar amount.",
                    "account_type": account_type,
                    "initial_deposit": initial_deposit,
                },
            )
        return templates.TemplateResponse(
            request,
            "sub_account_confirm.html",
            {"member": member, "account_type": account_type, "initial_deposit_cents": amount_cents},
        )


@router.post("/members/{member_id}/sub-accounts/confirm")
async def sub_account_confirm(
    request: Request,
    member_id: int,
    account_type: str = Form(...),
    initial_deposit_cents: int = Form(...),
):
    if (
        resp := check_simulate(
            request,
            dismiss_fields={
                "account_type": account_type,
                "initial_deposit_cents": initial_deposit_cents,
            },
        )
    ) is not None:
        return resp
    with db.connection() as conn:
        member = db.get_member(conn, member_id)
        if member is None:
            return templates.TemplateResponse(
                request, "member_not_found.html", {"query": str(member_id)}, status_code=404
            )
        if account_type not in VALID_ACCOUNT_TYPES or initial_deposit_cents < 0:
            return templates.TemplateResponse(
                request,
                "sub_account_form.html",
                {"member": member, "error": "Invalid sub-account details submitted."},
                status_code=400,
            )
        new_id = db.create_account(conn, member_id, account_type, initial_deposit_cents)
        account = db.get_account(conn, new_id)
        return templates.TemplateResponse(request, "sub_account_success.html", {"account": account})
