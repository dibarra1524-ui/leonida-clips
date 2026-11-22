"""fetch.py - runs in GitHub Actions every 2 hours.
Pulls the fastest-rising GTA VI Twitch clips from approved streamers, renders the best new ones
into queue/<clip_id>.mp4 + .json, and prunes old files. Needs TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET."""
import datetime as dt, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import render, learn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
GAME = os.environ.get('GAME_NAME', 'Grand Theft Auto VI')
PER_RUN = int(os.environ.get('PER_RUN', '3'))        # non-campaign clips rendered per run
CAMP_PER_STREAMER = int(os.environ.get('CAMP_PER_STREAMER', '3'))   # campaign streamers get their own slots on top of PER_RUN
CAMP_MIN_ACTION = float(os.environ.get('CAMP_MIN_ACTION', '0.2'))   # campaign clips pay for featuring the streamer, so a lower energy bar
QUEUE_MAX = int(os.environ.get('QUEUE_MAX', '24'))
MIN_VIEWS = int(os.environ.get('MIN_VIEWS', '25'))
LANGS = set(os.environ.get('CLIP_LANGS', 'en').split(','))   # page language(s); a Spanish page would set es
MIN_ACTION = float(os.environ.get('MIN_ACTION', '0.35'))   # clips scoring below this (calm, silent, dead air) are dropped
AUTO_MAX = int(os.environ.get('AUTO_MAX', '25'))      # cap on the streamer list when trend radar adds people
HOT = os.environ.get('HOT') == '1'                       # fast lane: only campaign streamers, only clips rising right now
HOT_WINDOW_MIN = int(os.environ.get('HOT_WINDOW_MIN', '90'))
HOT_MIN_VIEWS = int(os.environ.get('HOT_MIN_VIEWS', '15'))
HOT_VEL = float(os.environ.get('HOT_VEL', '0.5'))          # views per minute since the clip was made
HOT_MAX = int(os.environ.get('HOT_MAX', '2'))
BAD_TITLE = re.compile(r'spoil|ending|final mission|last mission|credits|finale|beat the game|leak|strip|horny|lap ?dance|private dance|nsfw|naked|nude|thirst|rizz|hooker|prostitut|sexy|boob|twerk', re.I)


def load(p, default):
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p))
    except ValueError:
        print('unreadable json, ignoring:', p)
        return default


def _plain(o):
    # numpy numbers and other non-JSON types (e.g. float32 action scores) become plain Python values
    if hasattr(o, 'item'):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def save(p, obj):
    txt = json.dumps(obj, indent=1, default=_plain)    # serialize first, so a bad value can never half-write the file
    tmp = p + '.tmp'
    open(tmp, 'w').write(txt)
    os.replace(tmp, p)


class Twitch:
    def __init__(self):
        cid, sec = os.environ['TWITCH_CLIENT_ID'], os.environ['TWITCH_CLIENT_SECRET']
        body = urllib.parse.urlencode({'client_id': cid, 'client_secret': sec,
                                       'grant_type': 'client_credentials'}).encode()
        tok = json.load(urllib.request.urlopen('https://id.twitch.tv/oauth2/token', body, timeout=30))
        self.h = {'Client-Id': cid, 'Authorization': 'Bearer ' + tok['access_token']}

    def get(self, path, params):
        q = urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request('https://api.twitch.tv/helix/%s?%s' % (path, q), headers=self.h)
        for i in range(3):
            try:
                return json.load(urllib.request.urlopen(req, timeout=30))['data']
            except urllib.error.HTTPError as e:
                if e.code == 429 and i < 2:
                    time.sleep(5)
                    continue
                raise


