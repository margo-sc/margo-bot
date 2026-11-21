"""База продуктов и генератор меню на 7 дней под норму КБЖУ."""
from __future__ import annotations

import difflib
import random
import zlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MEAL_TYPE = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "snack", "snack1": "snack"}
MEAL_LABEL = {"breakfast": "🌅 Завтрак", "snack1": "🍏 Перекус", "lunch": "🍽 Обед",
              "snack": "🥛 Перекус", "dinner": "🌙 Ужин"}
EGG_G = 55
PROTEIN_CATS = {"poultry", "meat", "fish", "seafood", "cottage", "tofu", "salted_fish", "canned_fish", "surimi",
                "egg_white"}
# разумный потолок одной порции по категориям, г — чтобы не было «750 г брокколи»
CAP = {"veg": 400, "fruit": 250, "berries": 250, "oil": 15, "nuts": 40, "nutbutter": 30, "avocado": 100,
       "grain": 110, "pasta": 110, "oats": 90, "potato": 450, "bread": 120, "lavash": 90, "cheese": 50,
       "milk": 300, "kefir": 400, "yogurt": 350, "cottage": 350, "eggs": 220, "poultry": 300, "meat": 280,
       "fish": 300, "seafood": 300, "tofu": 300, "salted_fish": 150, "canned_fish": 160, "surimi": 120,
       "egg_white": 240, "soft_cheese": 50, "crispbread": 40, "noodles": 110, "corn": 100, "ham": 40,
       "aroma": 50, "sauce": 20}
LEGUMES_DRY_CAP = 90
SWAPPABLE = {"yogurt", "berries", "fruit", "cheese", "bread", "oil", "kefir", "milk"}


