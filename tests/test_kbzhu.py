
MacBook Pro — Mac, Connected
Скорее всего, дело в том, что ты проходила бота уже не первый раз. Подарок был привязан к первому меню: если у тебя уже было меню (например, после первого обновления), при пересборке книга не отправляется. Проверю код и сделаю так, чтобы книга приходила каждой, кто её ещё не получил, — и тебе тоже.

поняла

Причину нашла. Книга приходила только с самым первым меню. Ты, похоже, уже собирала меню раньше, например когда проверяла бот после первого обновления. Поэтому при пересборке бот решил, что книгу ты уже получила.

Я исправила это: теперь «ПП-завтраки» приходят с каждым новым меню. Тесты проходят. Сообщения на 1, 2, 3 и последующие дни по-прежнему запускаются только один раз, чтобы девушку не засыпало повторами.

Нужно поменять одну строчку.

Шаг 1. Поправь строку на GitHub
Открой github.com/margo-sc/margo-bot/edit/main/bot/kbzhu/flow.py.
Нажми Cmd + F и найди if first_menu:. Таких строк две, нужна первая — сразу под ней идёт await self.send_gifts(uid) (около строки 660).
Замени в этой строке только if first_menu: на if True:. Пробелы в начале строки не трогай.
Вторую строку if first_menu:, под которой await self.schedule_follow(uid), оставь как есть.
Нажми «Commit changes…» и ещё раз «Commit changes».
Напиши «проверь».
Шаг 2. После моей проверки обнови сервер
ssh root@138.249.8.89 → пароль.
cd /opt/margo-bot && git pull && systemctl restart margo-bot
Шаг 3. Получи книгу
Собери меню ещё раз: «🔸 Узнать свою норму КБЖУ» → «🔄 Пересчитать норму» → анкета для меню. После 7 дней меню придёт «ПП-завтраки».

Я хочу, чтобы когда ты выдаешь КБЖУ, ой, когда ты выдаешь на 7 дней питания скажи мне, ну, составленное питание, я хочу, чтобы там еще был способ приготовления. Добавь, пожалуйста, какие-то специи, соль, я не знаю, такой вот способ приготовления обязательно нужен. Хорошо? И не помню, граммовка есть, не есть, там сейчас посмотрю. Да, граммовка есть, способ приготовления добавь, пожалуйста, чтобы они понимали, как это готовить.

Переписываю шаблоны для ужинов и перекусов
Хорошо. Сделаю так, чтобы у каждого блюда в меню был способ приготовления: как готовить, сколько минут и какие специи (соль, перец, паприка, чеснок, травы, лимон). Сейчас «как готовить» есть только у части блюд — у твоих рецептов и то не всегда. Перепишу тексты так, чтобы они подходили, даже если бот заменил продукт: например, «птицу обжарь» вместо «курицу обжарь».

Shared 4 files, ran 4 commands
Сделала: теперь под каждым блюдом в меню есть «👩‍🍳 Как готовить» — на все 79 блюд, а не только на твои рецепты. В тексте сказано, как готовить, сколько минут или при какой температуре и какие специи класть: соль, перец, паприку, чеснок, травы, лимонный сок, корицу или ваниль для сладкого. Тексты написаны так, чтобы подходить и к заменам: «птицу обжарь», «крупу отвари», «ягоды или фрукты». Тесты проходят: у каждого блюда во всех вариантах меню есть способ приготовления, и каждый день помещается в одно сообщение Telegram.

Вот так теперь выглядит блюдо:

🍽 Обед · Стир-фрай: крупа, белок и овощи на сковороде
• Рис — 95 г (сухой вес)
• Куриное филе — 100 г
• Брокколи — 160 г
• Оливковое масло — 10 г (2 ч. л.)
👩‍🍳 Как готовить: Отвари крупу. Белок нарежь полосками и обжарь на сильном огне 5–6 минут, добавь овощи, чеснок и соевый соус (или соль и специи) и жарь ещё 4–5 минут. Подавай с крупой.

Правку с книгой-подарком ты в flow.py ещё не вносила, поэтому я включила её в этот же файл. Отдельно строчку менять не нужно.

