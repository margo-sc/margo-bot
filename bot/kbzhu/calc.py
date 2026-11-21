"""Расчёт нормы КБЖУ по методике Маргариты (Миффлин — Сан Жеор, женщины)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

ACTIVITY = {"min": 1.2, "low": 1.375, "mid": 1.55, "high": 1.725}

# отношения с едой: calm — всё спокойно, binge — срывы, diets — круг диет, fear — боюсь есть
SOFT_DEFICIT_FOOD = {"binge", "diets", "fear"}

KCAL_FLOOR = 1300
DEFICIT = 0.15
DEFICIT_OBESE = 0.20      # при ИМТ ≥ 30
DEFICIT_SOFT = 0.10       # срывы, круг диет, «боюсь есть»
SURPLUS = 0.10
PROTEIN = 1.8            # г на кг
PROTEIN_OBESE = 1.3       # при ИМТ ≥ 30
PROTEIN_LOW_CARB_FIX = 1.6
FAT = 0.9                 # г на кг
FAT_MIN = 50              # г в день
CARB_MIN = 100            # г в день
TREAT_SHARE = 0.10        # доля калорий на любимое


@dataclass
class Norm:
    bmr: int
    tdee: int
    bmi: float
    kcal: int
    protein: int
    fat: int
    carbs: int
    treat_kcal: int
    deficit_pct: int        # минус — дефицит, плюс — профицит
    floor_applied: bool

    def as_dict(self) -> dict:
        return asdict(self)


def bmi(weight: float, height: float) -> float:
    return weight / (height / 100) ** 2


def calculate(age: int, height: float, weight: float, activity: str, goal: str, food: str = "calm") -> Norm:
    """goal: lose / keep / gain."""
    b = 10 * weight + 6.25 * height - 5 * age - 161
    tdee = b * ACTIVITY[activity]
    i = bmi(weight, height)

    if goal == "lose":
        d = DEFICIT
        if i >= 30:
            d = DEFICIT_OBESE
        if food in SOFT_DEFICIT_FOOD:
            d = DEFICIT_SOFT
        kcal = tdee * (1 - d)
        pct = -round(d * 100)
    elif goal == "gain":
        kcal = tdee * (1 + SURPLUS)
        pct = round(SURPLUS * 100)
    else:
        kcal = tdee
        pct = 0

    floor = max(b, KCAL_FLOOR)
    floor_applied = False
    if kcal < floor:
        kcal, floor_applied = floor, True
    kcal = int(round(kcal / 10) * 10)

    protein = weight * (PROTEIN_OBESE if i >= 30 else PROTEIN)
    fat = max(weight * FAT, FAT_MIN)
    carbs = (kcal - protein * 4 - fat * 9) / 4
    if carbs < CARB_MIN and i < 30:
        protein = weight * PROTEIN_LOW_CARB_FIX
        carbs = (kcal - protein * 4 - fat * 9) / 4
    carbs = max(carbs, 0)

    return Norm(
        bmr=int(round(b)), tdee=int(round(tdee)), bmi=round(i, 1), kcal=kcal,
        protein=int(round(protein)), fat=int(round(fat)), carbs=int(round(carbs)),
        treat_kcal=int(round(kcal * TREAT_SHARE)), deficit_pct=pct, floor_applied=floor_applied,
    )
