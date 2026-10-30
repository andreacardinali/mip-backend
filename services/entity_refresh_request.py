from pydantic import BaseModel, Field, model_validator

from services.entities import RefreshMode


class RefreshEntitiesRequest(BaseModel):
    mode: RefreshMode = Field(default=RefreshMode.MISSING)
    symbol: str | None = Field(default=None, examples=["AAPL"])
    create_if_missing: bool = False
    full_refresh: bool = False

    @model_validator(mode="after")
    def validate_request(self):
        if self.mode == RefreshMode.SYMBOL and not self.symbol:
            raise ValueError("symbol is required when mode is SYMBOL")
        if self.symbol:
            self.symbol = self.symbol.strip().upper()
        return self