Шаг 1. Замени 4 файла на GitHub
Способ тот же: открой файл в TextEdit → Cmd + A, Cmd + C → открой ссылку → в окне с кодом Cmd + A, Cmd + V → «Commit changes…» → «Commit changes».

kbzhu_food.yaml → github.com/margo-sc/margo-bot/edit/main/kbzhu_food.yaml
menu.py → github.com/margo-sc/margo-bot/edit/main/bot/kbzhu/menu.py
flow.py → github.com/margo-sc/margo-bot/edit/main/bot/kbzhu/flow.py
test_kbzhu.py → github.com/margo-sc/margo-bot/edit/main/tests/test_kbzhu.py
Перед вставкой проверь, что имя файла в TextEdit совпадает с концом ссылки. Напиши «проверь».

Шаг 2. Сервер (после моей проверки)
ssh root@138.249.8.89 → пароль.
cd /opt/margo-bot && git pull && systemctl restart margo-bot
Шаг 3. Проверь в боте
Пересобери меню: «🔸 Узнать свою норму КБЖУ» → «🔄 Пересчитать норму» → анкета для меню. Под каждым блюдом должно появиться «Как готовить», а после меню — книга «ПП-завтраки».

Kbzhu food
YAML
Menu
PY
Flow
PY
Test kbzhu
PY




Test kbzhu · PY
"""Тесты расчёта КБЖУ, генератора меню и сценария в боте."""
from __future__ import annotations
 
import random
import time
from pathlib import Path
 
import pytest
 
from bot.content import Content
from bot.kbzhu.calc import calculate
from bot.kbzhu.menu import FoodBase, MenuBuilder, Prefs, fmt_day
from test_bot import ADMIN, CLIENT, callbacks, env, upd_callback, upd_message  # noqa: F401
 
ROOT = Path(__file__).resolve().parent.parent
FB = FoodBase(ROOT / "kbzhu_food.yaml")
 
 
# ------------------------------------------------------------------ расчёт
def test_example_from_scenario():
    n = calculate(30, 165, 70, "mid", "lose", "calm")
    assert (n.bmr, n.tdee, n.kcal) == (1420, 2201, 1870)
    assert (n.protein, n.fat, n.carbs) == (126, 63, 200)
    assert n.treat_kcal == 187 and n.deficit_pct == -15
 
 
def test_soft_deficit_for_binge_diets_fear():
    for food in ("binge", "diets", "fear"):
        n = calculate(30, 165, 70, "mid", "lose", food)
        assert n.deficit_pct == -10 and n.kcal == 1980
 
 
def test_obese_deficit_and_protein():
    n = calculate(40, 165, 95, "low", "lose", "calm")
    assert n.bmi >= 30 and n.deficit_pct == -20
    assert n.protein == round(95 * 1.3)
 
 
def test_floor_never_below_bmr_or_1300():
    n = calculate(60, 150, 45, "min", "lose", "calm")
    assert n.floor_applied and n.kcal >= max(n.bmr, 1300)
 
 
def test_fat_minimum_50():
    n = calculate(25, 155, 48, "mid", "keep", "calm")
    assert n.fat == 50
 
 
def test_gain_and_keep():
    assert calculate(30, 165, 60, "mid", "gain").deficit_pct == 10
    assert calculate(30, 165, 60, "mid", "keep").kcal == round(calculate(30, 165, 60, "mid", "keep").tdee / 10) * 10
 
 
# ------------------------------------------------------------------ база и меню
def test_food_base_valid():
    assert FB.validate() == []
    assert Content(ROOT / "content.yaml").reload() == []
 
 
def test_lookup_synonyms_and_typos():
    assert FB.lookup("Картошка") == "potato"
    assert FB.lookup("гричка") == "buckwheat"
    assert FB.lookup("сёмга") == "red_fish"
    assert FB.lookup("пломбир") == "ice_cream"
    assert FB.lookup("трубочка со сгущёнкой") is None
 
 
