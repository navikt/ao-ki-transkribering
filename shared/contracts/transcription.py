from pydantic import BaseModel


class Segment(BaseModel):
    start: float
    slutt: float
    tekst: str
    taler: str | None = None


class TranskripsjonSvar(BaseModel):
    tekst: str
    segmenter: list[Segment]
    advarsler: list[str] = []


class HelseSvar(BaseModel):
    status: str


class KlarSvar(BaseModel):
    status: str
    modell: str | None = None
    enhet: str | None = None
