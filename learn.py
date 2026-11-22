#!/usr/bin/env python3
"""learn.py - the Leonida Replay improvement loop.

Every post carries a recipe:
  edit genes   - how the clip was cut and styled (chosen here, applied by render.py / finish.py)
  clip traits  - what kind of clip it was (streamer, topics, length, how fast it was rising, post hour)
After Instagram numbers come in, each post gets a score relative to its neighbours (so a slow week
doesn't punish everything), and every gene option / trait value gets a running "lift".

  choose()        -> edit genes for the next clip: mostly the best-scoring options, some exploration,
                     and anything never tried gets tried first
  rank(meta, base)-> base rising-score x learned multipliers for streamer, topics, length, trend boost
  ingest(path)    -> read a Windsor Instagram metrics json and score posts
  report()        -> LEARNING.md

  python3 learn.py ingest metrics.json
  python3 learn.py report
"""
import calendar, json, math, os, random, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, 'state', 'history.json')     # written only by the posting runs
TREND = os.path.join(HERE, 'state', 'trend.json')      # written only by the fetch runs (GitHub Actions)

# ---- edit genes: option lists. New options get added by the weekly lab run. -----------------------
GENES = {
    'max_len':    [20, 30, 45, 60],              # seconds kept after smart trim
    'trim':       ['smart', 'none'],             # smart = best loudness window, starts just before the action
    'cap_size':   [72, 84, 96],                  # caption font size
    'cap_pos':    ['low', 'mid'],                # captions low on the gameplay or right under the seam
    'hook_style': ['streamer', 'reaction', 'stakes', 'question'],
    'hook_case':  ['upper', 'title'],
    'opener':     ['normal', 'cold'],            # cold = flash the payoff (~1.2 s) first, then play from the start
    'ending':     ['runon', 'hard'],             # hard = cut 0.5 s after the last big moment, no tail
    'punch':      ['off', 'on'],                 # on = 1.15x punch-in zoom on the gameplay for 0.6 s at the loudest moment
}
HOOK_STYLES = {
    'streamer': 'lead with the streamer name and what they did ("XQC STEALS HIS FIRST CAR")',
    'reaction': 'describe the reaction, not the event ("HE COULD NOT BELIEVE THIS")',
    'stakes':   'tease the payoff without giving it away ("WAIT FOR THE COPS")',
    'question': 'ask a short question the clip answers ("WHO TAUGHT HIM TO DRIVE?")',
}
EXPLORE = 0.25          # chance to pick a random option per gene
TOPICS = {              # keyword -> topic tag (titles + transcripts)
    'police': r'cop|police|wanted|chase|arrest|swat|sheriff',
    'car': r'\bcar\b|drive|driving|crash|steal|stole|jack|getaway|bike',
    'npc': r'npc|lady|guy|man|woman|dude|grandma',
    'money': r'money|cash|rob|heist|bank|store|\$',
    'glitch': r'glitch|bug|physics|ragdoll|broke',
    'rage': r'rage|mad|angry|scream|lost it|tilted',
    'funny': r'lol|lmao|funny|laugh|bro|wtf|no way|insane|crazy',
    'map': r'map|beach|city|vice|leonida|swamp|keys|everglades|gator|alligator',
    'weapon': r'gun|shoot|shot|fight|punch|knife',
}


def load():
    h = json.load(open(HIST)) if os.path.exists(HIST) else {'posts': []}
    h['trend'] = json.load(open(TREND)) if os.path.exists(TREND) else {}
    return h


def save(h):
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    json.dump({'posts': h['posts']}, open(HIST, 'w'), indent=1)


def save_trend(tr):
    os.makedirs(os.path.dirname(TREND), exist_ok=True)
    json.dump(tr, open(TREND, 'w'), indent=1)


def topics_of(text):
    t = (text or '').lower()
    return sorted(k for k, rx in TOPICS.items() if re.search(rx, t))


def len_bucket(d):
    return '<20' if d < 20 else '20-35' if d < 35 else '35-50' if d < 50 else '50+'