def _prefs(**kw):
    base = dict(products=["chicken", "turkey", "beef", "eggs", "cottage", "white_fish", "buckwheat", "rice", "oats",
                          "bread", "potato", "cucumber", "tomato", "broccoli", "banana", "apple", "berries", "milk",
                          "greek", "kefir", "cheese", "nuts"],
                treats=["chocolate"], exclusions=[], meals="3+1", cooking="daily")
    base.update(kw)
    return Prefs(**base)
 
 
def test_menu_hits_norm():
    n = calculate(30, 165, 70, "mid", "lose", "calm").as_dict()
    days, relaxed = MenuBuilder(FB).build(_prefs(), n, seed=1)
    assert len(days) == 7 and not relaxed
    for d in days:
        assert abs(d.totals["kcal"] - n["kcal"]) / n["kcal"] < 0.05
        assert abs(d.totals["protein"] - n["protein"]) / n["protein"] < 0.2
        assert d.treat and d.treat.product == "chocolate"
 
 
def test_menu_respects_exclusions_and_selection():
    n = calculate(30, 165, 70, "mid", "keep").as_dict()
    days, _ = MenuBuilder(FB).build(_prefs(exclusions=["lactose", "no_meat"], excluded_products=["tomato"]), n, seed=3)
    banned = FB.excluded_products(["lactose", "no_meat"]) | {"tomato"}
    allowed = set(_prefs().products) | set(FB.pantry)
    for d in days:
        for m in d.meals:
            for it in m.items:
                assert it.product not in banned
                assert it.product in allowed
 
 
def test_no_dish_twice_in_a_row_and_variety():
    n = calculate(30, 165, 70, "mid", "lose").as_dict()
    days, _ = MenuBuilder(FB).build(_prefs(), n, seed=5)
    lunches = [next(m.dish_id for m in d.meals if m.key == "lunch") for d in days]
    assert all(a != b for a, b in zip(lunches, lunches[1:]))
    proteins = {it.product for d in days for m in d.meals for it in m.items}
    assert {"chicken", "turkey"} <= proteins
 
 
def test_batch_cooking_repeats_lunch_and_dinner():
    n = calculate(30, 165, 70, "mid", "lose").as_dict()
    days, _ = MenuBuilder(FB).build(_prefs(cooking="batch"), n, seed=2)
    for a, b in ((0, 1), (2, 3), (4, 5)):
        la = [m for m in days[a].meals if m.key == "lunch"][0]
        lb = [m for m in days[b].meals if m.key == "lunch"][0]
        assert la.dish_id == lb.dish_id
        assert [(i.product, i.grams) for i in la.items] == [(i.product, i.grams) for i in lb.items]
 
 
def test_quick_only_quick_dishes():
    n = calculate(30, 165, 70, "mid", "lose").as_dict()
    days, _ = MenuBuilder(FB).build(_prefs(cooking="quick"), n, seed=2)
    quick = {d["id"] for d in FB.dishes if d.get("quick")}
    ids = [m.dish_id for d in days for m in d.meals]
    assert sum(i in quick for i in ids) / len(ids) > 0.8
 
 
def test_random_profiles_accuracy():
    """Типичные клиентки: 22–45 лет, 55–90 кг, в основном снижение веса."""
    rng = random.Random(11)
    mb = MenuBuilder(FB)
    errs = {"kcal": [], "protein": []}
    for t in range(30):
        n = calculate(rng.randint(22, 45), rng.randint(155, 175), rng.randint(55, 90),
                      rng.choice(["min", "low", "mid", "high"]), rng.choice(["lose", "lose", "keep"])).as_dict()
        prods = [k for g in FB.groups for k in rng.sample(g["items"], rng.randint(3, min(6, len(g["items"]))))]
        days, _ = mb.build(Prefs(products=prods, treats=["chocolate"], exclusions=[],
                                 meals=rng.choice(["3", "3+1", "4-5"])), n, days=3, seed=t)
        for d in days:
            for k in errs:
                errs[k].append(abs(d.totals[k] - n[k]) / n[k])
    assert max(errs["kcal"]) < 0.10 and sum(errs["kcal"]) / len(errs["kcal"]) < 0.02
    assert sum(errs["protein"]) / len(errs["protein"]) < 0.10
 
 
