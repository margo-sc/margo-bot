"""Загрузка и проверка content.yaml."""
from __future__ import annotations

import copy
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
        extra = self.path.with_name("kbzhu.yaml")
        kb = yaml.safe_load(extra.read_text(encoding="utf-8")) if extra.exists() else None
        if kb and kb.get("enabled", True):
            merge_kbzhu(data, kb)
        problems = validate(data)
        self.data = data
        return problems

    @property
    def kbzhu(self) -> dict:
        return self.data.get("_kbzhu", {})

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


def merge_kbzhu(data: dict, kb: dict) -> None:
    """Подключает расчёт КБЖУ (kbzhu.yaml) к основному content.yaml, не меняя сам content.yaml:
    ссылки, цепочки после меню и товары со скидкой."""
    data["_kbzhu"] = kb
    links = data.setdefault("deeplinks", {})
    for code, dl in (kb.get("deeplinks") or {}).items():
        links.setdefault(code, dl)
    chains = data.setdefault("chains", {})
    disc0 = kb.get("discount") or {}
    pct0 = int(disc0.get("percent") or 0)
    prods0 = data.get("products", {})

    def _price(key: str, off: int) -> str:
        v = int(round(int(prods0.get(key, {}).get("price", 0) or 0) * (100 - off) / 100))
        return f"{v:,}".replace(",", " ")

    prices = {"discount": pct0, "hours": int(disc0.get("hours") or 72),
              "vedenie_old": _price("vedenie", 0), "vedenie_new": _price("vedenie", pct0),
              "konsult_old": _price("konsult", 0), "konsult_new": _price("konsult", pct0)}

    def _fill(t):  # цены подставляем сразу, имя ({first_name}) — при отправке
        return render(str(t or ""), **prices)

    chains["kbzhu_follow"] = {"stop_on_purchase": True, "steps": [
        {"text": _fill(s.get("text")), "starts_discount": bool(s.get("starts_discount")),
         "buttons": [[_fill(b[0]), b[1]] for b in (s.get("buttons") or [])] or None}
        for s in kb.get("follow") or []]}
    gifts = kb.get("gifts") or []
    if gifts:
        chains["kbzhu_gifts"] = {"stop_on_purchase": False, "steps": [
            {"text": g.get("text", ""), "file": g.get("file")} for g in gifts]}
    nf = kb.get("norm_only_follow")
    if nf:
        chains["kbzhu_norm_only"] = {"stop_on_purchase": True,
                                     "steps": [{"text": nf.get("text", ""), "buttons": nf.get("buttons")}]}
    products = data.setdefault("products", {})
    for key, p in (kb.get("products") or {}).items():
        products.setdefault(key, p)  # если такой товар уже есть в content.yaml — главный он
    screens = data.setdefault("screens", {})
    singles = (kb.get("screen_buttons") or {}).get("recepty_singles")
    if singles and "recepty_singles" not in screens:
        screens["recepty_singles"] = {"text": "Каждый сборник — 199 ₽, а все три вместе — 499 ₽ 👇",
                                      "buttons": list(singles) + [["📚 Все 3 за 499 ₽", "product:recepty"],
                                                                  ["← Все продукты", "screen:catalog"]]}
    disc = kb.get("discount") or {}
    pct = int(disc.get("percent") or 0)
    products = data.setdefault("products", {})
    for key in disc.get("products") or []:
        p = products.get(key)
        if not p or not pct or not p.get("payable", True):
            continue  # ведение оплачивается по счёту — скидку Маргарита даёт сама
        d = copy.deepcopy(p)
        d["price"] = int(round(p["price"] * (100 - pct) / 100))
        d["name"] = f"{p.get('name', key)} (скидка {pct}%)"
        d["card"] = (f"🎁 <b>Твоя цена после расчёта КБЖУ — {d['price']:,} ₽ вместо {int(p['price']):,} ₽</b>\n\n"
                     .replace(",", " ") + (p.get("card") or ""))
        d["hidden"] = True
        products[f"{key}_kbzhu"] = d


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
        elif kind == "interest" and not (products.get(arg) or {}).get("interest"):
            problems.append(f"{where}: у продукта '{arg}' нет блока interest")
        elif kind not in ("screen", "product", "pay", "lead", "anketa", "url", "answer", "help", "interest",
                          "kbzhu"):
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
        if p.get("contact"):
            c = p["contact"]
            if not (isinstance(c, (list, tuple)) and len(c) == 2):
                problems.append(f"products.{key}.contact: нужно [\"Текст кнопки\", \"действие\"]")
            else:
                check_action(str(c[1]), f"products.{key}.contact")
        acc = d.get("access")
        if acc is not None:
            if not isinstance(acc, dict) or not re.fullmatch(r"[a-z0-9_]{1,40}", str(acc.get("group", ""))):
                problems.append(f"products.{key}.delivery.access: нужен group — латиница, цифры и _")
            elif acc.get("days") is not None and not (isinstance(acc["days"], int) and acc["days"] > 0):
                problems.append(f"products.{key}.delivery.access.days: нужно целое число дней")
            elif acc.get("link") and not str(acc["link"]).startswith("https://t.me/"):
                problems.append(f"products.{key}.delivery.access.link: ссылка должна начинаться с https://t.me/")
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