def kick_clips(slug, since_dt):
    """GTA VI clips from a Kick channel (public v2 endpoint), shaped like Twitch clips."""
    req = urllib.request.Request('https://kick.com/api/v2/channels/%s/clips?sort=view&time=day' % slug,
                                 headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                          'Chrome/131 Safari/537.36', 'Accept': 'application/json'})
    data = json.load(urllib.request.urlopen(req, timeout=30)).get('clips') or []
    out = []
    for k in data:
        if (k.get('category') or {}).get('slug') != 'grand-theft-auto-vi' or k.get('is_mature'):
            continue
        ts = k['created_at'].rstrip('Z').split('+')[0]
        base, _, frac = ts.partition('.')
        created = dt.datetime.fromisoformat(base).replace(tzinfo=dt.timezone.utc)
        if created < since_dt:
            continue
        title = k.get('title') or ''
        if not re.search(r'[aeiou].*\s', title, re.I):       # Kick clip titles are often keyboard mash
            title = 'GTA 6 moment'
        out.append({'id': 'kick_' + k['id'].replace('clip_', ''), 'url': k.get('video_url') or k['clip_url'],
                    'title': title, 'view_count': k.get('views') or k.get('view_count') or 0,
                    'created_at': created.strftime('%Y-%m-%dT%H:%M:%SZ'), 'duration': k.get('duration') or 0,
                    'broadcaster_id': 'kick:' + slug, 'game_id': 'kick-gta6', 'language': 'en',
                    'video_id': 'kick' + str(k.get('livestream_id')),
                    'vod_offset': int(created.timestamp()) % 100000,
                    '_kick_channel': (k.get('channel') or {}).get('username') or slug})
    return out


def score(c, now):
    age = (now - dt.datetime.fromisoformat(c['created_at'].replace('Z', '+00:00'))).total_seconds() / 3600
    return c['view_count'] / (age + 2) ** 1.2