def test_no_pp_desserts_anywhere():
    assert "pp_desserts" not in FB.treat_buttons and not FB.treat_sets
    assert not [k for k in FB.products if k.startswith("pp_")]
 
 
def test_every_meal_has_cooking_method():
    n = calculate(30, 165, 70, "mid", "lose").as_dict()
    for prefs in (_prefs(), _prefs(cooking="quick"), _prefs(meals="4-5", exclusions=["lactose"])):
        days, _ = MenuBuilder(FB).build(prefs, n, seed=2)
        for i, d in enumerate(days):
            assert all(m.how for m in d.meals)
            text = fmt_day(FB, i + 1, d)
            assert text.count("Как готовить") == len(d.meals)
            assert len(text) < 4000          # одно сообщение Telegram
 
 
def test_alt_title_when_product_replaced():
    n = calculate(30, 165, 70, "mid", "lose").as_dict()
    mb = MenuBuilder(FB)
    dish = next(d for d in FB.dishes if d["id"] == "l_turkey_meatballs")
    items = mb._resolve(dish, {"chicken", "rice"})
    assert mb.title_for(dish, items).startswith("Тефтели из птицы")
    items = mb._resolve(dish, {"turkey", "rice"})
    assert mb.title_for(dish, items) == dish["title"]
 
 
def test_fmt_day_units():
    n = calculate(30, 165, 70, "mid", "lose").as_dict()
    days, _ = MenuBuilder(FB).build(_prefs(), n, seed=1)
    text = "\n".join(fmt_day(FB, i + 1, d) for i, d in enumerate(days))
    assert "День 7" in text and "сухой вес" in text and "шт." in text and "🍫 Любимое" in text
 
 
# ------------------------------------------------------------------ сценарий в боте
async def go(dp, bot, *updates):
    for u in updates:
        await dp.feed_update(bot, u)
 
 
def texts(s, uid=CLIENT):
    return s.texts_to(uid)
 
 
async def pass_norm(dp, bot, food="calm", goal="lose", age="30", weight="70"):
    await go(dp, bot,
             upd_message(CLIENT, "/start insta_kbzhu"),
             upd_callback(CLIENT, "k:consent"),
             upd_message(CLIENT, "Аня"),
             upd_message(CLIENT, age),
             upd_callback(CLIENT, "k:preg:no"),
             upd_message(CLIENT, "165"),
             upd_message(CLIENT, weight),
             upd_callback(CLIENT, "k:act:mid"),
             upd_callback(CLIENT, f"k:food:{food}"),
             upd_callback(CLIENT, f"k:goal:{goal}"))
 
 
async def test_start_screen_has_kbzhu_first(env):
    app, s, dp, bot = env
    await go(dp, bot, upd_message(CLIENT, "/start"))
    first = s.last_markup(CLIENT).inline_keyboard[0][0]
    assert "КБЖУ" in first.text and first.callback_data == "a:kbzhu:start"
 
 