def traits(meta, post_hour=None):
    """Clip traits used for ranking. post_hour is the local hour it was posted."""
    tr = {'streamer': meta.get('login', '?').lower(),
          'len': len_bucket(meta.get('kept', meta.get('duration', 30))),
          'layout': meta.get('layout', '?')}
    if meta.get('action') is not None:
        a = meta['action']
        tr['action'] = 'low' if a < 0.45 else 'mid' if a < 0.6 else 'high'
    for t in topics_of(meta.get('title', '') + ' ' + meta.get('transcript', '')):
        tr['topic:' + t] = 1
    if post_hour is not None:
        tr['hour'] = '%02d' % (int(post_hour) // 3 * 3)
    return tr


# ---- scoring --------------------------------------------------------------------------------------
def post_score(m, dur):
    """One number per post. Views dominate; retention, shares and saves break ties.
    log-views so one viral outlier doesn't swamp everything."""
    views = max(m.get('views', 0), 0)
    s = math.log10(views + 10)
    watch = m.get('avg_watch', 0) or 0
    if dur and watch:
        s += 0.8 * min(watch / dur, 1.2)
    reach = max(m.get('reach', 0), 1)
    s += 3.0 * (m.get('shares', 0) + m.get('saves', 0)) / reach
    skip = m.get('skip_rate')
    if skip is not None:
        s -= 0.5 * (skip / 100 if skip > 1 else skip)
    return s


def scored(h):
    """Posts with numbers, each with a lift = its score minus the median of its 8 neighbours."""
    ps = [p for p in h['posts'] if p.get('metrics') and p['metrics'].get('age_h', 0) >= 20]
    out = []
    for i, p in enumerate(ps):
        nb = [q['score'] for q in ps[max(0, i - 4):i + 5] if q is not p and 'score' in q]
        med = sorted(nb)[len(nb) // 2] if nb else p['score']
        out.append((p, p['score'] - med if nb else 0.0))
    return out


def lifts(h):
    """(group, value) -> (evidence, average lift). Newer posts count more (half-life ~ 40 posts)."""
    acc = {}
    sp = scored(h)
    n = len(sp)
    for i, (p, lift) in enumerate(sp):
        w = 0.5 ** ((n - 1 - i) / 40)
        keys = [('gene:' + g, str(v)) for g, v in p.get('genes', {}).items()]
        keys += [(k.split(':')[0] if ':' in k else k, k.split(':', 1)[1] if ':' in k else str(v))
                 for k, v in p.get('traits', {}).items()]
        for k in keys:
            e, s = acc.get(k, (0.0, 0.0))
            acc[k] = (e + w, s + w * lift)
    return {k: (e, s / e) for k, (e, s) in acc.items()}


def shrunk(e, avg, k=3.0):
    """Average lift pulled toward 0 until there is evidence (k posts' worth)."""
    return avg * e / (e + k)


# ---- choosing -------------------------------------------------------------------------------------
def choose(h=None, rnd=random):
    h = h or load()
    L = lifts(h)
    used = {}
    for p in h['posts']:
        for g, v in p.get('genes', {}).items():
            used[(g, str(v))] = used.get((g, str(v)), 0) + 1
    genes = {}
    for g, opts in GENES.items():
        fresh = [o for o in opts if used.get((g, str(o)), 0) < 2]   # every option gets 2 tries first
        if fresh:
            genes[g] = rnd.choice(fresh)
        elif rnd.random() < EXPLORE:
            genes[g] = rnd.choice(opts)
        else:
            sc = {o: shrunk(*L.get(('gene:' + g, str(o)), (0, 0))) for o in opts}
            # softmax so close options still share airtime
            ws = [math.exp(sc[o] / 0.25) for o in opts]
            genes[g] = rnd.choices(opts, ws)[0]
    return genes


def rank(meta, base, h=None):
    """Rising score x learned multipliers x trend boost."""
    h = h or load()
    L = lifts(h)
    mult = 1.0
    for k, v in traits(meta).items():
        grp, val = (k.split(':')[0], k.split(':', 1)[1]) if ':' in k else (k, str(v))
        e, a = L.get((grp, val), (0, 0))
        mult *= math.exp(0.6 * shrunk(e, a))
    tr = h.get('trend', {})
    for t in topics_of(meta.get('title', '')):
        mult *= 1 + min(tr.get('topics', {}).get(t, 0), 1.0) * 0.5          # up to +50% for a hot topic
    mult *= 1 + min(tr.get('streamers', {}).get(meta.get('login', '').lower(), 0), 1.0) * 0.5
    return base * mult


# ---- recording ------------------------------------------------------------------------------------
def record(meta, media_id, posted_at, post_hour):
    h = load()
    h['posts'].append({'id': meta['id'], 'media_id': media_id, 'posted_at': posted_at,
                       'genes': meta.get('genes', {}), 'traits': traits(meta, post_hour),
                       'hook': meta.get('hook'), 'duration': meta.get('kept', meta.get('duration'))})
    save(h)


def ingest(path):
    """Windsor instagram get_data json (fields media_id, timestamp, media_views, media_reach,
    media_like_count, media_comments_count, media_shares, media_saved, media_reel_avg_watch_time,
    media_reel_skip_rate)."""
    raw = json.load(open(path))
    rows = raw.get('data', raw) if isinstance(raw, dict) else raw
    by = {}
    for r in rows:
        mid = str(r.get('media_id', ''))
        if mid:
            by[mid] = r
    h = load()
    now = time.time()
    n = 0
    for p in h['posts']:
        r = by.get(str(p.get('media_id')))
        if not r:
            continue
        ts = calendar.timegm(time.strptime(p['posted_at'][:19], '%Y-%m-%dT%H:%M:%S'))
        aw = r.get('media_reel_avg_watch_time') or 0
        aw = aw / 1000 if aw > 300 else aw                 # API reports ms
        p['metrics'] = {'views': r.get('media_views') or 0, 'reach': r.get('media_reach') or 0,
                        'likes': r.get('media_like_count') or 0, 'comments': r.get('media_comments_count') or 0,
                        'shares': r.get('media_shares') or 0, 'saves': r.get('media_saved') or 0,
                        'avg_watch': aw, 'skip_rate': r.get('media_reel_skip_rate'),
                        'age_h': round((now - ts) / 3600, 1)}
        p['score'] = round(post_score(p['metrics'], p.get('duration') or 30), 3)
        n += 1
    save(h)
    report(h)
    sp = scored(h)
    print('learned so far: %d posts with numbers (%d mature), best lift %s' %
          (n, len(sp), max((round(l, 2) for _, l in sp), default='n/a')))


def report(h=None):
    h = h or load()
    L = lifts(h)
    groups = {}
    for (g, v), (e, a) in L.items():
        groups.setdefault(g, []).append((shrunk(e, a), e, v))
    lines = ['# What Leonida Replay has learned', '',
             'Posts: %d. Scored (20h+ old): %d. Exploration: %d%%.' % (len(h['posts']), len(scored(h)), EXPLORE * 100), '',
             'Lift = how far posts with this option beat their neighbours (log-views + retention). Positive is good.', '']
    for g in sorted(groups):
        lines += ['## ' + g, '', '| option | evidence | lift |', '|---|---|---|']
        for s, e, v in sorted(groups[g], reverse=True):
            lines.append('| %s | %.1f | %+.2f |' % (v, e, s))
        lines.append('')
    tr = h.get('trend', {})
    if tr:
        lines += ['## Trending right now (from the last fetch)', '',
                  'Topics: ' + ', '.join('%s %.2f' % kv for kv in sorted(tr.get('topics', {}).items(), key=lambda x: -x[1])[:8]),
                  '', 'Streamers: ' + ', '.join('%s %.2f' % kv for kv in sorted(tr.get('streamers', {}).items(), key=lambda x: -x[1])[:8]),
                  '', 'Hook ideas from trending Shorts titles:', ''] + ['- ' + t for t in tr.get('titles', [])[:10]] + ['']
    open(os.path.join(HERE, 'LEARNING.md'), 'w').write('\n'.join(lines))


if __name__ == '__main__':
    if sys.argv[1:2] == ['ingest']:
        ingest(sys.argv[2])
    elif sys.argv[1:2] == ['report']:
        report()
        print(open(os.path.join(HERE, 'LEARNING.md')).read())
    elif sys.argv[1:2] == ['choose']:
        print(json.dumps(choose()))
    else:
        print(__doc__)
