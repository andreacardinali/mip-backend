from fastapi import APIRouter

router = APIRouter()

@router.get("")
def get_portfolios():
    return []

@router.post("")
def create_portfolio(payload: dict):
    return payload