async def test_full_flow_norm_menu_discount(env):
    app, s, dp, bot = env
    await pass_norm(dp, bot)
    out = texts(s)
    assert any("1 870 ккал" in t for t in out)
    assert any("тестовое меню на 7 дней" in t for t in out)
    prof = await app.db.kbzhu_get(CLIENT)
    assert prof["norm"]["kcal"] == 1870 and prof["name"] == "Аня"
    assert await app.db.has_tag(CLIENT, "kbzhu_norm") and await app.db.has_tag(CLIENT, "consent_pd")
    assert any("Расчёт КБЖУ" in t for t in texts(s, ADMIN))
    u = await app.db.get_user(CLIENT)
    assert u["source"] == "insta"
 
    await go(dp, bot, upd_callback(CLIENT, "k:menu"))
    for k in ("chicken", "eggs", "cottage"):
        await go(dp, bot, upd_callback(CLIENT, f"k:t:{k}"))
    await go(dp, bot, upd_callback(CLIENT, "k:next"))          # гарниры
    for k in ("buckwheat", "rice", "oats", "bread"):
        await go(dp, bot, upd_callback(CLIENT, f"k:t:{k}"))
    await go(dp, bot, upd_callback(CLIENT, "k:own"), upd_message(CLIENT, "картошка, трубочка"))
    assert any("Добавила: Картофель" in t and "трубочка" in t for t in texts(s))
    for _ in range(4):                                          # овощи, фрукты, молочное, жиры
        await go(dp, bot, upd_callback(CLIENT, "k:next"))
    await go(dp, bot, upd_callback(CLIENT, "k:next"))          # → любимое
    await go(dp, bot, upd_callback(CLIENT, "k:tt:chocolate"), upd_callback(CLIENT, "k:next"))
    await go(dp, bot, upd_callback(CLIENT, "k:x:lactose"), upd_callback(CLIENT, "k:next"))
    await go(dp, bot, upd_callback(CLIENT, "k:meals:3+1"), upd_callback(CLIENT, "k:cook:daily"))
 
    out = texts(s)
    days = [t for t in out if t.startswith("📅")]
    assert len(days) == 7
    assert not any("Творог" in t or "Молоко" in t for t in days)   # без лактозы
    assert any("Шоколад" in t for t in days)
    assert any("3 сборника" in t and "499" in t for t in out)          # сразу после меню — сборники
    assert "a:product:recepty" in callbacks(s.last_markup(CLIENT))
    assert not any("скидка 15%" in t for t in out)                     # личная работа — только на 3-й день
    prof = await app.db.kbzhu_get(CLIENT)
    assert prof["menu"] and prof["menu_at"]
    assert await app.db.has_pending(CLIENT, "chain:kbzhu_follow")
    top = await app.db.kbzhu_unknown_top()
    assert ("трубочка", 1) in top
 
    # до предложения на 3-й день скидки нет
    await go(dp, bot, upd_callback(CLIENT, "a:kbzhu:buy:konsult"))
    assert "a:pay:konsult" in callbacks(s.last_markup(CLIENT))
    # 3-й день: предложение личной работы запускает скидку
    await app.kbzhu.start_discount(CLIENT)
    prof = await app.db.kbzhu_get(CLIENT)
    assert prof["offer_at"]
    # скидочная консультация
    await go(dp, bot, upd_callback(CLIENT, "k:buy:konsult"))
    assert "2 125" in texts(s)[-1] and "a:pay:konsult_kbzhu" in callbacks(s.last_markup(CLIENT))
    # ведение со скидкой: кнопка в личку и уведомление со счётом
    await go(dp, bot, upd_callback(CLIENT, "k:buy:vedenie"))
    assert "Скидка 15%" in texts(s)[-1]
    assert any("/schet 222 vedenie 8662" in t for t in texts(s, ADMIN))
 
    # после 72 часов — обычная цена
    prof = await app.db.kbzhu_get(CLIENT)
    prof["offer_at"] = int(time.time()) - 73 * 3600
    await app.db.kbzhu_save(CLIENT, prof)
    await go(dp, bot, upd_callback(CLIENT, "a:kbzhu:buy:konsult"))
    assert "a:pay:konsult" in callbacks(s.last_markup(CLIENT))
 
 
async def test_follow_chain_and_feedback(env):
    app, s, dp, bot = env
    await app.kbzhu.schedule_follow(CLIENT)
    await app.db.touch_user(CLIENT, "anya", "Аня", None)
    await app.db.conn.execute("UPDATE jobs SET run_at=0 WHERE user_id=?", (CLIENT,))
    for job in await app.db.due_jobs():
        await app.run_job(job)
    out = texts(s)
    assert any("как прошёл первый день" in t for t in out)
    assert any("Неделя прошла" in t for t in out)
    day3 = next(t for t in out if "третий день" in t)
    assert "8 662" in day3 and "2 125" in day3 and "{" not in day3
    prof_ok = await app.db.kbzhu_get(CLIENT)
    assert prof_ok is None or prof_ok.get("offer_at") is None or prof_ok["offer_at"] > 0
    await go(dp, bot, upd_callback(CLIENT, "a:kbzhu:fb:hungry"))
    assert "голод" in texts(s)[-1]
 
 
