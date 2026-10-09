"""Робокасса: форма оплаты (POST) и проверка уведомления ResultURL.

Документация: https://docs.robokassa.ru/
- Подпись платежа:  MerchantLogin:OutSum:InvId[:Receipt]:Пароль#1
  (Receipt в подписи — JSON, закодированный urlencode один раз)
- Подпись ResultURL: OutSum:InvId:Пароль#2[:Shp_x=y ...] (Shp_ по алфавиту)
- Ответ на ResultURL: OK<InvId>
"""
from __future__ import annotations

import hashlib
import html
import json
from urllib.parse import quote

PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
_ALGOS = {"md5", "sha1", "sha256", "sha384", "sha512"}


def money(amount: float | int) -> str:
    """Сумма строкой: 990 -> '990.00'. Одна и та же строка идёт в подпись и в форму."""
    return f"{float(amount):.2f}"


def _hash(text: str, algo: str) -> str:
    algo = algo if algo in _ALGOS else "md5"
    return hashlib.new(algo, text.encode("utf-8")).hexdigest()


def build_receipt(name: str, amount: float, payment_object: str = "service", sno: str = "") -> str:
    """Чек для 54-ФЗ, уже закодированный urlencode (как он идёт в подпись и форму)."""
    receipt: dict = {
        "items": [
            {
                "name": name[:128],
                "quantity": 1,
                "sum": round(float(amount), 2),
                "payment_method": "full_payment",
                "payment_object": payment_object or "service",
                "tax": "none",
            }
        ]
    }
    if sno:
        receipt["sno"] = sno
    return quote(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), safe="")


def payment_fields(
    *,
    login: str,
    pass1: str,
    out_sum: str,
    inv_id: int,
    description: str,
    receipt: str | None = None,
    test: bool = False,
    algo: str = "md5",
) -> dict[str, str]:
    parts = [login, out_sum, str(inv_id)]
    if receipt:
        parts.append(receipt)
    parts.append(pass1)
    fields = {
        "MerchantLogin": login,
        "OutSum": out_sum,
        "InvId": str(inv_id),
        "Description": description[:100],
        "SignatureValue": _hash(":".join(parts), algo),
        "Culture": "ru",
        "Encoding": "utf-8",
    }
    if receipt:
        fields["Receipt"] = receipt
    if test:
        fields["IsTest"] = "1"
    return fields


def autosubmit_page(fields: dict[str, str], title: str) -> str:
    inputs = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in fields.items()
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Оплата — {html.escape(title)}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f1ea;color:#3b2a20;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px}}
.c{{max-width:420px;text-align:center}}button{{background:#b5643f;color:#fff;border:0;border-radius:12px;
padding:14px 22px;font-size:16px;cursor:pointer}}</style></head>
<body><div class="c"><p>Переходим на страницу оплаты Робокассы…</p>
<form id="f" method="POST" action="{PAY_URL}">{inputs}
<button type="submit">Перейти к оплате</button></form></div>
<script>document.getElementById('f').submit();</script></body></html>"""


def check_result(params: dict[str, str], pass2: str, algo: str = "md5") -> bool:
    """Проверяет подпись уведомления ResultURL. params — все параметры запроса."""
    out_sum = params.get("OutSum", "")
    inv_id = params.get("InvId", "")
    sign = params.get("SignatureValue", "")
    if not (out_sum and inv_id and sign):
        return False
    shp = sorted((k, v) for k, v in params.items() if k.startswith("Shp_"))
    base = ":".join([out_sum, inv_id, pass2] + [f"{k}={v}" for k, v in shp])
    return _hash(base, algo).lower() == sign.lower()
