#!/usr/bin/env python3
"""Bot que avisa promoções e cupons de placa de vídeo (fonte: Pelando).

Uso:
    python3 bot_gpu.py            # roda em loop, checando a cada X minutos
    python3 bot_gpu.py --once     # checa uma vez e sai
    python3 bot_gpu.py --test     # mostra o que encontraria agora, sem marcar como visto

Avisos: Telegram (se configurado em config.json) + notificação no desktop + terminal.
Só usa a biblioteca padrão do Python.
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
SEEN_FILE = BASE / "vistos.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

DEFAULT_CONFIG = {
    "buscas": ["placa de video", "rtx 5060", "rtx 5070", "rx 9060 xt", "rx 9070", "cupom placa de video"],
    "modelos_de_interesse": ["rtx 5060", "rtx 5070", "rx 9060", "rx 9070", "rtx 4060", "rx 7600"],
    "preco_maximo": 4500,
    "intervalo_minutos": 15,
    "ignorar_palavras": ["notebook", "computador", "pc gamer", "placa mae", "placa-mae", "monitor", "cabo"],
    "telegram_token": "",
    "telegram_chat_id": "",
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower())


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))
        print(f"Criei {CONFIG_FILE.name} com valores padrão — edite à vontade.")
    cfg = {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    # No GitHub Actions o token e o chat_id vêm dos "secrets", não do arquivo.
    cfg["telegram_token"] = os.environ.get("TELEGRAM_TOKEN") or cfg["telegram_token"]
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or cfg["telegram_chat_id"]
    return cfg


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except (OSError, ValueError):
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:]))


def _decode(v):
    # O Pelando serializa os dados como [tipo, valor] (formato das "ilhas" do Astro).
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
        t, x = v
        return [_decode(i) for i in x] if t == 1 else _decode(x)
    if isinstance(v, dict):
        return {k: _decode(w) for k, w in v.items()}
    return v


def buscar_pelando(termo):
    slug = urllib.parse.quote(re.sub(r"\s+", "-", termo.strip().lower()))
    req = urllib.request.Request(f"https://www.pelando.com.br/busca/{slug}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", "replace")
    for raw in re.findall(r'props="([^"]*initialData[^"]*)"', page):
        data = _decode(json.loads(html.unescape(raw)))
        try:
            return data["initialData"]["deals"]["deals"] or []
        except (KeyError, TypeError):
            continue
    return []


def interessa(deal, cfg):
    if deal.get("status") != "active" or deal.get("kind") == "discussion":
        return False
    titulo = norm(deal.get("title"))
    if any(norm(p) in titulo for p in cfg["ignorar_palavras"]):
        return False
    eh_cupom = deal.get("kind") == "coupon" or "cupom" in titulo
    eh_gpu = "placa de video" in titulo or re.search(r"\b(rtx|gtx|rx|arc)\s?[a-z]?\d{3,4}", titulo)
    if not (eh_gpu or (eh_cupom and any(w in titulo for w in ("placa", "gpu", "hardware", "rtx", "rx")))):
        return False
    modelos = [norm(m) for m in cfg["modelos_de_interesse"]]
    if modelos and not eh_cupom and not any(m in titulo for m in modelos):
        return False
    preco = deal.get("price")
    if preco and cfg.get("preco_maximo") and preco > cfg["preco_maximo"]:
        return False
    return True


def formatar(d):
    loja = (d.get("store") or {}).get("name", "?")
    preco = f"R$ {d['price']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if d.get("price") else "—"
    extra = []
    if d.get("discountPercentage"):
        extra.append(f"{d['discountPercentage']}% off")
    if d.get("kind") == "coupon" or "cupom" in norm(d.get("title")):
        extra.append("🎟️ CUPOM")
    link = f"https://www.pelando.com.br/d/{d['slug']}"
    return (f"🔥 {d['title']}\n💰 {preco} — {loja}"
            + (f" ({', '.join(extra)})" if extra else "")
            + f"\n🌡️ {d.get('temperature', 0)}° no Pelando\n🔗 {link}")


def avisar(msg, cfg):
    print(msg, "\n" + "-" * 40)
    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        body = urllib.parse.urlencode({"chat_id": cfg["telegram_chat_id"], "text": msg}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage", body, timeout=20)
        except Exception as e:
            print("Falha ao enviar no Telegram:", e)
    try:
        subprocess.run(["notify-send", "Promoção de placa de vídeo", msg.splitlines()[0]], timeout=5)
    except Exception:
        pass


def checar(cfg, seen, marcar=True):
    novos = 0
    for termo in cfg["buscas"]:
        try:
            deals = buscar_pelando(termo)
        except Exception as e:
            print(f"Erro buscando '{termo}': {e}")
            continue
        for d in deals:
            if d["id"] in seen or not interessa(d, cfg):
                continue
            if marcar:
                seen.add(d["id"])
            avisar(formatar(d), cfg)
            novos += 1
        time.sleep(2)  # educado com o site
    return novos


def main():
    cfg = load_config()
    if "--test" in sys.argv:
        n = checar(cfg, set(), marcar=False)
        print(f"{n} oferta(s) ativa(s) que batem com seus filtros.")
        return
    seen = load_seen()
    while True:
        n = checar(cfg, seen)
        save_seen(seen)
        print(time.strftime("[%H:%M]"), f"{n} novidade(s).")
        if "--once" in sys.argv:
            return
        time.sleep(cfg["intervalo_minutos"] * 60)


if __name__ == "__main__":
    main()