async def test_follow_stops_after_purchase_and_blocks_warmup(env):
    app, s, dp, bot = env
    await app.kbzhu.schedule_follow(CLIENT)
    await go(dp, bot, upd_callback(CLIENT, "a:lead:mify"))
    assert not await app.db.has_pending(CLIENT, "chain:warmup")
    order = await app.db.create_order(CLIENT, "kniga", 990)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    assert not await app.db.has_pending(CLIENT, "chain:kbzhu_follow")
 
 
async def test_minor_branch(env):
    app, s, dp, bot = env
    await go(dp, bot, upd_message(CLIENT, "/start insta_kbzhu"), upd_callback(CLIENT, "k:consent"),
             upd_message(CLIENT, "Аня"), upd_message(CLIENT, "16"))
    assert "организм ещё растёт" in texts(s)[-1]
    assert await app.db.kbzhu_get(CLIENT) is None
    assert CLIENT not in app.kbzhu.state
 
 
async def test_pregnant_branch(env):
    app, s, dp, bot = env
    await go(dp, bot, upd_message(CLIENT, "/start insta_kbzhu"), upd_callback(CLIENT, "k:consent"),
             upd_message(CLIENT, "Аня"), upd_message(CLIENT, "29"), upd_callback(CLIENT, "k:preg:yes"))
    assert "дефицит калорий давать нельзя" in texts(s)[-1]
    assert "a:product:konsult" in callbacks(s.last_markup(CLIENT))
    assert await app.db.kbzhu_get(CLIENT) is None
    assert any("беременна" in t for t in texts(s, ADMIN))
 
 
async def test_fear_gets_soft_deficit_and_note(env):
    app, s, dp, bot = env
    await pass_norm(dp, bot, food="fear")
    prof = await app.db.kbzhu_get(CLIENT)
    assert prof["norm"]["deficit_pct"] == -10
    assert any("психолог пищевого поведения" in t for t in texts(s))
    assert any("⚠️" in t for t in texts(s, ADMIN))
 
 
async def test_binge_recommends_book(env):
    app, s, dp, bot = env
    await pass_norm(dp, bot, food="binge")
    assert any("Я снова сорвалась" in t for t in texts(s))
 
 
async def test_low_bmi_lose_offers_maintenance(env):
    app, s, dp, bot = env
    await pass_norm(dp, bot, weight="47")
    assert "ниже нормы" in texts(s)[-1]
    assert await app.db.kbzhu_get(CLIENT) is None
    await go(dp, bot, upd_callback(CLIENT, "k:goal:keep"))
    prof = await app.db.kbzhu_get(CLIENT)
    assert prof["goal"] == "keep" and prof["norm"]["deficit_pct"] == 0
 
 
async def test_validation_messages(env):
    app, s, dp, bot = env
    await go(dp, bot, upd_message(CLIENT, "/start insta_kbzhu"), upd_callback(CLIENT, "k:consent"),
             upd_message(CLIENT, "Аня"), upd_message(CLIENT, "тридцать"))
    assert "просто число" in texts(s)[-1]
    await go(dp, bot, upd_message(CLIENT, "30"), upd_callback(CLIENT, "k:preg:no"), upd_message(CLIENT, "1.65"))
    assert app.kbzhu.state[CLIENT].data["height"] == 165
    await go(dp, bot, upd_message(CLIENT, "500"))
    assert "Проверь" in texts(s)[-1]
    await go(dp, bot, upd_message(CLIENT, "62,5"))
    assert app.kbzhu.state[CLIENT].data["weight"] == 62.5
    await go(dp, bot, upd_message(CLIENT, "привет"))          # на шаге с кнопками
    assert "кнопках" in texts(s)[-1]
 
 
async def test_repeat_entry_shows_my_norm_and_delete(env):
    app, s, dp, bot = env
    await pass_norm(dp, bot)
    await go(dp, bot, upd_callback(CLIENT, "a:kbzhu:start"))
    assert "уже посчитана" in texts(s)[-1]
    assert "k:delete" in callbacks(s.last_markup(CLIENT))
    await go(dp, bot, upd_callback(CLIENT, "k:delete"))
    assert await app.db.kbzhu_get(CLIENT) is None
    assert not await app.db.has_tag(CLIENT, "kbzhu_norm")
 
 