def norm_text(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^a-zа-я0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Item:
    product: str
    grams: float
    lo: float
    hi: float
    base: float
    slot: int = -1        # номер строки в блюде
    first: bool = True    # взят первый (основной) вариант продукта
    add: bool = False     # добавлено сверх рецепта
    core: bool = False    # продукт из рецепта Маргариты — пропорции держим близко к рецепту


@dataclass
class Meal:
    key: str            # breakfast / snack1 / lunch / snack / dinner
    dish_id: str
    title: str
    items: list[Item]
    how: str = ""
    how_slots: list = field(default_factory=list)   # строки рецепта, без которых «как готовить» не показываем


@dataclass
class Day:
    meals: list[Meal]
    treat: Item | None
    totals: dict = field(default_factory=dict)


class FoodBase:
    def __init__(self, path: Path):
        self.path = path
        self.reload()

    def reload(self) -> list[str]:
        d = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.products: dict[str, dict] = d.get("products", {})
        self.groups: list[dict] = d.get("groups", [])
        self.treat_buttons: list[str] = d.get("treat_buttons", [])
        self.treat_sets: dict[str, dict] = d.get("treat_sets") or {}
        self.exclusions: dict[str, dict] = d.get("exclusions", {})
        self.pantry: list[str] = d.get("pantry", [])
        self.meal_plans: dict[str, dict] = {str(k): v for k, v in (d.get("meal_plans") or {}).items()}
        self.dishes: list[dict] = d.get("dishes", [])
        self.by_cat: dict[str, list[str]] = {}
        for k, p in self.products.items():
            self.by_cat.setdefault(p.get("cat", ""), []).append(k)
        self._index: dict[str, str] = {}
        for k, p in self.products.items():
            for word in [p.get("name", "")] + list(p.get("syn") or []):
                w = norm_text(word)
                if w and w not in self._index:
                    self._index[w] = k
        return self.validate()

    def validate(self) -> list[str]:
        problems = []
        for k, p in self.products.items():
            for f in ("name", "cat", "k", "p", "f", "c"):
                if f not in p:
                    problems.append(f"kbzhu_food: у продукта {k} нет поля {f}")
        for key, ts in self.treat_sets.items():
            for k in ts.get("items", []):
                if k not in self.products:
                    problems.append(f"kbzhu_food: в наборе {key} нет продукта {k}")
        for k in self.treat_buttons:
            if k not in self.products and k not in self.treat_sets:
                problems.append(f"kbzhu_food: в treat_buttons нет продукта {k}")
        for g in self.groups:
            for k in g.get("items", []):
                if k not in self.products:
                    problems.append(f"kbzhu_food: в группе «{g.get('title')}» нет продукта {k}")
        for d in self.dishes:
            if MEAL_TYPE.get(d.get("meal")) is None:
                problems.append(f"kbzhu_food: у блюда {d.get('id')} неверный meal")
            for it in d.get("items", []):
                for pk in it.get("pick", []):
                    if pk.startswith("@"):
                        if pk[1:] not in self.by_cat:
                            problems.append(f"kbzhu_food: блюдо {d.get('id')}: нет категории {pk}")
                    elif pk not in self.products:
                        problems.append(f"kbzhu_food: блюдо {d.get('id')}: нет продукта {pk}")
        for plan in self.meal_plans.values():
            if abs(sum(plan.values()) - 1) > 0.01:
                problems.append("kbzhu_food: доли в meal_plans должны давать 1")
        return problems

    # ---------- поиск продукта по тексту ----------
    def lookup(self, text: str) -> str | None:
        w = norm_text(text)
        if not w:
            return None
        if w in self._index:
            return self._index[w]
        close = difflib.get_close_matches(w, list(self._index), n=1, cutoff=0.8)
        return self._index[close[0]] if close else None

    def expand(self, pick: list[str]) -> list[str]:
        out: list[str] = []
        for pk in pick:
            keys = self.by_cat.get(pk[1:], []) if pk.startswith("@") else [pk]
            out += [k for k in keys if k not in out]
        return out

    def excluded_products(self, excl: list[str]) -> set[str]:
        out: set[str] = set()
        for e in excl:
            rule = self.exclusions.get(e) or {}
            for c in rule.get("cats", []):
                out.update(self.by_cat.get(c, []))
            out.update(rule.get("products", []))
        return out

    def expand_treats(self, keys: list[str]) -> list[str]:
        out: list[str] = []
        for k in keys:
            for x in (self.treat_sets[k].get("items", []) if k in self.treat_sets else [k]):
                if x in self.products and x not in out:
                    out.append(x)
        return out

    def label(self, key: str) -> str:
        if key in self.treat_sets:
            return self.treat_sets[key].get("title", key)
        return self.products[key]["name"].split(" (")[0]

    def nut(self, key: str) -> tuple[float, float, float, float]:
        p = self.products[key]
        return p["k"] / 100, p["p"] / 100, p["f"] / 100, p["c"] / 100


# ---------------------------------------------------------------------- решатель граммовок
def _solve(fb: FoodBase, meals: list[Meal], shares: dict[str, float], target: dict) -> None:
    """Подбирает граммы так, чтобы день попал в КБЖУ, а приёмы пищи — в свои доли калорий.
    Квадратичная функция ошибки, покоординатный спуск с границами."""
    items = [(mi, it) for mi, m in enumerate(meals) for it in m.items]
    if not items:
        return
    K = max(target["kcal"], 1)
    rows: list[tuple[list[float], float, float]] = []  # (коэффициенты, цель, вес)
    nuts = [fb.nut(it.product) for _, it in items]
    for idx, w in ((1, 3.0), (2, 3.0), (3, 3.0)):   # белки, жиры, углеводы
        t = max(target[("kcal", "protein", "fat", "carbs")[idx]], 1)
        rows.append(([n[idx] / t for n in nuts], 1.0, w))
    rows.append(([n[0] / K for n in nuts], 1.0, 6.0))  # калории — главное
    for mi, m in enumerate(meals):
        share = shares.get(m.key, 0)
        if share <= 0:
            continue
        coef = [nuts[j][0] / (share * K) if items[j][0] == mi else 0.0 for j in range(len(items))]
        rows.append((coef, 1.0, 0.05))
    reg = 0.001
    x = [it.grams for _, it in items]
    resid = [sum(c[j] * x[j] for j in range(len(x))) - t for c, t, _ in rows]
    for _ in range(300):
        moved = 0.0
        for j, (_, it) in enumerate(items):
            s = max(it.base, 50.0)
            num = reg * it.base / s ** 2
            den = reg / s ** 2
            for r, (c, t, w) in enumerate(rows):
                a = c[j]
                if a:
                    num += w * a * (a * x[j] - resid[r])
                    den += w * a * a
            new = min(max(num / den, it.lo), it.hi)
            if new != x[j]:
                d = new - x[j]
                for r, (c, _, _) in enumerate(rows):
                    if c[j]:
                        resid[r] += c[j] * d
                moved = max(moved, abs(d))
                x[j] = new
        if moved < 0.05:
            break
    for j, (_, it) in enumerate(items):
        it.grams = x[j]


def _round_item(fb: FoodBase, it: Item) -> float:
    p = fb.products[it.product]
    g = it.grams
    if p.get("piece"):
        n = max(round(g / p["piece"]), 1 if it.lo > 0 else 0)
        return n * p["piece"]
    if p.get("cat") == "oil":
        return round(g / 5) * 5
    step = 5 if (p.get("dry") or g < 100) else 10
    return max(round(g / step) * step, 0)


def _fix_after_round(fb: FoodBase, meals: list[Meal], target: dict) -> None:
    """После округления граммов калории могут уйти на несколько процентов — доводим гарнирами и белком."""
    def kcal() -> float:
        return sum(fb.nut(it.product)[0] * it.grams for m in meals for it in m.items)
    for _ in range(12):
        diff = target["kcal"] - kcal()
        if abs(diff) < target["kcal"] * 0.02:
            return
        best = None
        for m in meals:
            for it in m.items:
                p = fb.products[it.product]
                if p.get("piece") or p.get("cat") in ("oil", "veg"):
                    continue
                step = 5 if (p.get("dry") or it.grams < 100) else 10
                g = it.grams + (step if diff > 0 else -step)
                if g < it.lo or g > it.hi:
                    continue
                gain = fb.nut(it.product)[0] * step
                score = abs(abs(diff) - gain)
                if best is None or score < best[0]:
                    best = (score, it, g)
        if not best:
            return
        best[1].grams = best[2]


def totals(fb: FoodBase, day: Day) -> dict:
    k = pr = f = c = 0.0
    items = [it for m in day.meals for it in m.items] + ([day.treat] if day.treat else [])
    for it in items:
        n = fb.nut(it.product)
        k += n[0] * it.grams
        pr += n[1] * it.grams
        f += n[2] * it.grams
        c += n[3] * it.grams
    return {"kcal": round(k), "protein": round(pr), "fat": round(f), "carbs": round(c)}


# ---------------------------------------------------------------------- генератор
@dataclass
class Prefs:
    products: list[str]
    treats: list[str]
    exclusions: list[str]
    meals: str = "3+1"           # "3" / "3+1" / "4-5"
    cooking: str = "daily"       # daily / batch / quick
    excluded_products: list[str] = field(default_factory=list)  # «✍️ своё» в исключениях


class MenuBuilder:
    def __init__(self, fb: FoodBase):
        self.fb = fb

    def _allowed(self, prefs: Prefs, relax: bool) -> set[str]:
        excl = self.fb.excluded_products(prefs.exclusions) | set(prefs.excluded_products)
        if relax:
            base = {k for k, p in self.fb.products.items() if not p.get("extra") and p.get("cat") != "treat"}
            base |= set(prefs.products)
        else:
            base = set(prefs.products) | set(self.fb.pantry)
        return base - excl

    def _resolve(self, dish: dict, allowed: set[str], usage: dict | None = None) -> list[Item] | None:
        items = []
        for slot, it in enumerate(dish.get("items", [])):
            pick_all = self.fb.expand(it.get("pick", []))
            options = [k for k in pick_all if k in allowed]
            if not options:
                if it.get("opt"):
                    continue
                return None
            # чаще всего бывает первый вариант, но по дням продукты чередуются
            if dish.get("src") == "margo" and options[0] == pick_all[0]:
                key = options[0]  # в рецептах Маргариты — её продукт, если он есть у девушки
            else:
                key = min(options, key=lambda k: ((usage or {}).get(k, 0), options.index(k)))
            if usage is not None:
                usage[key] = usage.get(key, 0) + 1
            lo, hi = float(it.get("min", 0)), float(it.get("max", it["g"] * 1.5))
            if dish.get("src") == "margo" and not it.get("add"):
                lo = max(lo, float(it["g"]) * 0.5)  # продукт из рецепта не убираем до нуля
            # йогурт вместо греческого, другие ягоды, лёгкий сыр вместо обычного — рецепт по сути тот же
            same = key == pick_all[0] or (self.fb.products[key]["cat"] in SWAPPABLE
                                          and self.fb.products[key]["cat"] == self.fb.products[pick_all[0]]["cat"])
            items.append(Item(product=key, grams=float(it["g"]), lo=lo, hi=hi, base=float(it["g"]),
                              slot=slot, first=same, add=bool(it.get("add")),
                              core=dish.get("src") == "margo" and not it.get("add")))
        return items

    def candidates(self, meal_type: str, prefs: Prefs) -> tuple[list[dict], bool]:
        """Блюда, которые можно собрать из продуктов девушки. Если таких нет — разрешаем базовые продукты."""
        for relax in (False, True):
            allowed = self._allowed(prefs, relax)
            ds = [d for d in self.fb.dishes if d.get("meal") == meal_type
                  and self._resolve(d, allowed) is not None]
            if prefs.cooking == "quick":
                q = [d for d in ds if d.get("quick")]
                ds = q if len(q) >= 2 else ds
            elif prefs.cooking == "batch" and meal_type in ("lunch", "dinner"):
                b = [d for d in ds if d.get("batch")]
                ds = b if len(b) >= 2 else ds
            if ds:
                return ds, relax
        return [], True

    @staticmethod
    def title_for(dish: dict, items: list[Item]) -> str:
        """Если основной продукт заменён (индейка вместо курицы, булгур вместо гречки) — запасное название."""
        by_slot = {it.slot: it for it in items}
        for rule in dish.get("alt") or []:
            if any(sl not in by_slot or not by_slot[sl].first for sl in rule.get("slots", [])):
                return rule.get("title", dish.get("title", ""))
        return dish.get("title", "")

    @staticmethod
    def fit_meals(meals: str, kcal: float) -> str:
        """Большую норму в 3 приёма не уместить без огромных порций — добавляем перекус."""
        if meals == "3" and kcal > 2100:
            meals = "3+1"
        if meals == "3+1" and kcal > 2700:
            meals = "4-5"
        return meals

    def build(self, prefs: Prefs, norm: dict, days: int = 7, seed: int = 0,
              variants: int = 14) -> tuple[list[Day], bool]:
        rng = random.Random(seed)
        prefs.meals = self.fit_meals(prefs.meals, norm["kcal"])
        plan = self.fb.meal_plans.get(prefs.meals) or self.fb.meal_plans["3+1"]
        excl = self.fb.excluded_products(prefs.exclusions) | set(prefs.excluded_products)
        treats = [t for t in self.fb.expand_treats(prefs.treats) if t not in excl]
        rng.shuffle(treats)
        relaxed_any = False
        pools: dict[str, list[dict]] = {}
        relax_of: dict[str, bool] = {}
        for mt in set(MEAL_TYPE[m] for m in plan):
            ds, rel = self.candidates(mt, prefs)
            rng.shuffle(ds)
            pools[mt], relax_of[mt] = ds, rel
            relaxed_any = relaxed_any or rel
        state = {"used": {mt: [] for mt in pools}, "usage": {}, "prev_batch": {}}
        out: list[Day] = []
        for d in range(days):
            best = None
            for v in range(variants):
                trial = self._build_day(d, v, plan, pools, relax_of, prefs, norm, treats, state, rng)
                if trial and (best is None or trial[0] < best[0]):
                    best = trial
            if best is None:
                out.append(Day(meals=[], treat=None, totals={"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}))
                continue
            _, day, new_state = best
            state = new_state
            out.append(day)
        return out, relaxed_any

    def _build_day(self, d, v, plan, pools, relax_of, prefs, norm, treats, state, rng):
        import copy
        st = copy.deepcopy(state)
        used, usage, prev_batch = st["used"], st["usage"], st["prev_batch"]
        day_used: set[str] = set()
        day_main: set[str] = set()
        meals: list[Meal] = []
        for mkey in plan:
            mt = MEAL_TYPE[mkey]
            pool = pools.get(mt) or []
            if not pool:
                continue
            allowed = self._allowed(prefs, relax_of[mt])
            if prefs.cooking == "batch" and mt in ("lunch", "dinner") and d % 2 == 1 and mkey in prev_batch:
                src = prev_batch[mkey]
                meals.append(Meal(mkey, src.dish_id, src.title,
                                  [Item(i.product, i.grams, i.lo, i.hi, i.grams, i.slot, i.first, i.add)
                                   for i in src.items], src.how, list(src.how_slots)))
                day_used.add(src.dish_id)
                day_main.add(src.items[0].product)
                continue
            recent = used[mt][-2:] if len(pool) > 3 else used[mt][-1:]
            # сначала блюда, которые были реже всего; при равенстве — свой порядок для каждого варианта дня
            order = sorted(pool, key=lambda x: (used[mt].count(x["id"]),
                                                zlib.crc32(f"{d}:{v}:{mkey}:{x['id']}".encode())))
            choice, items = None, None
            for strict in (True, False):
                for dish in order:
                    if dish["id"] in day_used or (strict and dish["id"] in recent):
                        continue
                    trial = self._resolve(dish, allowed, dict(usage))
                    if trial is None:
                        continue
                    if strict and trial[0].product in day_main:
                        continue  # один и тот же основной продукт дважды за день — только если иначе никак
                    choice, items = dish, self._resolve(dish, allowed, usage)
                    break
                if choice:
                    break
            if choice is None:
                choice = next((x for x in pool if x["id"] not in day_used), pool[0])
                items = self._resolve(choice, allowed, usage)
            if items is None:
                continue
            day_main.add(items[0].product)
            used[mt].append(choice["id"])
            day_used.add(choice["id"])
            how_slots = [i for i, x in enumerate(choice.get("items", [])) if not x.get("add")]
            meals.append(Meal(mkey, choice["id"], self.title_for(choice, items), items, choice.get("how", ""),
                              how_slots))
        if not meals:
            return None

        treat = None
        if treats and norm.get("treat_kcal"):
            tk = treats[d % len(treats)]
            p = self.fb.products[tk]
            g = min(p.get("cap", 50), norm["treat_kcal"] * 100 / p["k"])
            if p.get("piece"):  # ПП-конфеты, кусочек торта — целыми штуками
                n = max(1, int(g / p["piece"] + 0.5))
                while n > 1 and n * p["piece"] > p.get("cap", n * p["piece"]):
                    n -= 1
                g = n * p["piece"]
            else:
                g = round(g / 5) * 5
            if g >= 10:
                treat = Item(tk, g, g, g, g)
        tn = self.fb.nut(treat.product) if treat else (0, 0, 0, 0)
        tg = treat.grams if treat else 0
        target = {"kcal": norm["kcal"] - tn[0] * tg, "protein": norm["protein"] - tn[1] * tg,
                  "fat": norm["fat"] - tn[2] * tg, "carbs": norm["carbs"] - tn[3] * tg}
        shares = {m.key: plan.get(m.key, 0) for m in meals}
        s = sum(shares.values()) or 1
        shares = {k: val / s for k, val in shares.items()}
        fresh = [m for m in meals if not (prefs.cooking == "batch" and MEAL_TYPE[m.key] in ("lunch", "dinner")
                                          and d % 2 == 1 and m.key in prev_batch)]
        for m in fresh:  # порции масштабируем под калории приёма пищи (у кого-то 1 300, у кого-то 3 000)
            base_k = sum(self.fb.nut(it.product)[0] * it.base for it in m.items) or 1
            f = min(max(shares[m.key] * target["kcal"] / base_k, 0.6), 2.5)
            for it in m.items:
                piece = self.fb.products[it.product].get("piece")
                cat0 = self.fb.products[it.product]["cat"]
                # белок не урезаем ниже нормальной порции: 80 г курицы в обеде — это мало
                keep_lo = piece or cat0 in PROTEIN_CATS or it.core
                it.lo = it.lo * min(f, 1) * (1 if keep_lo else 0.7)
                cat = self.fb.products[it.product]["cat"]
                if cat != "veg":  # овощами калории не добираем — только больше гарнира и белка
                    cap = LEGUMES_DRY_CAP if (cat == "legumes" and self.fb.products[it.product].get("dry")) \
                        else CAP.get(cat, 9999)
                    cap *= max(1.0, norm["kcal"] / 2300)  # у спортивных девушек на 2 800+ ккал порции больше
                    it.hi = min(it.hi * max(f, 1), max(cap, it.lo))
                    if it.core:  # рецепт должен остаться узнаваемым: не больше двух «рецептурных» порций
                        it.hi = max(min(it.hi, it.base * 2), it.lo)
                if piece:
                    it.hi = max(it.hi, piece)
                it.base = min(max(it.base * f, it.lo), it.hi)
                it.grams = it.base
        # заготовки со вчера — граммы не меняем
        for m in meals:
            if m not in fresh:
                for it in m.items:
                    it.lo = it.hi = it.grams
        _solve(self.fb, meals, shares, target)
        for m in meals:
            for it in m.items:
                it.grams = _round_item(self.fb, it)
            m.items = [it for it in m.items if it.grams >= 5]
        _fix_after_round(self.fb, meals, target)
        for m in meals:  # рецепт показываем, только если продукты в блюде — именно из рецепта
            if m.how and not all(any(it.slot == sl and it.first for it in m.items) for sl in m.how_slots):
                m.how = ""
        if prefs.cooking == "batch":
            for m in fresh:
                if MEAL_TYPE[m.key] in ("lunch", "dinner"):
                    prev_batch[m.key] = m
        day = Day(meals=meals, treat=treat)
        day.totals = totals(self.fb, day)
        t = day.totals
        score = (3 * abs(t["kcal"] - norm["kcal"]) / max(norm["kcal"], 1)
                 + abs(t["protein"] - norm["protein"]) / max(norm["protein"], 1)
                 + abs(t["fat"] - norm["fat"]) / max(norm["fat"], 1)
                 + abs(t["carbs"] - norm["carbs"]) / max(norm["carbs"], 1))
        return score, day, st


# ---------------------------------------------------------------------- текст для Telegram
def _plural(n: int, one: str) -> str:
    forms = {"кусочек": ("кусочек", "кусочка", "кусочков")}
    if one not in forms:
        return one
    a, b, c = forms[one]
    if n % 10 == 1 and n % 100 != 11:
        return a
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return b
    return c


def fmt_item(fb: FoodBase, it: Item, custom_names: dict | None = None) -> str:
    p = fb.products[it.product]
    name = (custom_names or {}).get(it.product) or p["name"]
    g = int(it.grams)
    if p.get("piece"):
        n = max(round(g / p["piece"]), 1)
        unit = _plural(n, p.get("piece_name", "шт."))
        return f"{name} — {n} {unit}"
    if p.get("ml"):
        return f"{name} — {g} мл"
    if p.get("cat") == "oil":
        spoon = {5: "1 ч. л.", 10: "2 ч. л.", 15: "1 ст. л."}.get(g, "")
        return f"{name} — {g} г ({spoon})" if spoon else f"{name} — {g} г"
    if p.get("dry"):
        return f"{name} — {g} г (сухой вес)"
    return f"{name} — {g} г"


def fmt_day(fb: FoodBase, n: int, day: Day, custom_names: dict | None = None,
            treat_note: str = "без чувства вины", show_how: bool = True) -> str:
    t = day.totals
    lines = [f"📅 <b>День {n}</b> — {t['kcal']:,} ккал · Б {t['protein']} · Ж {t['fat']} · У {t['carbs']}".replace(",", " ")]
    for m in day.meals:
        lines.append("")
        lines.append(f"<b>{MEAL_LABEL.get(m.key, m.key)}</b> · {m.title}")
        for it in m.items:
            lines.append("• " + fmt_item(fb, it, custom_names))
        if show_how and m.how:
            lines.append(f"<i>👩‍🍳 {m.how}</i>")
    if day.treat:
        p = fb.products[day.treat.product]
        lines.append("")
        lines.append(f"<b>🍫 Любимое</b> · {fmt_item(fb, day.treat, custom_names)} — {treat_note}")
        if show_how and p.get("recipe") and p.get("how"):
            lines.append(f"<i>👩‍🍳 Мой ПП-рецепт: {p['how']}</i>")
    return "\n".join(lines)
