#!/usr/bin/env python3
"""Build the HAUNT dApp as a single self-contained HTML file.

Embeds audited chain data (SOL amounts and winners are chain state; replay dates estimated from round cadence):
  - leaders.json : top 150 wallets by deploy volume (+lifetime stats top 25)
  - rounds.json  : 200 recent rounds with full 25-tile data
  - replays.json : 8 rounds with per-deploy timelines
Plus per-wallet tile fingerprints computed from deploy events.
"""
import json, glob, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BUND = os.path.join(HERE, "bundles")

leaders = json.load(open(os.path.join(BUND, "leaders.json")))
rounds = json.load(open(os.path.join(BUND, "rounds.json")))
replays = json.load(open(os.path.join(BUND, "replays.json")))
meta = json.load(open(os.path.join(BUND, "meta.json")))

# per-wallet fingerprints from deploy events
events = []
for f in sorted(glob.glob(os.path.join(HERE, "data", "deploys_*.jsonl"))):
    for line in open(f):
        line = line.strip()
        if line:
            events.append(json.loads(line))

leader_wallets = {L["w"] for L in leaders["leaders"]}
fp = defaultdict(lambda: {"tiles": [0.0]*25, "man": 0, "auto": 0})
for e in events:
    if e["wallet"] not in leader_wallets:
        continue
    w = fp[e["wallet"]]
    per = e["amount_sol"] / max(1, len(e["squares"]))
    for q in e["squares"]:
        w["tiles"][q] += per
    # Zero-SOL / empty-square events are phantoms — exclude from the strategy split
    if not e["squares"] or not e["amount_sol"]:
        continue
    if e["strategy"] == "manual":
        w["man"] += 1
    else:
        w["auto"] += 1
# ---- anomaly exposure: deploy events x anomaly signals (build-time) ----
# Mirrors AnomalyModule.detect() EXACTLY (z-scores, thresholds, severity bars) --
# parity-verified 86/86 signals (V3-corpus build of Oct 7; the live V4 window
# shows 28) against the JS module. If detect() changes, this
# must change with it. Exposure records PRESENCE only, never causation.
import math as _math
def _okR(r):
    def _fin(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and _math.isfinite(v)
    return (_fin(r.get("r")) and _fin(r.get("d")) and _fin(r.get("m")) and _fin(r.get("w"))
        and 0 <= r["w"] < 25 and isinstance(r.get("t"), list) and len(r["t"]) == 25)
def _z(a):
    n = len(a)
    if not n: return (0.0, 1.0)
    m = sum(a) / n
    v = sum((x - m) ** 2 for x in a) / n
    return (m, _math.sqrt(v) or 1.0)
_W = [r for r in rounds["recent"] if _okR(r)]
_zs = _z([x["d"] for x in _W]); _ms = _z([x["m"] for x in _W])
_cs = _z([(max(x["t"]) / x["d"] if x["d"] > 0 else 0) for x in _W])
_tm, _ts = [], []
for _i in range(25):
    _a, _b = _z([x["t"][_i] for x in _W]); _tm.append(_a); _ts.append(_b)
def _sev(z):
    a = abs(z)
    return "extreme" if a >= 3 else "strong" if a >= 2.5 else "notable" if a >= 2 else None
_sigs = {}
for _r in _W:
    _lst = []
    _c = max(_r["t"]) / _r["d"] if _r["d"] > 0 else 0
    _zp = (_r["d"] - _zs[0]) / _zs[1]; _zm = (_r["m"] - _ms[0]) / _ms[1]; _zc = (_c - _cs[0]) / _cs[1]
    if _sev(_zp): _lst.append({"type": "pot", "sev": _sev(_zp), "z": _zp, "tile": None, "label": "Pot spike" if _zp > 0 else "Pot drought"})
    if _sev(_zm): _lst.append({"type": "crowd", "sev": _sev(_zm), "z": _zm, "tile": None, "label": "Crowd surge" if _zm > 0 else "Ghost round"})
    if _sev(_zc): _lst.append({"type": "whale", "sev": _sev(_zc), "z": _zc, "tile": None, "label": "Whale strike" if _zc > 0 else "Spread thin"})
    for _i, _v in enumerate(_r["t"]):
        _zt = (_v - _tm[_i]) / (_ts[_i] or 1)
        _st = "extreme" if _zt >= 4 else "strong" if _zt >= 3.5 else "notable" if _zt >= 3 else None
        if _st: _lst.append({"type": "tile", "sev": _st, "z": _zt, "tile": _i, "label": f"Tile {_i} flooded"})
    if _lst: _sigs[_r["r"]] = _lst
_event_rounds = {e["round"] for e in events}
_covered = sorted(r["r"] for r in _W if r["r"] in _event_rounds)
_covered_set = set(_covered)
_flood_tiles = {r: [sg["tile"] for sg in ss if sg["type"] == "tile"] for r, ss in _sigs.items()}
_wround = defaultdict(lambda: defaultdict(lambda: {"sol": 0.0, "hot": 0.0}))
for e in events:
    w = e["wallet"]; r = e["round"]
    if w not in leader_wallets or r not in _covered_set: continue
    cell = _wround[w][r]
    cell["sol"] += e["amount_sol"]
    _ft = _flood_tiles.get(r)
    if _ft and e["squares"]:
        _per = e["amount_sol"] / len(e["squares"])
        cell["hot"] += _per * sum(1 for q in e["squares"] if q in _ft)
_SEVW = {"extreme": 3, "strong": 2, "notable": 1}
expo = {}
for w, rmap in _wround.items():
    _p = len(rmap)
    if _p < 3: continue
    _anom = {r: c for r, c in rmap.items() if r in _sigs}
    _tops = []
    for r, c in _anom.items():
        _best = max(_sigs[r], key=lambda sg: abs(sg["z"]))
        _tops.append({"r": r, "sev": _best["sev"], "label": _best["label"],
                      "z": round(abs(_best["z"]), 2), "sol": round(c["sol"], 2), "hot": round(c["hot"], 2)})
    _tops.sort(key=lambda t: (-_SEVW[t["sev"]], -t["z"], -t["r"]))
    expo[w] = {"p": _p, "a": len(_anom), "rate": round(len(_anom) / _p, 4),
               "sol": round(sum(c["sol"] for c in _anom.values()), 2),
               "hot": round(sum(c["hot"] for c in _anom.values()), 2),
               "top": _tops[:5]}
expo_meta = {"covered": len(_covered), "from": _covered[0] if _covered else None,
             "to": _covered[-1] if _covered else None, "window": len(_W),
             "anom_rounds": len(_sigs)}

fingerprints = {w: {"t": [round(x, 6) for x in v["tiles"]],
                    "man": v["man"], "auto": v["auto"]}
                for w, v in fp.items()}

DATA = {"leaders": leaders, "rounds": rounds, "replays": replays,
        "fp": fingerprints, "expo": expo, "expoMeta": expo_meta,
        "meta": {"built_at": meta.get("built_at"),
                 "events_total": meta.get("events_total"),
                 "events_funded": meta.get("events_funded"),
                 "rounds": meta.get("rounds"),
                 "note": "All values from ORE V4 chain state, collected live by this project's own poller + scanner. Miner layout 752B audited; "
                         "DeployEvent 121B audited; winner=(r1^r2^r3^r4)%25 per program source."}}
data_js = json.dumps(DATA, separators=(",", ":"))

html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,interactive-widget=resizes-content">
<title>ORE HAUNT — ORE Miner Intelligence</title>
<style>
/* ============ ORE HAUNT design system ============ */
@keyframes pageIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.page-enter{animation:pageIn .18s var(--ease)}
:root{
  --bg:#06060e; --bg2:#0a0a15;
  --panel:#0e0e1d; --panel2:#141428; --panel3:#1b1b36;
  --line:#222244; --line2:#34345e;
  --txt:#e9e9f8; --dim:#9b9bc6; --faint:#63638c;
  --gold:#ffb000; --gold2:#ffd25c; --amber:#ffb000; --gold-soft:rgba(255,176,0,.10); --gold-line:rgba(255,176,0,.40);
  --green:#00ffa3; --green-soft:rgba(0,255,163,.08); --green-line:rgba(0,255,163,.32);
  --violet:#8b5cf6; --violet-soft:rgba(139,92,246,.10);
  --red:#ff5470;
  --r-s:8px; --r-m:14px; --r-l:20px; --r-pill:999px;
  --t-xs:10px; --t-s:12px; --t-m:14px; --t-l:17px; --t-xl:22px; --t-xxl:30px;
  --touch:44px; --ease:cubic-bezier(.22,.8,.28,1);
  --shadow:0 8px 28px rgba(0,0,0,.5);
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Roboto,"Segoe UI",sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;color:var(--txt);font-family:var(--font);font-size:var(--t-m);line-height:1.5;
  background:radial-gradient(1100px 520px at 50% -8%,#0e0e26 0%,var(--bg) 62%) fixed,var(--bg);
  padding-bottom:calc(158px + env(safe-area-inset-bottom));-webkit-font-smoothing:antialiased}
/* ---------- header ---------- */
.hd{position:sticky;top:0;z-index:20;background:rgba(6,6,14,.93);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--line);padding:calc(10px + env(safe-area-inset-top)) 16px 10px}
.brand{display:flex;align-items:center;gap:10px}
.infoBtn{margin-left:auto;flex:0 0 auto;width:44px;height:44px;border-radius:50%;border:1px solid var(--line2);background:rgba(255,255,255,.04);color:var(--dim);font-size:19px;font-weight:800;font-style:italic;font-family:Georgia,serif;cursor:pointer;display:flex;align-items:center;justify-content:center}
.infoBtn:active{transform:scale(.94)}
.brand .ghost{font-size:24px;filter:drop-shadow(0 0 10px var(--green))}
.wordmark{font-size:19px;font-weight:800;letter-spacing:3px;white-space:nowrap}
@media (max-width:380px){.hd{padding-left:10px;padding-right:10px}.brand{gap:6px}.wordmark{font-size:16px;letter-spacing:1.5px}.badge{font-size:8px;letter-spacing:1px;padding-left:7px;padding-right:7px}.infoBtn{width:40px;height:40px}}
.badge{display:inline-block;font-size:9px;letter-spacing:1.5px;white-space:nowrap;flex:0 0 auto;color:#06130d;background:var(--green);
  border-radius:6px;padding:3px 8px;font-weight:800}
.clockline{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-top:8px;flex-wrap:wrap}
#dateBanner{font-size:20px;font-weight:800;letter-spacing:.2px;white-space:nowrap}
#timeBanner{font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap}
.hd .sub{color:var(--faint);font-size:var(--t-xs);margin-top:3px}
.hd .sub b{color:var(--dim)}
/* ---------- layout ---------- */
main{padding:14px 14px 0;max-width:580px;margin:0 auto}
main>section{animation:pageIn .34s var(--ease)}
@keyframes pageIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.hidden{display:none!important}
/* ---------- type ---------- */
.eyebrow{font-size:var(--t-xs);font-weight:800;letter-spacing:2.5px;color:var(--gold);text-transform:uppercase;margin:0 0 4px}
.sec-h{margin:4px 0 14px}
.sec-h h2{margin:0;font-size:var(--t-xl);font-weight:800;letter-spacing:.2px}
.sec-h p{margin:5px 0 0;color:var(--dim);font-size:var(--t-s);line-height:1.5}
.big{font-size:var(--t-xl);font-weight:800;font-variant-numeric:tabular-nums}
.lbl{font-size:var(--t-xs);color:var(--dim);letter-spacing:1.2px;text-transform:uppercase;font-weight:700}
.fineprint{font-size:var(--t-xs);color:var(--faint);line-height:1.6}
p.body{font-size:var(--t-m);color:var(--txt);line-height:1.65;margin:0 0 10px}
.code{display:block;font-family:var(--mono);font-size:var(--t-s);background:#080814;border:1px solid var(--line);
  border-radius:var(--r-s);padding:10px 12px;color:var(--green);margin:10px 0;overflow-x:auto}
/* ---------- cards ---------- */
.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);
  border-radius:var(--r-l);padding:16px;margin-bottom:12px;box-shadow:0 2px 14px rgba(0,0,0,.28)}
.card-h{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px}
.card-h .eyebrow{margin:0}
/* ---------- buttons ---------- */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;
  background:linear-gradient(180deg,var(--gold2),var(--gold));color:#1c1200;border:none;border-radius:var(--r-m);
  padding:12px 20px;min-height:var(--touch);font-weight:800;font-size:var(--t-m);font-family:var(--font);cursor:pointer;
  box-shadow:0 4px 16px rgba(255,176,0,.22);transition:transform .12s var(--ease),filter .12s}
.btn:active{transform:scale(.97);filter:brightness(1.1)}
.btn-ghost{display:inline-flex;align-items:center;justify-content:center;gap:8px;flex:1;
  background:rgba(255,255,255,.03);border:1px solid var(--line2);color:var(--txt);border-radius:var(--r-m);
  padding:12px 16px;min-height:var(--touch);font-weight:700;font-size:var(--t-m);font-family:var(--font);cursor:pointer;
  transition:transform .12s var(--ease),border-color .12s}
.btn-ghost:active{transform:scale(.97);border-color:var(--gold-line)}
.prevnext{display:flex;gap:10px;margin:12px 0 4px}
/* ---------- chips ---------- */
.chip{flex:0 0 auto;min-height:var(--touch);padding:8px 15px;border-radius:var(--r-pill);border:1px solid var(--line);
  background:var(--panel);color:var(--txt);font-size:var(--t-s);font-weight:600;font-family:var(--font);cursor:pointer;
  transition:all .18s var(--ease);white-space:nowrap}