async def test_stale_button_after_restart(env):
    app, s, dp, bot = env
    await go(dp, bot, upd_callback(CLIENT, "k:t:chicken"))
    assert any("сначала" in t for t in texts(s))
 
 
async def test_admin_stats_and_export(env):
    app, s, dp, bot = env
    await pass_norm(dp, bot)
    await go(dp, bot, upd_message(ADMIN, "/kbzhu"))
    assert "Получили норму: 1" in texts(s, ADMIN)[-1]
    await go(dp, bot, upd_message(ADMIN, "/kbzhu_excel"))
    from aiogram.methods import SendDocument
    doc = [c for c in s.calls if isinstance(c, SendDocument)][-1]
    assert doc.document.filename.endswith(".csv")
    assert "Аня" in doc.document.data.decode("utf-8")
 
 
async def test_gift_breakfast_book_with_menu(env, tmp_path):
    app, s, dp, bot = env
    from aiogram.methods import SendDocument
    (tmp_path / "files" / "pp-zavtraki.pdf").write_bytes(b"%PDF-1.4 test")
    await app.db.touch_user(CLIENT, "anya", "Аня", None)
    await app.kbzhu.send_gifts(CLIENT)
    docs = [c for c in s.calls if isinstance(c, SendDocument) and c.chat_id == CLIENT]
    assert len(docs) == 1 and any("ПП-завтраки" in t for t in texts(s))
 
 
async def test_recipe_collections_for_sale(env, tmp_path):
    app, s, dp, bot = env
    from aiogram.methods import SendDocument
    # в каталоге первой строкой — 4 сборника за 499
    await go(dp, bot, upd_callback(CLIENT, "a:screen:catalog"))
    first = s.last_markup(CLIENT).inline_keyboard[0][0]
    assert "499" in first.text and first.callback_data == "a:product:recepty"
    await go(dp, bot, upd_message(CLIENT, "/start insta_recepty"))
    assert "31 рецепт" in texts(s)[-1] and "a:pay:recepty" in callbacks(s.last_markup(CLIENT))
    # оплата → 4 файла
    for f in ("pp-obedy.pdf", "pp-uzhiny.pdf", "pp-perekusy.pdf"):
        (tmp_path / "files" / f).write_bytes(b"%PDF-1.4 test")
    order = await app.db.create_order(CLIENT, "recepty", 499)
    await app.db.mark_paid(order["id"])
    await app.on_paid(await app.db.order_by_id(order["id"]))
    docs = [c for c in s.calls if isinstance(c, SendDocument) and c.chat_id == CLIENT]
    assert len(docs) == 3 and all(d.protect_content for d in docs)
    # по отдельности
    await go(dp, bot, upd_callback(CLIENT, "a:screen:recepty_singles"))
    assert "a:product:recepty_perekusy" in callbacks(s.last_markup(CLIENT))
    assert "a:product:recepty_deserty" not in callbacks(s.last_markup(CLIENT))
 
 
async def test_day2_offers_collections(env):
    app, s, dp, bot = env
    await app.db.touch_user(CLIENT, "anya", "Аня", None)
    await app.kbzhu.schedule_follow(CLIENT)
    await app.db.conn.execute("UPDATE jobs SET run_at=0 WHERE user_id=?", (CLIENT,))
    for job in await app.db.due_jobs():
        await app.run_job(job)
    assert any("3 сборника" in t and "499" in t for t in texts(s))
 
 
async def test_gift_comes_with_every_new_menu(env, tmp_path):
    app, s, dp, bot = env
    await pass_norm(dp, bot)
    prof = await app.db.kbzhu_get(CLIENT)
    prof["menu_at"] = 1   # меню уже собирали раньше
    await app.db.kbzhu_save(CLIENT, prof)
    st = __import__("bot.kbzhu.flow", fromlist=["KState"]).KState(step="cooking", selected=["chicken", "buckwheat"])
    await app.kbzhu.make_menu(CLIENT, st)
    assert any("ПП-завтраки" in t for t in texts(s))
 
Claude finished the response
