from fastapi import APIRouter

router = APIRouter()

@router.get("")
def get_watchlists():
    return []

@router.post("")
def create_watchlist(payload: dict):
    return payload