.chip:active{transform:scale(.94)}
.chip:focus-visible,.row:focus-visible,.btn:focus-visible,.btn-ghost:focus-visible,.sim-tile:focus-visible,.back:focus-visible,
nav button:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.chip.on{border-color:var(--gold-line);background:var(--gold-soft);color:var(--gold);font-weight:800}
/* ---- .skr companion ---- */
.cmp-wrap{display:flex;gap:14px;align-items:center}
.cmp-ghost{flex:0 0 auto;width:84px;height:100px;position:relative;filter:drop-shadow(0 6px 16px rgba(0,0,0,.45))}
.cmp-ghost svg{width:100%;height:100%;display:block}
.cmp-ghost.idle svg{animation:cmpFloat 3.2s ease-in-out infinite}
@keyframes cmpFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
.cmp-xpbar{height:8px;border-radius:99px;background:#1a1a2e;border:1px solid var(--line);overflow:hidden;margin-top:8px}
.cmp-xpfill{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--gold),#ffd76a);transition:width .5s ease}
.cmp-lvltag{font-size:11px;font-weight:800;letter-spacing:.12em;color:var(--gold)}
.cmp-trait{font-size:13px;color:var(--dim);line-height:1.6}
.cmp-stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.cmp-egg{font-size:52px;filter:grayscale(.4);opacity:.85}
/* ---- checkout ---- */
#payModal{position:fixed;inset:0;z-index:210;background:rgba(8,8,16,.94);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box}
#payModal.hidden{display:none}
.pay-tier{display:flex;align-items:center;gap:10px;padding:12px;border:1px solid var(--line);border-radius:12px;margin-bottom:8px;cursor:pointer;text-align:left;width:100%;background:transparent;color:var(--txt);font-size:15px;box-sizing:border-box}
.pay-tier.on{border-color:var(--gold-line);background:var(--gold-soft)}
.pay-tier .pt-price{margin-left:auto;font-weight:800;color:var(--gold);white-space:nowrap;flex-shrink:0}
.pay-sol{font-size:13px;color:var(--dim)}
#onboard{position:fixed;inset:0;z-index:200;background:rgba(8,8,16,.96);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box}
#onboard.hidden{display:none}
.ob-card{max-width:380px;width:100%;text-align:center}
.ob-slide{display:none}
.ob-slide.on{display:block;animation:obIn .3s ease}
@keyframes obIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.ob-ic{font-size:48px;margin-bottom:16px}
.ob-dots{display:flex;gap:8px;justify-content:center;margin:20px 0}
.ob-dot{width:8px;height:8px;border-radius:50%;background:var(--line)}
.ob-dot.on{background:var(--gold)}
.ob-nav{display:flex;gap:10px}
.ob-nav .btn{flex:1}
.chip b{font-variant-numeric:tabular-nums}
/* ---------- chat ---------- */
#t-chat h1{font-size:var(--t-xl);margin:0 0 2px;letter-spacing:.2px}
#t-chat>.sub{color:var(--dim);font-size:var(--t-s);margin:0 0 10px;line-height:1.45}
.chat-note{position:relative;padding:8px 38px 8px 12px;margin:0 0 10px;font-size:11px;line-height:1.45}
.chat-note-x{position:absolute;top:50%;right:4px;transform:translateY(-50%);width:32px;height:32px;border:none;background:none;color:var(--faint);font-size:13px;cursor:pointer}
.chat-chwrap{position:relative}
.chat-ch{display:flex;gap:8px;overflow-x:auto;padding:2px 0 8px;scrollbar-width:none;align-items:center}
.chat-ch::-webkit-scrollbar{display:none}
.chat-ch .chip{flex:0 0 auto}
.chat-chwrap.more::after{content:'';position:absolute;top:0;right:0;bottom:8px;width:46px;pointer-events:none;background:linear-gradient(90deg,rgba(6,6,14,0),#07070f 85%)}
.chat-who{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--dim);white-space:nowrap;flex:0 0 auto;padding-left:2px}
.chat-who b{color:var(--gold)}
.chat-list{display:flex;flex-direction:column;gap:16px;padding-bottom:10px}
.chat-msg{display:flex;gap:10px;align-items:flex-start}
.chat-msg.feed{cursor:pointer}
.chat-av{flex:0 0 38px;width:38px;height:38px;border-radius:50%;background:var(--panel3);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:19px}
.chat-body{flex:1;min-width:0}
.chat-meta{display:flex;align-items:baseline;gap:8px}
.chat-name{font-weight:800;font-size:13px;color:var(--txt)}
.chat-name.mine{color:var(--gold)}
.chat-name.sys{color:var(--violet,#a78bfa)}
.chat-time{font-size:10px;color:var(--faint)}
.chat-ctl{margin-left:auto;display:flex;align-items:center;gap:2px}
.chat-del{background:none;border:none;color:var(--faint);font-size:15px;cursor:pointer;padding:0;min-width:44px;min-height:44px;margin-top:-13px;margin-bottom:-13px;margin-right:-10px;display:inline-flex;align-items:center;justify-content:center}
.chat-rxadd{background:rgba(255,255,255,.03);border:1px solid var(--line);color:var(--dim);border-radius:999px;font-size:13px;cursor:pointer;min-width:40px;min-height:40px;margin-top:-11px;margin-bottom:-11px;display:inline-flex;align-items:center;justify-content:center;padding:0 8px}
.chat-txt{font-size:14px;line-height:1.5;overflow-wrap:anywhere;white-space:pre-wrap;margin-top:2px}
.chat-rx{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.rx-chip{border:1px solid var(--line);background:var(--panel3);border-radius:999px;padding:6px 12px;font-size:12px;cursor:pointer;color:var(--dim);min-height:40px;display:inline-flex;align-items:center;font-variant-numeric:tabular-nums}
.rx-chip.on{border-color:var(--gold-line);color:var(--gold);background:var(--gold-soft)}
.chat-err{font-size:var(--t-s);color:#ffb4b4;background:rgba(255,90,90,.08);border:1px solid rgba(255,110,110,.45);border-radius:var(--r-m);padding:10px 14px;line-height:1.55;margin-bottom:12px}
.chat-err.hint{color:var(--dim);background:rgba(255,255,255,.03);border:1px dashed var(--line2)}
.chat-shake{animation:chatShake .35s ease}
@keyframes chatShake{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}75%{transform:translateX(5px)}}
.chat-composer{position:fixed;left:0;right:0;bottom:calc(77px + env(safe-area-inset-bottom));z-index:25;display:flex;gap:8px;padding:10px 14px;background:rgba(7,7,16,.94);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-top:1px solid var(--line)}
.chat-composer input{flex:1;background:var(--panel3);border:1px solid var(--line);border-radius:999px;padding:0 16px;color:var(--txt);font-size:16px;min-height:44px;min-width:0}
.chat-send{flex:0 0 44px;width:44px;height:44px;border-radius:50%;border:none;background:linear-gradient(135deg,var(--gold2),var(--gold));color:#1a1200;font-size:17px;font-weight:900;cursor:pointer}
.chat-send:active{transform:scale(.92)}
.chat-empty{text-align:center;color:var(--dim);padding:36px 16px;font-size:14px;line-height:1.7}
/* ---------- list rows ---------- */
.row{display:flex;align-items:center;gap:12px;padding:12px 6px;border-bottom:1px solid rgba(255,255,255,.06);cursor:pointer;transition:background .15s}
.row:last-child{border-bottom:none}
.row:active{background:rgba(255,255,255,.035)}
.rank{flex:0 0 36px;height:36px;border-radius:12px;background:var(--panel3);display:flex;align-items:center;justify-content:center;
  color:var(--dim);font-weight:800;font-size:var(--t-s);font-variant-numeric:tabular-nums}
.rank.top1{background:var(--gold-soft);color:var(--gold);border:1px solid var(--gold-line)}
.rank.top2{background:rgba(255,255,255,.06);color:var(--txt);border:1px solid var(--line2)}
.rank.top3{background:rgba(255,140,60,.10);color:#ff9a5c;border:1px solid rgba(255,140,60,.35)}
.row-sub{font-size:var(--t-xs);color:var(--dim);margin-top:2px}
.row-big{font-size:var(--t-l);font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.row-unit{font-size:var(--t-xs);color:var(--dim);font-weight:600}
.chev{color:var(--faint);font-size:22px;font-weight:700;line-height:1}
.waddr{font-family:var(--mono);font-size:var(--t-s);overflow-wrap:anywhere;word-break:break-word}
/* ---------- kv ---------- */
.kv{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:9px 0;
  border-bottom:1px solid rgba(255,255,255,.05);font-size:var(--t-m)}
.kv:last-child{border-bottom:none}
.kv span{color:var(--dim);white-space:nowrap;flex:0 0 auto}
.kv b{text-align:right}
.kv b{font-variant-numeric:tabular-nums;text-align:right}
/* ---------- stat tiles ---------- */
.stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.stat-tile{background:var(--panel3);border:1px solid var(--line);border-radius:var(--r-m);padding:13px 14px}
.stat-tile .v{font-size:var(--t-xl);font-weight:800;font-variant-numeric:tabular-nums;line-height:1.2;overflow-wrap:anywhere}
.stat-tile .l{font-size:var(--t-xs);color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-top:3px;font-weight:700}
.split{display:flex;height:12px;border-radius:6px;overflow:hidden;background:#0a0a18;border:1px solid var(--line)}
.split i{display:block;height:100%;transition:width .5s var(--ease)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
/* ---------- player card / dossier ---------- */
.ident{flex:0 0 56px;width:56px;height:56px;border-radius:18px;display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:15px;color:#fff;text-shadow:0 1px 4px rgba(0,0,0,.5);border:1px solid rgba(255,255,255,.18)}
.player-top{display:flex;align-items:center;gap:14px}
.tag{font-size:var(--t-xs);padding:4px 10px;border-radius:var(--r-pill);background:var(--panel3);color:var(--dim);font-weight:700;
  display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.tag-violet{background:var(--violet-soft);color:var(--violet);border:1px solid rgba(139,92,246,.35)}
.anom-badge{flex:0 0 auto;font-size:10px;font-weight:800;letter-spacing:.5px;border:1px solid;border-radius:var(--r-pill);padding:3px 8px;white-space:nowrap}
.plan-row{display:flex;align-items:center;gap:10px;padding:13px 0;border-top:1px solid var(--line)}
.plan-row:first-child{border-top:none;padding-top:2px}
.tag-gold{background:var(--gold-soft);color:var(--gold);border:1px solid var(--gold-line)}
.tag-green{background:var(--green-soft);color:var(--green);border:1px solid var(--green-line)}
.sortrow{display:flex;gap:8px;margin-bottom:10px;overflow-x:auto;padding-bottom:2px;scrollbar-width:none}
.sortrow::-webkit-scrollbar{display:none}
.row-tag{flex:0 0 auto}
.waddr-1{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* ---------- fingerprints / heat tiles ---------- */
.fp-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}
.fp-tile{aspect-ratio:1;border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;
  font-size:11px;font-weight:800;gap:1px;transition:transform .15s;background:transparent;border:2px solid var(--line)}
.fp-tile .v{font-size:9px;font-weight:600;opacity:.85;font-variant-numeric:tabular-nums}
.fp-tile.win{outline:2px solid var(--gold);box-shadow:0 0 14px rgba(255,176,0,.5)}
.sim-tile{aspect-ratio:1;border-radius:10px;display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:800;background:#141428;border:1px solid var(--line);color:var(--dim);cursor:pointer;
  min-height:44px;transition:transform .12s}
.sim-tile:active{transform:scale(.92)}
.sim-tile.on{background:transparent;border:2px solid var(--gold);color:var(--gold);box-shadow:inset 0 0 12px rgba(255,176,0,.12)}
/* ---------- lenses ---------- */
.lens-card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);
  border-radius:var(--r-l);padding:14px;margin-bottom:12px}
.lens-head{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.lens-head .t{font-weight:800;font-size:var(--t-m)}
.lens-head .c{margin-left:auto;font-size:var(--t-xs);color:var(--dim);background:var(--panel3);
  border:1px solid var(--line);padding:4px 10px;border-radius:var(--r-pill);font-weight:700}
.rgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
@media(min-width:381px){.rgrid{grid-template-columns:repeat(6,1fr)}}
.lens{display:flex;flex-direction:column;gap:6px;min-width:0;cursor:pointer;border-radius:var(--r-m);padding:6px;transition:background .15s}
.lens:active{background:rgba(255,255,255,.05)}
.lens-date{align-self:center;font-size:var(--t-xs);font-weight:700;color:var(--dim);background:rgba(255,255,255,.04);
  border:1px solid rgba(255,255,255,.09);border-radius:8px;padding:3px 7px;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.lens-cell{border-radius:var(--r-m);padding:12px 4px 10px;text-align:center;border:1px solid rgba(255,255,255,.07)}
.lens-tile{font-size:26px;font-weight:800;color:var(--gold);line-height:1.1;text-shadow:0 0 18px rgba(255,176,0,.35)}
.lens-round{font-size:var(--t-xs);color:var(--dim);margin-top:3px;font-variant-numeric:tabular-nums}
.lens-sol{font-size:var(--t-s);font-weight:800;margin-top:2px;font-variant-numeric:tabular-nums}
.lens-time{align-self:center;font-size:var(--t-xs);font-weight:700;font-variant-numeric:tabular-nums;color:var(--txt)}
/* ---------- streaks ---------- */
.streak-row{display:flex;align-items:center;gap:12px;padding:9px 0;border-bottom:1px solid rgba(255,255,255,.06)}
.streak-row:last-child{border-bottom:none}
.streak-tile{flex:0 0 40px;height:40px;border-radius:12px;background:var(--gold-soft);border:1px solid var(--gold-line);
  color:var(--gold);font-weight:800;font-size:18px;display:flex;align-items:center;justify-content:center}
.streak-bar{flex:1;height:12px;background:#0c0c1c;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.streak-bar i{display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--gold));border-radius:6px;transition:width .5s var(--ease)}
.streak-n{flex:0 0 64px;text-align:right;font-size:var(--t-s);color:var(--dim);font-variant-numeric:tabular-nums;line-height:1.5}
/* ---------- replay picker ---------- */
.rfilm{display:flex;gap:10px;overflow-x:auto;padding:6px 2px 14px;scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.rfilm::-webkit-scrollbar{display:none}
.rpick{flex:0 0 auto;width:118px;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);
  border-radius:16px;padding:10px;cursor:pointer;user-select:none;scroll-snap-align:start;
  transition:transform .15s var(--ease),border-color .15s,box-shadow .15s}
.rpick:active{transform:scale(.94)}
.rpick.on{border-color:var(--gold-line);box-shadow:0 0 16px rgba(255,176,0,.22)}
.rpick .watching{position:absolute;top:6px;right:6px;font-size:9px;font-weight:800;letter-spacing:1px;color:#000;background:var(--gold);border-radius:999px;padding:3px 8px}
.rpick{position:relative}
.rpick-day{background:#101024;border:1px solid var(--line);border-radius:8px;padding:5px 2px;text-align:center;margin-bottom:6px}
.rpick-day .d{display:block;font-size:var(--t-xs);font-weight:800;letter-spacing:1.5px}
.rpick-day .m{display:block;font-size:9px;color:var(--dim);margin-top:1px}
.rpick-tile{background:var(--gold-soft);border:1px solid var(--gold-line);border-radius:10px;padding:8px 2px;text-align:center;margin-bottom:6px}
.rpick-tile .n{font-size:32px;font-weight:800;color:var(--gold);line-height:1;display:block;text-shadow:0 0 20px rgba(255,176,0,.4)}
.rpick-tile .l{font-size:8px;color:var(--dim);letter-spacing:2px;margin-top:3px;display:block;font-weight:700}
.rpick-meta{display:flex;justify-content:space-between;font-size:9px;color:var(--dim);margin:0 1px 6px;font-variant-numeric:tabular-nums}
.rpick-bar{height:5px;background:#0c0c1c;border:1px solid var(--line);border-radius:3px;overflow:hidden}
.rpick-bar i{display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--gold));border-radius:3px}
/* ---------- last-4 replay page ---------- */
.l4-timer{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--line);
  border-radius:var(--r-m);padding:9px 12px;margin-bottom:10px}
.l4-timer .ic{font-size:15px;line-height:1}
.l4-timer .tx{font-size:var(--t-xs);font-weight:800;letter-spacing:1.6px;color:var(--dim);text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.l4-timer b{margin-left:auto;font-size:var(--t-s);font-weight:800;color:var(--gold);font-variant-numeric:tabular-nums;white-space:nowrap;text-transform:uppercase}
.l4-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.l4-card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:var(--r-l);
  padding:7px;cursor:pointer;position:relative;min-width:0;transition:transform .15s var(--ease),border-color .15s,box-shadow .15s}
.l4-card:active{transform:scale(.97)}
.l4-card.on{border-color:var(--gold-line);box-shadow:0 0 16px rgba(255,176,0,.22)}
.l4-head{display:flex;align-items:center;justify-content:space-between;gap:6px;min-height:22px}
.l4-round{font-size:var(--t-m);font-weight:800;font-variant-numeric:tabular-nums}
.l4-ago{display:flex;justify-content:flex-end;margin-top:3px;line-height:1.3}
.l4-ago .ago-el{font-size:9.5px;font-weight:800;letter-spacing:.5px;color:var(--gold);font-variant-numeric:tabular-nums;text-transform:uppercase;white-space:nowrap}
.rpick-ago{margin:0 1px 6px;text-align:center;line-height:1.3}
.rpick-ago .ago-el{font-size:9px;font-weight:800;letter-spacing:.5px;color:var(--gold);font-variant-numeric:tabular-nums;text-transform:uppercase;white-space:nowrap}
.l4-play{font-size:9px;font-weight:800;letter-spacing:1.2px;color:var(--gold);background:var(--gold-soft);
  border:1px solid var(--gold-line);border-radius:999px;padding:4px 9px;white-space:nowrap}
.l4-watch{font-size:9px;font-weight:800;letter-spacing:1px;color:#000;background:var(--gold);border-radius:999px;padding:4px 8px;white-space:nowrap}
.l4-board{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;width:75%;margin:5px auto 4px}
.l4-board .fp-tile{line-height:1.1;border-radius:7px}
.l4-board .fp-tile .v{font-size:9px}
.l4-win{display:flex;align-items:baseline;gap:4px;font-size:11px;white-space:nowrap;overflow:hidden}
.l4-win .crown{font-size:11.5px}
.l4-win b{color:var(--gold);font-weight:800}
.l4-win span{color:var(--dim);font-variant-numeric:tabular-nums}
.l4-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:4px}
.l4-stat{background:var(--panel3);border:1px solid var(--line);border-radius:var(--r-s);padding:5px 2px;text-align:center;min-width:0}
.l4-stat .v{font-size:var(--t-s);font-weight:800;font-variant-numeric:tabular-nums;line-height:1.25}
.l4-stat .l{font-size:7.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.3px;font-weight:700;white-space:nowrap}
.l4-when{margin-top:4px;font-size:9.5px;color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#topPicker{padding-bottom:24px}
#topPicker .reel-label{scroll-margin-bottom:96px}
@media (max-width:380px){
  #t-replay .sec-h p{display:none}
  #t-replay .sec-h .eyebrow{display:none}
  .l4-grid{gap:8px}
  .l4-card{padding:6px}
  .l4-head{min-height:19px}
  .l4-ago{margin-top:2px}
  .l4-ago .ago-el{font-size:9px}
  .l4-board{gap:3px;width:73%;margin:4px auto 3px}
  .l4-board .fp-tile{border-radius:6px;border-width:1.5px}
  .l4-board .fp-tile .v{font-size:8px}
  .l4-timer{padding:5px 10px;margin-bottom:8px}
  .l4-win{font-size:9.5px;gap:3px}
  .l4-win .crown{font-size:11px}
  .l4-when .l4-wd{display:none}
  .l4-stats{display:flex;flex-wrap:wrap;gap:1px 10px;margin-top:3px;line-height:1.55}
  .l4-stat{display:inline;background:none;border:none;padding:0;text-align:left;white-space:nowrap}
  .l4-stat .v{display:inline;font-size:10.5px}
  .l4-stat .l{display:inline;font-size:8px;letter-spacing:.5px}
  .l4-when{font-size:9px}
}
/* ---------- player ---------- */
.player{background:#05050d;border:1px solid var(--line);border-radius:var(--r-l);overflow:hidden;margin-bottom:12px;box-shadow:var(--shadow)}
.hh{padding:12px 14px 10px;background:rgba(255,255,255,.025);border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px}
.hh-row{display:flex;align-items:center;gap:8px;min-width:0}
.hh-date{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--gold-line);background:var(--gold-soft);
  border-radius:var(--r-pill);padding:7px 13px;font-size:var(--t-s);font-weight:800;color:var(--gold);white-space:nowrap}
.hh-round{margin-left:auto;color:var(--dim);font-size:var(--t-s);font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.hh-time{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);border-radius:var(--r-pill);
  padding:7px 13px;font-size:var(--t-s);font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.hh-clock{flex:1 1 auto;text-align:center;color:var(--green);font-size:16px;font-weight:800;font-variant-numeric:tabular-nums;
  min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 0 12px rgba(0,255,163,.35)}
.hh-miners{display:inline-flex;align-items:center;gap:5px;font-size:var(--t-s);font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.hh-mute{flex:0 0 auto;width:var(--touch);min-height:var(--touch);border-radius:12px;border:1px solid var(--green-line);
  background:var(--green-soft);font-size:17px;cursor:pointer;padding:0;transition:transform .12s}
.hh-mute:active{transform:scale(.92)}
#stage{position:relative;background:#030309}
#rpc{display:block;width:100%}
.aftermath-ov{position:absolute;left:12px;right:12px;bottom:12px;pointer-events:none;opacity:0;transform:translateY(18px) scale(.97);transition:opacity .5s var(--ease),transform .5s var(--ease)}
.aftermath-ov.show{opacity:1;transform:none}
.aftermath-card{background:rgba(8,8,20,.94);border:1px solid var(--gold-line);border-radius:16px;padding:14px;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);box-shadow:0 0 34px rgba(255,176,0,.28);text-align:center}
.aftermath-tile{font-size:24px;font-weight:800;color:var(--gold);letter-spacing:1px;margin:4px 0;text-shadow:0 0 22px rgba(255,176,0,.5)}
.aftermath-sub{font-size:var(--t-s);color:var(--dim);font-variant-numeric:tabular-nums}
.telemetry{padding:14px}
.tele-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.tele{background:var(--panel3);border:1px solid var(--line);border-radius:var(--r-m);padding:12px 6px;text-align:center}
.tele-v{font-size:var(--t-l);font-weight:800;font-variant-numeric:tabular-nums;line-height:1.2;overflow-wrap:anywhere}
.tele-v.gold{color:var(--gold)} .tele-v.green{color:var(--green)}
.tele-l{font-size:var(--t-xs);color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-top:4px;font-weight:700}
/* ---------- film reel ---------- */
.reel-label{display:flex;align-items:center;gap:8px;margin:16px 2px 8px}
.reel-label .t{font-size:var(--t-xs);font-weight:800;letter-spacing:2px;color:var(--dim);text-transform:uppercase}
.reel-label .line{flex:1;height:1px;background:var(--line)}
#miniStrip{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 16px;scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch;scrollbar-width:none}
#miniStrip::-webkit-scrollbar{display:none}
.mini-card{flex:0 0 112px;scroll-snap-align:start;cursor:pointer;background:var(--panel);border:1px solid var(--line);
  border-radius:var(--r-m);padding:7px;transition:transform .15s var(--ease),border-color .15s,box-shadow .15s}
.mini-card:active{transform:scale(.93)}
.mini-card.on{border-color:var(--gold-line);box-shadow:0 0 14px rgba(255,176,0,.22)}
.mini-card canvas{display:block;width:100%;aspect-ratio:1;border-radius:8px;background:#0a0a16}
.mini-meta{display:flex;align-items:baseline;justify-content:space-between;margin-top:6px;padding:0 2px}
.mini-meta b{font-size:var(--t-s);font-variant-numeric:tabular-nums}
.mini-meta span{font-size:9px;color:var(--dim);font-variant-numeric:tabular-nums}
/* ---------- notes / empty / skeleton ---------- */
.note{font-size:var(--t-s);color:var(--dim);background:rgba(255,255,255,.025);border:1px dashed var(--line2);
  border-radius:var(--r-m);padding:12px 14px;margin-top:10px;line-height:1.6}
.empty{text-align:center;padding:34px 18px;color:var(--dim)}
.empty .ic{font-size:34px;display:block;margin-bottom:10px}
.skel{border-radius:10px;background:linear-gradient(90deg,var(--panel3) 25%,#23234e 50%,var(--panel3) 75%);
  background-size:200% 100%;animation:shimmer 1.3s infinite linear}
@keyframes shimmer{to{background-position:-200% 0}}
/* ---------- misc ---------- */
.back{display:inline-flex;align-items:center;gap:6px;min-height:var(--touch);color:var(--green);background:none;
  border:none;font-size:var(--t-m);font-weight:700;font-family:var(--font);padding:8px 4px;cursor:pointer;margin-bottom:2px}
.back:active{opacity:.7}
h2{font-size:var(--t-l);margin:14px 0 8px;letter-spacing:.3px}
input[type=text]{width:100%;background:#0a0a18;border:1px solid var(--line);color:var(--txt);
  border-radius:12px;padding:12px;font-size:16px;margin-bottom:8px;font-family:var(--font)}
input[type=text]:focus{outline:none;border-color:var(--gold-line)}
/* ---------- bottom nav ---------- */
nav{position:fixed;bottom:0;left:0;right:0;z-index:30;display:flex;background:rgba(7,7,16,.96);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid var(--line);
  padding:8px 8px calc(8px + env(safe-area-inset-bottom))}
nav button{flex:1;background:none;border:none;color:var(--faint);font-size:9px;font-weight:800;letter-spacing:1.2px;
  font-family:var(--font);padding:6px 0;cursor:pointer;min-height:54px;border-radius:14px;transition:color .18s,background .18s;position:relative}
nav button .ic{font-size:21px;display:block;margin-bottom:3px;transition:transform .18s var(--ease)}
nav button.on{color:var(--gold);background:var(--gold-soft)}
nav button.on .ic{transform:translateY(-1px);filter:drop-shadow(0 0 8px rgba(255,176,0,.55))}
/* ---------- stat share dialog ---------- */
#statShareDlg{position:fixed;left:12px;right:12px;bottom:90px;z-index:99;background:#12122a;
  border:1px solid var(--gold-line);border-radius:20px;padding:18px;box-shadow:var(--shadow);animation:msgIn .25s var(--ease)}
@media (max-width:380px){
  .hh-clock{font-size:14px}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
}

</style>
</head>
<body>
<header class="hd">
<div class="brand"><span class="ghost">👻</span><span class="wordmark">ORE HAUNT</span><span class="badge">SEEKER EXCLUSIVE</span><button class="infoBtn" onclick="show('t-about')" aria-label="Info, plans and FAQ">i</button></div>
<div class="clockline"><div id="dateBanner"></div><div id="timeBanner"></div></div>
<div class="sub" id="asof">ORE miner intelligence · verified layout data</div>
</header>
<script>
// timezone: auto-detected from the device, PST fallback
let TZ='America/Los_Angeles';
try{ const dtz=Intl.DateTimeFormat().resolvedOptions().timeZone; if(dtz) TZ=dtz; }catch(e){}
function tzAbbr(){ try{ const pz=new Intl.DateTimeFormat('en-US',{timeZone:TZ,timeZoneName:'short'}).formatToParts(new Date()).find(x=>x.type==='timeZoneName'); if(pz&&pz.value) return pz.value; }catch(e){} return ''; }
function fmtT(iso){ try{ const d=new Date(iso); if(isNaN(d.getTime())) return ''; return d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:TZ}); }catch(e){ return ''; } }
function fmtD(iso,opts){ try{ return new Date(iso).toLocaleDateString('en-US',Object.assign({timeZone:TZ},opts||{})); }catch(e){ return ''; } }


function renderFAQ(){const fl=document.getElementById('faqList'); if(!fl) return;
const FQ=[["What is ORE?","ORE is a mineable token on Solana. Miners deploy SOL onto a 25-tile board each round; one tile wins."],
["How is the winner picked?","The program mixes on-chain entropy and takes it mod 25. It's random — no tile, pattern, or strategy changes the odds."],
["What is a deploy?","SOL committed to tiles in a round. The deploy event records SOL per square, so a deploy's total is per-square amount × squares."],
["What does ORE HAUNT track?","The top 150 miners by SOL deployed, the last 200 rounds, and 8 full deploy-by-deploy replays — collected live from ORE V4 chain state by our own poller and scanner."],
["Why can't it predict winners?","Because winners are random. HAUNT shows how miners behave and what rounds cost — never what will win."],
["What do the strategy tags mean?","🐋 Whale = top 10% by SOL. 🎯 Sniper = half or more of SOL on one tile. 🕸 Spreader = SOL spread evenly (no tile over 15%). ⚖ Balanced = in between."],
["What is whale pressure?","How much of a round's pot sat on a single tile — a rough read of how whale-dominated it was."],
["Is the data live?","Yes — this build's rounds, miners and replays were collected live from ORE V4 chain state by our own poller + scanner, then bundled into the app. It's a live-collected snapshot at build time, not a real-time stream; the header shows how old the bundled data is."],
["What are the plans?","Free: top-25 leaderboard, 1 replay, top-25 dossiers. Day ($1), Monthly ($5), Lifetime ($50): everything — all 150 miners, all replays, simulator, similar miners, miner compare, tax CSV."],
["What is the tax CSV?","A download of a miner's aggregate activity (totals + per-tile) for your records. It's window aggregates, not a full transaction history — check with your accountant."]];
fl.innerHTML=FQ.map((q,i)=>`<div style="border-top:1px solid var(--line);padding:12px 2px">`
 +`<div role="button" tabindex="0" style="font-weight:700;font-size:14px;cursor:pointer;display:flex;justify-content:space-between;gap:8px" onclick="this.nextElementSibling.classList.toggle('hidden');this.querySelector('.chev').textContent=this.nextElementSibling.classList.contains('hidden')?'›':'⌄'" onkeydown="if(event.key==='Enter'){this.click()}"><span>${q[0]}</span><span class="chev">›</span></div>`
 +`<div class="hidden" style="color:var(--dim);font-size:13px;line-height:1.6;margin-top:8px">${q[1]}</div></div>`).join('');}

function refreshReplayPills(){
  const R=D.replays.replays.find(x=>x.round===currentRound); if(!R) return;
  const t0=R.timeline[0]?R.timeline[0].t:null;
  const dstr=t0?fmtD(t0,{weekday:'short',month:'short',day:'numeric'}).toUpperCase():'';
  const tstr=t0?fmtT(t0).slice(0,5)+' '+tzAbbr():'';
  const b=$('replayBody'); if(!b) return;
  const dh=b.querySelector('.hh-date span:last-child'); if(dh) dh.textContent=dstr;
  const th=b.querySelector('.hh-time span:last-child'); if(th) th.textContent=tstr;
}
function daypart(h){return h<5?'night':h<12?'morning':h<17?'afternoon':h<21?'evening':'night';}
function tickBanner(){const n=new Date();
const parts=new Intl.DateTimeFormat('en-US',{timeZone:TZ,hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(n);
const h=+parts.find(p=>p.type==='hour').value;
const db=document.getElementById('dateBanner'); if(db) db.textContent=n.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric',timeZone:TZ});
const tb=document.getElementById('timeBanner'); if(tb) tb.textContent=daypart(h)+' \u00B7 '+fmtT(n.toISOString())+' '+tzAbbr();}
function tick(){ tickBanner(); }
tick();setInterval(tick,30000);

function renderTierTable(){
  const tt=document.getElementById('tierTable');
  if(tt){
    const rows=[['Leaderboard','Top 25','All 150'],['Replays','Latest 1','All 8'],['Miner dossiers','Top 25','All 150'],['Strategy simulator','—','✓'],['Tax CSV export','—','✓'],['Wallet lookup','✓','✓'],['Similar miners','—','✓'],['Miner compare','—','✓'],['Whale consensus','—','✓'],['Cost per win','—','✓'],['Window report','—','✓']];
    tt.innerHTML='<div style="display:grid;grid-template-columns:1fr auto auto;gap:6px 12px;font-size:13px;align-items:center">'
     +'<div></div><div style="font-weight:800;color:var(--dim)">FREE</div><div style="font-weight:800;color:var(--gold)">PAID</div>'
     +rows.map(r=>`<div>${r[0]}</div><div style="text-align:center">${r[1]}</div><div style="text-align:center;color:var(--gold)">${r[2]}</div>`).join('')
     +'</div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">'
     +Object.keys(TIERS).filter(t=>t!=='free').map(t=>`<button class="chip" onclick="_payTier='${t}';openCheckout()">${TIERS[t].name} · ${TIERS[t].price}</button>`).join('')+'</div>';
  }
  const tb=document.getElementById('tierBadge');
  if(tb) tb.textContent=(typeof Entitlements!=='undefined'&&Entitlements.tier==='free')?'free':'preview · all unlocked';
}
</script>
<main>
<section id="t-intel" class="hidden">
<div class="sec-h"><div class="eyebrow">Intel</div><h2>Miner leaderboard</h2><p>Top 150 wallets by SOL deployed. Tap any miner for their dossier.</p></div>
<div class="card" style="display:flex;gap:8px;margin-bottom:10px">
<input id="walletSearch" type="text" placeholder="Paste a wallet address…" autocapitalize="off" spellcheck="false"
  style="flex:1;min-width:0;background:#1a1a2e;border:1px solid var(--line);border-radius:12px;padding:12px 14px;color:var(--txt);font-size:16px;box-sizing:border-box"
  onkeydown="if(event.key==='Enter')lookupWallet()">
<button class="btn" style="flex:0 0 auto;width:auto" onclick="lookupWallet()">Find</button>
</div>
<div id="walletResult"></div>
<div class="sortrow" role="tablist" aria-label="Sort miners">
<button class="chip on" data-sort="sol" onclick="setSort('sol')">💰 SOL</button>
<button class="chip" data-sort="deploys" onclick="setSort('deploys')">🔁 Deploys</button>
<button class="chip" data-sort="eff" onclick="setSort('eff')">📈 Efficiency</button>
<button class="chip" data-sort="sniper" onclick="setSort('sniper')">🎯 Snipers</button>
<button class="chip" data-sort="spreader" onclick="setSort('spreader')">🕸 Spreaders</button>
<button class="chip" data-sort="expo" onclick="setSort('expo')">⚡ Anomaly</button>
</div>
<div class="card" style="padding:6px 12px" id="leaderList"></div>
</section>
<section id="t-profile" class="hidden">
<button class="back" onclick="show('t-intel')">‹ Leaderboard</button>
<div id="profileBody"></div>
</section>
<section id="t-compare" class="hidden">
<button class="back" onclick="show('t-intel')">‹ Leaderboard</button>
<div class="sec-h"><div class="eyebrow">Intel</div><h2>Compare miners</h2><p>Two wallets, side by side. Paste addresses or pick from the leaderboard.</p></div>
<div id="compareBody"></div>
</section>
<section id="t-replay">
<div class="sec-h"><div class="eyebrow">Replay</div><h2>Last 4 hauntings</h2><p>Four fresh rounds, rebuilt deploy-by-deploy. Tap to play.</p></div>
<div id="topPicker"></div>
</section>
<section id="t-view" class="hidden">
<button class="back" onclick="show('t-replay')">‹ Replays</button>
<div id="replayBody"></div>
<div class="prevnext">
<button class="btn-ghost" onclick="stepReplay(-1)">‹ Prev</button>
<button class="btn-ghost" onclick="stepReplay(1)">Next ›</button>
</div>
<div class="reel-label"><span class="t" id="carouselLbl">Film reel</span><span class="line"></span></div>
<div id="miniStrip"></div>
<button class="btn-ghost" style="width:100%;margin-top:2px" onclick="goReplays()">Browse all hauntings →</button>
</section>
<section id="t-rounds" class="hidden">
<div class="sec-h"><div class="eyebrow">Rounds</div><h2>Round intel</h2><p>Every round, three ways to read it.</p></div>
<div class="sortrow" role="tablist" aria-label="Round intel views" style="margin-bottom:12px">
<button class="chip on" id="ri-explore" onclick="showIntel('explore')">🔍 Explore</button>
<button class="chip" id="ri-sim" onclick="showIntel('sim')">🧪 Simulate</button>
<button class="chip" id="ri-radar" onclick="showIntel('radar')">📡 Radar</button>
</div>
<div id="pane-explore">
<div id="roundList"></div>
<div id="roundDetailWrap" class="hidden">
<button class="back" onclick="closeRoundDetail()">‹ Explorer</button>
<div id="roundDetailBody"></div>
</div>
</div>
<div id="pane-sim" class="hidden">
<p style="color:var(--dim);font-size:13px;margin:0 0 12px">Backtest tile picks against real rounds. No invented payouts — hit rate and cost only.</p>
<div id="labBody"></div>
</div>
<div id="pane-radar" class="hidden">
<div id="anomalyBody"></div>
</div>
</section>
<section id="t-ghost" class="hidden">
<div class="sec-h"><div class="eyebrow">Companion</div><h2>Your ghost</h2><p>Born from your mining fingerprint — it grows as you explore.</p></div>
<div class="card" id="companionCard" style="margin-bottom:10px"><div id="companionBody"></div></div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">Plans</span><span class="tag tag-gold" id="planBadge">preview · all unlocked</span></div>
<div id="planTiers"></div>
<button class="btn-ghost" style="width:100%;margin-top:12px;min-height:44px" onclick="show('t-about')">Compare plans, methodology &amp; FAQ</button>
</div>
<div class="card" style="border-color:rgba(139,92,246,.45);box-shadow:0 0 18px rgba(139,92,246,.16)">
<div class="card-h"><span class="eyebrow" style="margin:0;color:var(--violet)">📡 Anomaly</span><span class="tag tag-violet">above every plan</span></div>
<div id="planAnomaly"></div>
</div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">The five temperaments</span></div>
<div class="kv"><span>🐋 Kraken</span><b>Whale — top of the board by SOL</b></div>
<div class="kv"><span>🎯 Phantom</span><b>Sniper — half or more on a single tile</b></div>
<div class="kv"><span>🕸 Mist</span><b>Spreader — SOL spread thin, no tile over 15%</b></div>
<div class="kv"><span>⚖ Watcher</span><b>Balanced — a mix of both instincts</b></div>
<div class="kv"><span>🌱 Rookie</span><b>Outside the tracked top 150 — for now</b></div>
<p class="body" style="margin-top:10px">Your ghost's temperament comes from how the hatched wallet actually mines in the observation window. It is a read of behavior, not a score — winners are still random.</p>
</div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">Grow your ghost</span></div>
<div class="kv"><span>Watch a replay</span><b>+15 XP</b></div>
<div class="kv"><span>Open a miner dossier</span><b>+10 XP</b></div>
<div class="kv"><span>Run the simulator</span><b>+10 XP</b></div>
<div class="kv"><span>Open a round</span><b>+5 XP</b></div>
<div class="fineprint" style="margin-top:10px">Preview build: your ghost and its XP live on this device only.</div>
</div>
</section>
<section id="t-about" class="hidden">
<button class="back" onclick="show('t-ghost')">‹ Ghost</button>
<div class="sec-h"><div class="eyebrow">Info</div><h2>Plans &amp; methodology</h2><p>How ORE HAUNT reads the chain — and what it doesn't claim.</p></div>
<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Plans</span><span class="tag tag-gold" id="tierBadge">preview</span></div>
<div id="tierTable"></div>
<div class="fineprint" style="margin-top:8px">Payments are live — SOL, ORE or SKR, verified on-chain on this device. This build opens with everything unlocked; add <b>?tier=free</b> to the URL to preview the free experience.</div></div>
<div class="card" style="border-color:var(--gold-line);background:linear-gradient(180deg,rgba(255,176,0,.06),transparent)"><div class="card-h"><span class="eyebrow" style="margin:0;color:var(--gold)">📡 Anomaly — members only</span><span class="tag tag-gold">$7 / day</span></div>
<p style="color:var(--dim);font-size:14px;line-height:1.65;margin:8px 0">The anomaly radar lives above every other plan — even Lifetime doesn't include it. Pot shocks, ghost crowds, whale strikes and flooded tiles, scored and ranked.</p>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px"><span class="chip" style="cursor:default">🔒 100 slots · rolling 24h</span><span class="chip" style="cursor:default">Capped · rolling 24h</span></div>
<div class="fineprint" style="margin-top:10px">Preview with <b>?tier=anomaly</b>.</div></div>
<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">FAQ</span></div><div id="faqList"></div></div>
<div class="card" style="border-color:rgba(139,92,246,.4);box-shadow:0 0 16px rgba(139,92,246,.18)">
<div style="color:var(--violet);font-weight:800;margin-bottom:6px;font-size:15px">✓ Audited chain data</div>
<div class="fineprint" style="font-size:12px">Miner layout (752 bytes) and DeployEvent (121 bytes) verified against ORE program source. Winners are random — no pattern exists.</div></div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">The data</span></div>
<div class="kv"><span>Rounds</span><b id="mRounds"></b></div>
<div class="kv"><span>Deploy events</span><b id="mEvents"></b></div>
<div class="kv"><span>Miner layout</span><b>752 bytes · audited</b></div>
<div class="kv"><span>DeployEvent</span><b>121 bytes · audited</b></div>
</div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">How winners are picked</span></div>
<p class="body">Each round, the ORE program takes four random numbers from the blockchain, mixes them together, and the remainder after dividing by 25 picks the winning tile. Nobody — not even the biggest miner — can predict or change it.</p>
<code class="code">winner = (r1 ^ r2 ^ r3 ^ r4) % 25</code>
</div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">What we estimate</span></div>
<p class="body">Replay times are real on-chain timestamps. Dates in the round explorer are estimated from round spacing (~63s per round) and marked with ~. Close, not exact.</p>
</div>
<div class="card">
<div class="card-h"><span class="eyebrow" style="margin:0">Limits</span></div>
<p class="body">Round accounts recycle about a day after close — history from before collection started is unrecoverable. Nothing here is financial advice.</p>
<div class="fineprint">Miner accounts: PDA ["miner", wallet] on oreV3EG1i9BEgiAJ8b177Z2S2rMarzak4NMv1kULvWv</div>
</div>
</section>
<section id="t-chat" class="hidden">
<p class="eyebrow">💬 CHAT</p>
<h1>The .skr floor</h1>
<p class="sub">Miners talking miners. Your handle is your ghost — no wallet exposed.</p>
<div class="note chat-note" id="chatNote">Local preview · on-device only · radar: real, read-only<button class="chat-note-x" onclick="ChatModule.hideNote()" aria-label="Dismiss note">✕</button></div>
<div class="chat-chwrap" id="chatChWrap"><div class="chat-ch" id="chatChips"></div></div>
<div class="chat-list" id="chatList"></div>
<div class="chat-err hidden" id="chatErr" role="alert"></div>
<div class="chat-composer hidden" id="chatComposer">
<input id="chatInput" type="text" placeholder="Message" autocomplete="off" oninput="ChatModule.draft()" onkeydown="if(event.key==='Enter')ChatModule.send()">
<button class="chat-send" onclick="ChatModule.send()" aria-label="Send message">➤</button>
</div>
</section>
</main>
<nav>
<button onclick="show('t-intel')" id="n-intel"><span class="ic">⛏</span>MINERS</button>
<button class="on" onclick="goReplays()" id="n-replay"><span class="ic">🎬</span>REPLAY</button>
<button onclick="showIntel('explore')" id="n-rounds"><span class="ic">📊</span>ROUNDS</button>
<button onclick="show('t-ghost')" id="n-ghost"><span class="ic">👻</span>GHOST</button>
<button onclick="show('t-chat')" id="n-chat"><span class="ic">💬</span>CHAT</button>
</nav>
<div id="payModal" class="hidden">
<div class="card" style="max-width:380px;width:100%">
<div class="card-h"><span class="eyebrow" style="margin:0">🔓 Unlock ORE HAUNT</span><button class="btn-ghost" onclick="closeCheckout()" aria-label="Close">✕</button></div>
<div id="payBody"></div>
</div>
</div>
<div id="onboard" class="hidden">
<div class="card ob-card">
<div class="ob-slide on" data-s="0">
<div class="ob-ic">👻</div>
<div class="eyebrow">ORE HAUNT</div>
<h2 style="margin:8px 0">Miner intelligence for ORE</h2>
<p style="color:var(--dim);font-size:14px;line-height:1.7">See who's really winning, replay real rounds deploy-by-deploy, and backtest strategies against 200 rounds of chain data.</p>
</div>
<div class="ob-slide" data-s="1">
<div class="ob-ic">⛏️</div>
<div class="eyebrow">Start free</div>
<h2 style="margin:8px 0">Scout the field</h2>
<p style="color:var(--dim);font-size:14px;line-height:1.7">Top-25 leaderboard, a full cinematic replay, and top-25 miner dossiers — free, no signup. Day, Monthly and Lifetime unlock the rest.</p>
</div>
<div class="ob-slide" data-s="2">
<div class="ob-ic">📡</div>
<div class="eyebrow">Go deeper</div>
<h2 style="margin:8px 0">For the obsessed</h2>
<p style="color:var(--dim);font-size:14px;line-height:1.7">Strategy simulator, miner compare, tax exports — and the Anomaly radar, a $7/day tier above everything else. Payments are live in SOL, ORE or SKR; this build opens with everything unlocked.</p>
</div>
<div class="ob-dots"><div class="ob-dot on"></div><div class="ob-dot"></div><div class="ob-dot"></div></div>
<div class="ob-nav">
<button class="btn-ghost" id="obSkip" onclick="obGo(2)">Skip</button>
<button class="btn" id="obNext" onclick="obNext()">Next</button>
</div>
</div>
</div>
<script>
/* roundRect polyfill for older WebViews */
if(!CanvasRenderingContext2D.prototype.roundRect){CanvasRenderingContext2D.prototype.roundRect=function(x,y,w,h,r){this.moveTo(x+r,y);this.arcTo(x+w,y,x+w,y+h,r);this.arcTo(x+w,y+h,x,y+h,r);this.arcTo(x,y+h,x,y,r);this.arcTo(x,y,x+w,y,r);this.closePath();return this;}}
</script>
<script>
/* HAUNT Replay Engine — cinematic canvas round replay.
 * 60fps, pooled particles, zero per-frame allocation in the hot loop.
 * Phases: INTRO → HAUNT → REVEAL → AFTERMATH. (Tension is audio-only: filter sweep.)
 */
(function(){
'use strict';

// ---------- utils ----------
const clamp=(v,a,b)=>v<a?a:v>b?b:v;
// thermal heat scale: cold blue -> blue -> green -> amber -> red hot
const HEAT_STOPS=[[10,16,32],[30,64,175],[16,185,129],[245,158,11],[239,68,68]];

const lerp=(a,b,t)=>a+(b-a)*t;
const easeOut=t=>1-Math.pow(1-t,3);
const easeInOut=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;

// ---------- audio ----------
const Audio_ = {
  ctx:null, master:null, droneGain:null, droneFilter:null, muted:false, lastBlip:0,
  ensure(){
    if(this.ctx) { if(this.ctx.state==='suspended') this.ctx.resume(); return; }
    const C=window.AudioContext||window.webkitAudioContext; if(!C) return;
    this.ctx=new C(); this.master=this.ctx.createGain();
    this.master.gain.value=this.muted?0:0.9; this.master.connect(this.ctx.destination);
    // drone: two detuned sines through a lowpass
    this.droneFilter=this.ctx.createBiquadFilter(); this.droneFilter.type='lowpass';
    this.droneFilter.frequency.value=240; this.droneGain=this.ctx.createGain();
    this.droneGain.gain.value=0.035;
    const o1=this.ctx.createOscillator(),o2=this.ctx.createOscillator();
    o1.frequency.value=55; o2.frequency.value=55.7; o1.type=o2.type='sine';
    o1.connect(this.droneFilter); o2.connect(this.droneFilter);
    this.droneFilter.connect(this.droneGain); this.droneGain.connect(this.master);
    o1.start(); o2.start();
  },
  setTension(x){ // 0..1
    if(!this.ctx||!this.droneFilter)return;
    this.droneFilter.frequency.setTargetAtTime(240+x*1400,this.ctx.currentTime,.4);
    this.droneGain.gain.setTargetAtTime(.035+x*.05,this.ctx.currentTime,.4);
  },
  blip(amount){ // deploy chime, pitch by amount
    if(!this.ctx||this.muted)return;
    const now=performance.now(); if(now-this.lastBlip<70)return; this.lastBlip=now;
    const t=this.ctx.currentTime, o=this.ctx.createOscillator(), g=this.ctx.createGain();
    const f=520+140*Math.log10(1+amount*800);
    o.type='triangle'; o.frequency.value=f;
    g.gain.setValueAtTime(.09,t); g.gain.exponentialRampToValueAtTime(.0008,t+.18);
    o.connect(g); g.connect(this.master); o.start(t); o.stop(t+.2);
  },
  whale(){
    if(!this.ctx||this.muted)return;
    const _wn=performance.now(); if(_wn-(this._lastWhale||0)<300)return; this._lastWhale=_wn;
    const t=this.ctx.currentTime, o=this.ctx.createOscillator(), g=this.ctx.createGain();
    o.type='sine'; o.frequency.setValueAtTime(130,t); o.frequency.exponentialRampToValueAtTime(38,t+.45);
    g.gain.setValueAtTime(.22,t); g.gain.exponentialRampToValueAtTime(.0008,t+.5);
    o.connect(g); g.connect(this.master); o.start(t); o.stop(t+.55);
  },
  winner(){
    if(!this.ctx||this.muted)return;
    const notes=[220,261.63,293.66,329.63,392,440,523.25];
    notes.forEach((f,i)=>{
      const t=this.ctx.currentTime+i*.13, o=this.ctx.createOscillator(), g=this.ctx.createGain();
      o.type='triangle'; o.frequency.value=f;
      g.gain.setValueAtTime(0,t); g.gain.linearRampToValueAtTime(.12,t+.02);
      g.gain.exponentialRampToValueAtTime(.0008,t+.9);
      o.connect(g); g.connect(this.master); o.start(t); o.stop(t+1);
    });
  },
  toggleMute(){ this.muted=!this.muted;
    if(this.master) this.master.gain.value=this.muted?0:0.9; return this.muted; }
};

// ---------- particle pool ----------
// particle pool is per-engine (see constructor)

// ---------- engine ----------
class Engine{
  constructor(canvas, round, opts){
    this.silent=opts&&opts.silent;
    this.MAXP=420; this.pool=[]; for(let i=0;i<this.MAXP;i++) this.pool.push({on:false}); this._ring=0;
    this.cv=canvas; this.ctx=canvas.getContext('2d');
    this.round=round;
    if(!Number.isInteger(round.winner)||round.winner<0||round.winner>24) throw new Error('bad winner '+round.winner);
    // normalize timeline: ms offsets, stagger same-second events
    const evs=round.timeline.map(e=>({...e, ms:Date.parse(e.t)})).filter(e=>Number.isFinite(e.ms)&&Number.isFinite(e.s)&&e.q&&e.q.length&&e.q.every(q=>Number.isInteger(q)&&q>=0&&q<25));
    if(!evs.length) throw new Error('no valid events');
    let t0=Infinity; for(const e of evs) if(e.ms<t0) t0=e.ms;
    const perSec={};
    evs.forEach(e=>{ const s=Math.floor((e.ms-t0)/1000); perSec[s]=perSec[s]||[]; perSec[s].push(e); });
    Object.values(perSec).forEach(arr=>arr.forEach((e,i)=>{ e.off=(Math.floor((e.ms-t0)/1000)*1000)+ (i/arr.length)*1000; }));
    evs.sort((a,b)=>a.off-b.off);
    this.evs=evs; this.dur=Math.max(1000, evs[evs.length-1].off+1500); this.t0=t0;
    this.tileSol=new Float64Array(25); this.tileFlash=new Float64Array(25);
    this.totalSol=0; this.deploysShown=0; this.evIdx=0;
    this.t=0; this.playing=false; this.speed=2; this.phase='intro';
    this.phaseT=0; this.shockwaves=[]; this.leader=0;
    this.whaleAt=evs.map(e=>e.s/Math.max(1,e.q.length)).sort((a,b)=>a-b)[Math.floor(evs.length*0.95)]||0;
    this.resize();
  }
  resize(){
    const r=this.cv.parentElement.getBoundingClientRect();
    const dpr=Math.min(2,window.devicePixelRatio||1);
    this.w=r.width; this.h=Math.min(Math.round(r.width*1.12), 500); // DESIGN-OVERHAUL: taller cinematic frame
    this.cv.width=this.w*dpr; this.cv.height=this.h*dpr;
    this.cv.style.width=this.w+'px'; this.cv.style.height=this.h+'px';
    this.ctx.setTransform(dpr,0,0,dpr,0,0);
    const pad=14, gap=7, n=5;
    this.cell=(Math.min(this.w,this.h)-pad*2-gap*(n-1))/n;
   
    this.ox=(this.w-(this.cell*n+gap*(n-1)))/2; this.oy=pad+6;
  }
  tileXY(i){ const c=i%5,r=(i/5)|0;
    return [this.ox+c*(this.cell+7), this.oy+r*(this.cell+7)]; }
  tileCX(i){ const [x,y]=this.tileXY(i); return [x+this.cell/2, y+this.cell/2]; }
  reset(){ this.afterT=0; this.tileSol.fill(0); this.tileFlash.fill(0); this.totalSol=0;
    this.deploysShown=0; this.evIdx=0; this.t=0; this.shockwaves.length=0;
    this.pool.forEach(p=>p.on=false); this.phase='intro'; this.phaseT=0;
    this.leader=0; }
  spawn(x,y,vx,vy,life,size,hue,alpha,grav){
    let p=null;
    for(let k=0;k<this.MAXP;k++){ const i=(this._ring+k)%this.MAXP; if(!this.pool[i].on){ p=this.pool[i]; this._ring=(i+1)%this.MAXP; break; } }
    if(!p){ p=this.pool[this._ring]; this._ring=(this._ring+1)%this.MAXP; }
    p.on=true;p.x=x;p.y=y;p.vx=vx;p.vy=vy;p.life=life;p.maxLife=life;p.delay=0;
    p.size=size;p.hue=hue;p.alpha=alpha;p.grav=grav||0;
    p.wisp=false;p.sx=0;p.sy=0;p.tx=0;p.ty=0;
    return p;
  }
  play(){ if(!this.silent) Audio_.ensure(); this.playing=true; if(this.phase==='after') this.reset(), this.playing=true; }
  pause(){ this.playing=false; }
  seek(frac){ // 0..1 — rebuild state deterministically
    this.reset();
    const target=frac*this.dur;
    while(this.evIdx<this.evs.length && this.evs[this.evIdx].off<=target){ this.apply(this.evs[this.evIdx],true); this.evIdx++; }
    this.t=target; this.deploysShown=this.evIdx;
    if(frac>=1){ this.doReveal(true); this.phase='reveal'; this.phaseT=0; }
    else this.phase = frac>0.02?'haunt':'intro';
  }
  apply(e,silent){
    const per=e.s/Math.max(1,e.q.length);
    e.q.forEach(q=>{ this.tileSol[q]+=per; if(!silent) this.tileFlash[q]=1; });
    this.totalSol+=e.s; this.deploysShown++;
    let _mx=0,_mi=0; for(let _i=0;_i<25;_i++) if(this.tileSol[_i]>_mx){_mx=this.tileSol[_i];_mi=_i;}
    this.leader=_mi;
    if(!silent){
      const whale=(e.s/Math.max(1,e.q.length))>=this.whaleAt;
      e.q.forEach(q=>{
        const [cx,cy]=this.tileCX(q);
        const n=whale?5:2;
        for(let k=0;k<n;k++){
          const a=Math.random()*Math.PI*2, sp=20+Math.random()*60;
          this.spawn(cx+(Math.random()-.5)*this.cell*.5, cy+(Math.random()-.5)*this.cell*.5,
            Math.cos(a)*sp, Math.sin(a)*sp-30, .5+Math.random()*.4,
            whale?3.4:2.2, whale?45:160, .9, 60);
        }
        // incoming wisp from edge
        const ex=Math.random()<.5?(Math.random()<.5?-20:this.w+20):Math.random()*this.w;
        const ey=ex<0||ex>this.w?Math.random()*this.h:(Math.random()<.5?-20:this.h+20);
        const w=this.spawn(ex,ey,0,0,.7,2.6,160,.85,0); w.tx=cx; w.ty=cy; w.wisp=true; w.sx=ex; w.sy=ey;
      });
      if(!this.silent){ if(whale) Audio_.whale(); else Audio_.blip(e.s); }
    }
  }
  update(dt){
    // dt seconds
    if(this.playing && this.phase!=='reveal' && this.phase!=='after'){
      this.t+=dt*1000*this.speed;
      while(this.evIdx<this.evs.length && this.evs[this.evIdx].off<=this.t){
        this.apply(this.evs[this.evIdx],false); this.evIdx++;
      }
      const p=this.t/this.dur;
      if(!this.silent) Audio_.setTension(clamp((p-.7)/.3,0,1));
      if(p>=1){ this.phase='reveal'; this.phaseT=0; this.doReveal(); }
      else if(p>0.02&&this.phase==='intro'){this.phase='haunt';}
    }
    if(this.phase==='reveal'){
      this.phaseT+=dt;
      if(this.phaseT>2.6){ this.phase='after'; this.afterT=0; if(this.onAfter) this.onAfter(); }
    }
    if(this.phase==='after'){
      this.afterT=(this.afterT||0)+dt;
      if(this.afterT>4){ this.reset(); this.playing=true; if(this.onReset) this.onReset(); }
    }
    // decay flashes
    for(let i=0;i<25;i++) if(this.tileFlash[i]>0) this.tileFlash[i]=Math.max(0,this.tileFlash[i]-dt*2.2);
    // particles
    for(let i=0;i<this.MAXP;i++){ const p=this.pool[i]; if(!p.on)continue;
      if(p.delay>0){ p.delay-=dt; continue; }
      p.life-=dt;
      if(p.life<=0){p.on=false;continue;}
      if(p.wisp){
        const k=1-Math.pow(1-clamp(1-p.life/p.maxLife,0,1),2);
        p.x=lerp(p.sx,p.tx,easeInOut(k)); p.y=lerp(p.sy,p.ty,easeInOut(k));
      }else{
        p.vy+=(p.grav||0)*dt; p.x+=p.vx*dt; p.y+=p.vy*dt;
      }
    }
    // shockwaves
    for(let i=this.shockwaves.length-1;i>=0;i--){ const s=this.shockwaves[i];
      s.r+=dt*260; s.a-=dt*.8; if(s.a<=0)this.shockwaves.splice(i,1); }
  }
  doReveal(silent){
    if(!this.silent){ if(!silent) Audio_.winner(); Audio_.setTension(0); }
    const [cx,cy]=this.tileCX(this.round.winner);
    for(let k=0;k<3;k++) this.shockwaves.push({x:cx,y:cy,r:10+k*8,a:1});
    for(let k=0;k<90;k++){
      const a=Math.random()*Math.PI*2, sp=40+Math.random()*160;
      const p=this.spawn(cx,cy,Math.cos(a)*sp,Math.sin(a)*sp-60,1+Math.random()*1.2,2.5+Math.random()*2.5,45,.95,120);
      if(p) p.delay=Math.random()*0.6; // fountain, not flash
    }
  }
  draw(){
    const c=this.ctx; c.clearRect(0,0,this.w,this.h);
    let mx=.0001; for(let k=0;k<25;k++){ const _v=this.tileSol[k]; if(_v>mx) mx=_v; }
    const dimmed=this.phase==='reveal'||this.phase==='after';
    const cellStep=this.cell+7, ox=this.ox, oy=this.oy, cell=this.cell;
    const thumbFont=`700 ${Math.max(11,cell*.28)}px system-ui`;
    const valFont=`700 ${Math.max(9,cell*.16)}px system-ui`;
    const idxFont=`${Math.max(8,cell*.11)}px system-ui`;
    for(let i=0;i<25;i++){
      const x=ox+(i%5)*cellStep, y=oy+((i/5)|0)*cellStep;
      const heat=this.tileSol[i]/mx;
      const isWin=i===this.round.winner;
      let r,g,b;
      if(dimmed&&!isWin){ r=14;g=14;b=26; }
      else{ const hh=heat<0?0:heat>1?1:heat, f=hh*4, ii=f>=4?3:(f|0), t=f-ii;
        const A=HEAT_STOPS[ii], B=HEAT_STOPS[ii+1];
        r=(A[0]+(B[0]-A[0])*t)|0; g=(A[1]+(B[1]-A[1])*t)|0; b=(A[2]+(B[2]-A[2])*t)|0; }
      const fl=this.tileFlash[i];
      const rad=9;
      // OUTLINE STYLE (user, 2026-10-12): heat reads as a colored outline on a dark base —
      // no fill. Hotter tile = thicker, brighter edge; deploy flash brightens the edge.
      c.fillStyle=(dimmed&&!isWin)?'rgb(10,10,18)':'rgb(10,13,24)';
      c.beginPath(); c.roundRect(x,y,cell,cell,rad); c.fill();
      if(dimmed&&!isWin){ c.strokeStyle='rgba(96,96,140,.35)'; c.lineWidth=1.5; }
      else{
        c.strokeStyle=`rgb(${Math.min(255,r+fl*90)|0},${Math.min(255,g+fl*120)|0},${Math.min(255,b+fl*60)|0})`;
        c.lineWidth=1.5+heat*2.5+fl*1.5;
      }
      c.beginPath(); c.roundRect(x,y,cell,cell,rad); c.stroke();
      if(isWin&&(this.phase==='reveal'||this.phase==='after')){
        c.strokeStyle=`rgba(255,176,0,${.6+.4*Math.sin(performance.now()/180)})`;
        c.lineWidth=3; c.beginPath(); c.roundRect(x-2,y-2,this.cell+4,this.cell+4,11); c.stroke();
      }
      // label — adaptive contrast; simplified on small thumbnails for legibility
      const isThumb=cell<32; // thumbnail vs full player
      const v=this.tileSol[i];
      // DESIGN-OVERHAUL FIX: labels must draw EVERY frame — draw() clearRect()s the canvas
      // each frame, so the old dirty-check left values visible for a single frame then blank.
      // 25 fillTexts/frame is negligible; correctness beats the micro-optimization.
      const lblTxt=v>=10?v.toFixed(0):(isThumb?v.toFixed(1):v.toFixed(2));
      c.fillStyle=(dimmed&&!isWin)?'#6a6a8a':'#d8f5e8';
      c.textAlign='center';
      c.font=isThumb?thumbFont:valFont;
      c.strokeStyle='rgba(0,0,0,.5)'; c.lineWidth=2.5;
      c.strokeText(lblTxt, x+cell/2, y+cell/2+(isThumb?cell*.1:4));
      c.fillText(lblTxt, x+cell/2, y+cell/2+(isThumb?cell*.1:4));
      if(!isThumb){
        c.font=idxFont; c.textAlign='left';
        c.fillStyle=(dimmed&&!isWin)?'#4a4a6a':'rgba(216,216,240,.55)';
        c.fillText(i, x+5, y+12);
      }
    }
    // shockwaves
    for(let si=0;si<this.shockwaves.length;si++){ const s=this.shockwaves[si];
      c.strokeStyle=`rgba(255,176,0,${s.a<0?0:s.a>1?1:s.a})`; c.lineWidth=3;
      c.beginPath(); c.arc(s.x,s.y,s.r,0,Math.PI*2); c.stroke();
    }
    // particles
    for(let i=0;i<this.MAXP;i++){ const p=this.pool[i]; if(!p.on)continue;
      const a=p.alpha*clamp(p.life/p.maxLife,0,1);
      c.fillStyle=`hsla(${p.hue},100%,${p.wisp?70:60}%,${a})`;
      c.beginPath(); c.arc(p.x,p.y,p.size,0,Math.PI*2); c.fill();
    }
    // intro title card
    if(this.phase==='intro'){
      c.fillStyle='rgba(5,5,12,.72)'; c.fillRect(0,0,this.w,this.h);
      c.textAlign='center'; c.fillStyle='#d8d8f0';
      const _th=cell<32;
      c.font=_th?`700 ${Math.max(13,cell*.5)}px system-ui`:'700 26px system-ui';
      try{ c.letterSpacing='3px'; }catch(e){}
      c.fillText('ROUND #'+this.round.round, this.w/2, this.h/2-(_th?14:26));
      try{ c.letterSpacing='0px'; }catch(e){}
      c.fillStyle='#8a8ab0'; c.font=_th?`${Math.max(9,cell*.2)}px system-ui`:'13px system-ui';
      c.fillText(this.evs.length+' deploys', this.w/2, this.h/2+(_th?4:6));
    }
  }
}

window.HauntReplay={Engine,Audio:Audio_};
})();

</script>
<script>
const D=__DATA__;
const $=id=>document.getElementById(id);
// ==== Tiers & entitlements ====
// Free: top-25 leaderboard, 1 replay, core lenses. Paid (day/monthly/lifetime): everything.
// Preview with ?tier=free. Default 'lifetime' = founder preview; the backend enforces real entitlements.
const TIERS={
  free:{name:'Free', price:'$0'},
  day:{name:'Day pass', price:'$1 / 24h'},
  monthly:{name:'Monthly', price:'$5 / mo'},
  lifetime:{name:'Lifetime', price:'$50 once'},
  anomaly:{name:'Anomaly', price:'$7 / day'},
};
const ANOMALY_SLOTS=100; // slots capped per rolling 24h — server-side enforcement activates when the backend hosts
// ==== Payments (Solana Pay + on-chain verification) ====
// Set PAYMENTS.treasury to your treasury wallet address to go live.
// Until then, checkout shows an honest "not configured" state — no fake charges.
/* ================= CHAT MODULE =================
   A Discord-style floor for .skr holders, built as one self-contained module:
   channels, identity, storage, reactions and rendering all live here.
   TRANSPORT: local only in this build — messages persist in localStorage on
   this device (key haunt_chat_v1). Nothing here pretends otherwise: the UI
   says "local preview" and the radar channel is real dataset signals, not
   simulated people. A live backend transport slots in behind this boundary
   (swap load/save/send delivery) without touching call sites.
   Call sites: show('t-chat') -> ChatModule.render()                        */
const ChatModule={
  channels:[
    {id:'floor', name:'#mining-floor', post:true},
    {id:'strat', name:'#strategies', post:true},
    {id:'radar', name:'#anomaly-radar', post:false},
  ],
  cur:'floor',
  KEY:'haunt_chat_v1',
  RX:['🔥','⛏','👀'],
  drafts:{},   // per-channel composer drafts, session-only (scoped on switch)
  rxOpen:{},   // msgId -> true: reaction picker expanded this session
  vvOn:false,  // visualViewport keyboard handling armed
  load(){ try{ const d=JSON.parse(localStorage.getItem(this.KEY)||'{}');
    return (d&&typeof d==='object'&&!Array.isArray(d))?d:{}; }catch(_){ return {}; } },
  save(d){ try{ localStorage.setItem(this.KEY, JSON.stringify(d)); return true; }catch(_){ return false; } },
  chan(){ return this.channels.find(c=>c.id===this.cur)||this.channels[0]; },
  /* Stored data is UNTRUSTED: every message is validated + coerced before it
     can render. Bad entries are dropped — never thrown on, never rendered raw,
     and no stored field reaches HTML without escaping or numeric coercion. */
  cleanRx(rx){
    const out={};
    if(rx&&typeof rx==='object'&&!Array.isArray(rx)) for(const e of this.RX){
      const n=Math.floor(Number(rx[e]));
      if(Number.isFinite(n)&&n>0) out[e]=n;
    }
    return out;
  },
  cleanMsg(m){
    if(!m||typeof m!=='object'||Array.isArray(m)) return null;
    const id=(typeof m.id==='string'||typeof m.id==='number')?String(m.id).replace(/[^a-zA-Z0-9_-]/g,''):'';
    if(!id) return null;
    let txt=(typeof m.txt==='string')?m.txt:(typeof m.txt==='number'||typeof m.txt==='boolean')?String(m.txt):'';
    txt=txt.replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g,''); // strip unpaired surrogates
    const name=(typeof m.name==='string'&&m.name.trim())?m.name:'anon.skr';
    const av=(typeof m.av==='string'&&m.av&&[...m.av].length<=4)?m.av:'👻';
    const t=(typeof m.t==='number'&&Number.isFinite(m.t)&&m.t>0)?m.t:null;
    const me=this.RX.includes(m.me)?m.me:null;
    return {id,t,name,av,txt,rx:this.cleanRx(m.rx),me};
  },
  cleanList(raw){ if(!Array.isArray(raw)) return []; const out=[];
    for(const m of raw){ const c=this.cleanMsg(m); if(c) out.push(c); } return out; },
  identity(){
    const c=(typeof cmpLoad==='function')?cmpLoad():null;
    if(c&&c.trait&&CMP_TRAITS[c.trait]){ const T=CMP_TRAITS[c.trait];
      return {name:T.name.toLowerCase()+'.skr', av:T.icon, hatched:true}; }
    return {name:'you.skr', av:'👻', hatched:false};
  },
  msgs(ch){ const d=this.load(); return this.cleanList(d[ch]); },
  feed(){
    // real anomaly signals as system messages, oldest -> newest, latest 30
    try{
      const sigs=AnomalyModule.signals().slice().sort((a,b)=>a.r-b.r).slice(-30);
      return sigs.map(sg=>{
        const rd=(D.rounds.recent||[]).find(x=>x.r===sg.r);
        const rt=rd&&typeof rtime==='function'?rtime(rd):null;
        return { sys:true, r:sg.r, av:'⚡', name:'haunt-radar',
          t: rt?rt.t:null, est: rt?!rt.real:false,
          txt:`Round #${sg.r} · ${sg.icon} ${sg.label} · ${sg.sev} — ${sg.detail}` };
      });
    }catch(_){ return []; }
  },
  feedTotal(){ try{ return AnomalyModule.signals().length; }catch(_){ return 0; } },
  noteOff(){ try{ return localStorage.getItem(this.KEY+'_note')==='1'; }catch(_){ return false; } },
  hideNote(){ try{ localStorage.setItem(this.KEY+'_note','1'); }catch(_){}
    const n=$('chatNote'); if(n) n.classList.add('hidden'); },
  draft(){ const inp=$('chatInput'); if(inp) this.drafts[this.cur]=inp.value; },
  go(id){ if(id===this.cur) return; this.cur=id; this.render(); },
  note(msg, isErr){ const e=$('chatErr'); if(!e) return;
    e.textContent=msg; e.classList.toggle('hint', !isErr); e.classList.remove('hidden');
    clearTimeout(this._noteT);
    if(!isErr) this._noteT=setTimeout(()=>{ e.classList.add('hidden'); }, 2400); },
  hideErr(){ const e=$('chatErr'); if(e) e.classList.add('hidden'); clearTimeout(this._noteT); },
  fadeCheck(){ const w=$('chatChWrap'), c=$('chatChips'); if(!w||!c) return;
    w.classList.toggle('more', c.scrollWidth>c.clientWidth+4 && c.scrollLeft<c.scrollWidth-c.clientWidth-8);
    if(!c.dataset.fadeBound){ c.dataset.fadeBound='1';
      c.addEventListener('scroll',()=>this.fadeCheck(),{passive:true}); } },
  watchVV(){ if(this.vvOn||!window.visualViewport) return; this.vvOn=true;
    const vv=window.visualViewport;
    vv.addEventListener('resize',()=>{ try{
      const comp=$('chatComposer'); if(!comp||comp.classList.contains('hidden')) return;
      const kb=Math.max(0, Math.round(window.innerHeight-vv.height-(vv.offsetTop||0)));
      if(kb>60){ comp.style.bottom=kb+'px'; // overlay keyboard: composer rides above it
        setTimeout(()=>window.scrollTo(0, document.body.scrollHeight), 60); }
      else comp.style.bottom=''; // layout-resizing shell: the fixed default already tracks
    }catch(_){} }); },
  render(){
    const chips=$('chatChips'); if(!chips) return;
    try{
      const idn=this.identity();
      const who=idn.hatched
        ? `<span class="chat-who">Posting as <b>${esc(idn.name)}</b> ${idn.av} · +2 XP/msg (max +10/day)</span>`
        : `<span class="chat-who">Posting as <b>you.skr</b> 👻 · <a href="#" onclick="show('t-ghost');return false" style="color:var(--gold)">hatch your ghost</a></span>`;
      chips.innerHTML=this.channels.map(c=>
        `<button class="chip${c.id===this.cur?' on':''}" onclick="ChatModule.go('${c.id}')">${c.name}</button>`).join('')+who;
      const note=$('chatNote'); if(note) note.classList.toggle('hidden', this.noteOff());
      this.renderList();
      const comp=$('chatComposer');
      if(comp){ comp.classList.toggle('hidden', !this.chan().post);
        const inp=$('chatInput'); if(inp){ inp.placeholder='Message '+this.chan().name;
          inp.value=this.drafts[this.cur]||''; } }
      this.watchVV();
      this.fadeCheck();
    }catch(_){ // a broken store must never brick the tab
      const el=$('chatList'); if(el) el.innerHTML=`<div class="chat-empty">🦇<br>The floor glitched — your stored messages could not be read.</div>`;
      const comp=$('chatComposer'); if(comp) comp.classList.toggle('hidden', !this.chan().post);
    }
  },
  renderList(){
    const el=$('chatList'); if(!el) return;
    try{
      const ch=this.chan();
      if(!ch.post){
        const f=this.feed(); const total=this.feedTotal()||f.length;
        el.innerHTML=(f.length?f.map(m=>this.msgHTML(m,true)).join('')
          : `<div class="chat-empty">⚡<br>No signals scored yet.</div>`)
          +`<div class="chat-empty" style="padding:10px 16px 0">⚡ Latest ${f.length} of ${total} signals — tap a signal to open the round. Read-only.</div>`;
        return;
      }
      const ms=this.msgs(ch.id);
      el.innerHTML=ms.length?ms.map((m,i)=>this.msgHTML(m,false,i)).join('')
        : `<div class="chat-empty">🦇<br>Nothing here yet — the first voice on the floor is yours.</div>`;
    }catch(_){
      el.innerHTML=`<div class="chat-empty">🦇<br>The floor glitched — your stored messages could not be read.</div>`;
    }
  },
  msgHTML(m, feed, idx){
    const idn=this.identity();
    const mid=String(m.id||'').replace(/[^a-zA-Z0-9_-]/g,'');
    const mine=!feed&&m.name===idn.name;
    let timeLbl='';
    if(feed) timeLbl=m.t?(m.est?'~':'')+fmtT(m.t).slice(0,5):'';
    else if(m.t){ const dt=new Date(m.t); if(!isNaN(dt)) timeLbl=dt.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:TZ}); }
    // Counts arrive coerced via cleanMsg; coerce again here so no stored value
    // can ever reach the HTML raw (the reaction-count XSS hole stays shut).
    const cnt=e=>{ const n=Math.floor(Number(m.rx&&m.rx[e])); return Number.isFinite(n)&&n>0?n:0; };
    const hasRx=!feed&&this.RX.some(e=>cnt(e)>0);
    const rx=(feed||(!hasRx&&!this.rxOpen[mid]))?'':`<div class="chat-rx">${this.RX.map(e=>{
      const n=cnt(e);
      return `<button class="rx-chip${m.me===e?' on':''}" onclick="ChatModule.toggleRx('${mid}','${e}')">${e}${n?' '+n:''}</button>`;}).join('')}</div>`;
    const rxadd=(!feed&&!hasRx&&!this.rxOpen[mid])
      ? `<button class="chat-rxadd" onclick="ChatModule.openRx('${mid}')" aria-label="Add reaction">＋</button>`:'';
    const del=(!feed&&mine)?`<button class="chat-del" data-idx="${idx==null?'':idx}" onclick="ChatModule.del('${mid}',this.dataset.idx)" aria-label="Delete message">✕</button>`:'';
    const ctl=(rxadd||del)?`<span class="chat-ctl">${rxadd}${del}</span>`:'';
    const click=feed?` onclick="showIntel('explore');roundDetail(${m.r})"`:'';
    return `<div class="chat-msg${feed?' feed':''}"${click}>
      <div class="chat-av">${esc(typeof m.av==='string'&&m.av?m.av:'👻')}</div>
      <div class="chat-body">
        <div class="chat-meta"><span class="chat-name${feed?' sys':mine?' mine':''}">${esc(m.name||'anon.skr')}</span>`
        +(timeLbl?`<span class="chat-time">${timeLbl}</span>`:'')+ctl+(feed?`<span style="margin-left:auto;color:var(--faint);font-size:20px;line-height:1">›</span>`:'')+`</div>
        <div class="chat-txt">${esc(m.txt||'')}</div>${rx}
      </div></div>`;
  },
  openRx(id){ this.rxOpen[id]=true; this.renderList(); },
  send(){
    const inp=$('chatInput'); if(!inp) return;
    const ch=this.chan(); if(!ch.post) return;
    const raw=inp.value, txt=raw.trim();
    if(!txt){ // whitespace-only: visible feedback instead of a mute no-op
      if(raw){ inp.classList.remove('chat-shake'); void inp.offsetWidth; inp.classList.add('chat-shake');
        this.note('Nothing to send — whitespace does not count. Type a message first. 👻', false); }
      return; }
    const idn=this.identity();
    const d=this.load(); const list=this.cleanList(d[ch.id]);
    const cps=[...txt]; // cap by code POINTS — truncation never splits an emoji
    list.push({id:'m'+Date.now().toString(36)+Math.floor(Math.random()*1e6).toString(36), t:Date.now(), name:idn.name, av:idn.av, txt:cps.length>500?cps.slice(0,500).join(''):txt, rx:{}, me:null});
    d[ch.id]=list.length>200?list.slice(-200):list;
    // companion XP: +2 per message, capped +10/day (anti-farm)
    const today=new Date().toDateString();
    if(!d.meta||typeof d.meta!=='object'||Array.isArray(d.meta)||d.meta.day!==today) d.meta={day:today, xp:0};
    if(typeof d.meta.xp!=='number'||!(d.meta.xp>=0)) d.meta.xp=0;
    if(d.meta.xp<10){ d.meta.xp+=2; if(typeof cmpXP==='function') cmpXP(2); }
    if(!this.save(d)){ // storage full/denied: the words stay in the box, error shows
      this.note('Could not save — this device\u2019s storage is full. Your message is still in the box: free up space, then send again.', true);
      window.scrollTo(0, document.body.scrollHeight);
      return; }
    this.hideErr();
    this.drafts[ch.id]=''; inp.value='';
    this.renderList();
    window.scrollTo(0, document.body.scrollHeight);
  },
  toggleRx(id, e){
    const ch=this.chan(); if(!ch.post) return;
    const d=this.load(); const list=this.cleanList(d[ch.id]);
    const m=list.find(x=>x.id===id); if(!m) return;
    m.rx=m.rx||{};
    if(m.me===e){ m.rx[e]=Math.max(0,(m.rx[e]||0)-1); if(!m.rx[e]) delete m.rx[e]; m.me=null; }
    else{ if(m.me){ m.rx[m.me]=Math.max(0,(m.rx[m.me]||0)-1); if(!m.rx[m.me]) delete m.rx[m.me]; } m.rx[e]=(m.rx[e]||0)+1; m.me=e; }
    d[ch.id]=list;
    this.save(d); this.renderList();
  },
  del(id, idx){
    const ch=this.chan(); if(!ch.post) return;
    const d=this.load(); const list=this.cleanList(d[ch.id]);
    let i=(idx===''||idx==null)?-1:parseInt(idx,10);
    if(!(i>=0&&i<list.length&&list[i].id===id)) i=list.findIndex(x=>x.id===id);
    if(i<0) return;
    list.splice(i,1); // exactly ONE message — even when another shares the id
    d[ch.id]=list;
    this.save(d); this.renderList();
  },
};

const PAYMENTS={
  treasury:'FRShnxX7vi64gtBoA6JPmH9MBuVqtheEQaxA1D17wKdP', // owner's wallet — resolved on-chain from raaru.skr (AllDomains ANS) 2026-10-12; payments are LIVE with on-device verification (backend:null below; server-side verification activates when backend is hosted and set here)
  backend:null, // haunt-backend base URL (e.g. 'https://api.example.com') — set to verify payments server-side; null = on-device verification as before
  oreMint:'oreoU2P8bN6jkk3jbaiVxYnG1dCXcYxwhwyK9jSybcp', // ORE (current mint, 11 dp) — re-verify before going live
  oreDecimals:11,
  skrMint:'SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3', // SKR (Seeker ecosystem token, 6 dp) — triple-verified 2026-10-12: Solana Mobile docs + Jupiter verified registry
  skrDecimals:6,
  rpc:'https://api.mainnet-beta.solana.com',
  usd:{day:1, monthly:5, lifetime:50, anomaly:7},
  durMs:{day:864e5, monthly:30*864e5, lifetime:0, anomaly:864e5}, // 0 = never expires
};
// Prices: Jupiter Price API v3 (lite endpoint). The old price.jup.ag/v6
// host no longer answers; v3 returns {<mint>:{usdPrice:<number>}}.
const SOL_MINT='So11111111111111111111111111111111111111112'; // wrapped SOL
let _solUsd=0, _solLive=false;
async function solPrice(){
  if(_solUsd>0) return _solUsd;
  try{
    const r=await fetch('https://lite-api.jup.ag/price/v3?ids='+SOL_MINT);
    const j=await r.json();
    const e=j[SOL_MINT];
    _solUsd=e&&e.usdPrice||0;
    _solLive=_solUsd>0;
  }catch(_){}
  if(!(_solUsd>0)){ _solUsd=180; _solLive=false; } // fallback estimate
  return _solUsd;
}
let _oreUsd=0;
async function orePrice(){
  if(_oreUsd>0) return _oreUsd;
  try{
    const r=await fetch('https://lite-api.jup.ag/price/v3?ids='+PAYMENTS.oreMint);
    const j=await r.json();
    const e=j[PAYMENTS.oreMint];
    _oreUsd=e&&e.usdPrice||0;
  }catch(_){}
  return _oreUsd; // 0 = unavailable — no invented fallback for ORE
}
let _skrUsd=0;
async function skrPrice(){
  if(_skrUsd>0) return _skrUsd;
  try{
    const r=await fetch('https://lite-api.jup.ag/price/v3?ids='+PAYMENTS.skrMint);
    const j=await r.json();
    const e=j[PAYMENTS.skrMint];
    _skrUsd=e&&e.usdPrice||0;
  }catch(_){}
  return _skrUsd; // 0 = unavailable — no invented fallback for SKR
}
function payLoad(){ try{ return JSON.parse(localStorage.getItem('haunt_purchases')||'{}'); }catch(_){ return {}; } }
function paySave(p){ try{ localStorage.setItem('haunt_purchases', JSON.stringify(p)); }catch(_){} }
function activePurchase(){
  const p=payLoad(), now=Date.now();
  let best=null;
  for(const t of ['anomaly','lifetime','monthly','day']){
    const e=p[t];
    if(e && (e.expires===0 || e.expires>now)){ best=t; break; }
  }
  return best;
}
const PREMIUM_FEATURES=['simulator','taxcsv','fullboard','allreplays','alldossiers','similar','compare','consensus','costperwin','weekly'];
const Entitlements={
  tier:(()=>{ try{ const q=new URLSearchParams(location.search).get('tier'); return TIERS[q]?q:'lifetime'; }catch(e){ return 'lifetime'; } })(),
  isPaid(){ return this.tier!=='free'; },
  isAnomaly(){ return this.tier==='anomaly'; },
  can(f){
    const ap=(typeof activePurchase==='function')?activePurchase():null;
    if(f==='anomaly') return this.isAnomaly()||ap==='anomaly';
    if(ap) return true;
    return this.isPaid()||PREMIUM_FEATURES.indexOf(f)<0; },
};
function lockedCard(feature, title, blurb){
  return `<div class="card"><div class="eyebrow">🔒 ${esc(title)}</div>`
    +`<p style="color:var(--dim);font-size:14px;margin:8px 0">${esc(blurb)}</p>`
    +`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">`
    +Object.keys(TIERS).filter(t=>t!=='free').map(t=>`<span class="chip" style="cursor:default">${TIERS[t].name} · ${TIERS[t].price}</span>`).join('')
    +`</div><button class="btn" style="width:100%;margin-top:12px" onclick="openCheckout()">🔓 Unlock with a pass</button></div>`;
}
const short=w=>w.slice(0,4)+'…'+w.slice(-4);
let _intelSub='explore';
function showIntel(sub){
  _intelSub=sub||_intelSub||'explore';
  show('t-rounds');
  ['explore','sim','radar'].forEach(v=>{
    const p=$('pane-'+v); if(p) p.classList.toggle('hidden', v!==_intelSub);
    const c=$('ri-'+v); if(c) c.classList.toggle('on', v===_intelSub);
  });
  if(_intelSub==='sim') renderLab();
  if(_intelSub==='radar') renderAnomaly();
  window.scrollTo(0,0);
}
function show(id){
const wasReplay=!$('t-replay').classList.contains('hidden')||!$('t-view').classList.contains('hidden');
const wasView=!$('t-view').classList.contains('hidden');
if(wasView&&id!=='t-view'){ stopReplay(); }
else if(wasReplay&&id!=='t-replay'&&id!=='t-view'){ stopReplay(); }
['t-intel','t-profile','t-compare','t-replay','t-view','t-rounds','t-ghost','t-about','t-chat'].forEach(t=>{const el=$(t); if(!el) return; el.classList.toggle('hidden', t!==id);});
{const _sel=$(id); if(_sel){_sel.classList.remove('page-enter'); void _sel.offsetWidth; _sel.classList.add('page-enter');}}
if(typeof closeStatShare==='function') closeStatShare();
document.querySelectorAll('nav button').forEach(b=>b.classList.remove('on'));
const n={ 't-intel':'n-intel','t-profile':'n-intel','t-compare':'n-intel','t-replay':'n-replay','t-view':'n-replay','t-rounds':'n-rounds','t-ghost':'n-ghost','t-about':'n-ghost','t-chat':'n-chat'}[id];if($(n))$(n).classList.add('on');window.scrollTo(0,0);
if(id==='t-chat'&&typeof ChatModule!=='undefined') ChatModule.render();
if(id==='t-view'&&!ENG&&!_replayFailed){ const _ord=replayOrder(); openReplay(currentRound||(_ord.length?_ord[0]:null)); return; }
if(id==='t-view'&&ENG&&!ENG.playing){ ENG.play();
miniENGs.forEach(e=>{try{if(!e.playing)e.play();}catch(_){}});}}
// ---- leaderboard + watchlist + strategy (session-only) ----
let _sort='sol', _whaleCut=null;
function whaleCut(){ if(_whaleCut==null){ const ss=(D.leaders.leaders||[]).map(L=>L.s).sort((a,b)=>a-b); _whaleCut=ss.length?ss[Math.floor(ss.length*0.9)]:Infinity; } return _whaleCut; }
// concentration of a miner's fingerprint: max tile share of total
function fpConc(w){ const F=D.fp[w]; if(!F||!F.t) return 0; const tot=F.t.reduce((a,v)=>a+v,0); return tot>0?Math.max(...F.t)/tot:0; }
// strategy tag: whale (top 10% SOL) > sniper (>=50% on one tile) > spreader (<=15% max) > balanced
function stratTag(L){ if(L.s>=whaleCut()) return ['🐋 Whale','tag-gold']; const F=D.fp[L.w];
  if(!F||!F.t) return ['—','']; const c=fpConc(L.w);
  if(c>=0.5) return ['🎯 Sniper','tag-violet']; if(c<=0.15) return ['🕸 Spreader','tag-green']; return ['⚖ Balanced','']; }
function setSort(k){ if(k==='expo'&&!Entitlements.can('anomaly')){ show('t-about'); return; } _sort=k; document.querySelectorAll('.sortrow .chip[data-sort]').forEach(b=>b.classList.toggle('on', b.dataset.sort===k)); renderLeaderboard(); }
function sortVal(L){ switch(_sort){
  case 'deploys': return -L.n;
  case 'eff': return -(L.n?L.s/L.n:0);
  case 'sniper': return -fpConc(L.w);
  case 'spreader': return fpConc(L.w);
  case 'expo': { const e=D.expo&&D.expo[L.w]; return (e&&e.p>=5)?-((e.a+2)/(e.p+8)):1; } // shrunk rate: small samples can't outrank proven ones
  default: return -L.s; } }
function lookupWallet(){
  const inp=$('walletSearch'), out=$('walletResult');
  const w=(inp.value||'').trim();
  if(!w){ out.innerHTML=''; return; }
  const L=(D.leaders.leaders||[]).find(x=>x.w===w||x.w.toLowerCase()===w.toLowerCase());
  const c=(typeof cmpLoad==='function')?cmpLoad():null;
  const hatchBtn=(c&&c.w&&(c.w===w||c.w.toLowerCase()===w.toLowerCase()))
    ? `<div class="note" style="text-align:center">👻 Companion already hatched.</div><button class="btn-ghost" style="width:100%;margin-top:8px;min-height:44px" onclick="show('t-ghost')">View your ghost</button>`
    : `<button class="btn" style="width:100%;margin-top:10px" data-w="${esc(w)}" onclick="cmpHatch(this.dataset.w)">🥚 Hatch companion from this wallet</button>`;
  if(L){ out.innerHTML=hatchBtn; profile(L.w); return; }
  out.innerHTML='<div class="note">No dossier for this wallet — outside the top 150 in our window. Only tracked miners have dossiers.</div>'+hatchBtn;
}

function expoChip(L){
  const e=D.expo&&D.expo[L.w];
  if(!e||e.a<3||e.rate<0.4) return '';
  return Entitlements.can('anomaly')
    ? `<span class="tag tag-violet" style="margin-left:6px">⚡ ${Math.round(e.rate*100)}% anomalous</span>`
    : `<span class="tag" style="margin-left:6px">⚡ anomaly-linked</span>`;
}
function renderLeaderboard(){
const el=$('leaderList'); if(!el) return;
try{
el.innerHTML='';
const _gRank={}; [...(D.leaders.leaders||[])].sort((a,b)=>b.s-a.s).forEach((L,gi)=>{_gRank[L.w]=gi+1;});
let rows=[...(D.leaders.leaders||[])].sort((a,b)=>sortVal(a)-sortVal(b));
if(!Entitlements.can('fullboard')) rows=rows.slice(0,25);
rows.forEach((L,i)=>{
const d=document.createElement('div'); d.className='row';
const _gr=_gRank[L.w]||(i+1);
const _rc=_gr<=3?' top'+_gr:'';
const [tagTxt,tagCls]=stratTag(L);
const eff=L.n?(L.s/L.n):0;
d.innerHTML=`<div class="rank${_rc}" title="rank #${_gr} by SOL">${_gr}</div>`
 +`<div style="flex:1 1 auto;min-width:0"><div class="waddr waddr-1">${esc(short(L.w))}</div>`
 +`<div style="margin-top:4px"><span class="tag ${tagCls}">${tagTxt}</span>${expoChip(L)}</div></div>`
 +`<div style="text-align:right;flex:0 0 auto"><div class="row-big">${L.s.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} <span class="row-unit">SOL</span></div><div class="row-sub">${eff.toFixed(2)} SOL/deploy</div></div>`
;
d.onclick=()=>profile(L.w); el.appendChild(d);});
if(!Entitlements.can('fullboard')) el.insertAdjacentHTML('beforeend','<div class="note" style="text-align:center">Showing top 25 — <b>125 more</b> with a Day pass.</div>');
}catch(e){ el.innerHTML='<div class="note">Leaderboard unavailable.</div>'; }}
renderLeaderboard();
// ==== Checkout ====
let _payTier='day', _paySol=0, _payCur='SOL', _payOre=0, _paySkr=0;
// In-flight guard for the pay buttons: while a quote/pay request is in
// flight, further taps are ignored and the checkout's buttons are
// disabled — a double-tap must not fire two backend quotes (for the
// anomaly tier, two quotes would burn two scarce slot holds).
let _payInFlight=false;
function _payBusy(on){
  _payInFlight=on;
  document.querySelectorAll('#payBody button').forEach(b=>{ b.disabled=on; });
}
function openCheckout(){
  const m=$('payModal'); if(!m) return;
  m.classList.remove('hidden');
  renderPayTiers();
}
function closeCheckout(){ const m=$('payModal'); if(m) m.classList.add('hidden'); }
async function renderPayTiers(){
  const b=$('payBody'); if(!b) return;
  const rate=await solPrice();
  const oreRate=_payCur==='ORE'?await orePrice():0;
  const skrRate=_payCur==='SKR'?await skrPrice():0;
  const tiers=['day','monthly','lifetime','anomaly'];
  const oreOk=_payCur!=='ORE'||oreRate>0;
  const skrOk=_payCur!=='SKR'||skrRate>0;
  const amt=t=>_payCur==='ORE'?(oreRate>0?(PAYMENTS.usd[t]/oreRate):null):_payCur==='SKR'?(skrRate>0?(PAYMENTS.usd[t]/skrRate):null):(PAYMENTS.usd[t]/rate);
  b.innerHTML=`<div style="display:flex;gap:8px;margin-bottom:12px">`
  +`<button class="chip${_payCur==='SOL'?' on':''}" style="flex:1;min-height:44px" onclick="_payCur='SOL';renderPayTiers()">◎ SOL</button>`
  +`<button class="chip${_payCur==='ORE'?' on':''}" style="flex:1;min-height:44px" onclick="_payCur='ORE';renderPayTiers()">⛏ ORE</button>`
  +`<button class="chip${_payCur==='SKR'?' on':''}" style="flex:1;min-height:44px" onclick="_payCur='SKR';renderPayTiers()">💎 SKR</button></div>`
  +tiers.map(t=>{
    const a=amt(t);
    const sub=t==='anomaly'?' · 100 slots/day':t==='lifetime'?' · one payment':t==='monthly'?' · all features':' · all features';
    return `<button class="pay-tier${t===_payTier?' on':''}" onclick="_payTier='${t}';renderPayTiers()">`
    +`<span><b>${TIERS[t].name}</b><br><span class="pay-sol">${TIERS[t].price}${sub}</span></span>`
    +`<span class="pt-price">${a===null?'—':a.toFixed(4)+' '+_payCur}</span></button>`;
  }).join('')
  +(!PAYMENTS.treasury
    ? `<div class="note" style="margin-top:12px">Payments aren't configured in this build yet — no charge will happen. Set <b>PAYMENTS.treasury</b> in the source to go live.</div>`
    : (!oreOk||!skrOk)
    ? `<div class="note" style="margin-top:12px">Couldn't fetch the ${_payCur} price just now — switch to SOL or try again in a moment.</div>`
    : `<button class="btn" style="width:100%;margin-top:12px" onclick="payNow()">Pay ${amt(_payTier).toFixed(4)} ${_payCur}</button>
       <div class="fineprint" style="margin-top:8px">Opens your Solana wallet. We verify the payment on-chain before unlocking.</div>`)
  +(_payCur==='ORE'
    ? (oreRate>0?`<div class="fineprint" style="margin-top:8px">Pay with what you mine · 1 ORE ≈ $${oreRate.toFixed(2)} (live rate)</div>`:`<div class="fineprint" style="margin-top:8px">Pay with what you mine · ORE price unavailable right now.</div>`)
    : _payCur==='SKR'
    ? (skrRate>0?`<div class="fineprint" style="margin-top:8px">The Seeker ecosystem token · 1 SKR ≈ $${skrRate.toFixed(4)} (live rate)</div>`:`<div class="fineprint" style="margin-top:8px">The Seeker ecosystem token · SKR price unavailable right now.</div>`)
    : `<div class="fineprint" style="margin-top:8px">1 SOL ≈ $${rate.toFixed(0)} (${_solLive?'live rate':'estimate'})</div>`);
}
// ==== haunt-backend quotes (opt-in via PAYMENTS.backend) ====
// Quote-first pay flow: the backend prices the tier and issues the memo,
// so the payment can settle server-side (consumed-signature ledger,
// entitlement granted to the payer bound from the chain). Non-anomaly
// quotes are anonymous — this client has no connected wallet. ANOMALY
// quotes are the exception: the backend now requires a wallet for them
// (slot holds are bound to an identity + source IP so they can't be
// rotated for free), so an anomaly quote from this client is answered
// {error:'wallet_required'} and — like ANY quote failure — falls back
// silently to the local memo+amount flow, until Seed Vault wallet
// binding lands on the client. EXCEPT a sold-out anomaly quote: falling
// back there would bypass the rolling-24h slot cap, so the checkout
// shows the honest sold-out state instead.
async function backendQuote(tier, currency){
  if(!PAYMENTS.backend) return null;
  try{
    const r=await fetch(String(PAYMENTS.backend).replace(/\/+$/,'')+'/v1/pay/quote',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({tier, currency})});
    const j=await r.json().catch(()=>null);
    if(!j) return null;
    if(j.error==='sold_out') return {soldOut:true, next_free_at:j.next_free_at||null};
    // wallet_required (anomaly without a bound wallet) and every other
    // error land here: return null -> caller falls back to the local flow.
    if(!r.ok||j.error||!j.memo||!j.request_id||!(j.amount>0)) return null;
    return j;
  }catch(_){ return null; }
}
function paySoldOut(nextFree){
  const b=$('payBody'); if(!b) return;
  const when=nextFree?new Date(nextFree*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):null;
  b.innerHTML=`<div class="note" style="text-align:center">⚡ <b>${TIERS.anomaly.name} passes are sold out</b> — all ${ANOMALY_SLOTS} rolling-24h slots are taken.${when?`<br>Next slot frees around <b>${when}</b>.`:''}</div>`
  +`<button class="btn-ghost" style="width:100%;margin-top:8px" onclick="renderPayTiers()">← Back</button>`;
}
function payNow(){
  if(!PAYMENTS.treasury) return;
  if(_payInFlight) return; // a quote/pay request is already in flight
  if(_payCur==='ORE'){ payNowOre(); return; }
  if(_payCur==='SKR'){ payNowSkr(); return; }
  _payBusy(true);
  (async()=>{
    try{
    let amt=null, memo=null, req=null;
    const q=await backendQuote(_payTier,'SOL');
    if(q&&q.soldOut){
      if(_payTier==='anomaly'){ paySoldOut(q.next_free_at); return; }
      // sold_out is anomaly-only server-side; anything else falls back.
    } else if(q){ amt=q.amount; memo=q.memo; req=q.request_id; }
    if(amt===null){
      const rate=await solPrice();
      amt=PAYMENTS.usd[_payTier]/rate;
      memo=`haunt:${_payTier}:${Date.now()}`;
    }
    _paySol=amt;
    const pend={tier:_payTier, sol:amt, memo, t:Date.now()};
    if(req){ pend.backend=true; pend.req=req; }
    try{ sessionStorage.setItem('haunt_pending', JSON.stringify(pend)); }catch(_){}
    const url=`solana:${PAYMENTS.treasury}?amount=${amt.toFixed(6)}&memo=${encodeURIComponent(memo)}`;
    const b=$('payBody');
    b.innerHTML=`<div style="text-align:center;padding:8px 0">`
    +`<div style="font-size:40px;margin-bottom:12px">👛</div>`
    +`<p style="font-size:15px;line-height:1.7">Opening your wallet…<br>Approve <b>${amt.toFixed(4)} SOL</b> for <b>${TIERS[_payTier].name}</b>.</p>`
    +`<button class="btn" style="width:100%;margin-top:12px" onclick="verifyPay()">I've paid — verify</button>`
    +`<button class="btn-ghost" style="width:100%;margin-top:8px" onclick="renderPayTiers()">← Back</button></div>`;
    location.href=url;
    } finally { _payBusy(false); } // re-enable on EVERY exit path
  })();
}

async function payNowOre(){
  if(!PAYMENTS.treasury) return;
  if(_payInFlight) return; // a quote/pay request is already in flight
  _payBusy(true);
  try{
  let amt=null, memo=null, req=null;
  const q=await backendQuote(_payTier,'ORE');
  if(q&&q.soldOut){
    if(_payTier==='anomaly'){ paySoldOut(q.next_free_at); return; }
    // sold_out is anomaly-only server-side; anything else falls back.
  } else if(q){ amt=q.amount; memo=q.memo; req=q.request_id; }
  if(amt===null){
    const rate=await orePrice();
    if(!(rate>0)){ renderPayTiers(); return; }
    amt=PAYMENTS.usd[_payTier]/rate;
    memo=`haunt:${_payTier}:${Date.now()}`;
  }
  _payOre=amt;
  const pend={tier:_payTier, cur:'ORE', amt, memo, t:Date.now()};
  if(req){ pend.backend=true; pend.req=req; }
  try{ sessionStorage.setItem('haunt_pending', JSON.stringify(pend)); }catch(_){}
  const url=`solana:${PAYMENTS.treasury}?amount=${amt.toFixed(6)}&spl-token=${PAYMENTS.oreMint}&memo=${encodeURIComponent(memo)}&label=${encodeURIComponent('ORE HAUNT')}`;
  const b=$('payBody');
  b.innerHTML=`<div style="text-align:center;padding:8px 0">`
  +`<div style="font-size:40px;margin-bottom:12px">⛏</div>`
  +`<p style="font-size:15px;line-height:1.7">Opening your wallet…<br>Approve <b>${amt.toFixed(4)} ORE</b> for <b>${TIERS[_payTier].name}</b>.</p>`
  +`<button class="btn" style="width:100%;margin-top:12px" onclick="verifyPay()">I've paid — verify</button>`
  +`<button class="btn-ghost" style="width:100%;margin-top:8px" onclick="renderPayTiers()">← Back</button></div>`;
  location.href=url;
  } finally { _payBusy(false); } // re-enable on EVERY exit path
}
async function payNowSkr(){
  if(!PAYMENTS.treasury) return;
  if(_payInFlight) return; // a quote/pay request is already in flight
  _payBusy(true);
  try{
  let amt=null, memo=null, req=null;
  const q=await backendQuote(_payTier,'SKR');
  if(q&&q.soldOut){
    if(_payTier==='anomaly'){ paySoldOut(q.next_free_at); return; }
    // sold_out is anomaly-only server-side; anything else falls back.
  } else if(q){ amt=q.amount; memo=q.memo; req=q.request_id; }
  if(amt===null){
    const rate=await skrPrice();
    if(!(rate>0)){ renderPayTiers(); return; }
    amt=PAYMENTS.usd[_payTier]/rate;
    memo=`haunt:${_payTier}:${Date.now()}`;
  }
  _paySkr=amt;
  const pend={tier:_payTier, cur:'SKR', amt, memo, t:Date.now()};
  if(req){ pend.backend=true; pend.req=req; }
  try{ sessionStorage.setItem('haunt_pending', JSON.stringify(pend)); }catch(_){}
  const url=`solana:${PAYMENTS.treasury}?amount=${amt.toFixed(6)}&spl-token=${PAYMENTS.skrMint}&memo=${encodeURIComponent(memo)}&label=${encodeURIComponent('ORE HAUNT')}`;
  const b=$('payBody');
  b.innerHTML=`<div style="text-align:center;padding:8px 0">`
  +`<div style="font-size:40px;margin-bottom:12px">💎</div>`
  +`<p style="font-size:15px;line-height:1.7">Opening your wallet…<br>Approve <b>${amt.toFixed(4)} SKR</b> for <b>${TIERS[_payTier].name}</b>.</p>`
  +`<button class="btn" style="width:100%;margin-top:12px" onclick="verifyPay()">I've paid — verify</button>`
  +`<button class="btn-ghost" style="width:100%;margin-top:8px" onclick="renderPayTiers()">← Back</button></div>`;
  location.href=url;
  } finally { _payBusy(false); } // re-enable on EVERY exit path
}
async function rpc(method, params){
  const r=await fetch(PAYMENTS.rpc,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
  return (await r.json()).result;
}
async function verifyPay(){
  const b=$('payBody'); if(!b) return;
  let pend=null; try{ pend=JSON.parse(sessionStorage.getItem('haunt_pending')||'null'); }catch(_){}
  const okTiers=['day','monthly','lifetime','anomaly'];
  const isOre=pend&&pend.cur==='ORE';
  const isSkr=pend&&pend.cur==='SKR';
  const isTok=isOre||isSkr;
  if(!pend||!PAYMENTS.treasury||okTiers.indexOf(pend.tier)<0||!(isTok?pend.amt>0:pend.sol>0)){ b.innerHTML='<div class="note">No pending payment found. Try again.</div>'; return; }
  b.innerHTML='<div class="note" style="text-align:center">Checking the chain…</div>';
  try{
    const sigs=await rpc('getSignaturesForAddress',[PAYMENTS.treasury,{limit:20}]);
    for(const s of (sigs||[])){
      if(s.blockTime*1000 < pend.t-36e5) continue;
      // Backend-quoted payment (pend.backend): let the haunt-backend settle
      // this signature first — it fetches the tx itself, checks the treasury
      // delta against its quote, consumes the signature exactly once and
      // grants the entitlement to the payer derived from the chain. A
      // {verified:true} answer accepts through the SAME local success path
      // as an on-device verification (paySave with the response tier). ANY
      // other outcome — unreachable, non-200, not verified — continues with
      // the on-device delta verification for this signature, unchanged.
      if(pend.backend && PAYMENTS.backend){
        try{
          const br=await fetch(String(PAYMENTS.backend).replace(/\/+$/,'')+'/v1/pay/verify',{method:'POST',headers:{'content-type':'application/json'},
            body:JSON.stringify({signature:s.signature})});
          if(br.ok){
            const bj=await br.json().catch(()=>null);
            if(bj&&bj.verified===true){
              const vt=okTiers.indexOf(bj.tier)>=0?bj.tier:pend.tier;
              const p=payLoad(); p[vt]={expires:PAYMENTS.durMs[vt]?Date.now()+PAYMENTS.durMs[vt]:0};
              paySave(p);
              b.innerHTML=`<div style="text-align:center;padding:12px 0"><div style="font-size:44px;margin-bottom:10px">✅</div>`
              +`<h2 style="margin:0 0 6px">${TIERS[vt].name} unlocked</h2>`
              +`<p style="color:var(--dim);font-size:14px">Payment verified on-chain.</p>`
              +`<button class="btn" style="width:100%;margin-top:12px" onclick="closeCheckout();location.reload()">Start using it</button></div>`;
              try{ sessionStorage.removeItem('haunt_pending'); }catch(_){}
              return;
            }
          }
        }catch(_){ /* backend unreachable — on-device verification continues */ }
      }
      const tx=await rpc('getTransaction',[s.signature,{maxSupportedTransactionVersion:0,encoding:'jsonParsed'}]);
      const logs=((tx&&tx.meta&&tx.meta.logMessages)||[]).join('\n');
      if(logs.indexOf(pend.memo)<0) continue;
      if(isTok){
        // SPL rail (ORE / SKR): verify the treasury's balance OF THE PENDING
        // PAYMENT'S OWN MINT grew by >= expected (base units). The balance
        // filter is keyed to the pending currency's mint, so a delta in any
        // other token (e.g. ORE against an SKR pending) can never verify it.
        const tokMint=isSkr?PAYMENTS.skrMint:PAYMENTS.oreMint;
        const tokDec=isSkr?PAYMENTS.skrDecimals:PAYMENTS.oreDecimals;
        let gained=0n;
        try{
          const bal=(arr)=>(arr||[]).filter(x=>x.mint===tokMint&&x.owner===PAYMENTS.treasury)
            .reduce((a,x)=>a+BigInt(x.uiTokenAmount.amount),0n);
          gained=bal(tx.meta.postTokenBalances)-bal(tx.meta.preTokenBalances);
        }catch(_){}
        const need=BigInt(Math.floor(pend.amt*Math.pow(10,tokDec)*0.99)); // 1% tolerance
        if(gained<need) continue;
      } else {
        // SOL: verify the treasury actually received >= expected lamports in this tx
        let lamports=0;
        try{
          const msg=tx.transaction.message, keys=msg.accountKeys||[];
          const ti=keys.findIndex(k=>(k.pubkey||k)===PAYMENTS.treasury);
          if(ti>=0) lamports=Math.max(0,(tx.meta.postBalances[ti]||0)-(tx.meta.preBalances[ti]||0));
        }catch(_){}
        const needLamports=Math.floor(pend.sol*1e9*0.99); // 1% slippage tolerance
        if(lamports<needLamports) continue;
      }
      const p=payLoad(); p[pend.tier]={expires:PAYMENTS.durMs[pend.tier]?Date.now()+PAYMENTS.durMs[pend.tier]:0};
      paySave(p);
      b.innerHTML=`<div style="text-align:center;padding:12px 0"><div style="font-size:44px;margin-bottom:10px">✅</div>`
      +`<h2 style="margin:0 0 6px">${TIERS[pend.tier].name} unlocked</h2>`
      +`<p style="color:var(--dim);font-size:14px">Payment verified on-chain.</p>`
      +`<button class="btn" style="width:100%;margin-top:12px" onclick="closeCheckout();location.reload()">Start using it</button></div>`;
      try{ sessionStorage.removeItem('haunt_pending'); }catch(_){}
      return;
    }
    b.innerHTML='<div class="note" style="text-align:center">No matching payment found yet. Wait a few seconds and try again — or check your wallet sent to the right address.</div>'
    +'<button class="btn-ghost" style="width:100%;margin-top:8px" onclick="verifyPay()">Retry verification</button>';
  }catch(e){
    b.innerHTML='<div class="note" style="text-align:center">Could not reach the network. Check your connection and retry.</div>'
    +'<button class="btn-ghost" style="width:100%;margin-top:8px" onclick="verifyPay()">Retry</button>';
  }
}

// ==== GHOST tab pricing — tier rows + anomaly card, checkout via openCheckout ====
function renderPlanTiers(){
  const el=$('planTiers'), an=$('planAnomaly'); if(!el) return;
  let ap=null; try{ ap=activePurchase(); }catch(_){}
  const bd=$('planBadge');
  if(bd) bd.textContent=ap?TIERS[ap].name+' active':(Entitlements.tier==='free'?'free plan':'preview · all unlocked');
  const desc={day:'Everything, for one day.',monthly:'Everything, all month.',lifetime:'Everything, forever.'};
  const row=t=>{
    const T=TIERS[t], active=ap===t, pp=T.price.split(' ');
    const main=pp[0], per=pp.slice(1).join(' ').replace(/^\/ ?/,'');
    return `<div class="plan-row"><div style="flex:1;min-width:0">`
    +`<div style="font-weight:800;font-size:15px">${T.name}${active?' <span class="tag tag-gold">active</span>':''}</div>`
    +`<div class="row-sub">${desc[t]}</div></div>`
    +`<div style="text-align:right;flex:0 0 auto"><div style="font-weight:800;font-size:18px;color:var(--gold)">${main}</div>`
    +`<div class="row-sub">${per}</div></div>`
    +(active?`<span style="flex:0 0 auto;margin-left:8px;color:var(--green);font-size:18px">✓</span>`
            :`<button class="btn" style="width:auto;flex:0 0 auto;margin-left:10px;padding:10px 18px;min-height:44px" onclick="_payTier='${t}';openCheckout()">Buy</button>`)
    +`</div>`;
  };
  el.innerHTML=['day','monthly','lifetime'].map(row).join('');
  if(an){
    const active=ap==='anomaly', pp=TIERS.anomaly.price.split(' ');
    an.innerHTML=`<div style="display:flex;align-items:center;gap:10px">`
    +`<div style="flex:1;min-width:0"><div style="font-weight:800;font-size:15px">Anomaly radar${active?' <span class="tag tag-violet">active</span>':''}</div>`
    +`<div class="row-sub">Pot shocks, whale strikes, flooded tiles.<br>100 slots / day · scored in ROUNDS.</div></div>`
    +`<div style="text-align:right;flex:0 0 auto"><div style="font-weight:800;font-size:18px;color:var(--violet)">${pp[0]}</div>`
    +`<div class="row-sub">${pp.slice(1).join(' ').replace(/^\/ ?/,'')}</div></div>`
    +(active?`<span style="flex:0 0 auto;margin-left:8px;color:var(--green);font-size:18px">✓</span>`
            :`<button class="btn" style="width:auto;flex:0 0 auto;margin-left:10px;padding:10px 18px;min-height:44px;background:var(--violet);color:#fff" onclick="_payTier='anomaly';openCheckout()">Unlock</button>`)
    +`</div>`;
  }
}
// ==== .skr companion ====
const CMP_TRAITS={
  whale:{name:'KRAKEN',icon:'🐋',c1:'#ffd76a',c2:'#b8860b',glow:'rgba(255,183,0,.35)',blurb:'Deploys heavy and moves markets. When the Kraken stirs, tiles flood.'},
  sniper:{name:'PHANTOM',icon:'🎯',c1:'#c4b5fd',c2:'#7c3aed',glow:'rgba(139,92,246,.35)',blurb:'Waits in the dark, then strikes one tile. Patience is the weapon.'},
  spreader:{name:'MIST',icon:'🕸️',c1:'#5eead4',c2:'#0d9488',glow:'rgba(45,212,191,.3)',blurb:'Everywhere at once. Covers the board so no round goes unplayed.'},
  balanced:{name:'WATCHER',icon:'⚖️',c1:'#e8e8f2',c2:'#8a8aa3',glow:'rgba(200,200,220,.25)',blurb:'Steady hands, even spread. Reads the board and plays it straight.'},
  rookie:{name:'ROOKIE',icon:'🥚',c1:'#9a9ab0',c2:'#55556a',glow:'rgba(150,150,170,.2)',blurb:'Not in the top 150 yet. The chain remembers what comes next.'}
};
function cmpTraitOf(L){
  if(!L) return 'rookie';
  const [txt]=stratTag(L);
  if(txt.indexOf('Whale')>=0) return 'whale';
  if(txt.indexOf('Sniper')>=0) return 'sniper';
  if(txt.indexOf('Spreader')>=0) return 'spreader';
  if(txt.indexOf('Balanced')>=0) return 'balanced';
  return 'rookie';
}
function cmpLoad(){ try{
    const c=JSON.parse(localStorage.getItem('haunt_companion')||'null');
    if(c && !CMP_TRAITS[c.trait]) c.trait='rookie';
    return c;
  }catch(_){ return null; } }
function cmpSave(c){ try{ localStorage.setItem('haunt_companion', JSON.stringify(c)); }catch(_){} }
function cmpLevel(xp){ return Math.floor(Math.sqrt(Math.max(0,xp||0)/50))+1; }
function cmpXpForLevel(lv){ return 50*(lv-1)*(lv-1); }
function cmpGhostSVG(t, big){
  const T=CMP_TRAITS[t]||CMP_TRAITS.rookie;
  const s=big?1:1;
  return `<svg viewBox="0 0 100 120" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="cg${t}" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="${T.c1}"/><stop offset="1" stop-color="${T.c2}"/></linearGradient></defs>
  <path d="M50 8 C24 8 14 30 14 55 L14 105 L26 96 L36 106 L48 96 L60 106 L70 96 L82 106 L86 100 L86 55 C86 30 76 8 50 8 Z"
    fill="url(#cg${t})" opacity="0.95"/>
  <ellipse cx="38" cy="52" rx="7" ry="9" fill="#0b0b14"/>
  <ellipse cx="62" cy="52" rx="7" ry="9" fill="#0b0b14"/>
  <circle cx="40" cy="50" r="2.4" fill="#fff"/><circle cx="64" cy="50" r="2.4" fill="#fff"/>
  <ellipse cx="50" cy="74" rx="6" ry="8" fill="#0b0b14" opacity="0.85"/>
  </svg>`;
}
function cmpHatch(w){
  const L=(D.leaders.leaders||[]).find(x=>x.w===w||x.w.toLowerCase()===w.toLowerCase());
  const t=cmpTraitOf(L);
  const c={w:L?L.w:w, trait:t, xp:75, hatched:Date.now()};
  cmpSave(c); renderCompanion(); toast('Your companion hatched!'); show('t-ghost');
  return c;
}
function cmpXP(n){
  const c=cmpLoad(); if(!c) return;
  const before=cmpLevel(c.xp);
  c.xp+=n; cmpSave(c);
  const after=cmpLevel(c.xp);
  renderCompanion();
  if(after>before) toast(`Companion reached level ${after}!`);
}
function renderCompanion(){
  const el=$('companionBody'); if(!el) return;
  const c=cmpLoad();
  if(!c){
    el.innerHTML=`<div class="cmp-wrap"><div class="cmp-egg">🥚</div>`
    +`<div style="flex:1"><div class="eyebrow" style="margin:0 0 4px">.skr companion</div>`
    +`<div style="font-weight:800;font-size:16px;margin-bottom:4px">Hatch your ghost</div>`
    +`<div class="cmp-trait">Find your wallet in the MINERS tab — your mining style hatches a companion that grows as you explore.</div></div></div>`
    +`<button class="btn" style="width:100%;margin-top:12px" onclick="show('t-intel')">Find my wallet</button>`;
    return;
  }
  const T=CMP_TRAITS[c.trait]||CMP_TRAITS.rookie;
  const L=(D.leaders.leaders||[]).find(x=>x.w===c.w);
  const lv=cmpLevel(c.xp), cur=cmpXpForLevel(lv), next=cmpXpForLevel(lv+1);
  const pct=Math.min(100,Math.round((c.xp-cur)/Math.max(1,next-cur)*100));
  const rank=L?((()=>{const s=[...(D.leaders.leaders||[])].sort((a,b)=>b.s-a.s);const i=s.findIndex(x=>x.w===c.w);return i>=0?'#'+(i+1):'—';})()):'unranked';
  el.innerHTML=`<div class="cmp-wrap"><div class="cmp-ghost idle" style="filter:drop-shadow(0 6px 18px ${T.glow})">${cmpGhostSVG(c.trait)}</div>`
  +`<div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">`
  +`<span style="font-weight:800;font-size:17px">${T.icon} ${T.name}</span><span class="cmp-lvltag">LV ${lv}</span></div>`
  +`<div class="cmp-trait" style="margin-top:2px">${T.blurb}</div>`
  +`<div class="cmp-xpbar"><div class="cmp-xpfill" style="width:${pct}%"></div></div>`
  +`<div class="cmp-stats">`
  +`<span class="tag">${esc(short(c.w))}</span>`
  +(L?`<span class="tag tag-gold">${rank} · ${L.s.toFixed(1)} SOL</span><span class="tag">${L.n} deploys</span>`:`<span class="tag">outside top 150</span>`)
  +`</div></div></div>`;
}
// hook XP into existing actions (guarded so nothing breaks if companion absent)
const _cmpProfile=typeof profile==='function'?profile:null;
// ---- profile ----
function shareText(t){
  if(navigator.share){ navigator.share({title:'ORE HAUNT',text:t}).catch(()=>{}); }
  else if(navigator.clipboard){ navigator.clipboard.writeText(t).then(()=>toast('Report copied')).catch(()=>toast(t)); }
  else toast(t);
}
function shareMinerCard(w){
  const L=(D.leaders.leaders||[]).find(x=>x.w===w); if(!L) return;
  const F=D.fp[w], tiles=F?F.t:[], tot=tiles.reduce((a,v)=>a+v,0);
  const [tagTxt]=stratTag(L);
  const topT=tot>0?tiles.indexOf(Math.max(...tiles)):-1;
  const cv=document.createElement('canvas'); cv.width=1080; cv.height=1350;
  const c=cv.getContext('2d');
  const g=c.createLinearGradient(0,0,0,1350); g.addColorStop(0,'#12121f'); g.addColorStop(1,'#0a0a14');
  c.fillStyle=g; c.fillRect(0,0,1080,1350);
  c.strokeStyle='rgba(255,176,0,.35)'; c.lineWidth=3; c.strokeRect(30,30,1020,1290);
  c.textAlign='center';
  c.fillStyle='#ffb000'; c.font='800 54px system-ui'; c.fillText('👻 ORE HAUNT',540,140);
  c.fillStyle='#8a8ab0'; c.font='600 30px system-ui'; c.fillText('MINER DOSSIER',540,190);
  c.fillStyle='#fff'; c.font='800 64px system-ui'; c.fillText(short(L.w),540,300);
  c.fillStyle='#ffb000'; c.font='700 44px system-ui'; c.fillText(tagTxt,540,380);
  const stats=[[L.s.toLocaleString(undefined,{maximumFractionDigits:1})+' SOL','deployed'],[String(L.n),'deploys'],[String(L.r),'rounds'],[(L.n?(L.s/L.n):0).toFixed(2)+' SOL','per deploy']];
  stats.forEach((st,i)=>{ const x=200+(i%2)*340, y=520+Math.floor(i/2)*150;
    c.fillStyle='#fff'; c.font='800 52px system-ui'; c.textAlign='center'; c.fillText(st[0],x+170,y);
    c.fillStyle='#8a8ab0'; c.font='500 28px system-ui'; c.fillText(st[1],x+170,y+45); });
  if(topT>=0){ c.fillStyle='#8a8ab0'; c.font='500 30px system-ui'; c.fillText('Top tile',540,880);
    c.fillStyle='#ffb000'; c.font='800 120px system-ui'; c.fillText('#'+topT,540,1000);
    c.fillStyle='#8a8ab0'; c.font='500 30px system-ui'; c.fillText(Math.round(tiles[topT]/tot*100)+'% of their SOL',540,1050); }
  // mini fingerprint heatmap
  if(tot>0){ const mx=Math.max(...tiles,0.000001);
    tiles.forEach((v,i)=>{ const a=Math.min(1,v/mx), gx=140+(i%5)*160, gy=1120+Math.floor(i/5)*52;
      c.fillStyle=`rgba(255,${Math.round(120+80*a)},0,${(0.15+a*0.75).toFixed(2)})`;
      c.fillRect(gx,gy,140,40); }); }
  c.fillStyle='#5a5a7a'; c.font='400 24px system-ui'; c.fillText('Winners are random — past rounds don\'t predict.',540,1250);
  cv.toBlob(blob=>{
    const file=new File([blob],'ore-haunt-miner.png',{type:'image/png'});
    if(navigator.share&&navigator.canShare&&navigator.canShare({files:[file]})){
      navigator.share({files:[file],title:'ORE HAUNT dossier'}).catch(()=>{});
    } else {
      const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
      a.download='ore-haunt-'+L.w.slice(0,8)+'.png'; document.body.appendChild(a); a.click();
      setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},500); toast('Card downloaded');
    }
  },'image/png');
}
function fpSim(w1,w2){
  const A=D.fp[w1], B=D.fp[w2]; if(!A||!A.t||!B||!B.t) return null;
  const n1=Math.hypot(...A.t), n2=Math.hypot(...B.t); if(!n1||!n2) return null;
  return A.t.reduce((a,v,i)=>a+v*B.t[i],0)/(n1*n2);
}
function similarMiners(w){
  const F0=D.fp[w]; if(!F0||!F0.t) return [];
  const v0=F0.t, n0=Math.hypot(...v0); if(!n0) return [];
  return (D.leaders.leaders||[]).filter(L=>L.w!==w&&D.fp[L.w]&&D.fp[L.w].t)
    .map(L=>{ const v=D.fp[L.w].t, dot=v.reduce((a,x,i)=>a+x*v0[i],0), n=Math.hypot(...v);
      return {L, sim:n?dot/(n0*n):0}; })
    .sort((a,b)=>b.sim-a.sim).slice(0,5);
}
let _cmpA='', _cmpB='';
function openCompare(a){ _cmpA=a||''; _cmpB=''; renderCompare(); show('t-compare'); }
function cmpCard(w){
  const L=(D.leaders.leaders||[]).find(x=>x.w===w); if(!L) return '<div class="card"><div class="note">Miner not found.</div></div>';
  const F=D.fp[w], tiles=F?F.t:[], tot=tiles.reduce((a,v)=>a+v,0);
  const [tagTxt,tagCls]=stratTag(L);
  const topT=tot>0?tiles.indexOf(Math.max(...tiles)):-1;
  return `<div class="card" style="flex:1;min-width:260px"><div class="card-h"><span class="waddr">${esc(short(L.w))}</span><span class="tag ${tagCls}">${tagTxt}</span></div>`
  +`<div class="stat-grid"><div class="stat-tile"><div class="v">${L.s.toLocaleString(undefined,{maximumFractionDigits:1})}</div><div class="l">SOL</div></div>`
  +`<div class="stat-tile"><div class="v">${L.n}</div><div class="l">deploys</div></div>`
  +`<div class="stat-tile"><div class="v">${L.r}</div><div class="l">rounds</div></div>`
  +`<div class="stat-tile"><div class="v">${(L.n?(L.s/L.n):0).toFixed(2)}</div><div class="l">SOL/deploy</div></div></div>`
  +(topT>=0?`<div class="kv" style="margin-top:10px"><span>Top tile</span><b>#${topT} · ${Math.round(tiles[topT]/tot*100)}% of SOL</b></div>`:'')
  +(tot>0?`<div class="lbl" style="margin:10px 0 6px">Tile fingerprint</div>`+heatTiles(tiles,-1):'')
  +`<button class="btn-ghost" style="width:100%;margin-top:10px" onclick="profile('${L.w}')">Open dossier ›</button></div>`;
}
function renderCompare(){
  const el=$('compareBody'); if(!el) return;
  if(!Entitlements.can('compare')){ el.innerHTML=lockedCard('compare','Miner compare','Scout two wallets side by side.'); return; }
  el.innerHTML=`<div class="card" style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <input id="cmpA" type="text" placeholder="Wallet A…" value="${esc(_cmpA)}" autocapitalize="off" spellcheck="false" style="flex:1;min-width:140px;background:#1a1a2e;border:1px solid var(--line);border-radius:10px;padding:12px;color:var(--txt);font-size:16px;box-sizing:border-box">
    <input id="cmpB" type="text" placeholder="Wallet B…" value="${esc(_cmpB)}" autocapitalize="off" spellcheck="false" style="flex:1;min-width:140px;background:#1a1a2e;border:1px solid var(--line);border-radius:10px;padding:12px;color:var(--txt);font-size:16px;box-sizing:border-box">
    <button class="btn" onclick="_cmpA=$('cmpA').value.trim();_cmpB=$('cmpB').value.trim();renderCompare()">Compare</button></div>
    <div id="cmpOut"></div>`;
  const out=$('cmpOut');
  if(_cmpA&&_cmpB){
    const _s=fpSim(_cmpA,_cmpB);
    const _banner=_s==null?'':`<div class="card" style="border-color:var(--gold-line);text-align:center;margin-bottom:12px">`
     +`<div class="eyebrow" style="color:var(--gold)">Betting similarity</div>`
     +`<div style="font-size:32px;font-weight:800">${Math.round(_s*100)}%</div>`
     +`<div class="fineprint">How alike their tile fingerprints are — same shape, same hunter.</div></div>`;
    out.innerHTML=_banner+`<div style="display:flex;gap:12px;flex-wrap:wrap">${cmpCard(_cmpA)}${cmpCard(_cmpB)}</div>`; }
  else if(_cmpA){ out.innerHTML='<div class="note">Enter a second wallet to compare against '+esc(short(_cmpA))+'.</div>'; }
}
function exportTaxCSV(w){
  const L=(D.leaders.leaders||[]).find(x=>x.w===w); if(!L) return;
  const F=D.fp[w]; const tiles=F?F.t:[];
  const rows=[['wallet',L.w],['total_sol_deployed',L.s.toFixed(6)],['deploy_count',L.n],['rounds_active',L.r],['export_note','Aggregates from ORE HAUNT observation window — not a full transaction history']];
  tiles.forEach((v,i)=>rows.push(['tile_'+i+'_sol',v.toFixed(6)]));
  const csv=rows.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='ore-haunt-tax-'+L.w.slice(0,8)+'.csv'; document.body.appendChild(a); a.click();
  setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); },500);
  toast('Tax CSV downloaded');
}
function profile(w){if(typeof cmpXP==='function')cmpXP(10);const _all=(D.leaders.leaders||[]);const L=_all.find(x=>x.w===w);const F=D.fp[w];const b=$('profileBody');if(!b)return;
if(!L){b.innerHTML='<div class="empty"><span class="ic">👻</span>Miner not found.</div>';show('t-profile');return;}
if(!Entitlements.can('alldossiers')&&_all.indexOf(L)>=25){b.innerHTML=lockedCard('alldossiers','Miner dossier','Full dossiers for all 150 miners.');show('t-profile');return;}
b.innerHTML='<div class="card"><div class="skel" style="height:72px;margin-bottom:12px"></div><div class="skel" style="height:150px"></div></div>';
show('t-profile');
requestAnimationFrame(()=>requestAnimationFrame(()=>{
let hc=0; for(const ch of String(w)) hc=(hc*31+ch.charCodeAt(0))%360;
let h=`<div class="card"><div class="player-top">`
  +`<div class="ident" style="background:conic-gradient(from ${hc}deg,hsl(${hc},65%,42%),hsl(${(hc+70)%360},65%,30%),hsl(${hc},65%,42%))">${esc(short(w).slice(0,2).toUpperCase())}</div>`
  +`<div style="flex:1;min-width:0"><div class="eyebrow" style="margin-bottom:2px">Miner</div>`
  +`<div class="waddr" style="font-size:13px;overflow-wrap:anywhere">${esc(w)}</div>`
  +`<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap"><span class="tag tag-violet">✓ verified layout</span><span class="tag tag-gold">rank #${(D.leaders.leaders||[]).findIndex(x=>x.w===w)+1}</span></div>`
  +`</div></div></div>`;
// Scout's take — plain-language, derived only from fingerprint + counts. No invented stats.
{const _ft=F?F.t:[],_ftot=_ft.reduce((a,v)=>a+v,0),_take=[];
if(F&&_ftot>0){
  const _mx=Math.max(..._ft),_mi=_ft.indexOf(_mx),_sh=_mx/_ftot;
  const _uni=_ft.every(v=>Math.abs(v-_ft[0])<1e-9);
  if(_uni) _take.push('Spreads evenly across all 25 tiles — no favorite.');
  else if(_sh>=0.5) _take.push(`Snipes tile ${_mi} with ${Math.round(_sh*100)}% of their SOL.`);
  else if(_sh>=0.25) _take.push(`Leans on tile ${_mi} (${Math.round(_sh*100)}%), spreads the rest.`);
  else _take.push(`Spreads across tiles, heaviest on tile ${_mi} (${Math.round(_sh*100)}%).`);
  if(L.s>=whaleCut()) _take.push('A whale — top 10% by SOL deployed.');
  const _tt=F.man+F.auto,_mp=_tt?Math.round(F.man/_tt*100):50;
  _take.push(_mp>=70?'Mostly manual — hands on the tiles.':_mp<=30?'Mostly automated — runs a bot.':'A mix of manual and automated play.');
}
_take.push(`Active in ${L.r} rounds with ${L.n} deploys.`);
h+=`<div class="card" style="border-color:var(--gold-line)"><div class="card-h"><span class="eyebrow" style="margin:0">👁 Scout's take</span></div>`
  +_take.map(t=>`<div style="font-size:14px;line-height:1.55;padding:3px 0">· ${t}</div>`).join('')+`</div>`;}
{const _ex=D.expo&&D.expo[w];
if(_ex){
  if(Entitlements.can('anomaly')){
    const _em=D.expoMeta||{};
    h+=`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">⚡ Anomaly exposure</span><span class="tag tag-violet">${Math.round(_ex.rate*100)}% of covered rounds</span></div>`
    +`<div style="font-size:14px;line-height:1.6">Present in <b>${_ex.a} of ${_ex.p} covered rounds</b> that turned anomalous · <b>${_ex.sol.toFixed(2)} SOL</b> deployed across them`
    +(_ex.hot>0?` · <b>${_ex.hot.toFixed(2)} SOL</b> of it on flooded tiles`:'')+`</div>`
    +_ex.top.map(t=>`<div class="row" style="padding:10px 4px" onclick="showIntel('explore');roundDetail(${t.r})" role="button" tabindex="0" onkeydown="if(event.key==='Enter'){showIntel('explore');roundDetail(${t.r})}">`
      +`<span style="flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:${t.sev==='extreme'?'#ff5470':t.sev==='strong'?'var(--gold)':'#8a8aa3'}"></span>`
      +`<div style="flex:1;min-width:0;margin-left:10px"><div style="font-weight:700;font-size:14px">Round #${t.r} · ${esc(t.label)}</div><div class="row-sub">${t.sev} · they deployed ${t.sol.toFixed(2)} SOL${t.hot>0?` · ${t.hot.toFixed(2)} on the flooded tile`:''}</div></div><div class="chev">›</div></div>`).join('')
    +`<div class="fineprint" style="margin-top:8px">Presence isn't causation — this shows where they played, not what they caused. Coverage: ${_em.covered} of ${_em.window} window rounds (deploy events end at #${_em.to}).</div></div>`;
  } else {
    h+=lockedCard('anomaly','Anomaly exposure','Which anomalous rounds this miner played in — and what they had riding on the hot tile.');
  }
}}
if(Entitlements.can('similar')){
  const _sim=similarMiners(w);
  if(_sim.length){ h+=`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Similar miners</span><span class="tag">fingerprint match</span></div>`
    +_sim.map(({L,sim})=>{ const [tt,tc]=stratTag(L);
      return `<div class="row" style="padding:10px 4px" onclick="profile('${L.w}')" role="button" tabindex="0" onkeydown="if(event.key==='Enter'){profile('${L.w}')}">`
      +`<div style="flex:1;min-width:0"><div class="waddr">${esc(short(L.w))}</div><div style="margin-top:4px"><span class="tag ${tc}">${tt}</span></div></div>`
      +`<div style="text-align:right"><div class="row-big">${Math.round(sim*100)}%</div><div class="row-sub">match</div></div><div class="chev">›</div></div>`; }).join('')
    +`</div>`; }
} else { h+=lockedCard('similar','Similar miners','Find wallets that bet like this one.'); }
h+=`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Window stats</span></div><div class="stat-grid">`
  +`<div class="stat-tile"><div class="v">${L.s.toLocaleString()}</div><div class="l">SOL deployed</div></div>`
  +`<div class="stat-tile"><div class="v">${L.n}</div><div class="l">Deploys</div></div>`
  +`<div class="stat-tile"><div class="v">${L.r}</div><div class="l">Rounds active</div></div>`
  +`<div class="stat-tile"><div class="v">${(L.n?(L.s/L.n):0).toFixed(2)}</div><div class="l">SOL / deploy</div></div>`;
if(L.lt_dep!=null){ h+=`<div class="stat-tile"><div class="v">${L.lt_dep.toLocaleString()}</div><div class="l">Lifetime SOL</div></div>`
  +`<div class="stat-tile"><div class="v">${(L.lt_ore||0).toLocaleString()}</div><div class="l">Lifetime ORE</div></div>`; }
h+=`</div>`;
if(F){const tot=F.man+F.auto, mp=tot?Math.round(F.man/tot*100):0;
h+=`<div style="margin-top:14px"><div class="lbl" style="margin-bottom:8px">Manual vs automated</div>`
  +`<div class="split"><i style="width:${mp}%;background:var(--green)"></i><i style="width:${100-mp}%;background:var(--gold)"></i></div>`
  +`<div style="display:flex;justify-content:space-between;margin-top:8px;font-size:12px;color:var(--dim)">`
  +`<span><i class="dot" style="background:var(--green)"></i>Manual ${mp}%</span>`
  +`<span><i class="dot" style="background:var(--gold)"></i>Automated ${100-mp}%</span></div></div>`}
h+=`</div>`;
h+=`<div style="display:flex;gap:8px;margin-top:12px"><button class="btn-ghost" style="flex:1" onclick="shareMinerCard('${w}')">🖼️ Share card</button><button class="btn-ghost" style="flex:1" onclick="openCompare('${w}')">⚖️ Compare</button></div>`;
h+=Entitlements.can('taxcsv')
  ?`<button class="btn-ghost" style="width:100%;margin-top:12px" onclick="exportTaxCSV('${w}')">📄 Export tax CSV</button>`
  :`<div style="margin-top:12px">`+lockedCard('taxcsv','Tax export','Download this miner\'s activity as CSV for tax records.')+`</div>`;
if(F){const mx=Math.max(...F.t,0.000001);const _uni=F.t.every(v=>Math.abs(v-F.t[0])<1e-9);
h+=`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Where they bet</span><span class="tag">tile fingerprint</span></div><div class="fp-grid">`;
F.t.forEach((v,i)=>{const a=Math.min(1,v/mx);h+=`<div class="fp-tile" style="border-color:${fpEdge(a)}"><span>${i}</span><span class="v">${v>=1?v.toFixed(1):v.toFixed(3)}</span></div>`});
h+=`</div><div class="fineprint" style="margin-top:10px">${_uni?'Even spread — this miner bets all 25 tiles equally.':'Brighter = more SOL on that tile across the observation window.'}</div></div>`}
b.innerHTML=h;
}));}
// DESIGN-OVERHAUL: thermal color ramp shared by dossier fingerprint + round heatmap
function fpEdge(t){
  t=Math.max(0,Math.min(1,t));
  const A=[90,100,150],B=[255,176,0],C=[255,84,112];
  const m=(X,Y,k)=>Math.round(X+(Y-X)*k);
  let r,g,b;
  if(t<.5){ const k=t*2; r=m(A[0],B[0],k); g=m(A[1],B[1],k); b=m(A[2],B[2],k); }
  else{ const k=(t-.5)*2; r=m(B[0],C[0],k); g=m(B[1],C[1],k); b=m(B[2],C[2],k); }
  return `rgba(${r},${g},${b},${(0.3+t*0.7).toFixed(2)})`;
}
// ==== StatsModule: player count + SOL deployed — separate from tile animation ====
// Correlated via update(engine) called each frame; reset() on replay reset.
const StatsModule={
  render(){
    return `<div class="tele-grid">`
      +`<div class="tele"><div class="tele-v" id="stDeploys">0</div><div class="tele-l">Players</div></div>`
      +`<div class="tele"><div class="tele-v gold" id="stSol">0</div><div class="tele-l">SOL deployed</div></div>`
      +`<div class="tele"><div class="tele-v green" id="stLeader">\u2014</div><div class="tele-l">Leader</div></div></div>`;
  },
  update(eng){
    const sd=document.getElementById('stDeploys');
    if(sd&&eng){
      sd.textContent=eng.deploysShown+' / '+eng.evs.length;
      const ss=document.getElementById('stSol');
      if(ss) ss.textContent=eng.totalSol.toFixed(2)+' SOL';
      const sl=document.getElementById('stLeader');
      if(sl) sl.textContent='tile '+eng.leader;
    }
  },
  reset(){
    const sd=document.getElementById('stDeploys'); if(sd) sd.textContent='0';
    const ss=document.getElementById('stSol'); if(ss) ss.textContent='0 SOL';
    const sl=document.getElementById('stLeader'); if(sl) sl.textContent='\u2014';
  }
};
// biggest pots: vibrant gold (money) — intensity scales with pot size
function potTint(r,maxD){
  const h=Math.min(1,r.d/(maxD||1));
  const a=(0.22+h*0.30).toFixed(2);
  return `linear-gradient(135deg, rgba(255,176,0,${a}), rgba(255,120,0,${(a*0.7).toFixed(2)}))`;
}
// most crowded: cool blue-violet (people) — intensity scales with miner count
function crowdTint(r,maxM){
  const h=Math.min(1,r.m/(maxM||1));
  const a=(0.20+h*0.28).toFixed(2);
  return `linear-gradient(135deg, rgba(80,120,255,${a}), rgba(140,80,255,${(a*0.7).toFixed(2)}))`;
}
// ---- rounds: three lenses over last 100 ----
function okR(r){ return r&&Number.isFinite(r.r)&&Number.isFinite(r.d)&&Number.isFinite(r.m)&&Number.isFinite(r.w)&&r.w>=0&&r.w<25&&Array.isArray(r.t)&&r.t.length===25; }
// ==== Round explorer helpers (module scope) ====
const _rmap={}; D.replays.replays.forEach(R=>{ _rmap[R.round]=R; });
function rtime(r){
  const R=_rmap[r.r];
  if(R&&R.timeline[0]) return {t:R.timeline[0].t, real:true}; // real chain time
  // estimate from round-ID cadence (anchor 438059 @ 2026-10-12T21:08:49Z, real replay time, + 63s/round measured on the live replay span 438052-438059)
  const ms=Date.parse('2026-10-12T21:08:49Z')+(r.r-438059)*63000;
  return {t:new Date(ms).toISOString(), real:false};
}
// ==== AnomalyModule — the self-contained anomaly layer ====
// Bundled rounds in -> scored signals out. Detection, grouping, caching,
// explorer filter state and badge UI all live in this one object.
// The ONLY call sites in the app:
//   renderRoundBrowser — badgeHTML + filterOn (explorer list/filter)
//   renderLenses       — filter chip -> toggleRoundAnom -> filterClick
//   roundDetail        — topFor (detail badge)
//   renderAnomaly      — signals (radar feed)
// To remove anomaly from ROUNDS: delete this block + the radar section
// below and those four call sites.
const AnomalyModule={
  _map:null, _sigs:null, filterOn:false,
  _z(a){ const n=a.length; if(!n) return {m:0,s:1};
    const m=a.reduce((x,v)=>x+v,0)/n, v=a.reduce((x,v)=>x+(v-m)*(v-m),0)/n;
    return {m, s:Math.sqrt(v)||1}; },
  detect(){
  const W=[...(D.rounds.recent||[])].filter(okR);
  if(W.length<10) return [];
  const zs=this._z(W.map(x=>x.d)), ms=this._z(W.map(x=>x.m));
  const cs=this._z(W.map(x=>x.d>0?Math.max(...x.t)/x.d:0));
  // per-tile funding baselines (tile floods need their own scale)
  const tm=[], ts=[];
  for(let i=0;i<25;i++){ const st=this._z(W.map(x=>x.t[i])); tm.push(st.m); ts.push(st.s); }
  const out=[];
  const sev=z=>{ const a=Math.abs(z); return a>=3?'extreme':a>=2.5?'strong':a>=2?'notable':null; };
  W.forEach(r=>{
    const c=r.d>0?Math.max(...r.t)/r.d:0;
    const zp=(r.d-zs.m)/zs.s, zm=(r.m-ms.m)/ms.s, zc=(c-cs.m)/cs.s;
    const s1=sev(zp), s2=sev(zm), s3=sev(zc);
    if(s1) out.push({r:r.r, type:'pot', icon:'💰', label:zp>0?'Pot spike':'Pot drought', z:zp, sev:s1,
      detail:r.d.toFixed(2)+' SOL vs '+zs.m.toFixed(2)+' avg'});
    if(s2) out.push({r:r.r, type:'crowd', icon:'👥', label:zm>0?'Crowd surge':'Ghost round', z:zm, sev:s2,
      detail:r.m+' miners vs '+ms.m.toFixed(0)+' avg'});
    if(s3) out.push({r:r.r, type:'whale', icon:'🐋', label:zc>0?'Whale strike':'Spread thin', z:zc, sev:s3,
      detail:Math.round(c*100)+'% on one tile vs '+Math.round(cs.m*100)+'% avg'});
    r.t.forEach((v,i)=>{
      const zt=(v-tm[i])/(ts[i]||1);
      const st=zt>=4?'extreme':zt>=3.5?'strong':zt>=3?'notable':null; // stricter: 5000 tile-observations
      if(st) out.push({r:r.r, type:'tile', icon:'🎯', label:'Tile '+i+' flooded', z:zt, sev:st,
        detail:v.toFixed(2)+' SOL vs '+tm[i].toFixed(2)+' tile avg'});
    });
  });
  out.sort((a,b)=>Math.abs(b.z)-Math.abs(a.z));
  return out;
  },
  signals(){ if(!this._sigs) this._sigs=this.detect(); return this._sigs; },
  byRound(){
    if(this._map) return this._map;
    const m=new Map();
    this.signals().forEach(a=>{ if(!m.has(a.r)) m.set(a.r,[]); m.get(a.r).push(a); });
    this._map=m; return m;
  },
  forRoundId(id){ return this.byRound().get(id)||[]; },
  topFor(id){ const l=this.forRoundId(id); return l.length?l[0]:null; },
  has(id){ return this.byRound().has(id); },
  can(){ return Entitlements.can('anomaly'); },
  badgeHTML(id){
    const top=this.topFor(id); if(!top) return '';
    if(!this.can()) return `<span class="anom-badge" style="color:var(--faint);border-color:var(--line2)">⚡</span>`;
    const col={extreme:'var(--red)',strong:'var(--gold)',notable:'var(--dim)'}[top.sev]||'var(--dim)';
    return `<span class="anom-badge" style="color:${col};border-color:${col}">⚡ ${top.sev}</span>`;
  },
  filterClick(){
    if(!this.can()){ showIntel('radar'); return; }
    this.filterOn=!this.filterOn;
    const c=$('roundAnomChip'); if(c) c.classList.toggle('on', this.filterOn);
    renderRoundBrowser();
  },
};
function toggleRoundAnom(){ AnomalyModule.filterClick(); }
// ==== Anomaly radar (ANOMALY tier) ====
// z-score detection across pot, crowd, concentration, and per-tile floods

let _anomFilter='all';
function setAnomFilter(k){ _anomFilter=k;
  document.querySelectorAll('#anomFilterRow .chip').forEach(b=>b.classList.toggle('on', b.dataset.k===k));
  renderAnomaly(); }
function renderAnomaly(){
  const el=$('anomalyBody'); if(!el) return;
  if(!Entitlements.can('anomaly')){
    el.innerHTML=`<div class="card" style="border-color:var(--gold-line);text-align:center;padding:28px 20px">`
    +`<div style="font-size:40px;margin-bottom:10px">📡</div>`
    +`<div class="eyebrow" style="color:var(--gold);margin-bottom:8px">Members only</div>`
    +`<div style="font-size:19px;font-weight:800;margin-bottom:10px">The anomaly radar lives above every plan.</div>`
    +`<p style="color:var(--dim);font-size:14px;line-height:1.6;margin:0 0 14px">Pot shocks, ghost crowds, whale strikes and flooded tiles — every statistically unusual round, scored and ranked. Not even Lifetime includes this.</p>`
    +`<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:14px">`
    +`<span class="chip" style="cursor:default;border-color:var(--gold-line);color:var(--gold)">$7 / day</span>`
    +`<span class="chip" style="cursor:default">🔒 ${ANOMALY_SLOTS} slots · rolling 24h</span></div>`
    +`<div class="fineprint">Capped at 100 slots per rolling 24 hours — the cap is client-side in this build; server-side enforcement activates when the backend hosts. Preview with <b>?tier=anomaly</b>.</div></div>`;
    return;
  }
  const all=AnomalyModule.signals();
  const counts={extreme:0,strong:0,notable:0};
  all.forEach(a=>counts[a.sev]++);
  const rows=all.filter(a=>_anomFilter==='all'||a.sev===_anomFilter);
  const sevColor={extreme:'var(--red)',strong:'var(--gold)',notable:'var(--dim)'};
  el.innerHTML=`<div class="card" style="border-color:var(--gold-line)"><div class="card-h"><span class="eyebrow" style="margin:0;color:var(--gold)">📡 Radar scan</span><span class="tag tag-gold">${all.length} signals</span></div>`
  +`<div class="stat-grid"><div class="stat-tile"><div class="v" style="color:var(--red)">${counts.extreme}</div><div class="l">extreme</div></div>`
  +`<div class="stat-tile"><div class="v" style="color:var(--gold)">${counts.strong}</div><div class="l">strong</div></div>`
  +`<div class="stat-tile"><div class="v">${counts.notable}</div><div class="l">notable</div></div></div>`
  +`<div class="fineprint" style="margin-top:8px">|z| ≥ 2 vs the 200-round window. Descriptive — unusual rounds, not predictions.</div></div>`
  +`<div class="sortrow" id="anomFilterRow" role="tablist" aria-label="Filter by severity" style="margin:14px 0 10px">`
  +[['all','All'],['extreme','Extreme'],['strong','Strong'],['notable','Notable']].map(([v,l])=>`<button class="chip${_anomFilter===v?' on':''}" data-k="${v}" onclick="setAnomFilter('${v}')"> ${l}</button>`).join('')
  +`</div>`
  +`<div id="anomFeed">`+(rows.length?rows.slice(0,60).map(a=>`<div class="card" style="margin-bottom:10px"><div style="display:flex;gap:10px;align-items:center">`
   +`<div style="font-size:26px">${a.icon}</div>`
   +`<div style="flex:1;min-width:0"><div style="font-weight:800">${a.label} <span style="color:var(--dim);font-weight:400">· round #${a.r}</span></div>`
   +`<div class="row-sub">${a.detail} · z=${a.z>=0?'+':''}${a.z.toFixed(2)}</div></div>`
   +`<span class="tag" style="color:${sevColor[a.sev]};border-color:currentColor">${a.sev}</span></div>`
   +`<button class="btn-ghost" style="width:100%;margin-top:10px;min-height:44px" onclick="roundDetail(${a.r})">Open round ›</button></div>`).join('')
   :'<div class="empty"><span class="ic">📡</span>Nothing at this severity.</div>')+`</div>`
  +(rows.length>60?`<div class="note" style="text-align:center">Showing top 60 of ${rows.length} — tighten the filter.</div>`:'');
}
let _roundSort='new', _roundQ='';
function setRoundSort(k){ _roundSort=k;
  document.querySelectorAll('#roundSortRow .chip').forEach(b=>b.classList.toggle('on', b.dataset.k===k));
  renderRoundBrowser(); }
function setRoundQ(v){ _roundQ=(v||'').trim(); renderRoundBrowser(); }
function renderRoundBrowser(){
  const el=$('roundBrowserList'); if(!el) return;
  let rows=[...(D.rounds.recent||[])].filter(okR);
  if(_roundQ) rows=rows.filter(r=>String(r.r).includes(_roundQ));
  if(AnomalyModule.filterOn) rows=rows.filter(r=>AnomalyModule.has(r.r));
  if(_roundSort==='pot') rows.sort((a,b)=>b.d-a.d);
  else if(_roundSort==='miners') rows.sort((a,b)=>b.m-a.m);
  else rows.sort((a,b)=>b.r-a.r);
  const n=$('roundBrowserCount'); if(n) n.textContent=rows.length;
  if(!rows.length){ el.innerHTML='<div class="empty"><span class="ic">🔍</span>No rounds match.</div>'; return; }
  el.innerHTML=rows.map(r=>{
    const rt=rtime(r), ts=fmtT(rt.t).slice(0,5);
    const when=(rt.real?'':'~')+ts;
    return `<div class="row" role="button" tabindex="0" aria-label="Round ${r.r}, tile ${r.w} won, ${r.d.toFixed(1)} SOL${AnomalyModule.topFor(r.r)&&AnomalyModule.can()?', anomalous, '+AnomalyModule.topFor(r.r).sev:''}" onclick="roundDetail(${r.r})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();roundDetail(${r.r})}">`
    +`<div class="rank" style="border:1px solid var(--gold-line);color:var(--gold);background:var(--gold-soft)">${r.w}</div>`
    +`<div style="flex:1;min-width:0"><div style="font-weight:800">Round #${r.r}</div>`
    +`<div class="row-sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.d.toFixed(2)} SOL &middot; ${r.m}👥 &middot; ${when}</div></div>`
    +AnomalyModule.badgeHTML(r.r)
    +`<div class="chev">&rsaquo;</div></div>`;
  }).join('');
}
function renderLenses(targetId){
const el=$(targetId||'roundList'); if(!el) return;
const Wall=[...(D.rounds.recent||[])].filter(okR); // all 200 for the browser
const W=[...Wall].reverse().slice(0,100); // ~2h lens window
if(!Wall.length){ el.innerHTML='<div class="empty"><span class="ic">📊</span>No rounds in this window yet.</div>'; return; }
const cell=(r,tint)=>{
  const t=rtime(r).t;
  const wd=fmtD(t,{weekday:'short'}).toUpperCase();
  const md=fmtD(t,{month:'short',day:'numeric'}).toUpperCase();
  const ts=fmtT(t);
  return `<div class="lens" role="button" tabindex="0" aria-label="Round ${r.r}, tile ${r.w} won, ${r.d.toFixed(1)} SOL" onclick="roundDetail(${r.r})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();roundDetail(${r.r})}">`
  +`<div class="lens-date">${wd} \u00B7 ${md}</div>`
  +`<div class="lens-cell" style="background:${tint}">`
  +`<div class="lens-tile">${r.w}</div>`
  +`<div class="lens-round">#${r.r}</div>`
  +`<div class="lens-sol">${r.d.toFixed(1)} SOL</div></div>`
  +`<div class="lens-time">${ts}</div></div>`; };
let h='';
// ==== round browser (all 200) ====
h+='<div class="lbl" style="margin:2px 0 10px">All rounds &middot; <span id="roundBrowserCount">200</span></div>'
 +'<input id="roundSearch" type="search" inputmode="numeric" placeholder="Search round #…" aria-label="Search round number" '
 +'style="width:100%;background:#1a1a2e;border:1px solid var(--line);border-radius:12px;padding:12px 14px;color:var(--txt);font-size:16px;box-sizing:border-box;margin-bottom:10px" '
 +'oninput="setRoundQ(this.value)">'
 +'<div class="sortrow" id="roundSortRow" role="tablist" aria-label="Sort rounds">'
 +'<button class="chip on" data-k="new" onclick="setRoundSort(\'new\')">🕒 Newest</button>'
 +'<button class="chip" data-k="pot" onclick="setRoundSort(\'pot\')">💰 Biggest pot</button>'
 +'<button class="chip" data-k="miners" onclick="setRoundSort(\'miners\')">👥 Most miners</button>'
 +'</div>'
 +(Entitlements.can('anomaly')
   ? '<div style="margin:0 0 10px"><button class="chip" id="roundAnomChip" onclick="toggleRoundAnom()">⚡ Anomalies only</button></div>'
   : '<div style="margin:0 0 10px"><button class="chip" id="roundAnomChip" onclick="toggleRoundAnom()">🔒 Anomalies · members</button></div>')
 +'<div id="roundBrowserList" style="max-height:520px;overflow-y:auto;overscroll-behavior:contain;border:1px solid var(--line);border-radius:var(--r-m);padding:0 6px;background:rgba(0,0,0,.18)"></div>'
 +'<div class="lbl" style="margin:20px 0 10px">Insights</div>';
// biggest pots — vibrant gold
const pots=[...W].sort((a,b)=>b.d-a.d).slice(0,6);
const maxD=Math.max(...pots.map(r=>r.d),0.001);
h+='<div class="lens-card"><div class="lens-head"><span>💰</span><span class="t">Biggest pots</span><span class="c">top 6</span></div><div class="rgrid">'+pots.map(r=>cell(r,potTint(r,maxD))).join('')+'</div></div>';
// most crowded — cool blue-violet
const crowd=[...W].sort((a,b)=>b.m-a.m).slice(0,6);
const maxM=Math.max(...crowd.map(r=>r.m),1);
h+='<div class="lens-card"><div class="lens-head"><span>👥</span><span class="t">Most miners</span><span class="c">top 6</span></div><div class="rgrid">'+crowd.map(r=>cell(r,crowdTint(r,maxM))).join('')+'</div></div>';
// window report (premium) — generated summary of the window
if(Entitlements.can('weekly')){
const _wr={}; W.forEach(r=>{_wr[r.w]=(_wr[r.w]||0)+1;});
const _tt=Object.entries(_wr).sort((a,b)=>b[1]-a[1])[0];
const _big=[...W].sort((a,b)=>b.d-a.d)[0];
const _avgP=W.reduce((a,r)=>a+r.d,0)/W.length;
const _avgM=W.reduce((a,r)=>a+r.m,0)/W.length;
const _wp=W.reduce((a,r)=>a+(r.d>0?Math.max(...r.t)/r.d:0),0)/W.length;
const _rpt=`ORE HAUNT window intel — ${W.length} rounds tracked. ${ _avgP.toFixed(1)} SOL average pot with ${_avgM.toFixed(0)} miners per round. `
 +`Tile ${_tt[0]} won the most (${_tt[1]} wins). Biggest pot: ${_big.d.toFixed(1)} SOL in round #${_big.r}. `
 +`Whale pressure averaged ${Math.round(_wp*100)}% of pot on the top tile. Winners are random — this is what happened, not what happens next.`;
h+='<div class="lens-card" style="border-color:var(--gold-line)"><div class="lens-head"><span>📰</span><span class="t">Window report</span><span class="c">auto-generated</span></div>'
 +`<p style="font-size:14px;line-height:1.7;color:var(--txt)">${_rpt}</p>`
 +`<button class="btn-ghost" style="width:100%;margin-top:10px" onclick="shareText(${JSON.stringify(_rpt).replace(/</g,'&lt;')})">📤 Share report</button></div>`;
} else { h+=lockedCard('weekly','Window report','A shareable summary of the window, generated from this window.'); }
// profitability — honest cost metrics from the window (no payout data in bundle, so no invented ROI)
{const _totD=W.reduce((a,r)=>a+r.d,0),_totM=W.reduce((a,r)=>a+r.m,0);
const _avgPot=_totD/W.length,_perMiner=_totM?_totD/_totM:0;
const _winShare=W.reduce((a,r)=>a+(r.d>0?r.t[r.w]/r.d:0),0)/W.length;
const _cheap=[...W].sort((a,b)=>a.d-b.d)[0]||{d:0,r:'—'}, _pricey=[...W].sort((a,b)=>b.d-a.d)[0]||{d:0,r:'—'};
h+='<div class="lens-card"><div class="lens-head"><span>💹</span><span class="t">Cost of play</span><span class="c">last 100</span></div>'
 +'<div class="stat-grid"><div class="stat-tile"><div class="v">'+_avgPot.toFixed(1)+' SOL</div><div class="l">avg pot / round</div></div>'
 +'<div class="stat-tile"><div class="v">'+_perMiner.toFixed(2)+' SOL</div><div class="l">avg cost / miner / round</div></div>'
 +'<div class="stat-tile"><div class="v">'+Math.round(_winShare*100)+'%</div><div class="l">avg winning-tile share of pot</div></div></div>'
 +'<div class="stat-grid" style="margin-top:10px"><div class="stat-tile"><div class="v">'+_cheap.d.toFixed(1)+' SOL</div><div class="l">cheapest round #'+_cheap.r+'</div></div>'
 +'<div class="stat-tile"><div class="v">'+_pricey.d.toFixed(1)+' SOL</div><div class="l">priciest round #'+_pricey.r+'</div></div>'
 +'<div class="stat-tile"><div class="v">'+(_totM/W.length).toFixed(0)+'</div><div class="l">avg miners / round</div></div></div>'
 +'<div class="fineprint" style="margin-top:8px">What it typically costs to play, and how concentrated winning positions are. Not a prediction — every round is random.</div></div>';}
// whale consensus — combined top-10 fingerprint (premium)
if(Entitlements.can('consensus')){
const _top10=(D.leaders.leaders||[]).slice(0,10);
const _agg=new Array(25).fill(0);
_top10.forEach(L=>{ const F=D.fp[L.w]; if(F&&F.t) F.t.forEach((v,i)=>{_agg[i]+=v;}); });
const _atot=_agg.reduce((a,v)=>a+v,0);
const _top5=_agg.map((v,i)=>[v,i]).sort((a,b)=>b[0]-a[0]).slice(0,5);
h+='<div class="lens-card" style="border-color:var(--gold-line)"><div class="lens-head"><span>🐋</span><span class="t">Whale consensus</span><span class="c">top 10 combined</span></div>'
 +heatTiles(_agg,-1)
 +'<div style="margin-top:10px">'+_top5.map(([v,i])=>`<div class="kv"><span>Tile ${i}</span><b>${_atot?Math.round(v/_atot*100):0}% of whale SOL</b></div>`).join('')+'</div>'
 +'<div class="fineprint" style="margin-top:6px">Where the 10 biggest miners collectively deploy. Not a prediction.</div></div>';
} else { h+=lockedCard('consensus','Whale consensus','See where the top 10 miners collectively deploy.'); }
// cost per win per tile (premium) — honest cost metric
if(Entitlements.can('costperwin')){
const _dep=new Array(25).fill(0), _wn=new Array(25).fill(0);
W.forEach(r=>{ r.t.forEach((v,i)=>{_dep[i]+=v;}); _wn[r.w]++; });
const _cpw=_dep.map((d,i)=>({t:i,cpw:_wn[i]?d/_wn[i]:Infinity,wn:_wn[i]})).filter(x=>x.wn>0).sort((a,b)=>a.cpw-b.cpw);
const _mx=_cpw.length?_cpw[_cpw.length-1].cpw:1;
h+='<div class="lens-card"><div class="lens-head"><span>🎯</span><span class="t">Cost per win</span><span class="c">by tile</span></div>'
 +_cpw.slice(0,8).map(x=>`<div class="row" style="padding:8px 4px"><div class="streak-tile">${x.t}</div>`
  +`<div style="flex:1"><div class="streak-bar"><i style="width:${Math.max(4,Math.round(x.cpw/_mx*100))}%"></i></div>`
  +`<div class="row-sub">${x.cpw.toFixed(1)} SOL deployed per win · ${x.wn} wins</div></div></div>`).join('')
 +'<div class="fineprint" style="margin-top:6px">Cheapest tiles to have won on — what winners cost, not what they paid out.</div></div>';
} else { h+=lockedCard('costperwin','Cost per win','Which tiles were cheapest to win on.'); }
h+='<div class="fineprint" style="margin:4px 2px 0">Browser covers all 200 rounds. Lenses use the last 100 (~2h). Tap any round for its heat map. Round dates are estimates (~).</div>';
el.innerHTML=h;
renderRoundBrowser();
}

function heatTiles(t,winner,cls){const mx=Math.max(...t,0.000001);let h=`<div class="fp-grid"${cls?` id="${cls}"`:''}>`;
t.forEach((v,i)=>{const a=Math.min(1,v/mx);h+=`<div class="fp-tile${i===winner?' win':''}" style="border-color:${fpEdge(a)}"><span>${i}</span><span class="v">${v>=10?v.toFixed(0):v.toFixed(2)}</span></div>`});return h+'</div>'}
function roundDetail(rn){if(typeof cmpXP==='function')cmpXP(5);const r=(D.rounds.recent||[]).find(x=>x.r===rn);if(!okR(r))return;
const list=$('roundList'),wrap=$('roundDetailWrap'),body=$('roundDetailBody');
if(!list||!wrap||!body) return;
body.innerHTML='<div class="card"><div class="skel" style="height:120px;margin-bottom:12px"></div><div class="skel" style="height:220px"></div></div>';
list.classList.add('hidden'); wrap.classList.remove('hidden'); window.scrollTo(0,0);
requestAnimationFrame(()=>requestAnimationFrame(()=>{
const _Wall=[...(D.rounds.recent||[])].filter(okR);
const _avgD=_Wall.reduce((a,x)=>a+x.d,0)/Math.max(1,_Wall.length);
const _avgM=_Wall.reduce((a,x)=>a+x.m,0)/Math.max(1,_Wall.length);
const _conc=r.d>0?Math.max(...r.t)/r.d:0;
const _anom=AnomalyModule.topFor(r.r); const _canA=AnomalyModule.can();
const _dPct=_avgD?((r.d-_avgD)/_avgD*100):0, _mPct=_avgM?((r.m-_avgM)/_avgM*100):0;
body.innerHTML=`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Round #${r.r}</span>`
  +(_anom?(_canA
    ? `<button class="tag tag-gold" style="cursor:pointer" onclick="showIntel('radar')" aria-label="View anomaly radar">⚡ anomalous · ${_anom.label}</button>`
    : `<button class="tag" style="cursor:pointer" onclick="showIntel('radar')" aria-label="Anomaly detected — Anomaly members only">⚡ anomalous · members</button>`):'')
  +`</div>`
  +`<div class="big" style="color:var(--gold);font-size:26px">Tile ${r.w} wins</div>`
  +`<div class="stat-grid" style="margin-top:12px">`
  +`<div class="stat-tile"><div class="v">${r.d.toFixed(2)}</div><div class="l">SOL deployed</div></div>`
  +`<div class="stat-tile"><div class="v">${r.m}</div><div class="l">Miners</div></div>`
  +`<div class="stat-tile"><div class="v">${Math.round(_conc*100)}%</div><div class="l">on winning tile</div></div>`
  +`<div class="stat-tile"><div class="v" style="color:${_dPct>=0?'var(--green)':'var(--red)'}">${_dPct>=0?'+':''}${_dPct.toFixed(0)}%</div><div class="l">pot vs avg</div></div></div>`
  +`<div class="kv" style="margin-top:10px"><span>Winner's crowd</span><b>${r.t[r.w].toFixed(3)} SOL on tile ${r.w}</b></div>`
  +`<div class="kv"><span>Miners vs avg</span><b>${_mPct>=0?'+':''}${_mPct.toFixed(0)}% (${_avgM.toFixed(0)} avg)</b></div></div>`
  +`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Where the SOL went</span><span class="tag tag-gold">gold ring = winner</span></div>${heatTiles(r.t,r.w)}</div>`
  +`<div class="fineprint" style="margin:4px 2px 0">Averages across all 200 rounds. Times are estimates (~) unless from a replay.</div>`;
}));}
function closeRoundDetail(){const list=$('roundList'),wrap=$('roundDetailWrap');
if(wrap)wrap.classList.add('hidden'); if(list)list.classList.remove('hidden'); window.scrollTo(0,0);}
let _goReplaysAt=0, _booted=false;
function goReplays(){
  const now=Date.now();
  // DESIGN-OVERHAUL FIX: boot autoplay first — t-replay is visible by default at boot,
  // so the old order made the _booted branch dead and the app opened on an empty picker
  if(!_booted){ _booted=true; const rs=[...D.replays.replays].sort((a,b)=>b.round-a.round); if(rs.length){ openReplay(rs[0].round); return; } }
  const onReplaySection=!$('t-replay').classList.contains('hidden')||!$('t-view').classList.contains('hidden');
  if(onReplaySection){ show('t-replay'); renderTopPicker(); return; } // already in replay context -> picker, never autoplay
  if(now-_goReplaysAt<800) return; // debounce only the expensive openReplay branch
  _goReplaysAt=now;
  show('t-replay'); // post-boot: REPLAY nav always lands on the picker
  renderTopPicker();
}
// ==== Strategy simulator (single) ====
let _simTiles=new Set([12]);
function simToggle(i){ if(_simTiles.has(i)) _simTiles.delete(i); else _simTiles.add(i); renderLab(); }
function simPreset(name){
  const P={
    corners:[0,4,20,24],
    edges:[1,2,3,5,9,10,14,15,19,21,22,23],
    cross:[2,7,10,11,12,13,14,17,22],
    center:[6,7,8,11,12,13,16,17,18],
    all:[...Array(25).keys()],
  };
  _simTiles=new Set(name==='clear'?[]:(P[name]||[]));
  renderLab();
}
function renderLab(){
  const el=$('labBody'); if(!el) return;
  if(!Entitlements.can('simulator')){ el.innerHTML=lockedCard('simulator','Strategy simulator','Backtest tile strategies against 200 real rounds.'); return; }
  let tiles='';
  for(let i=0;i<25;i++) tiles+=`<div class="sim-tile${_simTiles.has(i)?' on':''}" role="button" tabindex="0" aria-label="tile ${i}" onclick="simToggle(${i})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();simToggle(${i})}">${i}</div>`;
  el.innerHTML=`<div class="card"><div class="card-h"><span class="eyebrow" style="margin:0">Your picks</span><span class="tag">${_simTiles.size} tiles</span></div>`
  +`<p style="color:var(--dim);font-size:13px;margin:6px 0 10px">Tap tiles to bet on them, or start from a preset.</p>`
  +`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">`
  +[['corners','Corners'],['edges','Edges'],['cross','Cross'],['center','Center'],['all','All 25'],['clear','Clear']].map(([v,l])=>`<button class="chip" onclick="simPreset('${v}')">${l}</button>`).join('')
  +`</div><div class="fp-grid" style="margin-bottom:12px">${tiles}</div>`
  +`<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">`
  +`<label style="flex:1;min-width:120px;font-size:12px;color:var(--dim)">SOL per tile / round<br><input id="simSol" type="number" min="0.01" step="0.01" value="0.1" style="width:100%;margin-top:4px;background:#1a1a2e;border:1px solid var(--line);border-radius:10px;padding:10px;color:var(--txt);font-size:16px;box-sizing:border-box"></label>`
  +`<label style="flex:1;min-width:120px;font-size:12px;color:var(--dim)">Rounds<br><input id="simN" type="number" min="10" max="200" step="10" value="100" style="width:100%;margin-top:4px;background:#1a1a2e;border:1px solid var(--line);border-radius:10px;padding:10px;color:var(--txt);font-size:16px;box-sizing:border-box"></label>`
  +`</div><button class="btn" style="width:100%;margin-top:12px" onclick="runSim()">Run backtest</button></div><div id="simOut" style="margin-top:12px"></div>`;
}
function runSim(){if(typeof cmpXP==='function')cmpXP(10);
  const out=$('simOut'); if(!out) return;
  const tiles=[..._simTiles];
  if(!tiles.length){ out.innerHTML='<div class="note">Pick at least one tile.</div>'; return; }
  const sol=Math.max(0.001, parseFloat(($('simSol')||{}).value)||0.1);
  let n=Math.min(200, Math.max(10, parseInt(($('simN')||{}).value)||100));
  const W=[...(D.rounds.recent||[])].filter(okR).slice(-n);
  if(!W.length){ out.innerHTML='<div class="note">No rounds to test against.</div>'; return; }
  let hits=0; const perTile={};
  tiles.forEach(t=>perTile[t]=0);
  W.forEach(r=>{ if(tiles.indexOf(r.w)>=0){ hits++; perTile[r.w]++; } });
  const cost=tiles.length*sol*W.length, hitRate=hits/W.length*100, cph=hits?cost/hits:0;
  const baseRate=tiles.length/25*100;
  const best=tiles.slice().sort((a,b)=>perTile[b]-perTile[a])[0];
  out.innerHTML=`<div class="card"><div class="eyebrow">Results · last ${W.length} rounds</div>`
  +`<div class="stat-grid" style="margin-top:10px">`
  +`<div class="stat-tile"><div class="v">${hitRate.toFixed(1)}%</div><div class="l">hit rate</div></div>`
  +`<div class="stat-tile"><div class="v">${hits}/${W.length}</div><div class="l">rounds hit</div></div>`
  +`<div class="stat-tile"><div class="v">${cost.toFixed(2)} SOL</div><div class="l">total cost</div></div>`
  +`<div class="stat-tile"><div class="v">${hits?cph.toFixed(2)+' SOL':'—'}</div><div class="l">cost per hit</div></div></div>`
  +`<div class="fineprint" style="margin-top:10px">A random ${tiles.length}-tile pick hits ~${baseRate.toFixed(1)}% of rounds. `
  +(hitRate>baseRate+1?'Your picks beat random in this window.':hitRate<baseRate-1?'Your picks trailed random in this window.':'About in line with random.')+' '
  +`Best tile: #${best} (${perTile[best]} hits). Past rounds don't predict future winners.</div></div>`;
  out.scrollIntoView({behavior:'smooth',block:'nearest'});
}

// ==== StatShare: share stats as chat cards ====
const StatShare={
  getStats(){
    const rounds=(D.rounds.recent||[]).filter(okR);
    if(!rounds||!rounds.length) return [];
    const pots=[...rounds].sort((a,b)=>b.d-a.d).slice(0,3);
    const maxD=Math.max(...rounds.map(r=>r.d));
    return [
      {icon:'💰', label:'Biggest pot', value:pots[0].d.toFixed(2)+' SOL', sub:'round #'+pots[0].r},
      {icon:'👥', label:'Most miners', value:Math.max(...rounds.map(r=>r.m)), sub:'in one round'},
      {icon:'📊', label:'Avg pot', value:(rounds.reduce((s,r)=>s+r.d,0)/rounds.length).toFixed(2)+' SOL', sub:rounds.length+' rounds'},
    ];
  },
  share(idx){
    const stats=this.getStats();
    const st=stats[idx];
    if(!st) return;
    closeStatShare();
    const text=`ORE HAUNT — ${st.label}: ${st.value} (${st.sub})`;
    if(navigator.share){ navigator.share({title:'ORE HAUNT', text}).catch(()=>{}); }
    else if(navigator.clipboard){ navigator.clipboard.writeText(text).then(()=>toast('Stat copied — paste it anywhere')).catch(()=>{}); }
    else { toast(text); }
  }
};
function openStatShare(){
  let dlg=document.getElementById('statShareDlg');
  if(!dlg){
    dlg=document.createElement('div');
    dlg.id='statShareDlg';
    dlg.style.cssText='position:fixed;left:12px;right:12px;bottom:80px;z-index:99;background:#12121f;border:1px solid var(--amber);border-radius:16px;padding:16px;box-shadow:0 -8px 32px rgba(0,0,0,.6)';
    document.body.appendChild(dlg);
  }
  const stats=StatShare.getStats();
  dlg.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <b>📊 Share a stat</b>
    <button onclick="closeStatShare()" style="background:none;border:none;color:var(--dim);font-size:18px;cursor:pointer;padding:4px 8px">✕</button></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">`+
    stats.map((st,i)=>`<button onclick="StatShare.share(${i})" style="padding:12px;border-radius:12px;background:var(--panel);border:1px solid var(--line);cursor:pointer;text-align:center">
      <div style="font-size:20px">${st.icon}</div>
      <div style="font-size:12px;color:var(--dim);margin:4px 0">${st.label}</div>
      <div style="font-size:16px;font-weight:800;color:var(--amber)">${st.value}</div>
      <div style="font-size:10px;color:var(--dim)">${st.sub}</div>
    </button>`).join('')+`</div>`;
  dlg.style.display='block';
}
function closeStatShare(){
  const dlg=document.getElementById('statShareDlg');
  if(dlg) dlg.style.display='none';
}
let _toastT=null;
function toast(msg){
  let t=document.getElementById('toast');
  if(!t){ t=document.createElement('div'); t.id='toast';
    t.style.cssText='position:fixed;left:50%;bottom:110px;transform:translateX(-50%);z-index:200;background:#1a1a2e;border:1px solid var(--gold-line);color:var(--txt);border-radius:12px;padding:10px 16px;font-size:13px;max-width:84vw;text-align:center;box-shadow:var(--shadow);display:none';
    document.body.appendChild(t); }
  t.textContent=msg; t.style.display='block';
  clearTimeout(_toastT); _toastT=setTimeout(()=>{ t.style.display='none'; }, 2200);
}
// Escape HTML at the trust boundary — single helper for all interpolations
function esc(v){ return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
// default landing: latest animation (called at end of script)
let ENG=null, LOOPGEN=0, currentRound=null, miniENGs=[], _replayFailed=false;
let _watchdog=null;
// Stop all replay animation cleanly — no background loops
function stopReplay(){
  LOOPGEN++; // kills main loop and watchdog
  if(_watchdog){ clearInterval(_watchdog); _watchdog=null; }
  if(window._hauntRO){ try{ window._hauntRO.disconnect(); }catch(e){} window._hauntRO=null; }
  if(window._miniRO){ try{ window._miniRO.disconnect(); }catch(e){} window._miniRO=null; }
  if(ENG){ try{ ENG.pause(); }catch(_){} ENG=null; }
  if(typeof miniENGs!=='undefined') miniENGs.forEach(e=>{ try{ e.pause(); }catch(_){} });
  miniENGs=[];
  try{ const A=HauntReplay.Audio; if(A.ctx&&A.ctx.state==='running') A.ctx.suspend(); }catch(e){}
}
function renderMiniStrip(){
  const el=document.getElementById('miniStrip'); if(!el) return;
  const cl=document.getElementById('carouselLbl');
  if(cl){ const tab=(typeof pickTab!=='undefined')?pickTab:'hour';
    const cap=tab.charAt(0).toUpperCase()+tab.slice(1);
    cl.textContent='\uD83C\uDFAC '+(tab==='hour'?'last 4 \u2014 newest':('top 4 \u2014 '+cap)); }
  miniENGs.forEach(e=>{ try{e.pause();}catch(_){} }); miniENGs=[];
  const order=replayOrder().slice(0,4);
  if(!order.length){ el.innerHTML=''; return; }
  el.innerHTML=order.map(rn=>{
    const R=D.replays.replays.find(x=>x.round===rn);
    const t=R&&R.timeline[0]?fmtT(R.timeline[0].t).slice(0,5):'';
    const d=R&&R.timeline[0]?fmtD(R.timeline[0].t,{month:'short',day:'numeric'}):'';
    return `<div class="mini-card${rn===currentRound?' on':''}" onclick="if(rn===currentRound&&ENG)return;openReplay(${parseInt(rn,10)})" role="button" tabindex="0" aria-label="Replay round ${rn}" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openReplay(parseInt(rn,10))}">
    <canvas id="mini${parseInt(rn,10)}"></canvas>
    <div class="mini-meta"><b>#${rn}</b><span>${d} · ${t}</span></div></div>`;
  }).join('');
  order.forEach(rn=>{
    const R=D.replays.replays.find(x=>x.round===rn); if(!R) return;
    const cv=document.getElementById('mini'+rn); if(!cv) return;
    // Ensure canvas has dimensions before engine creation
    if(cv.clientWidth===0){ cv.style.minHeight='80px'; }
    let attempts=0;
    while(attempts<2){
      try{
        const e=new HauntReplay.Engine(cv,R,{silent:true}); miniENGs.push(e); e.play();
        cv.style.minHeight=''; // clear fallback after success
        break;
      }catch(e2){ attempts++; if(attempts>=2){ const _w=cv.parentElement; if(_w){ const _n=document.createElement('div'); _n.className='note'; _n.style.cssText='font-size:10px;padding:8px'; _n.textContent='replay unavailable'; _w.appendChild(_n); } } }
    }
  });
}
function openReplay(rn){if(typeof cmpXP==='function')cmpXP(15);
  const R=D.replays.replays.find(x=>x.round===rn); if(!R) return;
  currentRound=rn;
  _replayFailed=false; // optimistic; set true only if construction fails below
  stopReplay(); // kill previous animation before starting new one — seamless, no background loops
  const b=$('replayBody');
  const t0=R.timeline[0]?R.timeline[0].t:null;
  const dstr=t0?fmtD(t0,{weekday:'short',month:'short',day:'numeric'}).toUpperCase():'';
  const tstr=t0?fmtT(t0).slice(0,5)+' '+tzAbbr():'';
  const miners=R.agg&&R.agg.m!=null?R.agg.m:'\u2014';
  /* DESIGN-OVERHAUL: player markup restyled — same IDs (.hh-date/.hh-time spans for refreshReplayPills, #hudClock, #muteBtn, #stage, #rpc, #aftermath), same host-loop logic */
  b.innerHTML=`<div class="player">
    <div id="stage"><canvas id="rpc"></canvas><div id="aftermath" class="aftermath-ov"></div></div>
    <div class="telemetry">
      <div class="eyebrow" style="margin-bottom:10px">Round telemetry · live</div>
      ${StatsModule.render()}
    </div>
    <div class="hh">
      <div class="hh-row">
        <div class="hh-date"><span>\uD83D\uDCC5</span><span>${dstr}</span></div>
        <div class="hh-round">ROUND #${R.round} \u00B7 ${R.deploys} DEPLOYS</div>
      </div>
      <div class="hh-row">
        <div class="hh-time"><span>\uD83D\uDD50</span><span>${tstr}</span></div>
        <div class="hh-clock" id="hudClock">--:--:--</div>
        <div class="hh-miners"><span>⛏</span><span>${miners}</span></div>
        <button class="hh-mute" id="muteBtn" onclick="toggleMute()" aria-label="Mute">${HauntReplay.Audio.muted?'\uD83D\uDD07':'\uD83D\uDD0A'}</button>
      </div>
    </div>
  </div>
`;
  const cv=$('rpc');
  // Bulletproof engine creation with retry
  let engineAttempts=0;
  while(engineAttempts<3){
    try{
      ENG=new HauntReplay.Engine(cv,R);
      break;
    }catch(e){
      engineAttempts++;
      if(engineAttempts>=3){
        LOOPGEN++; ENG=null; _replayFailed=true; // kill any stale loop before showing error
        b.innerHTML='<div class="note" style="border-color:#f66"><b>Replay #'+rn+' failed to load.</b><br><button onclick="openReplay('+rn+')" style="margin-top:8px;padding:8px 16px;min-height:44px;border-radius:8px;background:var(--amber);color:#000;border:none;font-weight:700;cursor:pointer">Retry</button></div>';
        show('t-view');
        return;
      }
    }
  }
  /* DESIGN-OVERHAUL: aftermath is now an event overlay on the stage (same #aftermath hook) */
  ENG.onReset=()=>{ const el=$('aftermath'); if(el){ el.innerHTML=''; el.classList.remove('show'); } StatsModule.reset(); };
  ENG.onAfter=()=>{ const el=$('aftermath');
    el.innerHTML=`<div class="aftermath-card"><div class="eyebrow">Winner revealed</div><div class="aftermath-tile">TILE ${R.winner}</div><div class="aftermath-sub">${ENG.totalSol.toFixed(2)} SOL across ${R.deploys} deploys</div></div>`;
    el.classList.add('show'); };
  if(window._hauntRO) window._hauntRO.disconnect();
  window._hauntRO=new ResizeObserver(()=>ENG&&ENG.resize());
  window._hauntRO.observe($('stage'));
  if(window._miniRO) window._miniRO.disconnect();
  window._miniRO=new ResizeObserver(()=>{ if(typeof miniENGs!=='undefined') miniENGs.forEach(m=>{ try{ m.resize(); }catch(e){} }); });
  const _ms=$('miniStrip'); if(_ms) window._miniRO.observe(_ms);

  const gen=++LOOPGEN; let lastT=0; let lastBeat=Date.now(); let errStrikes=0; let queued=false; let hudTick=0;
  function loop(ts){
    queued=false; // this callback is firing — it is no longer merely queued
    if(gen!==LOOPGEN||!ENG) return;
    if(document.hidden){ requestAnimationFrame(loop); queued=true; return; } // pause when tab hidden
    const dt=Math.min(.05,(ts-lastT)/1000||0); lastT=ts;
    try{
      ENG.update(dt); ENG.draw();
      // HUD updates (protected — a throw here used to kill the rAF chain silently)
      if(typeof miniENGs!=='undefined') for(const m of miniENGs){ try{
        if(m.round.round===currentRound && ENG){
          // 1-to-1 sync: mirror the main engine's exact state
          m.t=ENG.t; m.tileSol.set(ENG.tileSol); m.tileFlash.set(ENG.tileFlash);
          m.totalSol=ENG.totalSol; m.deploysShown=ENG.deploysShown; m.evIdx=ENG.evIdx;
          m.phase=ENG.phase; m.phaseT=ENG.phaseT; m.leader=ENG.leader;
          m.draw();
        } else if(m.playing){ m.update(dt); m.draw(); }
      }catch(_){} }
      StatsModule.update(ENG);
      if((hudTick++%15)===0){ const hc=$('hudClock'); if(hc&&ENG.t0){ hc.textContent=fmtT(new Date(ENG.t0+ENG.t).toISOString())+' '+tzAbbr(); } }
      errStrikes=0; // reset on success
    }catch(e){
      // Engine or HUD crashed mid-frame — try to recover, with circuit breaker
      console.error('Replay loop error:', e);
      errStrikes++;
      if(errStrikes>=3){
        LOOPGEN++; // breaker wins: no watchdog resurrection, no mirror of a dead replay
        if(_watchdog){ clearInterval(_watchdog); _watchdog=null; }
        const failedRound=currentRound;
        ENG=null;
        const b=$('replayBody');
        if(b) b.innerHTML='<div class="note" style="border-color:#f66"><b>Replay #'+failedRound+' failed.</b><br><button onclick="openReplay('+failedRound+')" style="margin-top:8px;padding:8px 16px;min-height:44px;border-radius:8px;background:var(--amber);color:#000;border:none;font-weight:700;cursor:pointer">Retry</button></div>';
        return;
      }
      try{ ENG.reset(); }catch(_){}
    }
    lastBeat=Date.now();
    requestAnimationFrame(loop); queued=true;
  }
  // Kick the loop immediately — without this, nothing animates until watchdog fires (~4s)
  requestAnimationFrame(loop); queued=true;
  // Watchdog: heartbeat-based — restarts loop if no frame for 3s
  _watchdog=setInterval(()=>{
    if(gen!==LOOPGEN){ clearInterval(_watchdog); _watchdog=null; return; }
    if(document.hidden) return; // backgrounded tabs self-resume; never queue duplicates
    if(ENG && !queued && Date.now()-lastBeat>3000){
      lastT=0; lastBeat=Date.now();
      requestAnimationFrame(loop); queued=true;
    }
  }, 2000);
  show('t-view');
  ENG.play(); // autoplay
  // Render minis after view is visible so canvases have dimensions
  renderMiniStrip();
  if(!window._hauntAudioUnlock){ window._hauntAudioUnlock=true;
    document.addEventListener('pointerdown', ()=>{ try{ if(ENG&&!ENG.silent&&ENG.playing) HauntReplay.Audio.ensure(); }catch(e){} }); }
  renderTopPicker();
}

function playRound(rn){
  openReplay(rn);
  requestAnimationFrame(()=>{ const p=document.getElementById('replayBody'); if(p) p.scrollIntoView({block:'start'}); });
}
function replayOrder(){
  const now=Date.now(); const dayStart=tzDayStart();
  const seen={};
  const reps=[...D.replays.replays].filter(R=>R.timeline&&R.timeline[0]&&Number.isFinite(Date.parse(R.timeline[0].t))).filter(R=>{ if(seen[R.round]) return false; seen[R.round]=1; return true; });
  const tab=(typeof pickTab!=='undefined')?pickTab:'hour';
  let out;
  if(tab==='hour') out=reps.filter(R=>Date.parse(R.timeline[0].t)>=now-3600e5).sort((a,b)=>b.round-a.round).map(R=>R.round);
  else out=reps.filter(R=>Date.parse(R.timeline[0].t)>=dayStart&&daypartOf(R.timeline[0].t)===tab)
    .sort((a,b)=>(b.agg?b.agg.d:0)-(a.agg?a.agg.d:0)).map(R=>R.round);
  // DESIGN-OVERHAUL: stale seed data would otherwise leave prev/next, the carousel
  // and step navigation permanently empty (dead UI) — fall back to newest-first
  if(!out.length) out=reps.sort((a,b)=>b.round-a.round).map(R=>R.round);
  return out;
}
function stepReplay(d){
  // Navigate the carousel's 4 items (last 4 newest) with full loop logic
  const order=replayOrder().slice(0,4);
  if(!order.length) return;
  let i=order.indexOf(currentRound);
  if(i<0){
    // Not in carousel — start from edge
    i=d>0?0:order.length-1;
  } else {
    // Wrap around: prev from first → last, next from last → first
    i=(i+d+order.length)%order.length;
  }
  if(order[i]===currentRound&&ENG) return; // same replay — no teardown flicker
  openReplay(order[i]);
  // renderMiniStrip runs inside openReplay; no second rebuild
}
let pickTab='hour'; // 'hour'|'morning'|'afternoon'|'evening'|'night'
function setPickTab(t){ pickTab=t; renderTopPicker(); }
function tzDayStart(){
  // DST-safe: decompose "today midnight in TZ" via formatToParts, binary-search the UTC instant
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:TZ,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
  const g=t=>parts.find(p=>p.type===t).value;
  const ymd=g('year')+'-'+g('month')+'-'+g('day');
  const fmt=new Intl.DateTimeFormat('en-US',{timeZone:TZ,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  let lo=Date.now()-86400000*2, hi=Date.now()+86400000;
  for(let i=0;i<40&&lo<hi;i++){ const mid=(lo+hi)/2; const fp=fmt.formatToParts(new Date(mid)); const gg=t=>fp.find(p=>p.type===t).value;
    const key=gg('year')+'-'+gg('month')+'-'+gg('day')+' '+gg('hour')+':'+gg('minute');
    if(key<ymd+' 00:00') lo=mid+1; else hi=mid; }
  return Math.floor(lo/60000)*60000;
}
function daypartOf(iso){
  try{
    const h=(+new Intl.DateTimeFormat('en-US',{timeZone:TZ,hour:'2-digit',hour12:false}).format(new Date(iso)))%24;
    return h<5?'night':h<12?'morning':h<17?'afternoon':h<21?'evening':'night';
  }catch(e){ return 'night'; }
}
function pickCard(R,mx){
  const _isWatching=R.round===currentRound&&(typeof ENG!=='undefined')&&ENG;
  const endMsP=R.timeline&&R.timeline.length?Date.parse(R.timeline[R.timeline.length-1].t):0;
  const t=R.timeline[0]?fmtT(R.timeline[0].t).slice(0,5):'--:--';
  const sol=R.agg?R.agg.d:0;
  const wd=R.timeline[0]?fmtD(R.timeline[0].t,{weekday:'short'}).toUpperCase():'';
  const md=R.timeline[0]?fmtD(R.timeline[0].t,{month:'short',day:'numeric'}).toUpperCase():'';
  return `<div class="rpick${R.round===currentRound?' on':''}" onclick="openReplay(${parseInt(R.round,10)})" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openReplay(parseInt(R.round,10))}" role="button" tabindex="0" aria-label="Replay round ${parseInt(R.round,10)}, ${esc(wd)} ${esc(md)}, ${esc(t)}, tile ${parseInt(R.winner,10)}">`
    +(_isWatching?'<span class="watching">▶ WATCHING</span>':'')
    +`<div class="rpick-day"><span class="d">${wd}</span><span class="m">${md}</span></div>`
    +`<div class="rpick-tile"><span class="n">${R.winner}</span><span class="l">WINNER</span></div>`
    +`<div class="rpick-meta"><span>\uD83D\uDD50 ${t}</span><span>${R.deploys} deploys</span></div>`
    +`<div class="rpick-ago"><span class="ago-el" data-end="${Number.isFinite(endMsP)?endMsP:0}">${Number.isFinite(endMsP)&&endMsP?esc(agoText(endMsP)):''}</span></div>`
    +`<div class="rpick-bar"><i style="width:${Math.round(sol/mx*100)}%"></i></div></div>`;
}
// ---- LAST-4 PAGE: elapsed-time labels (header strip + per-card; text-only, single interval) ----
function agoText(endMs){
  const s=Math.max(0,Math.floor((Date.now()-endMs)/1000));
  const m=Math.floor(s/60);
  if(m<120) return m+' min ago';
  const h=Math.floor(m/60);
  if(h<48){ const mm=m%60; return h+' h'+(mm?' '+mm+' min':'')+' ago'; }
  const d=Math.floor(h/24), hh=h%24;
  return d+' d'+(hh?' '+hh+' h':'')+' ago';
}
let _agoInt=null, _agoEnd=0;
function tickAgo(){
  const els=document.querySelectorAll('.ago-el');
  if(!els.length){ if(_agoInt){ clearInterval(_agoInt); _agoInt=null; } return; }
  els.forEach(el=>{ const e=+el.getAttribute('data-end'); if(Number.isFinite(e)&&e>0) el.textContent=agoText(e); });
}
function durText(R){
  if(!R.timeline||!R.timeline.length) return '\u2014';
  const s=Math.max(0,Math.round((Date.parse(R.timeline[R.timeline.length-1].t)-Date.parse(R.timeline[0].t))/1000));
  return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');
}
function lastBoard(R){
  const t=(R.agg&&R.agg.t)||[];
  const mx=Math.max(...t,0.000001);
  let h='<div class="l4-board">';
  t.forEach((v,i)=>{ const a=Math.min(1,v/mx);
    h+=`<div class="fp-tile${i===R.winner?' win':''}" style="border-color:${fpEdge(a)}"><span class="v">${v>=10?v.toFixed(0):v.toFixed(2)}</span></div>`; });
  return h+'</div>';
}
function fullCard(R){
  const rn=parseInt(R.round,10), w=parseInt(R.winner,10);
  const watching=rn===currentRound&&(typeof ENG!=='undefined')&&ENG;
  const t0=R.timeline&&R.timeline[0]?R.timeline[0].t:null;
  const endMs=R.timeline&&R.timeline.length?Date.parse(R.timeline[R.timeline.length-1].t):0;
  const dstr=t0?fmtD(t0,{weekday:'short',month:'short',day:'numeric'}).toUpperCase():'';
  const wd=t0?fmtD(t0,{weekday:'short'}).toUpperCase():'', md=t0?fmtD(t0,{month:'short',day:'numeric'}).toUpperCase():'';
  const tstr=t0?fmtT(t0).slice(0,5)+' '+tzAbbr():'';
  const tot=R.agg?R.agg.d:0, winSol=(R.agg&&R.agg.t&&R.agg.t[w]!=null)?R.agg.t[w]:0;
  const mins=R.agg&&R.agg.m!=null?R.agg.m:'\u2014';
  const aria='Replay round '+rn+', '+dstr+', '+tstr+', tile '+w+' won with '+winSol.toFixed(3)+' SOL, '+tot.toFixed(3)+' SOL total, '+mins+' miners, '+R.deploys+' deploys';
  return `<div class="l4-card${rn===currentRound?' on':''}" onclick="openReplay(${rn})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openReplay(${rn})}" role="button" tabindex="0" aria-label="${esc(aria)}">`
    +`<div class="l4-head"><span class="l4-round">#${rn}</span>${watching?'<span class="l4-watch">\u25B6 WATCHING</span>':'<span class="l4-play">\u25B6 PLAY</span>'}</div>`
    +`<div class="l4-ago"><span class="ago-el" data-end="${Number.isFinite(endMs)?endMs:0}">${Number.isFinite(endMs)&&endMs?esc(agoText(endMs)):''}</span></div>`
    +lastBoard(R)
    +`<div class="l4-win"><span class="crown">\uD83C\uDFC6</span><b>TILE ${w}</b><span>\u00B7 ${winSol.toFixed(3)} SOL on it</span></div>`
    +`<div class="l4-stats"><div class="l4-stat"><div class="v">${tot.toFixed(3)}</div> <div class="l">Total SOL</div></div>`
    +`<div class="l4-stat"><div class="v">${mins}</div> <div class="l">Miners</div></div>`
    +`<div class="l4-stat"><div class="v">${R.deploys}</div> <div class="l">Deploys</div></div></div>`
    +`<div class="l4-when">${wd?`<span class="l4-wd">${esc(wd)}, </span>`:''}${esc(md)} \u00B7 ${esc(tstr)} \u00B7 \u23F1 ${durText(R)}</div></div>`;
}
function renderTopPicker(){
  const el=document.getElementById('topPicker'); if(!el) return;
  const seen={};
  const reps=[...D.replays.replays].filter(R=>R.timeline&&R.timeline[0]&&Number.isFinite(Date.parse(R.timeline[0].t))).filter(R=>{ if(seen[R.round]) return false; seen[R.round]=1; return true; });
  if(!reps.length){ el.innerHTML='<div class="empty"><span class="ic">👻</span>No replays in this window yet.</div>'; return; }
  reps.sort((a,b)=>b.round-a.round);
  if(!Entitlements.can('allreplays')) reps.splice(1);
  const last4=reps.slice(0,4), earlier=reps.slice(4);
  const mx=Math.max(...reps.map(R=>R.agg?R.agg.d:0),0.000001);
  const endMs=Date.parse(last4[0].timeline[last4[0].timeline.length-1].t);
  let h=`<div class="l4-timer"><span class="ic">\u23F1</span><span class="tx">Last replay \u00B7 ended</span><b id="lastAgo" class="ago-el" data-end="${Number.isFinite(endMs)?endMs:0}">\u2014</b></div>`;
  h+='<div class="l4-grid">'+last4.map(fullCard).join('')+'</div>';
  if(earlier.length){
    h+='<div class="reel-label"><span class="t">Earlier replays</span><span class="line"></span></div><div class="rfilm">';
    earlier.forEach(R=>{ h+=pickCard(R,mx); });
    h+='</div>';
  }
  el.innerHTML=h;
  _agoEnd=Number.isFinite(endMs)?endMs:0;
  tickAgo();
  if(!_agoInt&&_agoEnd) _agoInt=setInterval(tickAgo,20000);
}

function toggleMute(){ const m=HauntReplay.Audio.toggleMute(); $('muteBtn').textContent=m?'\uD83D\uDD07':'\uD83D\uDD0A'; }
(function(){try{const m=D.meta&&D.meta.built_at?Math.max(0,Math.round((Date.now()-Date.parse(D.meta.built_at))/60000)):null;
const age=m==null?'stale':(m<1?'just now':(m<60?m+' min old':Math.floor(m/60)+'h '+(m%60)+'m old'));
var acol=m==null?'var(--red)':(m>360?'var(--red)':(m>60?'var(--amber)':'var(--green)'));
const _rc=(D.rounds&&D.rounds.count)||0;
$('asof').innerHTML='ORE miner intelligence · top 150 by volume · '+_rc+' rounds · <span style="white-space:nowrap">data <b style="color:'+acol+'">'+age+'</b></span>';}catch(e){}})();
$('mEvents').textContent=((D.meta&&D.meta.events_funded)||0).toLocaleString()+' funded deploys';
$('mRounds').textContent=((D.rounds&&D.rounds.count)||0).toLocaleString()+' rounds · ORE V4 program';
let _obS=0;
function obShow(){ const o=$('onboard'); if(o) o.classList.remove('hidden'); }
function obHide(){ const o=$('onboard'); if(o) o.classList.add('hidden'); try{localStorage.setItem('haunt_onboarded','1');}catch(_){} }
function obGo(n){
  _obS=Math.max(0,Math.min(2,n));
  document.querySelectorAll('.ob-slide').forEach(el=>el.classList.toggle('on',+el.dataset.s===_obS));
  document.querySelectorAll('.ob-dot').forEach((el,i)=>el.classList.toggle('on',i===_obS));
  const nx=$('obNext'); if(nx) nx.textContent=_obS===2?'Start haunting':'Next';
  const sk=$('obSkip'); if(sk) sk.style.display=_obS===2?'none':'';
}
function obNext(){ if(_obS>=2){ obHide(); } else obGo(_obS+1); }
(function(){
  let seen=false;
  try{ seen=!!localStorage.getItem('haunt_onboarded'); }catch(_){}
  if(!seen) setTimeout(obShow,300);
})();
if(typeof renderCompanion==='function') renderCompanion();
if(typeof renderFAQ==='function') renderFAQ();
if(typeof renderTierTable==='function') renderTierTable();
if(typeof renderPlanTiers==='function') renderPlanTiers();
renderLenses();
goReplays();
</script>
</body>
</html>"""
html = html.replace("__DATA__", data_js)
# TREASURY GUARD (added 2026-10-12): the build FAILS LOUDLY rather than ever
# shipping a checkout whose payments point anywhere but the owner's wallet
# (raaru.skr, resolved on-chain via AllDomains ANS). If the treasury is ever
# changed deliberately, change EXPECTED_TREASURY in the same commit and note
# it in PATCH_NOTES.md.
EXPECTED_TREASURY = "FRShnxX7vi64gtBoA6JPmH9MBuVqtheEQaxA1D17wKdP"
_n = html.count(EXPECTED_TREASURY)
assert _n == 1, f"TREASURY GUARD: expected exactly 1 occurrence of the treasury address in the build, found {_n} — refusing to write haunt.html"
out = os.path.join(HERE, "haunt.html")
open(out, "w").write(html)
print(f"wrote {out} ({os.path.getsize(out)//1024}KB)")
