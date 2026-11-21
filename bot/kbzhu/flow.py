"""Сценарий «Норма КБЖУ + меню на 7 дней» внутри @margo_sale_bot.

Кнопки внутри сценария — callback «k:<команда>:<аргумент>».
Кнопки из цепочек и меню бота — действие «kbzhu:<команда>» (через App.do).
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import html
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto)

from ..content import render
from .calc import calculate
from .menu import FoodBase, MenuBuilder, Prefs, fmt_day

if TYPE_CHECKING:
    from ..core import App

log = logging.getLogger("margo.kbzhu")
MSK = dt.timezone(dt.timedelta(hours=3))

ACT = ["min", "low", "mid", "high"]
FOOD = ["calm", "binge", "diets", "fear"]
GOALS = ["lose", "keep", "gain"]
FOOD_TITLE = {"calm": "всё спокойно", "binge": "срывы и переедания", "diets": "круг диет",
              "fear": "⚠️ боюсь есть, ограничиваю"}
GOAL_TITLE = {"lose": "снизить вес", "keep": "сохранить вес", "gain": "набрать"}
ACT_TITLE = {"min": "минимальная", "low": "лёгкая", "mid": "средняя", "high": "высокая"}
SIMILAR_TREATS = ["chocolate", "cookies", "ice_cream", "candies", "chips", "cake", "pizza"]


@dataclass
class KState:
    step: str
    data: dict = field(default_factory=dict)
    selected: list = field(default_factory=list)
    treats: list = field(default_factory=list)
    custom_names: dict = field(default_factory=dict)
    excl: list = field(default_factory=list)
    excl_products: list = field(default_factory=list)
    group: int = 0
    pending_word: str = ""
    awaiting: str = ""   # name / age / height / weight / own / treat / excl — ждём текст


def _num(text: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def _money(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


class Kbzhu:
    def __init__(self, app: "App"):
        self.app = app
        self.state: dict[int, KState] = {}
        self.fb = FoodBase(app.cfg.content_path.with_name("kbzhu_food.yaml"))
        self.builder = MenuBuilder(self.fb)

    # ------------------------------------------------------------------ настройки
    @property
    def cfg(self) -> dict:
        return self.app.content.kbzhu

    @property
    def enabled(self) -> bool:
        return bool(self.cfg) and self.cfg.get("enabled", True)

    def t(self, key: str, **kw) -> str:
        return render((self.cfg.get("texts") or {}).get(key, ""), **kw).strip()

    def b(self, key: str, **kw) -> str:
        return render((self.cfg.get("buttons") or {}).get(key, key), **kw)

    def reload(self) -> list[str]:
        return self.fb.reload()

    def screen_buttons(self, screen: str) -> list:
        if not self.enabled:
            return []
        extra = (self.cfg.get("screen_buttons") or {}).get(screen) or []
        known = set(self.app.content.products)
        return [list(b) for b in extra if not str(b[1]).startswith("product:") or b[1].split(":", 1)[1] in known]

    def start_buttons(self) -> list:
        sb = self.cfg.get("start_button")
        return [list(sb)] if self.enabled and sb else []

    # ------------------------------------------------------------------ отправка
    def kb(self, rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
        """rows: [[(текст, 'k:...' | 'a:...' | 'url:...')]]"""
        out = []
        for row in rows:
            r = []
            for text, data in row:
                if data.startswith("url:"):
                    r.append(InlineKeyboardButton(text=text, url=data[4:]))
                else:
                    r.append(InlineKeyboardButton(text=text, callback_data=data[:64]))
            out.append(r)
        return InlineKeyboardMarkup(inline_keyboard=out)

    async def send(self, uid: int, text: str, rows=None) -> bool:
        return await self.app.send(uid, text, self.kb(rows) if rows else None)

    async def _name(self, uid: int, st: KState | None = None) -> str:
        if st and st.data.get("name"):
            return html.escape(st.data["name"])
        prof = await self.app.db.kbzhu_get(uid)
        if prof and prof.get("name"):
            return html.escape(prof["name"])
        return (await self.app._user_vars(uid))["first_name"]

    # ------------------------------------------------------------------ вход через действия
    async def action(self, uid: int, arg: str) -> None:
        cmd, _, rest = arg.partition(":")
        if not self.enabled:
            return await self.app.show_screen(uid, "start")
        if cmd in ("", "start"):
            return await self.entry(uid)
        if cmd == "menu":
            return await self.start_menu(uid)
        if cmd == "fb":
            await self.app.db.add_tag(uid, f"kbzhu_fb_{rest}"[:64])
            return await self.send(uid, self.t(f"fb_{rest}") or "Спасибо 🤍")
        if cmd == "buy":
            return await self.buy(uid, rest)
        if cmd == "reviews":
            return await self.reviews(uid)
        return await self.entry(uid)

    async def entry(self, uid: int) -> None:
        await self.app.db.add_tag(uid, "kbzhu_start")
        prof = await self.app.db.kbzhu_get(uid)
        if prof and prof.get("norm"):
            rows = [[(self.b("my_norm"), "k:norm")]]
            if prof.get("menu"):
                rows.append([(self.b("my_menu"), "k:mymenu")])
            else:
                rows.append([(self.b("menu_yes"), "k:menu")])
            rows += [[(self.b("recalc"), "k:restart")], [(self.b("delete"), "k:delete")],
                     [(self.b("back"), "a:screen:start")]]
            return await self.send(uid, self.t("already", name=await self._name(uid)), rows)
        await self.send(uid, self.t("intro"))
        await self.ask_consent(uid)

    async def ask_consent(self, uid: int) -> None:
        url = self.cfg.get("policy_url", "https://rita-fitt.ru/privacy.html")
        await self.send(uid, self.t("consent", policy_url=url),
                        [[(self.t("consent_yes") or "Согласна", "k:consent")],
                         [(self.t("consent_policy") or "Политика", f"url:{url}")]])

    # ------------------------------------------------------------------ кнопки «k:»
    async def on_callback(self, c: CallbackQuery) -> None:
        uid = c.from_user.id
        parts = c.data.split(":")
        cmd, arg = parts[1], (parts[2] if len(parts) > 2 else "")
        st = self.state.get(uid)

        # команды, которым не нужно состояние анкеты
        simple = {"consent", "restart", "norm", "mymenu", "delete", "menu", "normonly", "later", "buy", "cont"}
        if cmd in simple:
            await self._drop_markup(c)
            if cmd == "consent":
                await self.app.db.add_tag(uid, "consent_pd")
                await self.app.db.add_tag(uid, "kbzhu_consent")
                self.state[uid] = KState(step="name", awaiting="name")
                return await self.ask(uid)
            if cmd == "restart":
                await self.app.db.add_tag(uid, "kbzhu_consent")
                self.state[uid] = KState(step="name", awaiting="name")
                return await self.ask(uid)
            if cmd == "norm":
                return await self.show_norm(uid)
            if cmd == "mymenu":
                return await self.show_menu(uid)
            if cmd == "delete":
                self.state.pop(uid, None)
                await self.app.db.kbzhu_delete(uid)
                return await self.send(uid, self.t("deleted"), [[(self.b("back"), "a:screen:start")]])
            if cmd == "menu":
                return await self.start_menu(uid)
            if cmd == "normonly":
                await self.app.db.add_tag(uid, "kbzhu_norm_only")
                await self.schedule_norm_only(uid)
                return await self.send(uid, self.t("norm_only"), [[(self.b("menu_yes"), "k:menu")]])
            if cmd == "later":
                return await self.send(uid, self.t("later"))
            if cmd == "buy":
                return await self.buy(uid, arg)
            if cmd == "cont":
                return
        if not st:  # бот перезапускался или анкета уже закончилась
            await self._drop_markup(c)
            await self.send(uid, self.t("expired") or "Давай начнём сначала 🙂")
            return await self.entry(uid)

        if cmd == "name":                                   # «Зови меня …»
            await self._drop_markup(c)
            return await self.on_answer(uid, "name", c.from_user.first_name or "")
        if cmd in ("preg", "act", "food", "goal"):
            await self._mark_choice(c)
            return await self.on_answer(uid, cmd, arg)
        if cmd == "t":                                      # переключить продукт
            return await self.toggle(c, st, arg)
        if cmd == "tt":                                     # переключить любимое
            return await self.toggle_treat(c, st, arg)
        if cmd == "x":                                      # переключить исключение
            return await self.toggle_excl(c, st, arg)
        if cmd == "own":
            st.awaiting = {"products": "own", "treats": "treat", "excl": "excl"}.get(st.step, "")
            prompt = {"own": "ask_own", "treat": "ask_treat_own", "excl": "ask_excl_own"}.get(st.awaiting)
            return await self.send(uid, self.t(prompt)) if prompt else None
        if cmd == "sim":                                    # «на что похоже» для своего любимого
            await self._drop_markup(c)
            if st.pending_word and arg in self.fb.products:
                st.treats.append(arg)
                st.custom_names[arg] = st.pending_word
                await self.send(uid, self.t("own_found", items=html.escape(st.pending_word)),
                                [[(self.b("next"), "k:next")]])
                st.pending_word = ""
            return
        if cmd == "next":
            await self._drop_markup(c)
            return await self.next_menu_step(uid, st)
        if cmd == "meals":
            await self._mark_choice(c)
            st.data["meals"] = arg
            st.step = "cooking"
            return await self.send(uid, self.t("ask_cooking"), [
                [(self.b("cook_daily"), "k:cook:daily")], [(self.b("cook_batch"), "k:cook:batch")],
                [(self.b("cook_quick"), "k:cook:quick")]])
        if cmd == "cook":
            await self._mark_choice(c)
            st.data["cooking"] = arg
            return await self.make_menu(uid, st)

    async def _drop_markup(self, c: CallbackQuery) -> None:
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass

    async def _mark_choice(self, c: CallbackQuery) -> None:
        """Показывает выбранный вариант в сообщении и убирает кнопки."""
        label = next((b.text for row in (c.message.reply_markup.inline_keyboard if c.message and c.message.reply_markup
                                          else []) for b in row if b.callback_data == c.data), "")
        try:
            if label and c.message and c.message.text:
                await c.message.edit_text(f"{html.escape(c.message.text)}\n\n<b>{html.escape(label)}</b>",
                                          parse_mode="HTML")
            else:
                await c.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ текстовые ответы
    def wants_text(self, uid: int) -> bool:
        return uid in self.state

    async def on_text(self, uid: int, text: str) -> None:
        st = self.state.get(uid)
        if not st:
            return
        if not st.awaiting:
            return await self.send(uid, self.t("use_buttons"))
        field_ = st.awaiting
        if field_ in ("name", "age", "height", "weight"):
            return await self.on_answer(uid, field_, text)
        if field_ == "own":
            return await self.add_own(uid, st, text)
        if field_ == "treat":
            return await self.add_own_treat(uid, st, text)
        if field_ == "excl":
            return await self.add_own_excl(uid, st, text)

    # ------------------------------------------------------------------ анкета
    async def ask(self, uid: int) -> None:
        st = self.state[uid]
        name = await self._name(uid, st)
        s = st.step
        if s == "name":
            st.awaiting = "name"
            tg = (await self.app._user_vars(uid))["first_name"]
            rows = [[(self.b("name_tg", first_name=tg), "k:name")]] if tg and tg != "привет" else None
            return await self.send(uid, self.t("ask_name"), rows)
        if s == "age":
            st.awaiting = "age"
            return await self.send(uid, self.t("ask_age", name=name))
        if s == "preg":
            st.awaiting = ""
            return await self.send(uid, self.t("ask_preg"), [
                [(self.b("preg_no"), "k:preg:no")], [(self.b("preg_yes"), "k:preg:yes")],
                [(self.b("preg_bf"), "k:preg:bf")]])
        if s == "height":
            st.awaiting = "height"
            return await self.send(uid, self.t("ask_height"))
        if s == "weight":
            st.awaiting = "weight"
            return await self.send(uid, self.t("ask_weight"))
        if s == "act":
            st.awaiting = ""
            return await self.send(uid, self.t("ask_activity"),
                                   [[(self.b(f"act_{a}"), f"k:act:{a}")] for a in ACT])
        if s == "food":
            st.awaiting = ""
            return await self.send(uid, self.t("ask_food"), [[(self.b(f"food_{f}"), f"k:food:{f}")] for f in FOOD])
        if s == "goal":
            st.awaiting = ""
            return await self.send(uid, self.t("ask_goal"), [[(self.b(f"goal_{g}"), f"k:goal:{g}")] for g in GOALS])

    async def on_answer(self, uid: int, field_: str, value: str) -> None:
        st = self.state.get(uid)
        if not st or st.step != field_:
            return
        nxt = {"name": "age", "age": "preg", "preg": "height", "height": "weight", "weight": "act",
               "act": "food", "food": "goal"}
        if field_ == "name":
            name = re.sub(r"\s+", " ", value).strip()[:40]
            if not name:
                return await self.ask(uid)
            st.data["name"] = name
        elif field_ in ("age", "height", "weight"):
            v = _num(value)
            ex = {"age": "28", "height": "165", "weight": "62,5"}[field_]
            if v is None:
                return await self.send(uid, self.t("bad_number", example=ex))
            if field_ == "age":
                if v < 18:
                    self.state.pop(uid, None)
                    await self.app.db.add_tag(uid, "kbzhu_minor")
                    return await self.send(uid, self.t("minor"), [[(self.b("back"), "a:screen:start")]])
                if v > 75:
                    return await self.send(uid, self.t("bad_age"))
                st.data["age"] = int(v)
            elif field_ == "height":
                if v < 3:  # написали в метрах: 1.65
                    v *= 100
                if not 140 <= v <= 200:
                    return await self.send(uid, self.t("bad_height"))
                st.data["height"] = round(v, 1)
            else:
                if not 35 <= v <= 200:
                    return await self.send(uid, self.t("bad_weight"))
                st.data["weight"] = round(v, 1)
        elif field_ == "preg":
            st.data["preg"] = value
            if value in ("yes", "bf"):
                return await self.pregnant(uid, st, value)
        elif field_ == "act":
            if value not in ACT:
                return
            st.data["activity"] = value
        elif field_ == "food":
            if value not in FOOD:
                return
            st.data["food"] = value
        elif field_ == "goal":
            if value not in GOALS:
                return
            st.data["goal"] = value
            return await self.finish_norm(uid, st)
        st.step = nxt[field_]
        await self.ask(uid)

    async def pregnant(self, uid: int, st: KState, value: str) -> None:
        self.state.pop(uid, None)
        await self.app.db.add_tag(uid, "kbzhu_preg")
        await self.send(uid, self.t("pregnant"), [
            [(self.b("konsult_plain"), "a:product:konsult")], [(self.b("vedenie_plain"), "a:product:vedenie")]])
        what = "беременна" if value == "yes" else "кормит грудью"
        await self.app.alert(
            f"🤰 <b>Расчёт КБЖУ</b>: {html.escape(st.data.get('name', ''))}, возраст {st.data.get('age', '?')} — "
            f"{what}. Расчёт не делали, бот предложил консультацию.", about_uid=uid)

    async def finish_norm(self, uid: int, st: KState) -> None:
        d = st.data
        bmi = d["weight"] / (d["height"] / 100) ** 2
        if d["goal"] == "lose" and bmi < 18.5:
            st.step = "goal"
            await self.app.db.add_tag(uid, "kbzhu_low_bmi")
            return await self.send(uid, self.t("low_bmi", name=await self._name(uid, st)), [
                [(self.b("maintain"), "k:goal:keep")], [(self.b("konsult_plain"), "k:buy:konsult")]])
        n = calculate(d["age"], d["height"], d["weight"], d["activity"], d["goal"], d["food"])
        prof = await self.app.db.kbzhu_get(uid) or {}
        prof.update({k: d[k] for k in ("name", "age", "height", "weight", "activity", "food", "goal")})
        prof["norm"] = n.as_dict()
        prof["norm_at"] = int(time.time())
        prof.pop("menu", None)
        await self.app.db.kbzhu_save(uid, prof)
        await self.app.db.add_tag(uid, "kbzhu_norm")
        await self.app.db.add_tag(uid, f"kbzhu_food_{d['food']}")
        await self.show_norm(uid, prof)
        if n.floor_applied:
            await self.send(uid, self.t("floor_note", kcal=_money(n.kcal)))
        if d["food"] == "fear":
            await self.send(uid, self.t("fear_note"), [[(self.b("vedenie_plain"), "a:product:vedenie")]])
        self.state.pop(uid, None)
        await self.send(uid, self.t("offer_menu"), [[(self.b("menu_yes"), "k:menu")], [(self.b("menu_no"), "k:normonly")]])
        if self.cfg.get("notify_each", True):
            u = await self.app.db.get_user(uid)
            src = html.escape((u["source"] if u else "") or "—")
            mark = "⚠️ " if d["food"] == "fear" else ""
            await self.app.alert(
                f"🔸 {mark}<b>Расчёт КБЖУ</b>: {html.escape(d['name'])}, возраст {d['age']}, {d['height']:g} см, "
                f"{d['weight']:g} кг\nЦель: {GOAL_TITLE[d['goal']]} · активность: {ACT_TITLE[d['activity']]}\n"
                f"С едой: {FOOD_TITLE[d['food']]}\nНорма: {n.kcal} ккал · Б {n.protein} · Ж {n.fat} · У {n.carbs}\n"
                f"Источник: {src}", about_uid=uid)

    async def show_norm(self, uid: int, prof: dict | None = None) -> None:
        prof = prof or await self.app.db.kbzhu_get(uid)
        if not prof or not prof.get("norm"):
            return await self.entry(uid)
        n = prof["norm"]
        text = self.t("result", name=html.escape(prof.get("name", "")), kcal=_money(n["kcal"]),
                      protein=n["protein"], fat=n["fat"], carbs=n["carbs"], treat=n["treat_kcal"])
        text += "\n\n" + self.t(f"result_{prof.get('goal', 'keep')}")
        extra = {"binge": "binge_note", "diets": "diets_note"}.get(prof.get("food", ""))
        rows = None
        if extra:
            text += "\n\n" + self.t(extra)
            rows = [[(self.b("book" if extra == "binge_note" else "textbook"),
                      "a:product:kniga" if extra == "binge_note" else "a:product:uchebnik")]]
        await self.send(uid, text, rows)

    # ------------------------------------------------------------------ анкета для меню
    async def start_menu(self, uid: int) -> None:
        prof = await self.app.db.kbzhu_get(uid)
        if not prof or not prof.get("norm"):
            return await self.entry(uid)
        await self.app.db.add_tag(uid, "kbzhu_menu_start")
        await self.app.db.cancel(uid, group_key="chain:kbzhu_norm_only")
        st = KState(step="products", data={k: prof.get(k) for k in ("name", "goal", "food")})
        last = prof.get("prefs") or {}
        st.selected = list(last.get("products", []))      # при пересборке — прошлый выбор
        self.state[uid] = st
        await self.show_group(uid, st)

    def _group_kb(self, st: KState) -> InlineKeyboardMarkup:
        g = self.fb.groups[st.group]
        rows, row = [], []
        for k in g["items"]:
            mark = "✅ " if k in st.selected else ""
            short = self.fb.label(k)
            row.append((f"{mark}{short}", f"k:t:{k}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([(self.b("own"), "k:own")])
        rows.append([(self.b("next"), "k:next")])
        return self.kb(rows)

    async def show_group(self, uid: int, st: KState) -> None:
        st.step, st.awaiting = "products", ""
        g = self.fb.groups[st.group]
        text = self.t("ask_products", group=g["title"], step=st.group + 1, total=len(self.fb.groups))
        await self.app.send(uid, text, self._group_kb(st))

    async def toggle(self, c: CallbackQuery, st: KState, key: str) -> None:
        if st.step != "products" or key not in self.fb.products:
            return
        if key in st.selected:
            st.selected.remove(key)
        else:
            st.selected.append(key)
        try:
            await c.message.edit_reply_markup(reply_markup=self._group_kb(st))
        except Exception:  # noqa: BLE001
            pass

    def _treat_kb(self, st: KState) -> InlineKeyboardMarkup:
        rows, row = [], []
        for k in self.fb.treat_buttons:
            mark = "✅ " if k in st.treats else ""
            row.append((f"{mark}{self.fb.label(k)}", f"k:tt:{k}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows += [[(self.b("own"), "k:own")], [(self.b("next"), "k:next")]]
        return self.kb(rows)

    async def toggle_treat(self, c: CallbackQuery, st: KState, key: str) -> None:
        if st.step != "treats" or (key not in self.fb.products and key not in self.fb.treat_sets):
            return
        st.treats.remove(key) if key in st.treats else st.treats.append(key)
        try:
            await c.message.edit_reply_markup(reply_markup=self._treat_kb(st))
        except Exception:  # noqa: BLE001
            pass

    def _excl_kb(self, st: KState) -> InlineKeyboardMarkup:
        rows = [[(("✅ " if not st.excl and not st.excl_products else "") + self.b("excl_none"), "k:x:none")]]
        row = []
        for k, rule in self.fb.exclusions.items():
            row.append((("✅ " if k in st.excl else "") + rule.get("title", k), f"k:x:{k}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows += [[(self.b("own"), "k:own")], [(self.b("next"), "k:next")]]
        return self.kb(rows)

    async def toggle_excl(self, c: CallbackQuery, st: KState, key: str) -> None:
        if st.step != "excl":
            return
        if key == "none":
            st.excl, st.excl_products = [], []
        elif key in self.fb.exclusions:
            st.excl.remove(key) if key in st.excl else st.excl.append(key)
        try:
            await c.message.edit_reply_markup(reply_markup=self._excl_kb(st))
        except Exception:  # noqa: BLE001
            pass

    async def next_menu_step(self, uid: int, st: KState) -> None:
        st.awaiting = ""
        if st.step == "products":
            if st.group + 1 < len(self.fb.groups):
                st.group += 1
                return await self.show_group(uid, st)
            main = {"poultry", "meat", "fish", "seafood", "eggs", "cottage", "tofu", "salted_fish", "canned_fish",
                    "surimi", "egg_white", "legumes"}
            carbs = {"grain", "pasta", "potato", "oats", "bread", "lavash", "crispbread", "legumes", "noodles"}
            cats = {self.fb.products[k]["cat"] for k in st.selected}
            if not (cats & main) or not (cats & carbs):
                st.group = 0
                await self.send(uid, self.t("few_products"))
                return await self.show_group(uid, st)
            st.step = "treats"
            return await self.app.send(uid, self.t("ask_treats"), self._treat_kb(st))
        if st.step == "treats":
            st.step = "excl"
            return await self.app.send(uid, self.t("ask_excl"), self._excl_kb(st))
        if st.step == "excl":
            st.step = "meals"
            return await self.send(uid, self.t("ask_meals"), [
                [(self.b("meals_3"), "k:meals:3")], [(self.b("meals_31"), "k:meals:3+1")],
                [(self.b("meals_45"), "k:meals:4-5")]])

    def _split(self, text: str) -> list[str]:
        return [w.strip() for w in re.split(r"[,;\n]+|\s+и\s+", text or "") if w.strip()][:10]

    async def add_own(self, uid: int, st: KState, text: str) -> None:
        found, unknown = [], []
        for w in self._split(text):
            k = self.fb.lookup(w)
            if not k:
                unknown.append(w)
                await self.app.db.kbzhu_unknown_add(w.lower())
                continue
            if self.fb.products[k]["cat"] == "treat":
                if k not in st.treats:
                    st.treats.append(k)
            elif k not in st.selected:
                st.selected.append(k)
            found.append(self.fb.products[k]["name"])
        parts = []
        if found:
            parts.append(self.t("own_found", items=html.escape(", ".join(found))))
        if unknown:
            parts.append(self.t("own_unknown", items=html.escape(", ".join(unknown))))
        parts.append(self.t("own_more"))
        await self.send(uid, "\n\n".join(parts), [[(self.b("next"), "k:next")]])

    async def add_own_treat(self, uid: int, st: KState, text: str) -> None:
        words = self._split(text)
        found = []
        for w in words:
            k = self.fb.lookup(w)
            if k and self.fb.products[k]["cat"] == "treat":
                if k not in st.treats:
                    st.treats.append(k)
                found.append(self.fb.products[k]["name"])
            elif k:
                if k not in st.selected:
                    st.selected.append(k)
                found.append(self.fb.products[k]["name"])
            else:
                await self.app.db.kbzhu_unknown_add(w.lower())
                st.pending_word = w[:40]
                rows, row = [], []
                for s in SIMILAR_TREATS:
                    if s in self.fb.products:
                        row.append((self.fb.products[s]["name"], f"k:sim:{s}"))
                        if len(row) == 2:
                            rows.append(row)
                            row = []
                if row:
                    rows.append(row)
                if found:
                    await self.send(uid, self.t("own_found", items=html.escape(", ".join(found))))
                return await self.send(uid, self.t("treat_similar", word=html.escape(w)), rows)
        await self.send(uid, self.t("own_found", items=html.escape(", ".join(found))) + "\n\n" + self.t("own_more"),
                        [[(self.b("next"), "k:next")]])

    async def add_own_excl(self, uid: int, st: KState, text: str) -> None:
        found = []
        for w in self._split(text):
            k = self.fb.lookup(w)
            if k:
                st.excl_products.append(k)
                if k in st.selected:
                    st.selected.remove(k)
                found.append(self.fb.products[k]["name"])
        msg = self.t("excl_found", items=html.escape(", ".join(found))) if found else self.t("excl_unknown")
        await self.send(uid, msg + "\n\n" + self.t("own_more"), [[(self.b("next"), "k:next")]])

    # ------------------------------------------------------------------ меню
    async def make_menu(self, uid: int, st: KState) -> None:
        prof = await self.app.db.kbzhu_get(uid)
        if not prof or not prof.get("norm"):
            return await self.entry(uid)
        self.state.pop(uid, None)
        prefs = Prefs(products=list(st.selected), treats=list(st.treats), exclusions=list(st.excl),
                      excluded_products=list(st.excl_products), meals=st.data.get("meals", "3+1"),
                      cooking=st.data.get("cooking", "daily"))
        fitted = MenuBuilder.fit_meals(prefs.meals, prof["norm"]["kcal"])
        if fitted != prefs.meals:
            await self.send(uid, self.t("meals_bumped"))
        await self.send(uid, self.t("building"))
        try:
            days, relaxed = await asyncio.to_thread(self.builder.build, prefs, prof["norm"], 7, uid)
        except Exception as e:  # noqa: BLE001
            log.exception("menu build failed: %s", e)
            await self.app.alert(f"⚠️ Не получилось собрать меню КБЖУ: {html.escape(str(e))[:300]}", about_uid=uid)
            return await self.send(uid, "Что-то пошло не так 🙈 Маргарита уже знает и поможет лично.")
        texts = [fmt_day(self.fb, i + 1, d, st.custom_names) for i, d in enumerate(days) if d.meals]
        prof["prefs"] = {"products": prefs.products, "treats": prefs.treats, "exclusions": prefs.exclusions,
                         "excluded_products": prefs.excluded_products, "meals": prefs.meals,
                         "cooking": prefs.cooking, "custom_names": st.custom_names}
        prof["menu"] = texts
        first_menu = not prof.get("menu_at")
        prof["menu_at"] = prof.get("menu_at") or int(time.time())
        await self.app.db.kbzhu_save(uid, prof)
        await self.app.db.add_tag(uid, "kbzhu_menu")
        for tx in texts:
            await self.app.send(uid, tx)
            await asyncio.sleep(0.3)
        if relaxed:
            await self.send(uid, self.t("relaxed"))
        await self.send(uid, self.t("menu_done"))
        if True:  # книга «ПП-завтраки» — с каждым новым меню
            await self.send_gifts(uid)
        await self.offer_collections(uid)
        if first_menu:
            await self.schedule_follow(uid)

    async def show_menu(self, uid: int) -> None:
        prof = await self.app.db.kbzhu_get(uid)
        if not prof or not prof.get("menu"):
            return await self.start_menu(uid)
        for tx in prof["menu"]:
            await self.app.send(uid, tx)
            await asyncio.sleep(0.3)
        await self.send(uid, "👆", [[(self.b("recalc"), "k:restart")], [(self.b("back"), "a:screen:start")]])

    # ------------------------------------------------------------------ скидка и покупки
    def _disc(self) -> dict:
        return self.cfg.get("discount") or {}

    async def discount_deadline(self, uid: int) -> int | None:
        """Unix-время окончания скидки или None, если скидки нет / она закончилась."""
        d = self._disc()
        if not d.get("percent"):
            return None
        prof = await self.app.db.kbzhu_get(uid)
        if not prof or not prof.get("offer_at"):
            return None
        end = prof["offer_at"] + int(d.get("hours", 72)) * 3600
        return end if time.time() < end else None

    def _price(self, key: str, discounted: bool) -> int:
        p = self.app.content.products.get(key, {})
        price = int(p.get("price", 0) or 0)
        if discounted:
            price = int(round(price * (100 - int(self._disc().get("percent", 0))) / 100))
        return price

    async def offer_collections(self, uid: int) -> None:
        """Сразу после меню: сборники рецептов (обеды, ужины, перекусы)."""
        if "recepty" not in self.app.content.products:
            return
        rows = [[(self.b("collections_all"), "a:product:recepty")]]
        if "recepty_singles" in self.app.content.screens:
            rows.append([(self.b("collections_singles"), "a:screen:recepty_singles")])
        await self.send(uid, self.t("collections_offer"), rows)

    async def start_discount(self, uid: int) -> None:
        """Скидка на ведение и консультацию начинается с предложения на 3-й день."""
        prof = await self.app.db.kbzhu_get(uid)
        if prof and not prof.get("offer_at"):
            prof["offer_at"] = int(time.time())
            await self.app.db.kbzhu_save(uid, prof)

    async def buy(self, uid: int, what: str) -> None:
        await self.app.db.add_tag(uid, "kbzhu_buy_click")
        deadline = await self.discount_deadline(uid)
        if what == "konsult":
            key = "konsult_kbzhu" if deadline and "konsult_kbzhu" in self.app.content.products else "konsult"
            return await self.app.show_product(uid, key)
        if what == "vedenie":
            if not deadline or "vedenie" not in (self._disc().get("products") or []):
                return await self.app.show_product(uid, "vedenie")
            await self.app.db.add_tag(uid, "interest_vedenie")
            from urllib.parse import quote
            url = "https://t.me/margo_tyutyunnik?text=" + quote(self.t("vedenie_prefill"))
            it = (self.app.content.products.get("vedenie") or {}).get("interest") or {}
            if it.get("url", "").startswith("https://t.me/"):
                url = it["url"].split("?")[0] + "?text=" + quote(self.t("vedenie_prefill"))
            dl = dt.datetime.fromtimestamp(deadline, MSK).strftime("%d.%m %H:%M")
            await self.send(uid, self.t("vedenie_discount", discount=self._disc().get("percent", 0), deadline=dl),
                            [[(self.b("write_margo"), f"url:{url}")]])
            price = self._price("vedenie", True)
            return await self.app.alert(
                f"🤍 <b>Хочет в личное ведение</b> — пришла из расчёта КБЖУ, скидка {self._disc().get('percent')}% "
                f"до {dl} (МСК).\nСчёт со скидкой: <code>/schet {uid} vedenie {price}</code>", about_uid=uid)
        return await self.app.show_screen(uid, "catalog")

    async def reviews(self, uid: int) -> None:
        ids = json.loads(await self.app.db.kv_get("kbzhu_reviews", "[]") or "[]")
        if ids:
            try:
                for i in range(0, len(ids), 10):
                    await self.app.bot.send_media_group(uid, [InputMediaPhoto(media=f) for f in ids[i:i + 10]])
            except Exception as e:  # noqa: BLE001
                log.warning("reviews album failed: %s", e)
        await self.send(uid, self.t("reviews"), [[(self.b("vedenie_plain"), "k:buy:vedenie")],
                                                 [(self.b("catalog"), "a:screen:catalog")]])

    # ------------------------------------------------------------------ цепочки после меню
    @staticmethod
    def _at(day: int, hhmm: str, base: float | None = None) -> int:
        start = dt.datetime.fromtimestamp(base or time.time(), MSK)
        h, m = (int(x) for x in (hhmm or "12:00").split(":"))
        target = (start + dt.timedelta(days=int(day))).replace(hour=h, minute=m, second=0, microsecond=0)
        return int(target.timestamp())

    async def schedule_follow(self, uid: int) -> None:
        steps = [(i, self._at(s.get("day", i + 1), s.get("time", "12:00"))) for i, s in
                 enumerate(self.cfg.get("follow") or [])]
        if not steps:
            return
        await self.app.db.cancel(uid, group_key="chain:kbzhu_norm_only")
        # пока идут сообщения после меню, прогрев после гайда не нужен — иначе бот пишет дважды в день
        await self.app.db.cancel(uid, group_key="chain:warmup")
        await self.app.db.schedule(uid, "kbzhu_follow", steps, "chain:kbzhu_follow", {})

    async def send_gifts(self, uid: int) -> None:
        """Книги рецептов: день 0 — сразу, остальные — цепочкой kbzhu_gifts (идёт и после покупки)."""
        gifts = self.cfg.get("gifts") or []
        later = []
        for i, g in enumerate(gifts):
            if int(g.get("day", 0)) == 0:
                await self.send(uid, render(g.get("text", ""), **await self.app._user_vars(uid)).strip())
                if g.get("file") and not await self.app.send_file(uid, g["file"], protect=False):
                    await self.app.alert(f"⚠️ Не загружен файл <code>{html.escape(g['file'])}</code> — отправь его боту "
                                         f"с подписью <code>/file {html.escape(g['file'])}</code>", about_uid=uid)
            else:
                later.append((i, self._at(g["day"], g.get("time", "10:00"))))
        if later:
            await self.app.db.schedule(uid, "kbzhu_gifts", later, "gifts:kbzhu", {})
        await self.app.db.add_tag(uid, "kbzhu_gifts")

    async def schedule_norm_only(self, uid: int) -> None:
        nf = self.cfg.get("norm_only_follow")
        if nf:
            await self.app.db.schedule(uid, "kbzhu_norm_only", [(0, self._at(nf.get("day", 1), nf.get("time", "19:00")))],
                                       "chain:kbzhu_norm_only", {})

    # ------------------------------------------------------------------ для Маргариты
    async def stats_text(self, since: int = 0) -> str:
        db = self.app.db
        c = {t: await db.count_tag(t, since) for t in
             ("kbzhu_start", "kbzhu_consent", "kbzhu_norm", "kbzhu_menu_start", "kbzhu_menu", "kbzhu_buy_click",
              "kbzhu_preg", "kbzhu_minor", "kbzhu_low_bmi", "kbzhu_food_fear", "kbzhu_food_binge",
              "kbzhu_food_diets", "kbzhu_norm_only")}
        cur = await db.conn.execute(
            "SELECT COUNT(DISTINCT o.user_id), COALESCE(SUM(o.amount),0) FROM orders o "
            "JOIN tags t ON t.user_id=o.user_id AND t.tag='kbzhu_norm' "
            "WHERE o.status='paid' AND o.paid_at>=t.created_at AND o.paid_at>=?", (since,))
        buyers, revenue = await cur.fetchone()
        top = await db.kbzhu_unknown_top(10, since)
        lines = ["<b>Расчёт КБЖУ" + (" за 7 дней" if since else "") + "</b>",
                 f"Нажали «узнать норму»: {c['kbzhu_start']}",
                 f"Дали согласие: {c['kbzhu_consent']}",
                 f"Получили норму: {c['kbzhu_norm']}",
                 f"Начали анкету для меню: {c['kbzhu_menu_start']}",
                 f"Получили меню: {c['kbzhu_menu']}",
                 f"Нажимали «купить»: {c['kbzhu_buy_click']}",
                 f"Купили после расчёта: {buyers} чел., {_money(revenue)} ₽",
                 "",
                 f"Беременные / кормящие: {c['kbzhu_preg']} · младше 18: {c['kbzhu_minor']} · низкий вес: {c['kbzhu_low_bmi']}",
                 f"Срывы: {c['kbzhu_food_binge']} · круг диет: {c['kbzhu_food_diets']} · боюсь есть: {c['kbzhu_food_fear']}",
                 f"Выбрали «пока только норма»: {c['kbzhu_norm_only']}"]
        if top:
            lines += ["", "<b>Чего нет в базе продуктов</b> (вписывали сами):"]
            lines += [f"{html.escape(w)} — {n}" for w, n in top]
        return "\n".join(lines)

    async def export_csv(self) -> BufferedInputFile:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "username", "имя в TG", "имя", "возраст", "рост", "вес", "активность", "с едой", "цель",
                    "ккал", "белки", "жиры", "углеводы", "меню", "источник", "дата"])
        for r in await self.app.db.kbzhu_all():
            d = json.loads(r["data"] or "{}")
            n = d.get("norm") or {}
            w.writerow([r["user_id"], r["username"] or "", r["first_name"] or "", d.get("name", ""), d.get("age", ""),
                        d.get("height", ""), d.get("weight", ""), ACT_TITLE.get(d.get("activity"), ""),
                        FOOD_TITLE.get(d.get("food"), "").replace("⚠️ ", ""), GOAL_TITLE.get(d.get("goal"), ""),
                        n.get("kcal", ""), n.get("protein", ""), n.get("fat", ""), n.get("carbs", ""),
                        "да" if d.get("menu") else "нет", r["source"] or "",
                        dt.datetime.fromtimestamp(r["created_at"], MSK).strftime("%d.%m.%Y")])
        data = ("﻿" + buf.getvalue()).encode("utf-8")
        return BufferedInputFile(data, filename=f"kbzhu_{dt.datetime.now(MSK):%Y-%m-%d}.csv")

    async def tick(self) -> None:
        """Раз в неделю (по понедельникам) — сводка Маргарите."""
        if not self.enabled:
            return
        now = dt.datetime.now(MSK)
        last = int(await self.app.db.kv_get("kbzhu_weekly", "0") or 0)
        if now.weekday() != 0 or now.hour < 10 or time.time() - last < 6 * 86400:
            return
        await self.app.db.kv_set("kbzhu_weekly", str(int(time.time())))
        await self.app.alert("📊 Сводка за неделю\n\n" + await self.stats_text(int(time.time()) - 7 * 86400))