def main():
    cfg = load('streamers.json', {'streamers': []})
    ok = [s for s in cfg['streamers'] if s.get('approved')]
    seen = load('state/seen.json', {'ids': [], 'moments': []})
    posted = load('state/posted.json', [])
    queued = [f[:-5] for f in os.listdir('queue') if f.endswith('.json')]
    prune(posted)
    if len(queued) >= QUEUE_MAX and not HOT:
        print('queue full (%d)' % len(queued))
        return
    tw = Twitch()
    game = tw.get('games', {'name': GAME})
    if not game:
        sys.exit('game not found on Twitch: %s' % GAME)
    gid = game[0]['id']
    hist = learn.load()
    if HOT:
        ok = [s for s in ok if isinstance(s.get('campaign'), dict)]
        print('fast lane: watching', ', '.join(s['login'] for s in ok))
    try:
        if HOT:
            raise RuntimeError('skipped in fast lane')
        trend_radar(tw, gid, cfg, hist)
        learn.save_trend(hist['trend'])
        ok = [s for s in cfg['streamers'] if s.get('approved')]
    except Exception as e:
        print('trend radar failed:', e)
    only = {x.strip().lower() for x in os.environ.get('ONLY', '').split(',') if x.strip()}
    if only:    # manual run limited to named streamers
        ok = [s for s in ok if s['login'].lower() in only]
        print('only:', ', '.join(s['login'] for s in ok))
    users = {}
    for i in range(0, len(ok), 100):
        for u in tw.get('users', {'login': [s['login'] for s in ok[i:i + 100] if not s.get('kick_only')]}):
            users[u['id']] = u
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - (dt.timedelta(minutes=HOT_WINDOW_MIN) if HOT else dt.timedelta(hours=36))).strftime('%Y-%m-%dT%H:%M:%SZ')
    clips = []
    for uid in users:
        for c in tw.get('clips', {'broadcaster_id': uid, 'started_at': since, 'first': 30}):
            if c['game_id'] != gid or c['id'] in seen['ids'] or c['id'] in queued or c.get('language', 'en')[:2] not in LANGS:
                continue
            if c['view_count'] < MIN_VIEWS or not (10 <= c['duration'] <= 60) or BAD_TITLE.search(c['title']):
                continue
            if HOT:
                mins = max(5.0, (now - dt.datetime.fromisoformat(c['created_at'].replace('Z', '+00:00'))).total_seconds() / 60)
                if c['view_count'] < HOT_MIN_VIEWS or c['view_count'] / mins < HOT_VEL:
                    continue
            if c.get('vod_offset') is not None and c.get('video_id'):
                m = '%s:%d' % (c['video_id'], c['vod_offset'] // 45)
                if m in seen['moments']:
                    continue
                c['_moment'] = m
            clips.append(c)
    since_dt = now - dt.timedelta(hours=36)
    for st in [s for s in ok if s.get('kick') and not HOT]:
        slug = st['kick']
        try:
            got = kick_clips(slug, since_dt)
        except Exception as e:
            print('kick failed for', slug, e)
            continue
        users['kick:' + slug] = {'id': 'kick:' + slug, 'login': st['login'], 'display_name': got[0]['_kick_channel'] if got else slug,
                                 'credit': 'kick.com/' + slug}
        for c in got:
            if c['id'] in seen['ids'] or c['id'] in queued or c['view_count'] < MIN_VIEWS * 4 or \
                    not (10 <= c['duration'] <= 60) or BAD_TITLE.search(c['title']):
                continue
            c['game_id'] = gid
            clips.append(c)
    for c in clips:
        c['_rank'] = learn.rank({'login': users[c['broadcaster_id']]['login'], 'title': c['title'],
                                 'duration': c['duration']}, score(c, now), hist)
    camp = {s['login'].lower() for s in ok if isinstance(s.get('campaign'), dict)}
    is_camp = lambda c: users[c['broadcaster_id']]['login'].lower() in camp
    clips.sort(key=lambda c: (is_camp(c), c['_rank']), reverse=True)
    room = (HOT_MAX if HOT else QUEUE_MAX - len(queued))
    # campaign streamers: up to CAMP_PER_STREAMER queued each, tried from a deeper candidate list (they pay per view)
    # everyone else: one moment per batch, max 2 per streamer, PER_RUN queued
    picked, moments, per = [], set(), {}
    for c in clips:
        cap = CAMP_PER_STREAMER * 3 if is_camp(c) else 2
        if c.get('_moment') in moments or per.get(c['broadcaster_id'], 0) >= cap:
            continue
        if not is_camp(c) and sum(1 for x in picked if not is_camp(x)) >= 3 * PER_RUN:
            continue
        picked.append(c)
        moments.add(c.get('_moment'))
        per[c['broadcaster_id']] = per.get(c['broadcaster_id'], 0) + 1
    print('%d candidate clips (%d campaign), trying %d, room %d' % (len(clips), sum(map(is_camp, clips)), len(picked), room))
    made, made_by, made_other = 0, {}, 0
    for c in picked:
        if made >= room:
            break
        b = c['broadcaster_id']
        if is_camp(c):
            if made_by.get(b, 0) >= CAMP_PER_STREAMER:
                continue
        elif made_other >= PER_RUN:
            continue
        ok_ = False
        try:
            ok_ = make(c, users[b], cfg, now, CAMP_MIN_ACTION if is_camp(c) else MIN_ACTION)
        except Exception as e:
            print('failed %s: %s' % (c['id'], e))
        if ok_:
            made += 1
            if is_camp(c):
                made_by[b] = made_by.get(b, 0) + 1
            else:
                made_other += 1
        seen['ids'].append(c['id'])
        if c.get('_moment'):
            seen['moments'].append(c['_moment'])
    seen['ids'], seen['moments'] = seen['ids'][-3000:], seen['moments'][-3000:]
    save('state/seen.json', seen)
    save('streamers.json', cfg)


def make(c, user, cfg, now, min_action=None):
    tmp = tempfile.mkdtemp()
    raw = os.path.join(tmp, 'raw.mp4')
    subprocess.run(['yt-dlp', '-q', '-f', 'best[height<=1080]/best', '-o', raw, c['url']], check=True, timeout=300)
    st = next(s for s in cfg['streamers'] if s['login'].lower() == user['login'].lower())
    layout = st.get('layout', 'auto')
    dst = 'queue/%s.mp4' % c['id']
    genes = learn.choose()
    cam, transcript, used, kept, action = render.render(
        raw, dst, cam=st.get('cam'), layout=layout, credit=user.get('credit') or 'twitch.tv/' + user['login'],
        genes=dict(genes, _title=c['title'], _min_action=MIN_ACTION if min_action is None else min_action))
    if used == 'rejected':
        print('dropped %s (%s): action score %s %s' % (c['id'], user['login'], action[0], action[1]))
        return False
    if cam and not st.get('cam'):
        st['cam'] = cam
    meta = {'id': c['id'], 'streamer': user['display_name'], 'login': user['login'], 'title': c['title'],
            'views': c['view_count'], 'created_at': c['created_at'], 'duration': c['duration'],
            'clip_url': c['url'], 'platform': 'kick' if c['id'].startswith('kick_') else 'twitch',
            'credit': user.get('credit') or 'twitch.tv/' + user['login'], 'layout': used, 'transcript': transcript,
            'score': round(c.get('_rank', score(c, now)), 1), 'campaign': st.get('campaign'),
            'genes': genes, 'kept': kept, 'action': action[0], 'action_parts': action[1],
            'queued_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'hot': HOT}
    save('queue/%s.json' % c['id'], meta)
    print('queued', c['id'], user['login'], c['view_count'], 'views', used, 'action', action[0])
    return True


def trend_radar(tw, gid, cfg, hist):
    """What is blowing up right now, across ALL of GTA VI on Twitch (not just our list):
    hot topics in clip titles, streamers whose clips are exploding or who are live with big crowds,
    and hook wording from the top GTA 6 Shorts on YouTube. Adds breakout streamers to the list."""
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')
    clips = tw.get('clips', {'game_id': gid, 'started_at': since, 'first': 100})
    topic_w, streamer_w, logins, lang = {}, {}, {}, {}
    for c in clips:
        lang[c['broadcaster_id']] = (c.get('language') or 'en')[:2]
        if lang[c['broadcaster_id']] not in LANGS:
            continue
        v = score(c, now)
        for t in learn.topics_of(c['title']):
            topic_w[t] = topic_w.get(t, 0) + v
        streamer_w[c['broadcaster_id']] = streamer_w.get(c['broadcaster_id'], 0) + v
    live = tw.get('streams', {'game_id': gid, 'first': 30})
    for st in live:
        lang[st['user_id']] = (st.get('language') or 'en')[:2]
        if lang[st['user_id']] not in LANGS:
            continue
        streamer_w[st['user_id']] = streamer_w.get(st['user_id'], 0) + st['viewer_count'] / 50
        logins[st['user_id']] = st['user_login']
    ids = [i for i in streamer_w if i not in logins]
    for i in range(0, len(ids), 100):
        for u in tw.get('users', {'id': ids[i:i + 100]}):
            logins[u['id']] = u['login']
    norm = lambda d: {k: round(v / max(d.values()), 3) for k, v in d.items()} if d else {}
    sw = norm({logins.get(k, k).lower(): v for k, v in streamer_w.items()})
    tr = {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'topics': norm(topic_w),
          'streamers': dict(sorted(sw.items(), key=lambda x: -x[1])[:30]),
          'titles': hist.get('trend', {}).get('titles', []), 'added': hist.get('trend', {}).get('added', [])}
    # breakout streamers join the list (unless blocked), up to AUTO_MAX in total
    have = {s['login'].lower() for s in cfg['streamers']}
    blocked = {b.lower() for b in cfg.get('blocked', [])}
    for login, w in sorted(sw.items(), key=lambda x: -x[1]):
        if cfg.get('top_n') or len(cfg['streamers']) >= AUTO_MAX or w < 0.25:
            break
        if login not in have and login not in blocked:
            cfg['streamers'].append({'login': login, 'approved': True, 'cam': None, 'auto': tr['at']})
            tr['added'] = (tr['added'] + [login])[-50:]
            print('trend radar added streamer', login, w)
    # hook wording: top GTA 6 Shorts on YouTube right now (best effort)
    try:
        out = subprocess.run(['yt-dlp', '-J', '--flat-playlist', 'ytsearch40:gta 6 shorts'],
                             capture_output=True, text=True, timeout=120).stdout
        ents = [e for e in json.loads(out).get('entries', []) if e.get('title')]
        ents.sort(key=lambda e: -(e.get('view_count') or 0))
        tr['titles'] = [e['title'][:90] for e in ents[:15]]
        for e in ents[:15]:
            for t in learn.topics_of(e['title']):
                tr['topics'][t] = round(min(1.0, tr['topics'].get(t, 0) + 0.1), 3)
    except Exception as e:
        print('youtube trend check failed:', e)
    hist['trend'] = tr
    print('trend topics', sorted(tr['topics'].items(), key=lambda x: -x[1])[:5])


def prune(posted):
    """Drop queued clips older than 30h (stale at launch speed) and posted files older than 3 days."""
    # file mtimes are reset by every git checkout, so ages come from the timestamps in the json files
    now = dt.datetime.now(dt.timezone.utc)
    age = lambda s: (now - dt.datetime.fromisoformat(s.replace('Z', '+00:00'))).total_seconds() / 3600
    for f in os.listdir('queue'):
        if f.endswith('.json'):
            meta = load(os.path.join('queue', f), None)
            if meta is None or age(meta.get('queued_at', '2000-01-01T00:00:00Z')) > 30:
                for ext in ('.json', '.mp4'):
                    q = os.path.join('queue', f[:-5] + ext)
                    if os.path.exists(q):
                        os.remove(q)
    for p in posted:
        f = os.path.join('out', p['id'] + '.mp4')
        if os.path.exists(f) and age(p.get('posted_at', '2000-01-01T00:00:00Z')) > 72:
            os.remove(f)


if __name__ == '__main__':
    main()
