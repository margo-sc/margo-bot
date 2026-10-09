"""Загрузка и проверка content.yaml."""
from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

_DELAY = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")


def parse_delay(value) -> int:
    """'30m' / '1h' / '3d' -> секунды. Число — минуты."""
    if isinstance(value, (int, float)):
        return int(value * 60)
    m = _DELAY.match(str(value))
    if not m:
        raise ValueError(f"Неверная задержка: {value!r} (нужно 30m, 1h или 3d)")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 60, "h": 3600, "d": 86400}[unit]


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(text: str, **kw) -> str:
    return (text or "").format_map(SafeDict(**{k: v for k, v in kw.items() if v is not None}))


class Content:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        self.reload()

    def reload(self) -> list[str]:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        problems = validate(data)
        self.data = data
        return problems

    # удобные геттеры
    @property
    def screens(self) -> dict:
        return self.data.get("screens", {})

    @property
    def products(self) -> dict:
        return self.data.get("products", {})

    @property
    def leads(self) -> dict:
        return self.data.get("leads", {})

    @property
    def anketas(self) -> dict:
        return self.data.get("anketas", {})

    @property
    def chains(self) -> dict:
        return self.data.get("chains", {})

    @property
    def deeplinks(self) -> dict:
        return self.data.get("deeplinks", {})

    @property
    def consent(self) -> dict:
        return self.data.get("consent", {})

    def sys(self, key: str) -> str:
        return self.data.get("system", {}).get(key, "")

    def product_vars(self, key: str) -> dict:
        p = self.products.get(key, {})
        price = p.get("price", 0)
        return {
            "product": key,
            "name": p.get("name", key),
            "price": f"{int(price):,}".replace(",", " ") if price else "",
            "split": str(math.ceil(price / 4)) if price else "",
        }


def _actions_in(buttons) -> list[str]:
    out = []
    for row in buttons or []:
        if isinstance(row, (list, tuple)) and len(row) == 2:
            out.append(str(row[1]))
    return out


def validate(data: dict) -> list[str]:
    """Возвращает список проблем (пустой — всё хорошо)."""
    problems: list[str] = []
    screens = data.get("screens", {})
    products = data.get("products", {})
    leads = data.get("leads", {})
    anketas = data.get("anketas", {})
    chains = data.get("chains", {})

    if "start" not in screens:
        problems.append("Нет экрана screens.start")

    def check_action(action: str, where: str) -> None:
        kind, _, arg = action.partition(":")
        arg = arg.replace("{product}", next(iter(products), ""))
        if kind == "screen" and arg not in screens:
            problems.append(f"{where}: нет экрана '{arg}'")
        elif kind in ("product", "pay") and arg not in products:
            problems.append(f"{where}: нет продукта '{arg}'")
        elif kind == "lead" and arg not in leads:
            problems.append(f"{where}: нет гайда '{arg}'")
        elif kind == "anketa" and arg not in anketas:
            problems.append(f"{where}: нет анкеты '{arg}'")
        elif kind not in ("screen", "product", "pay", "lead", "anketa", "url", "answer", "help"):
            problems.append(f"{where}: неизвестное действие '{action}'")

    for name, s in screens.items():
        for a in _actions_in(s.get("buttons")):
            check_action(a, f"screens.{name}")
    for key, p in products.items():
        if "price" not in p:
            problems.append(f"products.{key}: нет цены")
        d = p.get("delivery", {}) or {}
        if d.get("anketa") and d["anketa"] not in anketas:
            problems.append(f"products.{key}: нет анкеты '{d['anketa']}'")
        ac = p.get("after_chain")
        if ac and ac not in chains:
            problems.append(f"products.{key}: нет цепочки '{ac}'")
    for key, lead in leads.items():
        if lead.get("chain") and lead["chain"] not in chains:
            problems.append(f"leads.{key}: нет цепочки '{lead['chain']}'")
    for name, c in chains.items():
        for i, step in enumerate(c.get("steps", [])):
            try:
                parse_delay(step.get("delay", "0m"))
            except ValueError as e:
                problems.append(f"chains.{name}[{i}]: {e}")
            for a in _actions_in(step.get("buttons")):
                check_action(a, f"chains.{name}[{i}]")
    for code, dl in data.get("deeplinks", {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
            problems.append(f"deeplinks.{code}: код может содержать только латиницу, цифры, _ и -")
        check_action(str(dl.get("open", "screen:start")), f"deeplinks.{code}")
    for key, a in anketas.items():
        if not a.get("questions"):
            problems.append(f"anketas.{key}: нет вопросов")
        fu = a.get("followup")
        if fu and fu.get("button"):
            check_action(fu["button"][1], f"anketas.{key}.followup")
    return problems
